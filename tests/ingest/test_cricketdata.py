"""CricketData adapter tests (SPEC.md sections 4.2/7.1, Phase 2 session 2).

Every test runs offline against the recorded fixtures in
tests/fixtures/cricketdata/ (captured once by `python -m
ingest.record_fixtures`). No test hits the network or burns quota -
Decision 6's rule, and the reason the fixtures exist.

Decision 4's malformed cases are synthesised by mutating those real
bodies: the provider will duplicate, reorder, correct and drop, but not on
demand, and waiting for a bug we can't schedule is not a test strategy.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ingest.cricketdata import (
    BudgetExhausted,
    CricketDataClient,
    FixtureTransport,
    InningsSnapshot,
    LiveBudget,
    MatchSnapshot,
    TransitionVerdict,
    overs_to_balls,
    parse_match,
    reconstruct_innings,
    scheduled_balls,
    validate_transition,
)
from ingest.live_client import ReconstructionConfidence

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "cricketdata"


def _raw_matches() -> list[dict]:
    body = json.loads((FIXTURE_DIR / "current_matches.json").read_text(encoding="utf-8"))
    return body["data"]


def _snapshot(runs: int, wickets: int, balls: int, *, innings: int = 1, status: str = "") -> MatchSnapshot:
    scores = [InningsSnapshot(0, 0, 0)] * (innings - 1) + [InningsSnapshot(runs, wickets, balls)]
    return MatchSnapshot(
        provider_id="m1", name="A vs B", format="T20", status=status, venue="Ground, City",
        teams=("A", "B"), innings=tuple(scores), started=True, ended=False,
        reduced_overs=None, dls_target=None, observed_at=datetime.now(timezone.utc),
    )


# --- Parsing real recorded bodies -------------------------------------------


def test_overs_to_balls_reads_cricket_notation_not_decimals():
    assert overs_to_balls(18.1) == 109  # 18 overs and 1 ball
    assert overs_to_balls(20) == 120
    assert overs_to_balls(14.5) == 89
    assert overs_to_balls(0) == 0


def test_parse_real_current_matches_fixture():
    snapshots = [parse_match(raw) for raw in _raw_matches()]
    assert snapshots, "fixture should contain matches"
    first = snapshots[0]
    assert first.format == "T20"
    assert len(first.teams) == 2
    assert first.ball_count > 0
    # The recorded CPL matches are all finished - that is itself the real
    # state of the feed at capture time, not a fixture defect.
    assert all(s.started for s in snapshots)


def test_parse_extracts_dls_target_and_reduced_overs_from_free_text_status():
    raw = dict(_raw_matches()[0])
    raw["status"] = "England Women won by 9 wkts (Match reduced to 21 overs per side due to rain, DLS target 106)"
    snapshot = parse_match(raw)
    assert snapshot.reduced_overs == 21.0
    assert snapshot.dls_target == 106
    balls, reduced_unknown = scheduled_balls(snapshot)
    assert balls == 126
    assert reduced_unknown is False


def test_unparseable_reduction_is_flagged_not_silently_nominal():
    raw = dict(_raw_matches()[0])
    raw["status"] = "Match reduced due to rain"  # no over count to parse
    snapshot = parse_match(raw)
    balls, reduced_unknown = scheduled_balls(snapshot)
    assert balls == 120  # nominal fallback
    assert reduced_unknown is True  # ...but the caller is told not to trust it


def test_missing_match_type_does_not_crash_parsing():
    raw = {k: v for k, v in _raw_matches()[0].items() if k != "matchType"}
    snapshot = parse_match(raw)
    assert snapshot.format is None  # unsupported, to be refused downstream


# --- Reconstruction ----------------------------------------------------------


def test_single_new_ball_is_confirmed_and_takes_all_the_runs():
    deliveries = reconstruct_innings(InningsSnapshot(10, 1, 6), InningsSnapshot(14, 1, 7), innings_no=1)
    assert len(deliveries) == 1
    ball = deliveries[0]
    assert ball.runs_batter == 4
    assert ball.legal_ball_num == 7
    assert ball.confidence is ReconstructionConfidence.CONFIRMED


def test_multi_ball_gap_is_inferred_never_silently_attributed():
    deliveries = reconstruct_innings(InningsSnapshot(10, 0, 6), InningsSnapshot(17, 1, 9), innings_no=1)
    assert len(deliveries) == 3
    assert all(d.confidence is ReconstructionConfidence.INFERRED for d in deliveries)
    assert sum(d.runs_batter + d.runs_extras for d in deliveries) == 7  # total preserved exactly
    assert sum(1 for d in deliveries if d.wicket_type) == 1


def test_wicket_in_an_ambiguous_span_goes_on_the_last_ball_by_convention():
    deliveries = reconstruct_innings(InningsSnapshot(10, 0, 6), InningsSnapshot(10, 1, 9), innings_no=1)
    assert deliveries[-1].wicket_type is not None
    assert all(d.wicket_type is None for d in deliveries[:-1])


def test_runs_without_a_new_ball_is_an_extra_and_does_not_advance_legal_balls():
    deliveries = reconstruct_innings(InningsSnapshot(10, 0, 6), InningsSnapshot(11, 0, 6), innings_no=1)
    assert len(deliveries) == 1
    assert deliveries[0].extra_type == "wide"
    assert deliveries[0].runs_extras == 1
    assert deliveries[0].legal_ball_num == 6  # unchanged, as a wide should
    assert deliveries[0].confidence is ReconstructionConfidence.CONFIRMED


def test_no_change_produces_no_deliveries():
    assert reconstruct_innings(InningsSnapshot(10, 1, 6), InningsSnapshot(10, 1, 6), innings_no=1) == []


# --- Decision 4: the validation gate ----------------------------------------


def test_repeated_snapshot_is_a_no_op():
    snap = _snapshot(50, 2, 36)
    verdict, _ = validate_transition(snap, _snapshot(50, 2, 36))
    assert verdict is TransitionVerdict.NO_CHANGE


def test_balls_going_backwards_is_rejected():
    verdict, reason = validate_transition(_snapshot(50, 2, 36), _snapshot(50, 2, 30))
    assert verdict is TransitionVerdict.REJECT
    assert "backwards" in reason


def test_score_revised_down_is_a_correction_not_a_rejection():
    verdict, reason = validate_transition(_snapshot(50, 2, 36), _snapshot(46, 2, 36))
    assert verdict is TransitionVerdict.CORRECTION
    assert "revised down" in reason


def test_a_wicket_being_taken_back_is_also_a_correction():
    verdict, _ = validate_transition(_snapshot(50, 3, 36), _snapshot(50, 2, 36))
    assert verdict is TransitionVerdict.CORRECTION


def test_snapshot_for_a_different_match_is_rejected():
    other = _snapshot(50, 2, 36)
    other = MatchSnapshot(**{**other.__dict__, "provider_id": "somebody-else"})
    verdict, _ = validate_transition(_snapshot(50, 2, 36), other)
    assert verdict is TransitionVerdict.REJECT


def test_disappearing_innings_is_rejected():
    two_innings = _snapshot(5, 0, 3, innings=2)
    verdict, _ = validate_transition(two_innings, _snapshot(180, 6, 120, innings=1))
    assert verdict is TransitionVerdict.REJECT


def test_first_observation_is_always_accepted():
    verdict, _ = validate_transition(None, _snapshot(0, 0, 0))
    assert verdict is TransitionVerdict.ACCEPT


# --- Decision 3: budget ------------------------------------------------------


def test_budget_reads_the_providers_own_counter():
    budget = LiveBudget()
    budget.observe({"info": {"hitsToday": 37, "hitsLimit": 2000}})
    assert budget.hits_today == 37
    assert budget.remaining == 1963


def test_budget_refuses_a_match_it_cannot_afford_to_finish():
    budget = LiveBudget(hits_today=1950, hits_limit=2000)
    with pytest.raises(BudgetExhausted, match="refusing to start"):
        budget.require_affordable("T20", balls_bowled=0, interval_seconds=15.0)


def test_budget_allows_an_affordable_match():
    budget = LiveBudget(hits_today=0, hits_limit=2000)
    budget.require_affordable("T20", balls_bowled=0, interval_seconds=15.0)  # ~480 calls, affordable


def test_a_longer_interval_makes_a_match_affordable_again():
    # 300 calls left: a full T20 needs ~480 polls at 15s but only ~120 at
    # 60s, so backing off is what turns a refusal into a tracked match.
    budget = LiveBudget(hits_today=1700, hits_limit=2000)
    with pytest.raises(BudgetExhausted):
        budget.require_affordable("T20", balls_bowled=0, interval_seconds=15.0)
    budget.require_affordable("T20", balls_bowled=0, interval_seconds=60.0)


def test_interval_backs_off_between_overs_and_degrades_as_quota_runs_down():
    healthy = LiveBudget(hits_today=0, hits_limit=2000)
    assert healthy.interval(between_overs=False) == 15.0
    assert healthy.interval(between_overs=True) == 45.0

    strained = LiveBudget(hits_today=1850, hits_limit=2000)  # 92.5%
    assert strained.interval(between_overs=False) == 60.0

    critical = LiveBudget(hits_today=1980, hits_limit=2000)  # 99%
    assert critical.interval(between_overs=False) == 120.0  # degrades, never stops


# --- Transport ---------------------------------------------------------------


def test_fixture_transport_serves_recorded_bodies():
    transport = FixtureTransport(FIXTURE_DIR)
    body = transport.get("currentMatches", {"offset": "0"})
    assert body["status"] == "success"
    assert body["apikey"] == "REDACTED"  # never commit the key


def test_client_surfaces_a_provider_failure_body_as_an_error():
    from ingest.cricketdata import CricketDataError

    transport = FixtureTransport(FIXTURE_DIR, mapping={"currentMatches": "match_bbb_unavailable"})
    client = CricketDataClient(conn=None, transport=transport)
    with pytest.raises(CricketDataError, match="Not able to get BBB"):
        client.list_live_matches()


# --- Decision 2: entity resolution and its degradation path ------------------
# These use the writable cricket_training_test database (tests/conftest.py),
# never the real corpus - they insert matches rows and queue entities.


class LiveifiedTransport:
    """Serves the real recorded currentMatches body with the first match
    flipped back to in-progress. The recorded CPL matches had all finished
    by capture time; nothing about the shape changes, only `matchEnded`."""

    def __init__(self, fixture_dir: Path, ended: bool = False) -> None:
        self._dir = fixture_dir
        self._ended = ended

    def get(self, endpoint: str, params: dict[str, str]) -> dict:
        body = json.loads((self._dir / "current_matches.json").read_text(encoding="utf-8"))
        body["data"] = body["data"][:1]
        body["data"][0]["matchEnded"] = self._ended
        return body


def _seed_teams(conn, names: list[str]) -> None:
    with conn.cursor() as cur:
        for name in names:
            cur.execute("INSERT INTO teams (name) VALUES (%s) ON CONFLICT DO NOTHING", (name,))


def test_refuses_to_track_a_match_whose_team_cannot_be_resolved(conn):
    """An unresolvable team means no elo_diff and a NOT NULL
    batting_team_id - refusing is the designed degradation, and it must not
    take down a poll that covers every live match at once."""
    client = CricketDataClient(conn, LiveifiedTransport(FIXTURE_DIR))
    assert client.list_live_matches() == []
    assert client._rejections, "the refusal should be recorded, not silent"
    assert "unresolved" in client._rejections[0][1]


def test_live_resolution_never_creates_a_canonical_team(conn):
    """allow_create=False: minting an entity with no human in the loop is
    exactly the unsupervised guess a serving path must not make."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM teams")
        before = cur.fetchone()[0]

    CricketDataClient(conn, LiveifiedTransport(FIXTURE_DIR)).list_live_matches()

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM teams")
        assert cur.fetchone()[0] == before, "live path must not create canonical entities"
        cur.execute(
            "SELECT reason FROM unresolved_entities WHERE entity_kind = 'team' AND source = 'cricketdata'"
        )
        reasons = [r[0] for r in cur.fetchall()]
    assert reasons and all(r == "create_suppressed" for r in reasons)


def test_tracks_a_match_once_its_teams_resolve(conn):
    raw = _raw_matches()[0]
    _seed_teams(conn, list(raw["teams"]))

    client = CricketDataClient(conn, LiveifiedTransport(FIXTURE_DIR))
    summaries = client.list_live_matches()

    assert len(summaries) == 1
    state = client.get_match_state(summaries[0].match_id)
    assert state.status == "live"
    assert state.format == "T20"
    # Catch-up: the worker joined a match already in progress, so every
    # already-bowled ball is emitted as one inferred span.
    deliveries = client.get_deliveries_since(state.match_id, 0)
    assert len(deliveries) == state.ball_count > 0
    assert all(d.confidence is ReconstructionConfidence.INFERRED for d in deliveries)


def test_an_unresolvable_venue_degrades_instead_of_refusing(conn):
    """venue_id=None feeds NaN to both venue features - the identical
    cold-start path 24-33% of training matches already took."""
    raw = _raw_matches()[0]
    _seed_teams(conn, list(raw["teams"]))  # teams seeded, venue deliberately not

    client = CricketDataClient(conn, LiveifiedTransport(FIXTURE_DIR))
    summaries = client.list_live_matches()

    assert len(summaries) == 1, "an unknown venue must not stop the match being tracked"
    assert summaries[0].venue_id is None


def test_player_ids_are_absent_because_the_feed_has_none(conn):
    """The honest consequence of snapshot reconstruction, asserted rather
    than left implicit: no per-ball striker or bowler identity exists."""
    raw = _raw_matches()[0]
    _seed_teams(conn, list(raw["teams"]))

    client = CricketDataClient(conn, LiveifiedTransport(FIXTURE_DIR))
    match_id = client.list_live_matches()[0].match_id
    for delivery in client.get_deliveries_since(match_id, 0):
        assert delivery.batter_id is None
        assert delivery.bowler_id is None


def test_repeat_poll_of_an_unchanged_match_emits_nothing(conn):
    """SPEC.md section 7.1 step 2: most polls are no-ops, and a no-op must
    not duplicate deliveries."""
    raw = _raw_matches()[0]
    _seed_teams(conn, list(raw["teams"]))

    client = CricketDataClient(conn, LiveifiedTransport(FIXTURE_DIR))
    match_id = client.list_live_matches()[0].match_id
    before = len(client.get_deliveries_since(match_id, 0))

    assert client.poll(match_id) == []
    assert len(client.get_deliveries_since(match_id, 0)) == before


def test_match_row_is_idempotent_across_clients(conn):
    """Restarting the worker must reuse the same canonical match_id, not
    mint a second row for the same provider match."""
    raw = _raw_matches()[0]
    _seed_teams(conn, list(raw["teams"]))

    first = CricketDataClient(conn, LiveifiedTransport(FIXTURE_DIR)).list_live_matches()[0].match_id
    second = CricketDataClient(conn, LiveifiedTransport(FIXTURE_DIR)).list_live_matches()[0].match_id
    assert first == second


# --- Latency harness (Decision 5) -------------------------------------------


def test_latency_recorder_ignores_no_op_polls():
    """Most polls are no-ops (SPEC.md section 7.1 step 2). A poll that
    produced no deliveries measures nothing about how fast a ball reaches a
    prediction, so it must not dilute the distribution."""
    from ingest.measure_latency import LatencyRecorder

    recorder = LatencyRecorder()
    recorder.record(provider_rtt=0.2, reconstruction=0.01, prediction=0.05, new_deliveries=0)
    assert recorder.samples == []

    recorder.record(provider_rtt=0.2, reconstruction=0.01, prediction=0.05, new_deliveries=1)
    assert len(recorder.samples) == 1


def test_latency_summary_reports_our_own_chain_and_a_detection_bound():
    from ingest.measure_latency import LatencyRecorder

    recorder = LatencyRecorder()
    for rtt in (0.10, 0.20, 0.30):
        recorder.record(provider_rtt=rtt, reconstruction=0.01, prediction=0.04, new_deliveries=1)

    summary = recorder.summary(interval=30.0)
    assert summary["n"] == 3
    assert summary["poll_to_write_median"] == pytest.approx(0.25)
    # The bound includes the interval, because a ball can land just after a
    # poll and wait a full interval to be seen.
    assert summary["detection_bound_median"] == pytest.approx(30.25)


def test_latency_summary_is_empty_rather_than_invented_when_nothing_was_seen():
    from ingest.measure_latency import LatencyRecorder

    assert LatencyRecorder().summary(interval=15.0) == {"n": 0}
