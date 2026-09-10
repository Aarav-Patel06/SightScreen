"""Replay-specific tests (SPEC.md sections 7.2, 7.3, Phase 2 session 1):
phase-transition events against a real rain-interrupted match, and the
retry-with-backoff write wrapper (Decision 6).

Note on scope: `detect_phase_transition` only covers the ball-level events
(innings1_start/innings_break/innings2_ball/match_end) - pre_toss/toss are
only meaningful for a real provider with an actual pre-match phase where
time passes before a ball is bowled. Replay works from an already-complete
ball-by-ball delivery stream and has no such phase to observe; modeling
pre_toss/toss for replay would be artificial, not built here.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

from ingest.live_client import PhaseTransitionEvent
from ingest.replay import ReplayClient, detect_phase_transition, write_with_retry

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

# Same DLS-decided fixture as the incremental-vs-bulk parity test
# (tests/features/test_match_state.py) - cricsheet 1399119, target revised
# to 65 off 5 overs, 63 deliveries.
DLS_MATCH_ID = 5997
DLS_TARGET_RUNS = 65
DLS_TARGET_OVERS = 5.0


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


def test_innings_break_fires_exactly_once_with_the_dls_revised_target(conn):
    client = ReplayClient(conn, DLS_MATCH_ID, speed="instant")
    deliveries = client.get_deliveries_since(DLS_MATCH_ID, 0)
    assert deliveries, "DLS fixture not loaded - run the full corpus load first"

    events = []
    prev_innings = None
    for i, d in enumerate(deliveries):
        is_last = i == len(deliveries) - 1
        event = detect_phase_transition(prev_innings, d, is_last)
        if event:
            events.append(event)
        prev_innings = d.innings

    innings_breaks = [e for e in events if e == PhaseTransitionEvent.INNINGS_BREAK]
    assert len(innings_breaks) == 1, f"expected exactly one innings_break, got {events}"
    assert events[0] == PhaseTransitionEvent.INNINGS1_START
    assert events[-1] == PhaseTransitionEvent.MATCH_END

    # "Target locked" (section 7.2) - the moment innings_break fires, the
    # match's own DLS-revised target must already be the correct, final
    # figure (not derived, not innings_1_total + 1 - session 6's own
    # finding about why that derivation is wrong for a DLS match).
    assert client.target_runs == DLS_TARGET_RUNS
    assert client.target_overs == DLS_TARGET_OVERS


def test_innings_break_is_not_fooled_by_a_reduced_innings_ball_count(conn):
    """The actual robustness claim: detection comes from the innings field
    itself changing, not from "20 overs bowled" or "10 wickets fell" -
    a heuristic like that would misfire (or fail to fire) on this exact
    fixture, whose first innings is well short of a full 20 overs."""
    client = ReplayClient(conn, DLS_MATCH_ID, speed="instant")
    deliveries = client.get_deliveries_since(DLS_MATCH_ID, 0)
    innings1_deliveries = [d for d in deliveries if d.innings == 1]
    assert len(innings1_deliveries) < 120, (
        "fixture assumption violated - expected a reduced (well under 20-over) first innings"
    )

    prev_innings, break_index = None, None
    for i, d in enumerate(deliveries):
        event = detect_phase_transition(prev_innings, d, i == len(deliveries) - 1)
        if event == PhaseTransitionEvent.INNINGS_BREAK:
            break_index = i
        prev_innings = d.innings

    assert break_index == len(innings1_deliveries), (
        "innings_break must fire on the exact first ball of innings 2, "
        "regardless of how short innings 1 was"
    )


# --- Decision 6: graceful degradation on a Supabase write failure ---------


def test_write_with_retry_gives_up_cleanly_after_max_attempts():
    calls = []

    def _always_fails():
        calls.append(1)
        raise ConnectionError("simulated: Supabase project paused")

    result = write_with_retry(_always_fails, max_attempts=3, base_delay=0.001)
    assert result is False
    assert len(calls) == 3  # no more, no fewer - and no exception escaped


def test_write_with_retry_succeeds_after_a_transient_failure():
    calls = []

    def _fails_once_then_succeeds():
        calls.append(1)
        if len(calls) < 2:
            raise ConnectionError("simulated transient failure")

    result = write_with_retry(_fails_once_then_succeeds, max_attempts=3, base_delay=0.001)
    assert result is True
    assert len(calls) == 2
