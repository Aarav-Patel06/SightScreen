"""`matches.status` follows the match (UI mini-phase session 3).

The column was write-once. `_ensure_match_row` early-returns an existing row
and then `ON CONFLICT DO NOTHING`, and the loop read match-end from an
in-memory snapshot without writing it back - so a match inserted as 'live'
described itself as live forever. Four rows on Supabase still said 'live'
months after those matches ended, and every surface keying off the column got
a frozen answer that looked like the product working.

Two things are asserted here and both matter. That the transitions are
written, and that an UNCHANGED status writes nothing: this runs on every poll
of every live match, so a version that wrote unconditionally would be a
constant stream of no-op UPDATEs and a log line per poll.

No database. The connection is a recording fake, in the style of
test_live_predictor.py's `_RecordingConn`.
"""

from __future__ import annotations

from ingest.cricketdata import _status_of
from ingest.live_client import MatchSummary
from serving.live_loop import LivePredictor, run_once


class _Snapshot:
    """Only the two flags `_status_of` reads."""

    def __init__(self, started: bool, ended: bool):
        self.started = started
        self.ended = ended


class _RecordingCursor:
    def __init__(self, statements: list[tuple[str, tuple]], rowcount: int):
        self._statements = statements
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        self._statements.append((" ".join(sql.split()), params or ()))

    def fetchone(self):
        return None


class _RecordingConn:
    """Captures statements and reports a caller-chosen rowcount.

    rowcount is what `record_status` reads to decide whether anything
    changed, so the test drives it directly rather than simulating a table.
    """

    def __init__(self, rowcount: int = 1):
        self.statements: list[tuple[str, tuple]] = []
        self.rowcount = rowcount

    def cursor(self):
        return _RecordingCursor(self.statements, self.rowcount)

    def updates(self) -> list[tuple[str, tuple]]:
        return [s for s in self.statements if s[0].startswith("UPDATE matches")]


class _StatusClient:
    """A live client whose match walks scheduled -> live -> complete.

    Deliberately returns NO deliveries on the final poll. That is the case
    the old code missed: the last poll of a match is the one most likely to
    come back empty, and the status read used to sit inside the
    `if ... and deliveries:` branch.
    """

    def __init__(self, script):
        self._script = list(script)
        self._index = -1
        self.state_calls = 0

    def list_live_matches(self):
        self._index += 1
        return [MatchSummary(match_id=77, status="live", team_a=10, team_b=11, venue_id=7)]

    def poll(self, _match_id):
        return self._script[self._index][1]

    def get_match_state(self, _match_id):
        self.state_calls += 1
        status = self._script[self._index][0]

        class _State:
            match_id = 77
            format = "T20"
            target_runs = None
            target_overs = None

        state = _State()
        state.status = status
        return state

    def next_interval(self, _match_id):
        return 15.0


def _predictor(conn):
    return LivePredictor(conn, {"model_version": "v", "artifact": None}, None, log=lambda _m: None)


# --- the derivation ------------------------------------------------------


def test_status_is_three_valued_and_shared_by_both_call_sites():
    """The insert used to be two-valued ('live' unless ended) while
    get_match_state was three-valued. A match first polled before its toss
    went into the table as 'live' and stayed there."""
    assert _status_of(_Snapshot(started=False, ended=False)) == "scheduled"
    assert _status_of(_Snapshot(started=True, ended=False)) == "live"
    assert _status_of(_Snapshot(started=True, ended=True)) == "complete"
    # A match that ended is complete whether or not the feed said it started.
    assert _status_of(_Snapshot(started=False, ended=True)) == "complete"


# --- the write -----------------------------------------------------------


def test_an_unchanged_status_writes_nothing():
    """The guard that keeps this affordable. Every poll of every live match
    calls record_status, so without IS DISTINCT FROM this would be a no-op
    UPDATE per match per 15 seconds, and a log line with it."""
    conn = _RecordingConn(rowcount=0)
    changed = _predictor(conn).record_status(77, "live")

    assert changed is False
    assert len(conn.updates()) == 1, "it still issues the statement - the DB decides"
    sql, params = conn.updates()[0]
    assert "IS DISTINCT FROM" in sql, "the no-op must be the database's decision, not a read-first race"
    assert params == ("live", 77, "live")


def test_a_changed_status_reports_that_it_changed():
    conn = _RecordingConn(rowcount=1)
    assert _predictor(conn).record_status(77, "complete") is True


def test_record_status_does_not_touch_anything_but_status():
    """Adjacent columns on this row are mirrored from the corpus. A stray
    SET here would silently undo that."""
    conn = _RecordingConn(rowcount=1)
    _predictor(conn).record_status(77, "live")

    sql = conn.updates()[0][0]
    assert sql.startswith("UPDATE matches SET status = %s ")
    for column in ("winner", "target_runs", "result_method", "toss_winner", "competition"):
        assert column not in sql


# --- the transition, through the real loop -------------------------------


def test_a_match_transitions_scheduled_live_complete_across_polls():
    """The whole point, driven through run_once rather than asserted on the
    method in isolation.

    Note the final poll returns no deliveries. Before this change the status
    read lived inside `if predictor is not None and deliveries:`, so a match
    that ended on an empty poll never transitioned and never finished.
    """
    conn = _RecordingConn(rowcount=1)
    client = _StatusClient(
        [
            ("scheduled", []),
            ("live", [object()]),
            ("complete", []),
        ]
    )
    predictor = _predictor(conn)
    predictor.observe = lambda *_a, **_k: 0

    lines: list[str] = []
    tracked: dict[int, int] = {}
    for _ in range(3):
        run_once(client, tracked, log=lines.append, predictor=predictor)

    written = [params[0] for _sql, params in conn.updates()]
    assert written == ["scheduled", "live", "complete"]
    assert client.state_calls == 3, "status must be read on every poll, not only when balls arrive"
    assert any("status -> complete" in line for line in lines)


def test_match_end_is_announced_once_even_though_the_match_stays_listed():
    """A completed match stays in the provider's live list for a while, so
    finish() is reached on every poll until it drops off. Standing rule 14:
    a line that repeats becomes furniture and the next real one is read past.
    """
    conn = _RecordingConn(rowcount=0)
    client = _StatusClient([("complete", []), ("complete", []), ("complete", [])])
    predictor = _predictor(conn)
    predictor.observe = lambda *_a, **_k: 0

    lines: list[str] = []
    tracked: dict[int, int] = {}
    for _ in range(3):
        run_once(client, tracked, log=lines.append, predictor=predictor)

    finished = [line for line in lines if "outcomes resolve separately" in line]
    assert len(finished) == 1, f"match_end logged {len(finished)} times, expected once"


def test_no_predictor_means_no_status_write():
    """The pre-Phase-3 polling tests construct no predictor and must keep
    exercising exactly the path they always did - no database, no state
    call."""
    client = _StatusClient([("live", [])])
    lines: list[str] = []

    run_once(client, {}, log=lines.append, predictor=None)

    assert client.state_calls == 0
