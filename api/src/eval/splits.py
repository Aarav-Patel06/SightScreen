"""Temporal train/val/test splits (SPEC.md section 9.1, Phase 1 session 1).

The only place split logic may live, per section 9.1. Do not query
match_states/matches directly for training data anywhere else - this
module's `get_second_innings_split` is the only sanctioned path. See
docs/phase0-closeout.md's `has_reconciliation_anomaly` section for why the
exclusion predicate below is non-negotiable.

Boundary dates are module-private on purpose: nothing importable invites
another module to run its own `WHERE match_date <= TRAIN_END` filter.
`_classify_date` is a separate pure function specifically so the boundary
logic (train <=2023-12-31, val 2024, test >=2025-01-01) is unit-testable
against synthetic dates without needing real rows - the SQL WHERE clause
below is built from the same two constants, so the two can never drift
apart the way a hand-duplicated boundary would.

Enforced unconditionally, in this one place, every time:
  - innings = 2 (this model only predicts second-innings chases)
  - batting_team_won IS NOT NULL AND NOT has_reconciliation_anomaly
    (the exact predicate from docs/phase0-closeout.md)
  - required_run_rate IS NOT NULL - a fourth clause beyond the documented
    three, found by querying the real corpus this session: 992 rows pass
    the documented predicate but still have a NULL required_run_rate
    (DLS-decided matches with no explicit target - the same fallback gap
    match_state.py's own rebuild() report already counts, just not yet
    named as a training-set exclusion anywhere). A row with a valid label
    but no usable feature can't train the logistic baseline and can't be
    binned by runs-required for the base-rate baseline.
  - super-over rows: never present in match_states at all (Phase 0 session
    6's builder never generates them) - asserted here, not assumed, by
    checking the fetched innings values are all exactly 2.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import psycopg
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

Split = Literal["train", "val", "test"]

# Train <=2023-12-31, val all of 2024, test >=2025-01-01 (section 9.1).
_TRAIN_END = date(2023, 12, 31)
_VAL_END = date(2024, 12, 31)


def _classify_date(d: date) -> Split:
    """Pure boundary logic, unit-tested directly against synthetic dates
    (tests/eval/test_splits.py) rather than only against whatever dates the
    real corpus happens to contain."""
    if d <= _TRAIN_END:
        return "train"
    if d <= _VAL_END:
        return "val"
    return "test"


def _date_bounds(split: Split) -> tuple[date | None, date | None]:
    """(lower_exclusive, upper_inclusive) for the SQL WHERE clause, derived
    from the same two constants _classify_date uses."""
    if split == "train":
        return None, _TRAIN_END
    if split == "val":
        return _TRAIN_END, _VAL_END
    if split == "test":
        return _VAL_END, None
    raise ValueError(f"unknown split {split!r}")


@dataclass(frozen=True)
class SecondInningsDataset:
    """Training-ready second-innings rows for one split. Every array is the
    same length, one entry per delivery. match_id/match_date exist only to
    support leakage tests (no match_id in more than one split, strict date
    ordering) - they are not model features."""

    match_id: np.ndarray  # int64
    match_date: np.ndarray  # datetime64[D]
    required_run_rate: np.ndarray  # float32
    wickets_in_hand: np.ndarray  # int8, derived: 10 - wickets_fallen
    balls_remaining: np.ndarray  # int16
    runs_required: np.ndarray  # int16 - needed by the base-rate baseline's binning
    phase: np.ndarray  # object/str: 'powerplay' | 'middle' | 'death'
    label: np.ndarray  # int8, 0/1 - batting_team_won

    def __len__(self) -> int:
        return len(self.label)


def get_second_innings_split(conn, split: Split) -> SecondInningsDataset:
    """The only sanctioned way to get second-innings training data. Returns
    finished numpy arrays, not a connection, a query, or a date constant -
    there is no natural intermediate for another module to grab and filter
    differently itself."""
    lower, upper = _date_bounds(split)
    clauses = [
        "innings = 2",
        "batting_team_won IS NOT NULL",
        "NOT has_reconciliation_anomaly",
        "required_run_rate IS NOT NULL",
    ]
    params: list[date] = []
    if lower is not None:
        clauses.append("match_date > %s")
        params.append(lower)
    if upper is not None:
        clauses.append("match_date <= %s")
        params.append(upper)

    query = f"""
        SELECT match_id, match_date, innings, required_run_rate, wickets,
               balls_remaining, runs_required, phase, batting_team_won
        FROM match_states
        WHERE {' AND '.join(clauses)}
    """
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    if not rows:
        return SecondInningsDataset(
            match_id=np.array([], dtype=np.int64),
            match_date=np.array([], dtype="datetime64[D]"),
            required_run_rate=np.array([], dtype=np.float32),
            wickets_in_hand=np.array([], dtype=np.int8),
            balls_remaining=np.array([], dtype=np.int16),
            runs_required=np.array([], dtype=np.int16),
            phase=np.array([], dtype=object),
            label=np.array([], dtype=np.int8),
        )

    cols = list(zip(*rows))
    innings_col = np.array(cols[2], dtype=np.int16)
    # Assert, don't assume (module docstring): confirms Phase 0's invariant
    # that match_states never holds a super-over row still holds, by
    # checking the data actually fetched rather than trusting the WHERE
    # clause alone.
    assert set(np.unique(innings_col).tolist()) == {2}, (
        "get_second_innings_split fetched a row with innings != 2 - "
        "match_states should never contain super-over rows"
    )

    wickets = np.array(cols[4], dtype=np.int16)
    wickets_in_hand = (10 - wickets).astype(np.int8)
    assert wickets_in_hand.min() >= 0 and wickets_in_hand.max() <= 10, (
        "wickets_in_hand out of [0, 10] range - check for a wickets-fallen "
        "vs wickets-in-hand mixup"
    )

    return SecondInningsDataset(
        match_id=np.array(cols[0], dtype=np.int64),
        match_date=np.array(cols[1], dtype="datetime64[D]"),
        required_run_rate=np.array(cols[3], dtype=np.float32),
        wickets_in_hand=wickets_in_hand,
        balls_remaining=np.array(cols[5], dtype=np.int16),
        runs_required=np.array(cols[6], dtype=np.int16),
        phase=np.array(cols[7], dtype=object),
        label=np.array(cols[8], dtype=np.int8),
    )


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


if __name__ == "__main__":
    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as _conn:
        for _split in ("train", "val", "test"):
            _ds = get_second_innings_split(_conn, _split)
            print(f"{_split}: {len(_ds)} rows, {len(np.unique(_ds.match_id))} matches")
