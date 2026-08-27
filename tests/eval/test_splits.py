"""Leakage tests for eval/splits.py (SPEC.md section 9.1, Phase 1 session 1).

The real deliverable of this session, per your framing: these tests exist to
FAIL on a leaking split, not to pass on a correct one. Local-only, like the
other Phase 0 tests that need the real bulk-loaded corpus (test_match_state.
py, test_elo.py) - CI doesn't have LOCAL_DATABASE_URL pointed at real data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import psycopg
import pytest
from dotenv import dotenv_values
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from eval.metrics import brier_score
from eval.splits import _TRAIN_END, _VAL_END, _classify_date, get_second_innings_split

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        pytest.skip(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


@pytest.fixture(scope="module")
def conn():
    # autocommit=True: shared read-only connection across every test in this
    # module - see docs/phase0-closeout.md's autocommit lesson (an open
    # transaction from one SELECT can block an unrelated later statement).
    connection = psycopg.connect(_env()["LOCAL_DATABASE_URL"], autocommit=True)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def datasets(conn):
    return {split: get_second_innings_split(conn, split) for split in ("train", "val", "test")}


# --- Decision 2, test 3: boundary edge cases, pure function, no DB --------


def test_classify_date_boundaries():
    assert _classify_date(date(2023, 12, 31)) == "train"
    assert _classify_date(date(2024, 1, 1)) == "val"
    assert _classify_date(date(2024, 12, 31)) == "val"
    assert _classify_date(date(2025, 1, 1)) == "test"
    # sanity on the constants themselves, since the SQL WHERE clause is
    # built from these same two values
    assert _TRAIN_END == date(2023, 12, 31)
    assert _VAL_END == date(2024, 12, 31)


# --- Decision 2, test 1: no match_id in more than one split ----------------


def test_no_match_id_appears_in_more_than_one_split(datasets):
    train_ids = set(datasets["train"].match_id.tolist())
    val_ids = set(datasets["val"].match_id.tolist())
    test_ids = set(datasets["test"].match_id.tolist())
    assert train_ids & val_ids == set()
    assert train_ids & test_ids == set()
    assert val_ids & test_ids == set()


# --- Decision 2, test 2: strict date ordering ------------------------------


def test_dates_are_strictly_ordered_across_splits(datasets):
    max_train = datasets["train"].match_date.max()
    min_val = datasets["val"].match_date.min()
    max_val = datasets["val"].match_date.max()
    min_test = datasets["test"].match_date.min()

    assert max_train <= np.datetime64(_TRAIN_END)
    assert min_val > np.datetime64(_TRAIN_END)
    assert max_val <= np.datetime64(_VAL_END)
    assert min_test > np.datetime64(_VAL_END)
    assert max_train < min_val
    assert max_val < min_test


# --- Decision 2, test 5: every returned row satisfies the full predicate --


def test_predicate_completeness_against_the_database_directly(conn, datasets):
    """Cross-checks each split's row count against an independent re-run of
    the exact same predicate, restricted to that split's own date range.
    Deliberately NOT "no match_id in this split has any violating row
    anywhere in match_states" - a single match can legitimately have some
    balls that pass the predicate (valid required_run_rate) and others that
    don't (e.g. the assumption-1 NULL-required_run_rate edge case hitting
    just the final ball of an otherwise-normal chase) - excluding only the
    bad ball, not the whole match, is the correct behaviour, not a bug to
    flag."""
    bounds = {"train": (None, _TRAIN_END), "val": (_TRAIN_END, _VAL_END), "test": (_VAL_END, None)}
    for split, ds in datasets.items():
        lower, upper = bounds[split]
        clauses = [
            "innings = 2",
            "batting_team_won IS NOT NULL",
            "NOT has_reconciliation_anomaly",
            "required_run_rate IS NOT NULL",
        ]
        params = []
        if lower is not None:
            clauses.append("match_date > %s")
            params.append(lower)
        if upper is not None:
            clauses.append("match_date <= %s")
            params.append(upper)
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM match_states WHERE {' AND '.join(clauses)}", params)
            expected = cur.fetchone()[0]
        assert len(ds) == expected, f"{split}: dataset has {len(ds)} rows, independent query found {expected}"


# --- Decision 2, test 6: wickets_in_hand range -----------------------------


def test_wickets_in_hand_within_zero_to_ten(datasets):
    for ds in datasets.values():
        if len(ds) == 0:
            continue
        assert ds.wickets_in_hand.min() >= 0
        assert ds.wickets_in_hand.max() <= 10


# --- Decision 2, test 7: no super-over rows, ever --------------------------


def test_match_states_never_holds_a_super_over_row(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM match_states WHERE innings > 2")
        count = cur.fetchone()[0]
    assert count == 0


# --- Decision 2, test 4: the shuffled-split canary -------------------------


def _fit_predict_brier(train_ds, eval_ds) -> float:
    features = ["required_run_rate", "wickets_in_hand", "balls_remaining"]
    X_train = np.column_stack(
        [train_ds.required_run_rate, train_ds.wickets_in_hand, train_ds.balls_remaining]
    )
    X_eval = np.column_stack(
        [eval_ds.required_run_rate, eval_ds.wickets_in_hand, eval_ds.balls_remaining]
    )
    scaler = StandardScaler().fit(X_train)
    model = LogisticRegression().fit(scaler.transform(X_train), train_ds.label)
    y_prob = model.predict_proba(scaler.transform(X_eval))[:, 1]
    return brier_score(eval_ds.label, y_prob)


def test_shuffled_ball_level_split_scores_implausibly_well(datasets):
    """The canary every future model gets checked against: a split that
    ignores match_id and match_date entirely - drawing "train" and "test"
    as a random 80/20 partition of the SAME pool, rather than a genuinely
    held-out future period - must score better than the honest, correctly
    match/date-disjoint split. If this test ever fails (shuffled scores no
    better, or worse), something about the honest split stopped being
    meaningfully harder than a leaking one - worth investigating before
    trusting any other result in this session.
    """
    real_train, real_test = datasets["train"], datasets["test"]
    real_brier = _fit_predict_brier(real_train, real_test)

    pool_ids = ["delivery_id", "match_id", "match_date", "required_run_rate", "wickets_in_hand",
                "balls_remaining", "runs_required", "phase", "label"]

    def _concat(a, b):
        return {name: np.concatenate([getattr(a, name), getattr(b, name)]) for name in pool_ids}

    pooled = _concat(real_train, real_test)
    n = len(pooled["label"])
    rng = np.random.default_rng(0)
    shuffled_idx = rng.permutation(n)
    cut = int(n * 0.8)
    train_idx, test_idx = shuffled_idx[:cut], shuffled_idx[cut:]

    from eval.splits import SecondInningsDataset

    shuffled_train = SecondInningsDataset(**{k: v[train_idx] for k, v in pooled.items()})
    shuffled_test = SecondInningsDataset(**{k: v[test_idx] for k, v in pooled.items()})
    shuffled_brier = _fit_predict_brier(shuffled_train, shuffled_test)

    print(f"\nhonest test-split Brier: {real_brier:.4f}, shuffled-split Brier: {shuffled_brier:.4f}")
    assert shuffled_brier < real_brier, (
        "expected the leaking (shuffled) split to score better than the honest one - "
        f"got shuffled={shuffled_brier:.4f} vs honest={real_brier:.4f}"
    )
