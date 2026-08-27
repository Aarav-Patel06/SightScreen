"""win_prob_2nd.py leak/ablation checks (SPEC.md section 6.2, Phase 1 session 2).

Real data (local corpus), but a match-level SUBSET of it, not the full
train/val/test - these are fast gate checks run on every test invocation;
the full, real-data run (all matches, the numbers that go in the results
doc) is `eval/run_win_prob_2nd_eval.py`, run once, separately.

Subsetting by MATCH (not by row) preserves the one thing that matters for
these specific tests: a match's rows all share the same match-constant
features (elo_diff, venue rate, target) - the exact mechanism Decision 1's
canary is checking.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import psycopg
import pytest
from dotenv import dotenv_values

from eval.metrics import brier_score
from eval.splits import SecondInningsDataset, get_second_innings_split
from models.win_prob_2nd import PHASE_CODE_INDEX, VARIANT_FEATURES, build_feature_bundle, train_lgb

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

# Small enough that enrichment (a Python loop over unique matches) and
# LightGBM training both finish in seconds, large enough for early stopping
# and gain-importance rankings to be meaningful rather than pure noise.
N_TRAIN_MATCHES = 1200
N_TEST_MATCHES = 400


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        pytest.skip(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


@pytest.fixture(scope="module")
def conn():
    connection = psycopg.connect(_env()["LOCAL_DATABASE_URL"], autocommit=True)
    yield connection
    connection.close()


def _subset_by_matches(ds: SecondInningsDataset, n_matches: int, seed: int) -> SecondInningsDataset:
    unique_matches = np.unique(ds.match_id)
    rng = np.random.default_rng(seed)
    chosen = set(rng.choice(unique_matches, size=min(n_matches, len(unique_matches)), replace=False).tolist())
    mask = np.array([m in chosen for m in ds.match_id])
    return SecondInningsDataset(
        **{name: getattr(ds, name)[mask] for name in
           ("delivery_id", "match_id", "match_date", "required_run_rate", "wickets_in_hand",
            "balls_remaining", "runs_required", "phase", "label")}
    )


@pytest.fixture(scope="module")
def bundles(conn):
    train_ds = _subset_by_matches(get_second_innings_split(conn, "train"), N_TRAIN_MATCHES, seed=1)
    val_ds = _subset_by_matches(get_second_innings_split(conn, "val"), N_TRAIN_MATCHES // 4, seed=2)
    test_ds = _subset_by_matches(get_second_innings_split(conn, "test"), N_TEST_MATCHES, seed=3)
    return {
        "train": build_feature_bundle(conn, train_ds),
        "val": build_feature_bundle(conn, val_ds),
        "test": build_feature_bundle(conn, test_ds),
    }


# --- Decision 1: the canary must be loud -----------------------------------


def test_canary_shows_a_loud_gap_for_lightgbm(bundles):
    """LightGBM has the capacity session 1's linear baseline didn't - it can
    split on match-constant features (elo_diff, venue rate, target) and
    functionally memorize a match. A random ball-level split (ignoring
    match_id) should therefore score implausibly well, by a LARGE margin -
    if the gap is small, the canary hasn't demonstrated anything and this
    test fails deliberately, per this session's plan: a small gap here means
    the leak detector is broken, not that everything's fine.
    """
    train_b, test_b = bundles["train"], bundles["test"]

    X_train, names = train_b.select("state_venue_elo")
    X_test, _ = test_b.select("state_venue_elo")

    # Honest: train on train, test on test.
    honest_booster = train_lgb(X_train, train_b.label, X_test, test_b.label, names)
    honest_prob = honest_booster.predict(X_test, num_iteration=honest_booster.best_iteration)
    honest_brier = brier_score(test_b.label, honest_prob)

    # Shuffled: pool train+test, random 80/20 ROW-level split, ignoring
    # match_id entirely - the mistake splits.py exists to prevent.
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

    gap = honest_brier - shuffled_brier
    print(f"\nhonest Brier: {honest_brier:.4f}, shuffled Brier: {shuffled_brier:.4f}, gap: {gap:.4f}")
    assert gap >= 0.01, (
        f"canary gap only {gap:.4f} - too small to trust as a working leak detector for this "
        "model class; investigate the construction before reporting any other result this session"
    )


# --- Decision 3: feature importance as a leak detector ---------------------


def test_state_features_dominate_gain_importance(bundles):
    train_b, val_b = bundles["train"], bundles["val"]
    X_train, names = train_b.select("state_venue_elo")
    X_val, _ = val_b.select("state_venue_elo")
    booster = train_lgb(X_train, train_b.label, X_val, val_b.label, names)

    importance = dict(zip(names, booster.feature_importance(importance_type="gain")))
    ranked = sorted(importance, key=importance.get, reverse=True)

    as_of_features = {"venue_chase_win_rate", "venue_avg_first_innings", "elo_diff"}
    top_state_rank = min(ranked.index(f) for f in ("balls_remaining", "required_run_rate") if f in ranked)
    best_as_of_rank = min(ranked.index(f) for f in as_of_features if f in ranked)

    print(f"\nranked importance: {ranked}")
    assert top_state_rank < best_as_of_rank, (
        "an as-of aggregate outranks balls_remaining/required_run_rate by gain importance - "
        "investigate as a possible leak before trusting this model"
    )


# --- Decision 4: three-variant ablation smoke test --------------------------


def test_three_variants_all_train_and_produce_valid_scores(bundles):
    train_b, val_b, test_b = bundles["train"], bundles["val"], bundles["test"]
    results = {}
    for variant in VARIANT_FEATURES:
        X_train, names = train_b.select(variant)
        X_val, _ = val_b.select(variant)
        X_test, _ = test_b.select(variant)
        booster = train_lgb(X_train, train_b.label, X_val, val_b.label, names)
        prob = booster.predict(X_test, num_iteration=booster.best_iteration)
        results[variant] = brier_score(test_b.label, prob)

    print(f"\nablation (subset): {results}")
    for variant, brier in results.items():
        assert 0.0 <= brier <= 1.0, f"{variant} produced an out-of-range Brier: {brier}"
