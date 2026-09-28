"""Choosing how the model sees the run rate early in a chase (SPEC.md
section 11, "Next model task - the run rate before a chase has one").

Three stages, run in order, each a subcommand so the test split can only be
read by the last one:

  build   feature bundles for train/val/test, cached (the as-of lookups are
          the slow part and are identical for every candidate)
  select  every candidate retrained on train, early-stopped on the EARLY
          half of validation, scored raw on the LATE half. The winner is
          chosen by SELECTION_RULE below, written before any result existed.
  diagnose  validation select chunk only: the served artifact's early-chase
          bias beside the control's, split by format and quarter, and the
          five calibrators on the control scored early-chase as well as
          overall. Added after `select` found no candidate distinguishable
          from the control - it chooses nothing, it explains.
  final   the control (P) and the selection winner (A) through the served
          pipeline (early stopping on all of validation, five calibrators
          fit on the early half and selected on the late half, winner refit
          on all of it), then the test split touched once: P, A and the
          served artifact (S) scored in one pass. P vs S is the one
          promotable comparison, judged by PROMOTION_RULE; A is reported
          descriptively only.

All candidates share the served feature set `state_venue_elo_no_partnership`
and differ only in `current_run_rate` / `rrr_minus_crr` (features.run_rate).
The control keeps match_states' stored columns - exactly today's inputs.

Usage (from the api/ directory):
    python -m eval.run_rate_selection build  --cache DIR
    python -m eval.run_rate_selection select --cache DIR
    python -m eval.run_rate_selection diagnose --cache DIR
    python -m eval.run_rate_selection final  --cache DIR
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psycopg
from dotenv import dotenv_values

from eval.baselines import LogisticBaseline
from eval.metrics import brier_score, log_loss, paired_brier_match_clustered_ci, reliability_match_clustered
from eval.splits import get_second_innings_split
from models.calibration import CANDIDATES as CALIBRATORS
from models.win_prob_2nd import SEED, VARIANT_FEATURES, build_feature_bundle, train_lgb

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
REPORTS_DIR = REPO_ROOT / "api" / "data" / "eval_reports"
SERVED_ARTIFACT = REPO_ROOT / "api" / "data" / "models" / "win_prob_2nd" / "winprob2-20260910.pkl"
SERVED_VERSION = "winprob2-20260910"
SERVED_ORIGINAL_TEST_BRIER = 0.1232  # Phase 1's test split, as it stood on 2026-09-10

VARIANT = "state_venue_elo_no_partnership"
CALIBRATION_SPLIT_DATE = date(2024, 7, 1)  # the same date run_calibration_selection uses
FINAL_3_OVERS_BALLS = 18
FIRST_5_OVERS_BALLS = 30
SWITCH_WINDOW = 36  # balls 1..36 examined by the switch diagnostic

K_GRID = (6, 12, 24, 48)
N_GRID = (6, 12, 18, 30)

# Ordered simplest first - the tie-break order of SELECTION_RULE.
CANDIDATES: dict[str, dict] = {
    "control": {"kind": "stored"},
    "c_drop": {"kind": "none"},
    **{f"a_n{n}": {"kind": "min_balls", "n": n} for n in N_GRID},
    **{f"bt_k{k}": {"kind": "shrunk_target", "k": k} for k in K_GRID},
    **{f"b_k{k}": {"kind": "shrunk_prior", "k": k} for k in K_GRID},
}

SELECTION_RULE = (
    "On the validation select chunk (> 2024-07-01), raw scores. A candidate is ELIGIBLE unless its "
    "paired match-clustered 95% CI against the control shows a regression (control - candidate CI "
    "entirely below zero) overall or in the final 3 overs. Among eligible non-control candidates the "
    "best is the one with the lowest first-5-overs Brier. Every eligible candidate whose first-5-overs "
    "paired CI against the best includes zero is tied with it; the winner is the first tied candidate "
    "in CANDIDATES order (c, then a by N, then b' by k, then b by k)."
)


# Confirmed by the owner 2026-09-27, before any test-split number existed
# (docs/run-rate-selection.md, SPEC.md section 11).
NON_INFERIORITY_MARGIN = 0.0020
PROMOTION_RULE = (
    "P (state_venue_elo_no_partnership, today's run rate, the calibration rule's choice) against S "
    "(winprob2-20260910), paired match-clustered 95% CI of Brier_S - Brier_P, 2000 resamples, on the "
    "test split. Promote P (via section 8.4 shadow deployment) only if ALL hold: (1) overall CI lower "
    f"bound > -{NON_INFERIORITY_MARGIN}; (2) final-3-overs CI lower bound > -{NON_INFERIORITY_MARGIN}; "
    "(3) first-5-overs CI not entirely below zero; (4) P beats the three-feature logistic baseline, "
    "the paired CI of Brier_logistic - Brier_P entirely above zero (section 9.3). A (the selection "
    "winner) is descriptive only and cannot be promoted."
)


def promotion_verdict(served_minus_p: dict, logistic_minus_p: dict) -> dict:
    """PROMOTION_RULE applied to paired results; every condition reported,
    not just the conclusion."""
    conditions = {
        "overall_non_inferior": served_minus_p["overall"]["ci_low"] > -NON_INFERIORITY_MARGIN,
        "final_3_overs_non_inferior": served_minus_p["final_3_overs"]["ci_low"] > -NON_INFERIORITY_MARGIN,
        "first_5_overs_not_worse": not served_minus_p["first_5_overs"]["ci_high"] < 0,
        "beats_logistic_baseline": logistic_minus_p["ci_low"] > 0,
    }
    return {"rule": PROMOTION_RULE, "conditions": conditions, "promote": all(conditions.values())}


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


# --- features ----------------------------------------------------------------


def fit_prior(train_bundle) -> dict[str, float]:
    """Per-format innings-2 run rate from the TRAIN split only: each chase's
    last observed state, pooled as total runs over total balls."""
    c = train_bundle.columns
    df = pd.DataFrame({"m": train_bundle.match_id, "fmt": c["format"], "score": c["score"], "balls": c["balls_bowled"]})
    last = df.sort_values(["m", "balls"]).groupby("m").tail(1)
    by_fmt = last.groupby("fmt")[["score", "balls"]].sum()
    return {fmt: float(6.0 * row.score / row.balls) for fmt, row in by_fmt.iterrows()}


def resolved_spec(name: str, prior: dict[str, float]) -> dict:
    spec = dict(CANDIDATES[name])
    if spec["kind"] == "shrunk_prior":
        spec["prior"] = prior
    return spec


def design(bundle, spec: dict) -> tuple[np.ndarray, list[str]]:
    return bundle.select_with_run_rate(VARIANT, spec)


# --- metrics -----------------------------------------------------------------


def segments(bundle) -> dict[str, np.ndarray]:
    c = bundle.columns
    return {
        "overall": np.ones(len(bundle.label), dtype=bool),
        "final_3_overs": c["balls_remaining"] <= FINAL_3_OVERS_BALLS,
        "first_5_overs": c["balls_bowled"] < FIRST_5_OVERS_BALLS,
        "balls_2_to_6": (c["balls_bowled"] >= 1) & (c["balls_bowled"] <= 5),
    }


def segment_briers(y, p, segs) -> dict[str, float]:
    return {name: brier_score(y[m], p[m]) for name, m in segs.items()}


def paired(y, p_a, p_b, match_id, segs) -> dict[str, dict]:
    """(Brier_a - Brier_b) per segment; positive favours b."""
    return {name: paired_brier_match_clustered_ci(y[m], p_a[m], p_b[m], match_id[m]) for name, m in segs.items()}


def switch_diagnostic(p, match_id, delivery_id, balls_bowled) -> list[dict]:
    """For each ball b in 1..SWITCH_WINDOW: the change in p from the last
    prediction at b-1 balls to the first at b balls, one value per match.

    A calibrated probability is a martingale - its expected change across
    one ball is zero - so a mean change clearly away from zero at one ball
    is the feature switching, not cricket."""
    df = pd.DataFrame({"m": match_id, "d": delivery_id, "b": balls_bowled, "p": p}).sort_values(["m", "d"])
    first = df.groupby(["m", "b"])["p"].first()
    last = df.groupby(["m", "b"])["p"].last()
    out = []
    for b in range(1, SWITCH_WINDOW + 1):
        cur = first.xs(b, level="b")
        prev = last.xs(b - 1, level="b")
        delta = (cur - prev.reindex(cur.index)).dropna().to_numpy()
        se = delta.std(ddof=1) / np.sqrt(len(delta))
        out.append({
            "ball": b, "n_matches": int(len(delta)),
            "mean_change": float(delta.mean()), "ci_low": float(delta.mean() - 1.96 * se),
            "ci_high": float(delta.mean() + 1.96 * se), "mean_abs_change": float(np.abs(delta).mean()),
        })
    return out


def switch_summary(diag: list[dict], spec: dict) -> dict:
    by_ball = {row["ball"]: row for row in diag}
    switch_ball = {"stored": 1, "min_balls": spec.get("n")}.get(spec["kind"])
    off_zero = [r for r in diag if r["ci_low"] > 0 or r["ci_high"] < 0]
    worst = max(diag, key=lambda r: abs(r["mean_change"]))
    return {
        "switch_ball": switch_ball,
        "at_switch": by_ball.get(switch_ball) if switch_ball else None,
        "ball_1": by_ball[1],
        "worst_ball": worst,
        "n_balls_mean_change_off_zero": len(off_zero),
        "median_abs_change_balls_1_36": float(np.median([r["mean_abs_change"] for r in diag])),
    }


def select_winner(report: dict) -> tuple[str, dict]:
    vs_control = report["paired_vs_control"]
    eligible = [
        n for n in CANDIDATES
        if n != "control" and not any(vs_control[n][seg]["ci_high"] < 0 for seg in ("overall", "final_3_overs"))
    ]
    if not eligible:
        return "control", {"eligible": [], "best": None, "tied": []}
    best = min(eligible, key=lambda n: report["brier"][n]["first_5_overs"])
    tied = [n for n in eligible if n == best or report["paired_vs_best"][n]["ci_low"] <= 0 <= report["paired_vs_best"][n]["ci_high"]]
    winner = next(n for n in CANDIDATES if n in tied)
    return winner, {"eligible": eligible, "best": best, "tied": tied}


# --- stages ------------------------------------------------------------------


def build(cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
        for split in ("train", "val", "test"):
            start = time.monotonic()
            ds = get_second_innings_split(conn, split)
            bundle = build_feature_bundle(conn, ds)
            bundle.columns["delivery_id"] = ds.delivery_id
            bundle.columns["match_date"] = ds.match_date
            bundle.columns["phase"] = ds.phase
            joblib.dump(bundle, cache / f"{split}.joblib")
            print(f"{split}: {len(ds)} rows / {len(np.unique(ds.match_id))} matches "
                  f"({time.monotonic() - start:.0f}s)")


def _val_masks(val):
    fit = val.columns["match_date"] <= np.datetime64(CALIBRATION_SPLIT_DATE)
    return fit, ~fit


def select(cache: Path) -> dict:
    train, val = joblib.load(cache / "train.joblib"), joblib.load(cache / "val.joblib")
    fit, sel = _val_masks(val)
    prior = fit_prior(train)
    print(f"prior run rate (train, per format): {prior}")
    y_sel, m_sel = val.label[sel], val.match_id[sel]
    segs = {k: v[sel] for k, v in segments(val).items()}

    report: dict = {"selection_rule": SELECTION_RULE, "prior": prior, "brier": {}, "timing_seconds": {},
                    "best_iteration": {}, "switch": {}, "n_select_matches": int(len(np.unique(m_sel))),
                    "n_fit_matches": int(len(np.unique(val.match_id[fit])))}
    preds = {}
    for name in CANDIDATES:
        spec = resolved_spec(name, prior)
        X_train, names = design(train, spec)
        X_val, _ = design(val, spec)
        start = time.monotonic()
        booster = train_lgb(X_train, train.label, X_val[fit], val.label[fit], names)
        report["timing_seconds"][name] = time.monotonic() - start
        report["best_iteration"][name] = booster.best_iteration
        p = booster.predict(X_val[sel], num_iteration=booster.best_iteration)
        preds[name] = p
        report["brier"][name] = segment_briers(y_sel, p, segs)
        diag = switch_diagnostic(p, m_sel, val.columns["delivery_id"][sel], val.columns["balls_bowled"][sel])
        report["switch"][name] = {"summary": switch_summary(diag, spec), "by_ball": diag}
        b = report["brier"][name]
        print(f"{name:10s} {report['timing_seconds'][name]:6.0f}s it={booster.best_iteration:4d} "
              f"overall={b['overall']:.5f} final3={b['final_3_overs']:.5f} "
              f"first5={b['first_5_overs']:.5f} b2-6={b['balls_2_to_6']:.5f}")

    report["paired_vs_control"] = {n: paired(y_sel, preds["control"], preds[n], m_sel, segs) for n in CANDIDATES}
    best = min((n for n in CANDIDATES if n != "control"), key=lambda n: report["brier"][n]["first_5_overs"])
    first5 = segs["first_5_overs"]
    report["paired_vs_best"] = {
        n: paired_brier_match_clustered_ci(y_sel[first5], preds[n][first5], preds[best][first5], m_sel[first5])
        for n in CANDIDATES
    }
    winner, detail = select_winner(report)
    report["winner"], report["selection_detail"] = winner, detail
    print(f"winner: {winner}  ({detail})")

    np.savez(cache / "select_preds.npz", **preds)
    _write(report, "run_rate_selection")
    (cache / "winner.json").write_text(json.dumps({"winner": winner, "spec": resolved_spec(winner, prior)}), encoding="utf-8")
    return report


def _calibrate(raw_val, val, fit, sel) -> tuple[str, object, dict]:
    """The served pipeline's calibration selection (eval.run_calibration_selection):
    fit on the early chunk, pick by Brier on the late chunk, refit on all."""
    phase = val.columns["phase"]
    scores = {}
    for name, factory in CALIBRATORS.items():
        cal = factory().fit(raw_val[fit], val.label[fit], phase=phase[fit])
        scores[name] = brier_score(val.label[sel], cal.predict(raw_val[sel], phase=phase[sel]))
    winner = min(scores, key=scores.get)
    final = CALIBRATORS[winner]().fit(raw_val, val.label, phase=phase)
    return winner, final, scores


BIAS_BANDS = {"before_ball_1": (0, 0), "after_1_ball": (1, 1), "after_2_to_5_balls": (2, 5),
              "after_12_to_29_balls": (12, 29)}


def bias_by_band(y, p, match_id, balls_bowled, n_resamples: int = 2000, seed: int = 0) -> dict[str, dict]:
    """Mean (p - won) per early-chase band, in probability units, with a
    match-clustered 95% CI. Positive = optimistic for the chasing side."""
    rng = np.random.default_rng(seed)
    out = {}
    for band, (lo, hi) in BIAS_BANDS.items():
        mask = (balls_bowled >= lo) & (balls_bowled <= hi)
        ids, inv = np.unique(match_id[mask], return_inverse=True)
        if len(ids) == 0:
            out[band] = {"n_matches": 0}
            continue
        err = np.bincount(inv, weights=p[mask] - y[mask])
        n = np.bincount(inv)
        boot = np.empty(n_resamples)
        for i in range(n_resamples):
            c = np.bincount(rng.integers(0, len(ids), len(ids)), minlength=len(ids))
            boot[i] = (c * err).sum() / (c * n).sum()
        lo_ci, hi_ci = np.quantile(boot, [0.025, 0.975])
        out[band] = {"bias": float(err.sum() / n.sum()), "ci_low": float(lo_ci), "ci_high": float(hi_ci),
                     "mean_p": float(p[mask].mean()), "won": float(y[mask].mean()), "n_matches": int(len(ids))}
    return out


def diagnose(cache: Path) -> dict:
    """Validation select chunk only - the test split is not loaded."""
    train, val = joblib.load(cache / "train.joblib"), joblib.load(cache / "val.joblib")
    fit, sel = _val_masks(val)
    prior = fit_prior(train)
    y, mid = val.label[sel], val.match_id[sel]
    balls = val.columns["balls_bowled"][sel]
    segs = {k: v[sel] for k, v in segments(val).items()}
    fmt = val.columns["format"][sel]
    dates = pd.to_datetime(val.columns["match_date"][sel])
    quarter = np.array([f"{d.year}-Q{(d.month - 1) // 3 + 1}" for d in dates])

    served = joblib.load(SERVED_ARTIFACT)
    X_served = np.column_stack([val.columns[n] for n in served["feature_names"]]).astype(np.float64)
    raw = served["booster"].predict(X_served, num_iteration=served["booster"].best_iteration)
    probs = {"served": served["calibrator"].predict(raw, phase=val.columns["phase"])[sel]}
    probs["control_select"] = np.load(cache / "select_preds.npz")["control"]

    # The control as `final` would build it: early stopping on ALL of
    # validation, then each calibrator fit on the early chunk.
    X_train, names = design(train, resolved_spec("control", prior))
    X_val, _ = design(val, resolved_spec("control", prior))
    booster = train_lgb(X_train, train.label, X_val, val.label, names)
    raw_val = booster.predict(X_val, num_iteration=booster.best_iteration)
    phase = val.columns["phase"]
    for name, factory in CALIBRATORS.items():
        cal = factory().fit(raw_val[fit], val.label[fit], phase=phase[fit])
        probs[f"control_{name}"] = cal.predict(raw_val[sel], phase=phase[sel])

    report: dict = {
        "note": "validation select chunk (> 2024-07-01) only. served and control_<calibrator> were "
                "early-stopped on all of validation, so the chunk is in-sample for their round count; "
                "control_select was early-stopped on the fit chunk only.",
        "n_select_matches": int(len(np.unique(mid))),
        "control_best_iteration": booster.best_iteration,
        "brier": {n: segment_briers(y, p, segs) for n, p in probs.items()},
        "bias": {n: bias_by_band(y, p, mid, balls) for n, p in probs.items()},
        "calibrator_vs_identity": {
            name: paired(y, probs["control_identity"], probs[f"control_{name}"], mid, segs)
            for name in CALIBRATORS if name != "identity"
        },
        "served_minus_control_identity": paired(y, probs["served"], probs["control_identity"], mid, segs),
        "bias_by_format": {}, "bias_by_quarter": {},
    }
    for n in ("served", "control_select", "control_identity"):
        report["bias_by_format"][n] = {f: bias_by_band(y[fmt == f], probs[n][fmt == f], mid[fmt == f], balls[fmt == f])
                                       for f in sorted(set(fmt))}
        report["bias_by_quarter"][n] = {q: bias_by_band(y[quarter == q], probs[n][quarter == q], mid[quarter == q],
                                                        balls[quarter == q]) for q in sorted(set(quarter))}
    _write(report, "run_rate_diagnose")
    return report


def final(cache: Path) -> dict:
    chosen = json.loads((cache / "winner.json").read_text(encoding="utf-8"))
    train, val = joblib.load(cache / "train.joblib"), joblib.load(cache / "val.joblib")
    fit, sel = _val_masks(val)
    prior = fit_prior(train)
    import subprocess

    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "api/src"], capture_output=True, text=True,
                           cwd=REPO_ROOT).stdout.strip()
    if dirty:
        sys.exit("api/src has uncommitted changes - the test look must run on committed, pre-registered code")
    report: dict = {"winner": chosen["winner"], "spec": chosen["spec"], "calibration": {}, "timing_seconds": {},
                    "code_commit": commit}

    models = {}
    for name in dict.fromkeys(["control", chosen["winner"]]):
        spec = resolved_spec(name, prior)
        X_train, names = design(train, spec)
        X_val, _ = design(val, spec)
        start = time.monotonic()
        booster = train_lgb(X_train, train.label, X_val, val.label, names)
        report["timing_seconds"][name] = time.monotonic() - start
        raw_val = booster.predict(X_val, num_iteration=booster.best_iteration)
        cal_name, calibrator, cal_scores = _calibrate(raw_val, val, fit, sel)
        report["calibration"][name] = {"winner": cal_name, "select_chunk_brier": cal_scores,
                                       "best_iteration": booster.best_iteration}
        print(f"{name}: calibrator {cal_name}  {cal_scores}")
        models[name] = {"booster": booster, "calibrator": calibrator, "calibrator_name": cal_name,
                        "feature_names": names, "seed": SEED, "run_rate": spec}

    # --- the test split, touched once -----------------------------------------
    test = joblib.load(cache / "test.joblib")
    y, mid = test.label, test.match_id
    segs = segments(test)
    phase = test.columns["phase"]
    probs = {}
    for name, art in models.items():
        X, _ = design(test, art["run_rate"])
        raw = art["booster"].predict(X, num_iteration=art["booster"].best_iteration)
        probs[name] = art["calibrator"].predict(raw, phase=phase)
    served = joblib.load(SERVED_ARTIFACT)
    X_served = np.column_stack([test.columns[n] for n in served["feature_names"]]).astype(np.float64)
    raw = served["booster"].predict(X_served, num_iteration=served["booster"].best_iteration)
    probs["served"] = served["calibrator"].predict(raw, phase=phase)

    w = chosen["winner"]
    report["test"] = {
        "n_rows": int(len(y)), "n_matches": int(len(np.unique(mid))),
        "last_match_date": str(test.columns["match_date"].max()),
        "served_original_test_brier": SERVED_ORIGINAL_TEST_BRIER,
        "brier": {n: segment_briers(y, p, segs) for n, p in probs.items()},
        "log_loss_overall": {n: log_loss(y, p) for n, p in probs.items()},
        "paired": {
            "served_minus_winner": paired(y, probs["served"], probs[w], mid, segs),
            "control_minus_winner": paired(y, probs["control"], probs[w], mid, segs),
            "served_minus_control": paired(y, probs["served"], probs["control"], mid, segs),
        },
        "early_chase_bias": {n: bias_by_band(y, p, mid, test.columns["balls_bowled"]) for n, p in probs.items()},
        "reliability_first_5_overs": {
            n: reliability_match_clustered(y[segs["first_5_overs"]], p[segs["first_5_overs"]], mid[segs["first_5_overs"]])
            for n, p in probs.items()
        },
        "reliability_balls_2_to_6": {
            n: reliability_match_clustered(y[segs["balls_2_to_6"]], p[segs["balls_2_to_6"]], mid[segs["balls_2_to_6"]])
            for n, p in probs.items()
        },
        "switch": {
            n: switch_summary(switch_diagnostic(p, mid, test.columns["delivery_id"], test.columns["balls_bowled"]),
                              models[n]["run_rate"] if n in models else {"kind": "stored"})
            for n, p in probs.items()
        },
    }
    for n, b in report["test"]["brier"].items():
        print(f"test {n:10s} " + " ".join(f"{k}={v:.5f}" for k, v in b.items()))

    # Condition 4: the three-feature logistic baseline, fitted on the train
    # split, scored on the same test rows. The split is re-read (the baseline
    # takes datasets, not bundles) and aligned by delivery_id, since the
    # split query has no ORDER BY.
    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
        train_ds = get_second_innings_split(conn, "train")
        test_ds = get_second_innings_split(conn, "test")
    position = {d: i for i, d in enumerate(test_ds.delivery_id.tolist())}
    order = np.array([position[d] for d in test.columns["delivery_id"].tolist()])
    assert len(order) == len(test_ds) and np.array_equal(test_ds.label[order], y), "test split changed since build"
    logistic = LogisticBaseline().fit(train_ds).predict_proba(test_ds)[order]
    logistic_minus_p = paired_brier_match_clustered_ci(y, logistic, probs["control"], mid)
    report["test"]["logistic_minus_control_overall"] = logistic_minus_p
    report["test"]["promotion"] = promotion_verdict(report["test"]["paired"]["served_minus_control"], logistic_minus_p)
    print(f"promotion (P vs S): {report['test']['promotion']['conditions']} -> "
          f"{'PROMOTE' if report['test']['promotion']['promote'] else 'do not promote'}")

    np.savez(cache / "test_preds.npz", **probs)
    joblib.dump(models[w], cache / "winner_artifact.joblib")
    joblib.dump(models["control"], cache / "control_artifact.joblib")
    _write(report, "run_rate_final")
    return report


def _write(report: dict, stem: str) -> None:
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{stem}_{int(time.time())}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Report written to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="run_rate_selection")
    parser.add_argument("stage", choices=("build", "select", "diagnose", "final"))
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    {"build": build, "select": select, "diagnose": diagnose, "final": final}[args.stage](args.cache)
