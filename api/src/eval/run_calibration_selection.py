"""Calibration method selection and final Phase 1 evaluation (SPEC.md
sections 6.2/9.3, Phase 1 session 3).

Loads session 2's already-trained booster (unchanged this session - this is
calibration work, not retraining), fits all five calibration candidates on
an early chunk of validation, selects the winner on a later, held-out chunk
of validation (Decision 1 - fitting and selecting on the same data would be
circular), and touches test exactly once at the end: the winner's
reliability diagram, the paired bootstrap vs both section 9.2 baselines,
and a quarter-bucketed distribution-shift check (Decision 6).

Usage (from the api/ directory, with api/.env configured):
    python -m eval.run_calibration_selection
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import psycopg
from dotenv import dotenv_values

from eval.baselines import HistoricalBaseRateBaseline, LogisticBaseline
from eval.metrics import (
    brier_score,
    log_loss,
    paired_brier_match_clustered_ci,
    reliability_match_clustered,
)
from eval.splits import get_second_innings_split
from models.calibration import CANDIDATES, split_for_calibration
from models.registry import save_model_version
from models.win_prob_2nd import build_feature_bundle

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
REPORTS_DIR = REPO_ROOT / "api" / "data" / "eval_reports"

SESSION2_ARTIFACT = REPO_ROOT / "api" / "data" / "models" / "win_prob_2nd" / "seed42_1787830785.pkl"
CALIBRATION_SPLIT_DATE = date(2024, 7, 1)
FINAL_3_OVERS_BALLS = 18

# Nominal (unreduced) T20 boundaries, per Phase 0 session 6's Decision 5
# table: powerplay/middle at over 6 (84 balls remaining of 120), middle/
# death at over 15 (30 balls remaining). +-1 over either side for the
# monotonicity check's row window.
PP_MIDDLE_BOUNDARY_BALLS_REMAINING = 84
MIDDLE_DEATH_BOUNDARY_BALLS_REMAINING = 30
BOUNDARY_WINDOW_BALLS = 6


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def _select_chunk_report(y_true, raw_pred, phase, match_id) -> dict:
    brier = brier_score(y_true, raw_pred)
    reliability = reliability_match_clustered(y_true, raw_pred, match_id, n_bins=10)
    populated = [row for row in reliability if row["n"] > 0]
    failures = sum(1 for row in populated if not row["contains_predicted"])
    return {
        "brier": brier,
        "n_deciles_populated": len(populated),
        "n_deciles_failed": failures,
        "reliability": reliability,
    }


def check_boundary_monotonicity(conn, winner_name: str, winner) -> dict | None:
    """Decision 5: for a phase-stratified winner, does the SAME raw score
    calibrate differently just before vs. just after a phase boundary?
    Restricted to nominal-length T20 innings (target_overs IS NULL or 20)
    to avoid the reduced-overs scaling complication."""
    if "phase" not in winner_name:
        return None

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ms.balls_remaining, ms.phase
            FROM match_states ms
            JOIN matches m ON m.match_id = ms.match_id
            WHERE ms.innings = 2 AND ms.batting_team_won IS NOT NULL
              AND NOT ms.has_reconciliation_anomaly AND ms.required_run_rate IS NOT NULL
              AND m.format = 'T20' AND (m.target_overs IS NULL OR m.target_overs = 20)
              AND (
                ms.balls_remaining BETWEEN %(pp_lo)s AND %(pp_hi)s
                OR ms.balls_remaining BETWEEN %(md_lo)s AND %(md_hi)s
              )
            """,
            {
                "pp_lo": PP_MIDDLE_BOUNDARY_BALLS_REMAINING - BOUNDARY_WINDOW_BALLS,
                "pp_hi": PP_MIDDLE_BOUNDARY_BALLS_REMAINING + BOUNDARY_WINDOW_BALLS,
                "md_lo": MIDDLE_DEATH_BOUNDARY_BALLS_REMAINING - BOUNDARY_WINDOW_BALLS,
                "md_hi": MIDDLE_DEATH_BOUNDARY_BALLS_REMAINING + BOUNDARY_WINDOW_BALLS,
            },
        )
        rows = cur.fetchall()

    results = {}
    for boundary_name, phase_a, phase_b in (
        ("powerplay_middle", "powerplay", "middle"),
        ("middle_death", "middle", "death"),
    ):
        phases_present = {r[1] for r in rows}
        if phase_a not in phases_present or phase_b not in phases_present:
            results[boundary_name] = {"n_sample_scores": 0}
            continue
        # A representative set of raw scores that actually occur near this
        # boundary - sample a spread across [0.05, 0.95] rather than
        # depending on which exact rows the query returned.
        sample_scores = np.linspace(0.05, 0.95, 19)
        pred_a = winner.predict(sample_scores, phase=np.full(len(sample_scores), phase_a, dtype=object))
        pred_b = winner.predict(sample_scores, phase=np.full(len(sample_scores), phase_b, dtype=object))
        jumps = np.abs(pred_a - pred_b)
        results[boundary_name] = {
            "n_sample_scores": len(sample_scores),
            "median_jump": float(np.median(jumps)),
            "max_jump": float(np.max(jumps)),
        }
    return results


def run() -> dict:
    start = time.monotonic()
    artifact = joblib.load(SESSION2_ARTIFACT)
    booster, feature_names = artifact["booster"], artifact["feature_names"]
    print(f"loaded session 2 artifact: {SESSION2_ARTIFACT.name}, best_iteration={booster.best_iteration}")

    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
        val_ds = get_second_innings_split(conn, "val")
        test_ds = get_second_innings_split(conn, "test")
        val_bundle = build_feature_bundle(conn, val_ds)
        test_bundle = build_feature_bundle(conn, test_ds)
    print(f"val: {len(val_ds)} rows / {len(np.unique(val_ds.match_id))} matches, "
          f"test: {len(test_ds)} rows / {len(np.unique(test_ds.match_id))} matches")

    X_val, _ = val_bundle.select("state_venue_elo")
    X_test, _ = test_bundle.select("state_venue_elo")
    raw_val = booster.predict(X_val, num_iteration=booster.best_iteration)
    raw_test = booster.predict(X_test, num_iteration=booster.best_iteration)

    # --- Decision 1: temporal fit/select split of validation ---------------
    fit_mask = val_ds.match_date <= np.datetime64(CALIBRATION_SPLIT_DATE)
    select_mask = ~fit_mask
    n_fit_matches = len(np.unique(val_ds.match_id[fit_mask]))
    n_select_matches = len(np.unique(val_ds.match_id[select_mask]))
    print(f"calibration fit chunk: {fit_mask.sum()} rows / {n_fit_matches} matches "
          f"(<= {CALIBRATION_SPLIT_DATE})")
    print(f"calibration select chunk: {select_mask.sum()} rows / {n_select_matches} matches "
          f"(> {CALIBRATION_SPLIT_DATE})")

    report: dict = {
        "calibration_split_date": str(CALIBRATION_SPLIT_DATE),
        "n_fit_matches": n_fit_matches,
        "n_select_matches": n_select_matches,
    }

    # --- Decision 2: five candidates, fit on fit chunk, score on select ---
    candidates_fitted = {}
    comparison = {}
    for name, factory in CANDIDATES.items():
        calibrator = factory()
        calibrator.fit(raw_val[fit_mask], val_ds.label[fit_mask], phase=val_ds.phase[fit_mask])
        candidates_fitted[name] = calibrator

        select_pred = calibrator.predict(raw_val[select_mask], phase=val_ds.phase[select_mask])
        comparison[name] = _select_chunk_report(
            val_ds.label[select_mask], select_pred, val_ds.phase[select_mask], val_ds.match_id[select_mask]
        )
        print(f"candidate={name} select-chunk brier={comparison[name]['brier']:.4f} "
              f"decile_failures={comparison[name]['n_deciles_failed']}/{comparison[name]['n_deciles_populated']}")
    report["comparison"] = comparison

    winner_name = min(comparison, key=lambda k: comparison[k]["brier"])
    print(f"winner (by select-chunk Brier): {winner_name}")
    report["winner"] = winner_name

    # --- Decision 5: monotonicity, if the winner is phase-stratified -------
    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
        monotonicity = check_boundary_monotonicity(conn, winner_name, candidates_fitted[winner_name])
    report["monotonicity"] = monotonicity
    if monotonicity:
        print(f"monotonicity check: {monotonicity}")

    # --- Refit the winner on ALL of validation (fit+select) for the final
    # model - the fit/select split was for CHOOSING a method, not for
    # withholding data from the one that won. --------------------------
    final_calibrator = CANDIDATES[winner_name]()
    final_calibrator.fit(raw_val, val_ds.label, phase=val_ds.phase)

    # --- Test touched exactly once from here on -----------------------------
    calibrated_test = final_calibrator.predict(raw_test, phase=test_ds.phase)

    final_3 = test_ds.balls_remaining <= FINAL_3_OVERS_BALLS
    report["final_brier"] = {
        "overall": brier_score(test_ds.label, calibrated_test),
        "final_3_overs": brier_score(test_ds.label[final_3], calibrated_test[final_3]),
    }
    report["final_log_loss"] = {
        "overall": log_loss(test_ds.label, calibrated_test),
        "final_3_overs": log_loss(test_ds.label[final_3], calibrated_test[final_3]),
    }
    report["final_reliability"] = reliability_match_clustered(test_ds.label, calibrated_test, test_ds.match_id)
    print(f"FINAL test Brier: overall={report['final_brier']['overall']:.4f} "
          f"final_3_overs={report['final_brier']['final_3_overs']:.4f}")

    # --- Paired bootstrap vs both baselines ---------------------------------
    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
        train_ds = get_second_innings_split(conn, "train")
    logistic_prob = LogisticBaseline().fit(train_ds).predict_proba(test_ds)
    base_rate_prob = HistoricalBaseRateBaseline().fit(train_ds).predict_proba(test_ds)
    report["vs_baselines"] = {
        "logistic": paired_brier_match_clustered_ci(test_ds.label, logistic_prob, calibrated_test, test_ds.match_id),
        "historical_base_rate": paired_brier_match_clustered_ci(
            test_ds.label, base_rate_prob, calibrated_test, test_ds.match_id
        ),
    }

    # --- Decision 6: distribution shift over time ---------------------------
    def _quarter_label(d: np.datetime64) -> str:
        py_date = d.astype("datetime64[D]").item()
        return f"{py_date.year}-Q{(py_date.month - 1) // 3 + 1}"

    quarters = np.array([_quarter_label(d) for d in test_ds.match_date])
    by_quarter = {}
    for q in sorted(set(quarters.tolist())):
        mask = quarters == q
        rel = reliability_match_clustered(test_ds.label[mask], calibrated_test[mask], test_ds.match_id[mask])
        populated = [row for row in rel if row["n"] > 0]
        failures = sum(1 for row in populated if not row["contains_predicted"])
        by_quarter[q] = {
            "n_matches": int(len(np.unique(test_ds.match_id[mask]))),
            "brier": brier_score(test_ds.label[mask], calibrated_test[mask]),
            "n_deciles_failed": failures,
            "n_deciles_populated": len(populated),
        }
        print(f"quarter={q} n_matches={by_quarter[q]['n_matches']} brier={by_quarter[q]['brier']:.4f} "
              f"decile_failures={failures}/{len(populated)}")
    report["by_quarter"] = by_quarter

    # --- Persist the final artifact + local model_versions row --------------
    model_version = f"winprob2-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    final_artifact = {
        "booster": booster,
        "calibrator": final_calibrator,
        "calibrator_name": winner_name,
        "feature_names": feature_names,
        "seed": artifact["seed"],
    }
    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
        artifact_path = save_model_version(
            conn, model_version, final_artifact,
            train_end_date=date(2023, 12, 31),
            test_brier=report["final_brier"]["overall"],
            test_log_loss=report["final_log_loss"]["overall"],
        )
    report["model_version"] = model_version
    report["artifact_path"] = str(artifact_path)

    elapsed = time.monotonic() - start
    report["elapsed_seconds"] = elapsed
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"calibration_selection_{int(time.time())}.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Report written to {report_path} ({elapsed:.1f}s)")
    return report


if __name__ == "__main__":
    run()
