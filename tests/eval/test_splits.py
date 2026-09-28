"""Leakage tests for eval/splits.py (SPEC.md section 9.1, Phase 1 session 1).

The real deliverable of this session, per your framing: these tests exist to
FAIL on a leaking split, not to pass on a correct one. Local-only, like the
other Phase 0 tests that need the real bulk-loaded corpus (test_match_state.
py, test_elo.py) - CI doesn't have LOCAL_DATABASE_URL pointed at real data.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import psycopg
import pytest
from dotenv import dotenv_values

from eval.metrics import brier_score
from eval.splits import _TRAIN_END, _VAL_END, _classify_date, get_second_innings_split, NotInTestSplit, TEST_SPLIT_START, assert_in_test_split

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


# --- Decision 2, test 4: the leak canary ----------------------------------
#
# Rewritten 2026-09-27. The first version fitted a 3-feature logistic
# regression on the train split and scored the test split ("honest"), then
# refitted on a random 80% of train+test rows and scored the other 20%
# ("shuffled"), asserting shuffled < honest. It never measured leakage:
#
#   - a 3-feature linear model cannot memorise a match, so it has nothing to
#     gain from a leak (docs/phase1-session1-baselines.md said so at the time:
#     gap 0.0005, "uninformative for this baseline");
#   - the two Briers were over DIFFERENT rows - the test split vs a sample
#     that is 83-98% train-period rows - so the sign only said whether recent
#     chases were easier than old ones for that model.
#
# As the daily ingest added 2025-26 chases, the test split got easier for the
# logistic model (0.1406 on the first 241 test matches, 0.1317 on all 2,287)
# while the train-period rows stayed at 0.1337, and the sign flipped. Nothing
# leaked; the instrument could not have told us if something had.
#
# This version compares two models on the SAME rows, both fitted on the train
# split plus an equal number of validation-period rows - so period drift
# cancels - with a model that can exploit a leak (LightGBM, with match_date as
# the kind of match-constant fingerprint every real model has: target, Elo,
# venue). The only difference between the arms is whether the extra rows come
# from the SAME matches as the scored rows (a ball-level split, the leak
# splits.py prevents) or from different matches. It uses validation matches,
# not test ones, so it never scores the test split.

LEAK_GAP_THRESHOLD = 0.01  # measured 2026-09-27: leaky +0.027 to +0.034 over three seeds; no-leak |gap| < 0.004
_CANARY_PARAMS = {
    "objective": "binary", "learning_rate": 0.1, "num_leaves": 63, "min_data_in_leaf": 20,
    "verbosity": -1, "seed": 0, "deterministic": True, "force_row_wise": True,
}


def _canary_X(ds, idx=slice(None)) -> np.ndarray:
    return np.column_stack([
        ds.required_run_rate[idx], ds.wickets_in_hand[idx], ds.balls_remaining[idx], ds.runs_required[idx],
        ds.match_date[idx].astype("datetime64[D]").astype(np.int64),
    ]).astype(np.float64)


def _canary_brier(train_ds, extra_ds, extra_idx, eval_idx) -> float:
    import lightgbm as lgb

    X = np.vstack([_canary_X(train_ds), _canary_X(extra_ds, extra_idx)])
    y = np.concatenate([train_ds.label, extra_ds.label[extra_idx]])
    booster = lgb.train(_CANARY_PARAMS, lgb.Dataset(X, y), num_boost_round=300)
    return brier_score(extra_ds.label[eval_idx], booster.predict(_canary_X(extra_ds, eval_idx)))


def _leak_gap(datasets, *, leaky: bool, seed: int = 0) -> tuple[float, float]:
    """(reference Brier, arm Brier) on the same scored rows.

    Validation matches are halved into A and B. Half of A's rows are scored.
    The reference model sees train + rows from B (different matches). The arm
    sees train + an equal number of rows from A's other half (the same
    matches - a leak) if `leaky`, else a disjoint set of B rows (no leak)."""
    train_ds, val_ds = datasets["train"], datasets["val"]
    rng = np.random.default_rng(seed)
    match_ids = rng.permutation(np.unique(val_ds.match_id))
    in_a = np.isin(val_ds.match_id, match_ids[: len(match_ids) // 2])
    rows_a, rows_b = np.flatnonzero(in_a), np.flatnonzero(~in_a)
    rng.shuffle(rows_a)
    rng.shuffle(rows_b)
    scored, seen_a = rows_a[: len(rows_a) // 2], rows_a[len(rows_a) // 2:]
    # Every arm sees the same number of extra rows; B must supply two disjoint sets.
    n = min(len(seen_a), len(rows_b) // 2)
    seen_a = seen_a[:n]
    reference_rows, other_b_rows = rows_b[:n], rows_b[n: 2 * n]
    reference = _canary_brier(train_ds, val_ds, reference_rows, scored)
    arm = _canary_brier(train_ds, val_ds, seen_a if leaky else other_b_rows, scored)
    return reference, arm


def test_a_ball_level_leak_scores_implausibly_well(datasets):
    """The canary: a model that has seen other balls of the scored matches
    must beat one that has seen the same amount of same-period data from
    other matches, by a clear margin. If it doesn't, the detector is broken -
    investigate before trusting any other result."""
    reference, leaky = _leak_gap(datasets, leaky=True)
    print(f"\nleak canary: other matches {reference:.4f}, same matches {leaky:.4f}, gap {reference - leaky:+.4f}")
    assert reference - leaky >= LEAK_GAP_THRESHOLD, (
        f"leak canary gap only {reference - leaky:+.4f} (threshold {LEAK_GAP_THRESHOLD}) - the detector "
        "cannot see a ball-level leak; investigate before trusting any evaluation"
    )


def test_the_canary_stays_quiet_without_a_leak(datasets):
    """The negative control, so the canary's pass means something: when the
    extra rows come from other matches in BOTH arms, the gap must sit well
    under the threshold."""
    reference, arm = _leak_gap(datasets, leaky=False)
    print(f"\nno-leak control: {reference:.4f} vs {arm:.4f}, gap {reference - arm:+.4f}")
    assert abs(reference - arm) < LEAK_GAP_THRESHOLD, (
        f"two non-leaking arms differ by {reference - arm:+.4f} - the canary's gap is not measuring a leak"
    )


# --- the replay path's test-split gate ------------------------------------
#
# UI-PHASE-2.md section 2.1 requires the window be "asserted in code rather
# than observed". Before this, the boundary reached the replay path as the
# literal string "2025-01-01" in ingest/replay_log.build_manifest and was
# never re-checked per match. These prove the gate exists AND that it fires -
# a test that only calls it with valid input proves nothing (standing rule 8).


def test_test_split_start_is_derived_from_the_val_boundary():
    """Not a hand-written date. If _VAL_END moves, this moves with it."""
    assert TEST_SPLIT_START == _VAL_END + timedelta(days=1)
    assert TEST_SPLIT_START == date(2025, 1, 1)
    assert _classify_date(TEST_SPLIT_START) == "test"
    assert _classify_date(TEST_SPLIT_START - timedelta(days=1)) == "val"


def test_the_gate_accepts_the_first_test_day():
    assert_in_test_split(8154, date(2025, 1, 1))  # must not raise


@pytest.mark.parametrize(
    "match_date,split",
    [
        (date(2024, 12, 31), "the last val day"),
        (date(2024, 1, 1), "mid-val"),
        (date(2023, 12, 31), "the last train day"),
        (date(2019, 6, 2), "deep in train"),
    ],
)
def test_the_gate_refuses_everything_before_the_window(match_date, split):
    with pytest.raises(NotInTestSplit) as excinfo:
        assert_in_test_split(4242, match_date)
    message = str(excinfo.value)
    # The message has to name the match and both dates, because the caller
    # that trips this is a 250-match replay loop and "out of range" would
    # send someone back to the database to find out which one.
    assert "4242" in message
    assert match_date.isoformat() in message
    assert TEST_SPLIT_START.isoformat() in message


def test_the_gate_rejects_a_string_rather_than_comparing_it():
    """A str would compare against a date and raise TypeError deep in the
    comparison; an ISO string that happened to sort correctly would be worse.
    Caught at the boundary with a message that says which argument."""
    with pytest.raises(TypeError, match="match_date"):
        assert_in_test_split(1, "2025-01-01")
