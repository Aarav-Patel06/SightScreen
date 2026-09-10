"""LiveClient interchangeability tests (SPEC.md sections 4.2, 7.3, Phase 2
session 1).

One shared conformance suite (`_assert_conforms`), run against BOTH
`ReplayClient` (real, reads a real historical match from local Postgres)
and `_StaticLiveClient` (a minimal, hand-built test double) - proving the
interface guarantee is structural, not a convention one implementation
happens to follow. A future real-provider adapter runs through the
identical suite unmodified.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

from ingest.live_client import Delivery, MatchState, _StaticLiveClient
from ingest.replay import ReplayClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

FIXTURE_MATCH_ID = 6290  # Sussex v Gloucestershire (cricsheet 1410501), all out 106


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


def _assert_conforms(client, match_id: int) -> None:
    """The interface contract every LiveClient implementation must satisfy,
    regardless of what's behind it."""
    state = client.get_match_state(match_id)
    assert state.match_id == match_id
    assert isinstance(state, MatchState)
    assert state.ball_count >= 0

    summaries = client.list_live_matches()
    assert any(s.match_id == match_id for s in summaries)

    all_visible = client.get_deliveries_since(match_id, 0)
    assert len(all_visible) == state.ball_count
    assert all(isinstance(d, Delivery) for d in all_visible)

    if state.ball_count > 0:
        # get_match_state's innings must agree with what the delivery
        # stream itself implies - not a separately-tracked, driftable value.
        assert all_visible[-1].innings == state.innings

    # Sequential polling must reconstruct the same sequence with no gaps
    # or duplicates: everything from a later cursor is exactly the tail of
    # everything from the start.
    half = state.ball_count // 2
    if half > 0:
        tail_from_half = client.get_deliveries_since(match_id, half)
        assert all_visible[half:] == tail_from_half

    # Nothing "since the end" - polling again at the current count returns
    # nothing new (the actual "most polls are no-op" case section 7.1
    # describes).
    assert client.get_deliveries_since(match_id, state.ball_count) == []


def test_replay_client_conforms(conn):
    client = ReplayClient(conn, FIXTURE_MATCH_ID, speed="instant")
    _assert_conforms(client, FIXTURE_MATCH_ID)


def test_static_live_client_conforms():
    deliveries = [
        Delivery(innings=1, over_num=0, ball_in_over=1, legal_ball_num=1, batter_id=1,
                 non_striker_id=2, bowler_id=3, runs_batter=4, runs_extras=0,
                 extra_type=None, wicket_type=None, player_out_id=None),
        Delivery(innings=1, over_num=0, ball_in_over=2, legal_ball_num=2, batter_id=1,
                 non_striker_id=2, bowler_id=3, runs_batter=1, runs_extras=0,
                 extra_type=None, wicket_type=None, player_out_id=None),
        Delivery(innings=1, over_num=0, ball_in_over=3, legal_ball_num=3, batter_id=2,
                 non_striker_id=1, bowler_id=3, runs_batter=0, runs_extras=0,
                 extra_type=None, wicket_type="bowled", player_out_id=1),
    ]
    state = MatchState(
        match_id=99999, status="complete", innings=1, ball_count=len(deliveries),
        target_runs=None, target_overs=None, venue_id=1, format="T20",
        team_a=1, team_b=2, toss_winner=1, toss_decision="bat", winner=2,
    )
    client = _StaticLiveClient(state, deliveries)
    _assert_conforms(client, 99999)


def test_both_implementations_agree_on_the_shared_conformance_suite_shape():
    """Not a behavioral comparison (they replay different matches) - just
    confirms both are accepted by the exact same function with no
    implementation-specific branching, which is the actual point."""
    import inspect

    sig_replay = inspect.signature(ReplayClient.get_deliveries_since)
    sig_static = inspect.signature(_StaticLiveClient.get_deliveries_since)
    assert list(sig_replay.parameters) == list(sig_static.parameters)
