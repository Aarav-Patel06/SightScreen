"""Runs both section 9.2 baselines against the real test split and reports
Brier/log loss overall and bucketed (SPEC.md section 9.3, Phase 1 session 1).

Fits both baselines on train only, reports val (sanity/transparency) and
test (the committed number) - test is touched exactly once, here.

Usage (from the api/ directory, with api/.env configured):
    python -m eval.run_baselines
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
from eval.metrics import brier_match_clustered_ci, brier_score, bucketed_metrics, log_loss
from eval.splits import get_second_innings_split

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
REPORTS_DIR = REPO_ROOT / "api" / "data" / "eval_reports"

# Named section 9.3 cutoff available without extra data beyond what
# splits.py returns. "Start of chase" is reported via the existing `phase`
# column (powerplay) instead of a strict "first over" cut - splits.py's
# dataset doesn't carry each match's scheduled_balls (only match_state.py's
# internal build CTE has it), and a single global balls_remaining threshold
# for "first over" would be wrong for ODI vs T20 without it. Noted here
# rather than silently building a fragile approximation.
_FINAL_3_OVERS_BALLS = 18


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def _score(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    phase: np.ndarray,
    balls_remaining: np.ndarray,
    match_id: np.ndarray,
) -> dict:
    final_3 = balls_remaining <= _FINAL_3_OVERS_BALLS
    named_cutoffs = {
        "final_3_overs": {
            "n": int(final_3.sum()),
            "brier": brier_score(y_true[final_3], y_prob[final_3]),
            "log_loss": log_loss(y_true[final_3], y_prob[final_3]),
        },
        "not_final_3_overs": {
            "n": int((~final_3).sum()),
            "brier": brier_score(y_true[~final_3], y_prob[~final_3]),
            "log_loss": log_loss(y_true[~final_3], y_prob[~final_3]),
        },
    }
    return {
        "n": int(len(y_true)),
        "overall": {"brier": brier_score(y_true, y_prob), "log_loss": log_loss(y_true, y_prob)},
        # Match-clustered, not ball-clustered (section 9.3) - the honest bar
        # a future model has to clear, not just its own point estimate.
        "overall_brier_match_clustered_ci": brier_match_clustered_ci(y_true, y_prob, match_id),
        "final_3_overs_brier_match_clustered_ci": brier_match_clustered_ci(
            y_true[final_3], y_prob[final_3], match_id[final_3]
        ),
        "by_phase": bucketed_metrics(y_true, y_prob, phase),
        "by_named_cutoff": named_cutoffs,
    }


def run() -> dict:
    start = time.monotonic()
    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
        train = get_second_innings_split(conn, "train")
        val = get_second_innings_split(conn, "val")
        test = get_second_innings_split(conn, "test")

    logistic = LogisticBaseline().fit(train)
    base_rate = HistoricalBaseRateBaseline().fit(train)

    results: dict = {"n_train": len(train), "n_val": len(val), "n_test": len(test)}
    for name, model in (("logistic", logistic), ("historical_base_rate", base_rate)):
        results[name] = {}
        for split_name, ds in (("val", val), ("test", test)):
            probs = model.predict_proba(ds)
            results[name][split_name] = _score(ds.label, probs, ds.phase, ds.balls_remaining, ds.match_id)

    elapsed = time.monotonic() - start
    report = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        **results,
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"baselines_{int(time.time())}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report written to {report_path} ({elapsed:.1f}s)")

    return report


if __name__ == "__main__":
    run()
