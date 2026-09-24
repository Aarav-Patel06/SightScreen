"""What `mirror_match_rows` copies, and what it refuses to touch.

Two separate protections, both added after they were needed rather than
before, and both cheap to assert without a database.

The first is that `status` is never refreshed. Since the UI phase the live
worker owns that column; a mirror that refreshed it would stamp a match
currently in progress with the corpus row's 'complete', and the two writers
would fight with whichever ran last winning.

The second is the id-space guard. A match_id means different things on the
two databases: corpus ids come from cricsheet, live-worker ids from the
Supabase sequence. Migration 20260919000001 pushed that sequence to 1,000,000
so they could not collide again, but the three rows already at ids 1, 2 and 3
were deliberately not renumbered because their predictions reference them.
Mirroring a corpus row onto one of those replaces a real match with an
unrelated one, and it is silent, because the result is a perfectly
well-formed row describing the wrong game.
"""

from __future__ import annotations

import pytest

from ingest.replay_log import _MIRRORED_COLUMNS, _REFRESHED_COLUMNS, mirror_match_rows


class _Cursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        self._conn.statements.append((" ".join(sql.split()), params))

    def executemany(self, sql, rows):
        self._conn.statements.append((" ".join(sql.split()), list(rows)))

    def fetchall(self):
        return self._conn.next_result()


class _Conn:
    """A connection that answers each SELECT from a queue of result sets."""

    def __init__(self, rows=(), then=None):
        self.rows = list(rows)
        self._queue = [list(rows)] + [list(r) for r in (then or [])]
        self.statements: list[tuple] = []
        self.commits = 0

    def next_result(self):
        return self._queue.pop(0) if self._queue else []

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1


# --- the column list -----------------------------------------------------


def test_status_is_mirrored_on_insert_but_never_refreshed():
    """The row has to carry a status to satisfy NOT NULL, and must never have
    it overwritten afterwards."""
    assert "status" in _MIRRORED_COLUMNS
    assert "status" not in _REFRESHED_COLUMNS


def test_the_key_is_not_refreshed():
    assert "match_id" not in _REFRESHED_COLUMNS


def test_the_result_columns_are_carried():
    """Omitted before the UI phase on the reasoning that the label stays
    local. Correct while only the serving path read this table; wrong once a
    page had to say who won."""
    for column in ("winner", "result_method", "target_runs", "toss_winner", "toss_decision"):
        assert column in _MIRRORED_COLUMNS, column
        assert column in _REFRESHED_COLUMNS, column


def test_the_statement_updates_on_conflict_rather_than_doing_nothing():
    """DO NOTHING would mean the 107 rows written before the result columns
    existed could never receive them."""
    local, supabase = _Conn(rows=[(1,) * len(_MIRRORED_COLUMNS)]), _Conn()
    mirror_match_rows(local, supabase, [7191])

    insert = [s for s, _ in supabase.statements if s.startswith("INSERT INTO matches")]
    assert len(insert) == 1
    assert "ON CONFLICT (match_id) DO UPDATE SET" in insert[0]
    assert "status = EXCLUDED.status" not in insert[0]


# --- the id-space guard --------------------------------------------------


def test_refuses_to_mirror_over_a_live_worker_row():
    """The failure this exists for. On 2026-09-23 a backfill passed ids 1, 2
    and 3 and replaced three CPL 2026 rows with 2017 Pakistan-in-Australia
    ODIs, because the local corpus happens to have matches at those ids.
    """
    supabase = _Conn(rows=[(1,), (2,)])  # both carry external_ids->>'cricketdata'
    local = _Conn()

    with pytest.raises(ValueError, match="refusing to mirror"):
        mirror_match_rows(local, supabase, [1, 2, 7191])

    assert local.statements == [], "the corpus must not even be read once the ids are refused"
    assert supabase.commits == 0


def test_the_refusal_names_the_offending_ids():
    """A caller who passed a wrong id needs to know which one, not that
    something somewhere was wrong."""
    supabase = _Conn(rows=[(3,)])
    with pytest.raises(ValueError) as caught:
        mirror_match_rows(_Conn(), supabase, [3, 8532])

    assert "[3]" in str(caught.value)
    assert "20260919000001" in str(caught.value), "point at the migration that explains it"


def test_a_corpus_owned_row_mirrors_normally():
    """The guard must not block the ordinary case - 106 of the 107 rows."""
    local = _Conn(rows=[(8532,) + (None,) * (len(_MIRRORED_COLUMNS) - 1)])
    supabase = _Conn(rows=[])  # nothing carries a cricketdata id

    assert mirror_match_rows(local, supabase, [8532]) == 1
    assert supabase.commits == 1


# --- the dry run and the change-set assertion ----------------------------
#
# Both added after a backfill wrote three matches' data onto three unrelated
# matches and printed "mirrored 106 of 107" - a success message. The id-space
# guard above catches that specific case; these two catch the general one,
# which is a statement that mutates existing rows reporting only how many it
# touched rather than what it changed.


def _corpus_row(match_id: int, **overrides):
    values = {c: None for c in _MIRRORED_COLUMNS}
    values["match_id"] = match_id
    values.update(overrides)
    return tuple(values[c] for c in _MIRRORED_COLUMNS)


def _destination_row(match_id: int, **overrides):
    values = {c: None for c in _REFRESHED_COLUMNS}
    values.update(overrides)
    return (match_id, *(values[c] for c in _REFRESHED_COLUMNS))


def test_a_dry_run_writes_nothing_and_says_what_would_change(capsys):
    local = _Conn(rows=[_corpus_row(8532, competition="ILT20")])
    # First SELECT is the live-worker guard (empty), second is the comparison.
    supabase = _Conn(rows=[], then=[[_destination_row(8532, competition="Old name")]])

    assert mirror_match_rows(local, supabase, [8532], dry_run=True) == 0

    assert supabase.commits == 0
    assert not any(s.startswith("INSERT") for s, _ in supabase.statements)
    out = capsys.readouterr().out
    assert "1 row(s) would change" in out
    assert "match 8532: competition" in out


def test_a_dry_run_reports_an_unchanged_destination_as_unchanged():
    local = _Conn(rows=[_corpus_row(8532, competition="ILT20")])
    supabase = _Conn(rows=[], then=[[_destination_row(8532, competition="ILT20")]])

    assert mirror_match_rows(local, supabase, [8532], dry_run=True) == 0
    assert supabase.commits == 0


def test_an_unexpected_change_set_size_refuses_to_write():
    """The assertion that would have stopped the real incident: three rows
    behaving differently from the other 104 is exactly a change set of an
    unexpected size."""
    local = _Conn(rows=[_corpus_row(8532, competition="ILT20")])
    supabase = _Conn(rows=[], then=[[_destination_row(8532, competition="Old name")]])

    with pytest.raises(ValueError, match=r"1 row\(s\) would change, expected 0"):
        mirror_match_rows(local, supabase, [8532], expect_changed=0)

    assert supabase.commits == 0, "nothing may be written once the count is wrong"


def test_the_expected_change_set_size_is_allowed_through():
    local = _Conn(rows=[_corpus_row(8532, competition="ILT20")])
    supabase = _Conn(rows=[], then=[[_destination_row(8532, competition="Old name")]])

    assert mirror_match_rows(local, supabase, [8532], expect_changed=1) == 1
    assert supabase.commits == 1


def test_an_insert_is_not_counted_as_a_change():
    """A destination row that does not exist yet is an insert, and inserts are
    not the dangerous case - a wrong insert is visible as a new row, a wrong
    update leaves a well-formed row describing something else."""
    local = _Conn(rows=[_corpus_row(9999)])
    supabase = _Conn(rows=[], then=[[]])

    assert mirror_match_rows(local, supabase, [9999], expect_changed=0) == 1
    assert supabase.commits == 1
