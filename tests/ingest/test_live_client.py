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


def test_cricketdata_client_conforms(test_db_url):
    """The third implementation, added in Phase 2 session 2, runs through
    `_assert_conforms` UNCHANGED - no assertion was relaxed to accommodate a
    provider that supplies no ball-by-ball feed (Decision 1). The one
    adjustment was widening Delivery's player IDs to Optional, which the
    schema and the batch loader already required; this suite never asserted
    they were non-null.

    Uses the writable cricket_training_test database, not the real corpus -
    the adapter inserts a matches row for the live match it tracks.
    """
    import json
    from pathlib import Path

    from ingest.cricketdata import CricketDataClient

    fixture_dir = Path(__file__).resolve().parent.parent / "fixtures" / "cricketdata"

    class _LiveifiedTransport:
        def get(self, endpoint, params):
            body = json.loads((fixture_dir / "current_matches.json").read_text(encoding="utf-8"))
            body["data"] = body["data"][:1]
            body["data"][0]["matchEnded"] = False
            return body

    write_conn = psycopg.connect(test_db_url, autocommit=True)
    try:
        with write_conn.cursor() as cur:
            cur.execute("TRUNCATE matches, deliveries, match_states, teams, venues, "
                        "team_aliases, venue_aliases, unresolved_entities RESTART IDENTITY CASCADE")
            raw = json.loads((fixture_dir / "current_matches.json").read_text(encoding="utf-8"))["data"][0]
            for name in raw["teams"]:
                cur.execute("INSERT INTO teams (name) VALUES (%s) ON CONFLICT DO NOTHING", (name,))

        client = CricketDataClient(write_conn, _LiveifiedTransport())
        summaries = client.list_live_matches()
        assert summaries, "fixture match should be trackable once its teams resolve"
        _assert_conforms(client, summaries[0].match_id)
    finally:
        write_conn.close()


def test_all_implementations_agree_on_the_shared_conformance_suite_shape():
    """Not a behavioral comparison (they carry different matches) - just
    confirms all three are accepted by the exact same function with no
    implementation-specific branching, which is the actual point."""
    import inspect

    from ingest.cricketdata import CricketDataClient

    signatures = [
        inspect.signature(cls.get_deliveries_since)
        for cls in (ReplayClient, _StaticLiveClient, CricketDataClient)
    ]
    assert all(list(s.parameters) == list(signatures[0].parameters) for s in signatures)
