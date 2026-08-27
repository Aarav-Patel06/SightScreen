"""Full real-data evaluation for models/win_prob_2nd.py (SPEC.md sections
6.2/9.3, Phase 1 session 2): the loud canary, the three-variant ablation,
calibration, and the one-time paired bootstrap comparison against both
section 9.2 baselines. Writes a JSON report; the committed markdown doc
(docs/phase1-session2-win-prob.md) is written by hand from this report's
numbers, same as session 1.

Usage (from the api/ directory, with api/.env configured):
    python -m eval.run_win_prob_2nd_eval
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg
from dotenv import dotenv_values

from eval.baselines import HistoricalBaseRateBaseline, LogisticBaseline
from eval.metrics import (
    brier_match_clustered_ci,
    brier_score,
    bucketed_metrics,
    log_loss,
    paired_brier_match_clustered_ci,
    reliability_table,
)
from eval.splits import SecondInningsDataset, get_second_innings_split
from models.win_prob_2nd import (
    VARIANT_FEATURES,
    FeatureBundle,
    build_feature_bundle,
    fit_isotonic,
    train_lgb,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
REPORTS_DIR = REPO_ROOT / "api" / "data" / "eval_reports"
ARTIFACT_DIR = REPO_ROOT / "api" / "data" / "models" / "win_prob_2nd"

FINAL_3_OVERS_BALLS = 18


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def _bucket_report(y_true: np.ndarray, y_prob: np.ndarray, phase: np.ndarray, balls_remaining: np.ndarray) -> dict:
    final_3 = balls_remaining <= FINAL_3_OVERS_BALLS
    return {
        "overall": {"brier": brier_score(y_true, y_prob), "log_loss": log_loss(y_true, y_prob)},
        "by_phase": bucketed_metrics(y_true, y_prob, phase),
        "final_3_overs": {
            "n": int(final_3.sum()),
            "brier": brier_score(y_true[final_3], y_prob[final_3]),
            "log_loss": log_loss(y_true[final_3], y_prob[final_3]),
        },
    }


def _concat_bundle(a: FeatureBundle, b: FeatureBundle) -> FeatureBundle:
    columns = {name: np.concatenate([a.columns[name], b.columns[name]]) for name in a.columns}
    return FeatureBundle(
        columns=columns, label=np.concatenate([a.label, b.label]), match_id=np.concatenate([a.match_id, b.match_id])
    )


def run_canary(train_b: FeatureBundle, test_b: FeatureBundle) -> dict:
    """Decision 1: pooled train+test, ignore match_id/date entirely, random
    80/20 ROW-level split - the one place this session's evaluation
    deliberately touches test data outside the final one-time check, since
    the canary's whole point is to be a controlled leak experiment, not a
    model-selection step."""
    X_train, names = train_b.select("state_venue_elo")
    X_test, _ = test_b.select("state_venue_elo")

    honest_booster = train_lgb(X_train, train_b.label, X_test, test_b.label, names)
    honest_prob = honest_booster.predict(X_test, num_iteration=honest_booster.best_iteration)
    honest_brier = brier_score(test_b.label, honest_prob)

    X_pool = np.concatenate([X_train, X_test])
    y_pool = np.concatenate([train_b.label, test_b.label])
    rng = np.random.default_rng(0)
    idx = rng.permutation(len(y_pool))
    cut = int(len(idx) * 0.8)
    shuf_train_idx, shuf_test_idx = idx[:cut], idx[cut:]

    shuffled_booster = train_lgb(
        X_pool[shuf_train_idx], y_pool[shuf_train_idx],
        X_pool[shuf_test_idx], y_pool[shuf_test_idx], names,
    )
    shuffled_prob = shuffled_booster.predict(X_pool[shuf_test_idx], num_iteration=shuffled_booster.best_iteration)
    shuffled_brier = brier_score(y_pool[shuf_test_idx], shuffled_prob)

    return {
        "honest_brier": honest_brier,
        "shuffled_brier": shuffled_brier,
        "gap": honest_brier - shuffled_brier,
        "convincing": (honest_brier - shuffled_brier) >= 0.03,
        "loud_enough_to_trust": (honest_brier - shuffled_brier) >= 0.01,
    }


def run() -> dict:
    start = time.monotonic()
    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
        train_ds = get_second_innings_split(conn, "train")
        val_ds = get_second_innings_split(conn, "val")
        test_ds = get_second_innings_split(conn, "test")

        print(f"loaded splits: train={len(train_ds)}, val={len(val_ds)}, test={len(test_ds)}")
        train_b = build_feature_bundle(conn, train_ds)
        val_b = build_feature_bundle(conn, val_ds)
        test_b = build_feature_bundle(conn, test_ds)
        print("feature bundles built")

    report: dict = {"finished_at": None, "elapsed_seconds": None}

    # --- Decision 1: the loud canary, first - gate everything else --------
    canary = run_canary(train_b, test_b)
    print(f"canary: honest={canary['honest_brier']:.4f} shuffled={canary['shuffled_brier']:.4f} "
          f"gap={canary['gap']:.4f}")
    report["canary"] = canary
    if not canary["loud_enough_to_trust"]:
        report["STOPPED"] = "canary gap below 0.01 - investigate before trusting anything else"
        _write_report(report, start)
        return report

    # --- Decision 4: three-variant ablation, boosters kept for later ------
    boosters = {}
    ablation = {}
    for variant in VARIANT_FEATURES:
        X_train, names = train_b.select(variant)
        X_val, _ = val_b.select(variant)
        X_test, _ = test_b.select(variant)
        booster = train_lgb(X_train, train_b.label, X_val, val_b.label, names)
        boosters[variant] = (booster, names)
        prob_test = booster.predict(X_test, num_iteration=booster.best_iteration)
        ablation[variant] = _bucket_report(test_b.label, prob_test, test_ds.phase, test_ds.balls_remaining)
        print(f"variant={variant} test overall brier={ablation[variant]['overall']['brier']:.4f}")
    report["ablation"] = ablation

    # Paired deltas between successive variants (Decision 4).
    variant_order = ["state", "state_venue", "state_venue_elo"]
    ablation_pairs = {}
    for a, b in zip(variant_order, variant_order[1:]):
        prob_a = boosters[a][0].predict(test_b.select(a)[0], num_iteration=boosters[a][0].best_iteration)
        prob_b = boosters[b][0].predict(test_b.select(b)[0], num_iteration=boosters[b][0].best_iteration)
        ablation_pairs[f"{a}_vs_{b}"] = paired_brier_match_clustered_ci(
            test_b.label, prob_a, prob_b, test_b.match_id
        )
    report["ablation_pairs"] = ablation_pairs

    # --- Decision 3: feature importance, full model ------------------------
    full_booster, full_names = boosters["state_venue_elo"]
    importance = dict(zip(full_names, [float(x) for x in full_booster.feature_importance(importance_type="gain")]))
    report["feature_importance_gain"] = dict(sorted(importance.items(), key=lambda kv: -kv[1]))

    # --- Decision 5: calibration, fit on val only --------------------------
    X_val_full, _ = val_b.select("state_venue_elo")
    X_test_full, _ = test_b.select("state_venue_elo")
    p_val_raw = full_booster.predict(X_val_full, num_iteration=full_booster.best_iteration)
    iso = fit_isotonic(val_b.label, p_val_raw)

    p_test_raw = full_booster.predict(X_test_full, num_iteration=full_booster.best_iteration)
    p_test_calibrated = iso.predict(p_test_raw)

    report["calibration"] = {
        "pre": {
            "overall_brier": brier_score(test_b.label, p_test_raw),
            "reliability": reliability_table(test_b.label, p_test_raw, n_bins=10),
        },
        "post": {
            "overall_brier": brier_score(test_b.label, p_test_calibrated),
            "reliability": reliability_table(test_b.label, p_test_calibrated, n_bins=10),
        },
    }

    # --- Decision 7: paired bootstrap vs both session-1 baselines ---------
    logistic_baseline = LogisticBaseline().fit(train_ds)
    base_rate_baseline = HistoricalBaseRateBaseline().fit(train_ds)
    logistic_prob = logistic_baseline.predict_proba(test_ds)
    base_rate_prob = base_rate_baseline.predict_proba(test_ds)

    report["vs_baselines"] = {
        "logistic": paired_brier_match_clustered_ci(test_b.label, logistic_prob, p_test_calibrated, test_b.match_id),
        "historical_base_rate": paired_brier_match_clustered_ci(
            test_b.label, base_rate_prob, p_test_calibrated, test_b.match_id
        ),
    }
    report["model_bucketed"] = _bucket_report(test_b.label, p_test_calibrated, test_ds.phase, test_ds.balls_remaining)

    # --- Persist artifact ---------------------------------------------------
    import joblib

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACT_DIR / f"seed42_{int(time.time())}.pkl"
    joblib.dump({"booster": full_booster, "isotonic": iso, "feature_names": full_names, "seed": 42}, artifact_path)
    report["artifact_path"] = str(artifact_path)

    _write_report(report, start)
    return report


def _write_report(report: dict, start: float) -> None:
    elapsed = time.monotonic() - start
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["elapsed_seconds"] = elapsed
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"win_prob_2nd_{int(time.time())}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report written to {report_path} ({elapsed:.1f}s)")


if __name__ == "__main__":
    run()
