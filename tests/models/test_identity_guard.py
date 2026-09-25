"""The two-id-spaces guard on outcome resolution, proven to fire.

WHY THIS FILE EXISTS. `assert_same_matches` is the guard standing between the
serving database and the single worst silent corruption this project has
found: `matches.match_id` carries two id spaces, so Supabase match 3 is a CPL
2026 fixture while LOCAL match 3 is a 2017 Pakistan-Australia ODI. Resolving
one against the other attaches a real outcome to predictions about a different
game, and **nothing downstream can tell** - every row is well formed, the
Brier score is a number, the reliability diagram has ten bins.

Before 2026-09-25 it had no test at all. It was only ever observed passing,
which is the same state every other safeguard in this repository was in on the
day it turned out not to work. `MatchIdentityMismatch` appeared twice in
api/src and zero times in tests/.

No database: `_Conn` answers from a queue, the same shape
tests/ingest/test_mirror_columns.py uses for the sibling guard on the mirror.
That matters because this guard must be provable in CI, where neither database
is reachable - a proof that needs production credentials is a proof nobody
runs.
"""

from datetime import datetime

import pytest

from models.resolve_outcomes import MatchIdentityMismatch, assert_same_matches


class _Cursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        self._conn.statements.append((sql, params))

    def fetchall(self):
        return self._conn.next_result()


class _Conn:
    """Answers each SELECT from a queue of result sets."""

    def __init__(self, rows=()):
        self._queue = [list(rows)]
        self.statements: list[tuple] = []

    def next_result(self):
        return self._queue.pop(0) if self._queue else []

    def cursor(self):
        return _Cursor(self)


def _row(match_id, competition="Caribbean Premier League", fmt="T20", date="2026-08-20"):
    return (match_id, competition, fmt, datetime.fromisoformat(f"{date}T14:00:00"))


# --- the ordinary case, so the guard is not blocking everything ----------


def test_identical_rows_pass():
    rows = [_row(8532), _row(9337)]
    assert_same_matches(_Conn(rows), _Conn(rows), [8532, 9337])  # must not raise


def test_a_differing_start_TIME_on_the_same_DATE_is_not_a_mismatch():
    """Compared on the start DATE, deliberately. The mirror copies start_time
    exactly, but a guard that compared timestamps would fail on a timezone
    round-trip and teach everyone to ignore it."""
    local = _Conn([(8532, "CPL", "T20", datetime.fromisoformat("2026-08-20T14:00:00"))])
    remote = _Conn([(8532, "CPL", "T20", datetime.fromisoformat("2026-08-20T23:59:00"))])
    assert_same_matches(local, remote, [8532])


# --- the guard firing -----------------------------------------------------


def test_the_real_incident_is_caught():
    """The exact shape found on 2026-09-23, reconstructed.

    Supabase id 3 is a CPL 2026 match; corpus id 3 is a 2017 Pakistan tour of
    Australia ODI. Same integer, different game.
    """
    supabase = _Conn([_row(3, "Caribbean Premier League", "T20", "2026-09-18")])
    local = _Conn([_row(3, "Pakistan tour of Australia", "ODI", "2017-01-26")])

    with pytest.raises(MatchIdentityMismatch) as caught:
        assert_same_matches(local, supabase, [3])

    message = str(caught.value)
    assert "match_id 3" in message
    # Both sides must appear, or the reader cannot tell which database is the
    # surprising one.
    assert "Caribbean Premier League" in message
    assert "Pakistan tour of Australia" in message


@pytest.mark.parametrize(
    "field,local_row,remote_row",
    [
        ("competition", _row(7, competition="Big Bash League"), _row(7, competition="CPL")),
        ("format", _row(7, fmt="ODI"), _row(7, fmt="T20")),
        ("start date", _row(7, date="2026-08-20"), _row(7, date="2026-08-21")),
    ],
)
def test_each_compared_field_can_fail_on_its_own(field, local_row, remote_row):
    """Three fields are compared, so three fields must each be able to reject.

    The sql_guard file calls this non-vacuity: a three-field comparison where
    only one field can ever differ is a one-field comparison with a comment.
    """
    with pytest.raises(MatchIdentityMismatch, match="do not refer to the same match"):
        assert_same_matches(_Conn([local_row]), _Conn([remote_row]), [7])


def test_a_missing_row_on_either_side_is_a_mismatch():
    """Absence is not agreement. An id present on Supabase and absent from the
    corpus is precisely a live-worker row, which is the case that started all
    of this."""
    with pytest.raises(MatchIdentityMismatch):
        assert_same_matches(_Conn([]), _Conn([_row(1)]), [1])
    with pytest.raises(MatchIdentityMismatch):
        assert_same_matches(_Conn([_row(1)]), _Conn([]), [1])


def test_one_bad_id_in_a_good_batch_still_refuses():
    """The batch is resolved as a unit, so one wrong id must stop all of it
    rather than resolving the other 99 and reporting success."""
    good, bad = _row(8532), _row(3)
    local = _Conn([good, _row(3, "Pakistan tour of Australia", "ODI", "2017-01-26")])
    supabase = _Conn([good, bad])

    with pytest.raises(MatchIdentityMismatch) as caught:
        assert_same_matches(local, supabase, [8532, 3])
    assert "1 match id(s)" in str(caught.value)
    assert "match_id 8532" not in str(caught.value), "do not report the innocent id"


def test_the_message_says_what_to_do_instead():
    """A refusal without a next step gets worked around."""
    supabase = _Conn([_row(3)])
    local = _Conn([_row(3, "Pakistan tour of Australia", "ODI", "2017-01-26")])
    with pytest.raises(MatchIdentityMismatch) as caught:
        assert_same_matches(local, supabase, [3])
    assert "crosswalk" in str(caught.value)
