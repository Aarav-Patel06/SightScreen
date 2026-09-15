"""Cost of dropping the partnership features (SPEC.md sections 6.2/9.3,
Phase 2 session 2, Decision 5).

`measure_reconstruction` showed that at the shipped 15s poll interval,
2.66% of reconstructed states carry a wrong `partnership_balls` - above the
2% threshold pre-registered before any number was seen. The rule says: do
not serve the model a feature the live path cannot actually deliver. So
train the same model without the three partnership features and report what
that costs, paired and match-clustered, against the Phase 1 model.

Both variants are trained from one feature bundle with identical
hyperparameters and seed, so the only difference is the feature set.

Usage (from the api/ directory, with api/.env configured):
    python -m eval.measure_degraded_variant
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

from eval.metrics import brier_score, log_loss, paired_brier_match_clustered_ci
from eval.splits import get_second_innings_split
from models.win_prob_2nd import build_feature_bundle, train_lgb

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
REPORTS_DIR = REPO_ROOT / "api" / "data" / "eval_reports"

FULL_VARIANT = "state_venue_elo"
DEGRADED_VARIANT = "state_venue_elo_no_partnership"
FINAL_3_OVERS_BALLS = 18


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def run() -> dict:
    start = time.monotonic()
    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
        train_ds = get_second_innings_split(conn, "train")
        val_ds = get_second_innings_split(conn, "val")
        test_ds = get_second_innings_split(conn, "test")
        print(f"splits: train={len(train_ds)} val={len(val_ds)} test={len(test_ds)}")
        train_b = build_feature_bundle(conn, train_ds)
        val_b = build_feature_bundle(conn, val_ds)
        test_b = build_feature_bundle(conn, test_ds)
        print("feature bundles built")

    predictions: dict[str, np.ndarray] = {}
    report: dict = {"variants": {}}
    for variant in (FULL_VARIANT, DEGRADED_VARIANT):
        X_train, names = train_b.select(variant)
        X_val, _ = val_b.select(variant)
        X_test, _ = test_b.select(variant)
        booster = train_lgb(X_train, train_b.label, X_val, val_b.label, names)
        prob = booster.predict(X_test, num_iteration=booster.best_iteration)
        predictions[variant] = prob

        final_3 = test_ds.balls_remaining <= FINAL_3_OVERS_BALLS
        report["variants"][variant] = {
            "n_features": len(names),
            "features": names,
            "brier_overall": brier_score(test_b.label, prob),
            "brier_final_3_overs": brier_score(test_b.label[final_3], prob[final_3]),
            "log_loss_overall": log_loss(test_b.label, prob),
        }
        print(f"{variant:34s} features={len(names):2d} "
              f"test Brier={report['variants'][variant]['brier_overall']:.5f}")

    # Paired and match-clustered: both variants score the same matches, so
    # match difficulty cancels rather than inflating each interval
    # independently (SPEC.md section 9.3).
    report["paired_full_minus_degraded"] = paired_brier_match_clustered_ci(
        test_b.label, predictions[DEGRADED_VARIANT], predictions[FULL_VARIANT], test_b.match_id
    )

    elapsed = time.monotonic() - start
    report["elapsed_seconds"] = elapsed
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"degraded_variant_{int(time.time())}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    paired = report["paired_full_minus_degraded"]
    print(
        f"\ncost of dropping the partnership features: "
        f"{paired['point_diff']:+.5f} Brier "
        f"(95% CI [{paired['ci_low']:+.5f}, {paired['ci_high']:+.5f}], "
        f"{'significant' if paired['significant'] else 'NOT significant'})"
    )
    print(f"Report written to {path} ({elapsed:.1f}s)")
    return report


if __name__ == "__main__":
    run()
