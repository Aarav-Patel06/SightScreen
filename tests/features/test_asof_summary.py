"""As-of summary rebuild, the Elo tie-break, and staleness refusal
(SPEC.md section 15's 2026-09-10 decision, Phase 2 session 3).

Two fixtures, deliberately:

  `real_conn` - the real corpus, read-only. The date-grain design rests on
  two properties of the actual data, so they are asserted rather than
  assumed, and they are what would break first if the loader ever changed.

  `conn`      - tests/conftest.py's writable throwaway database, for the
  staleness tests, which need to corrupt a summary on purpose.

The parity gate itself lives in tests/db/test_asof_parity.py.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest

from features.as_of import StaleReferenceData, assert_reference_fresh
from features.asof_summary import (
    DERIVED_TABLES,
    ELO_SUMMARY,
    VENUE_SUMMARY,
    content_hash,
    rebuild_all,
    record_sync_state,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"


def _env() -> dict[str, str]:
    from dotenv import dotenv_values

    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        pytest.skip(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


@pytest.fixture(scope="module")
def real_conn():
    connection = psycopg.connect(_env()["LOCAL_DATABASE_URL"], autocommit=True)
    yield connection
    connection.close()


# --- The two corpus properties the date grain depends on ------------------


def test_no_match_starts_off_midnight_utc(real_conn):
    """The summary is keyed on a DATE. That is lossless only while every
    match starts at midnight - otherwise two matches on one day could need
    two different breakpoints, and collapsing them would lose an ordering
    the direct query could see."""
    with real_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM matches WHERE (start_time AT TIME ZONE 'UTC')::time <> '00:00:00'"
        )
        assert cur.fetchone()[0] == 0


def test_the_summary_date_key_equals_the_date_training_looks_up_by(real_conn):
    """Training's as_of_date comes from deliveries.match_date. The summary is
    keyed on (start_time AT TIME ZONE 'UTC')::date. If those ever diverge,
    every training lookup would silently shift by a day."""
    with real_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM (
                SELECT DISTINCT d.match_id, d.match_date, m.start_time
                FROM deliveries d JOIN matches m USING (match_id)
            ) x
            WHERE match_date <> (start_time AT TIME ZONE 'UTC')::date
            """
        )
        assert cur.fetchone()[0] == 0


# --- The Elo tie-break ----------------------------------------------------


def test_elo_summary_takes_the_last_match_of_a_shared_date(real_conn):
    """A team playing twice on one date writes two elo_ratings rows with an
    identical as_of. The summary must carry the END-OF-DAY rating, which is
    the one from the highest match_id (recompute_format walks matches
    ORDER BY start_time, match_id).

    This is the case that made the pre-session-3 `ORDER BY as_of DESC
    LIMIT 1` non-deterministic: with nothing after as_of to break the tie it
    returned whichever row heap order surfaced.
    """
    with real_conn.cursor() as cur:
        cur.execute(
            """
            WITH tied AS (
                SELECT team_id, format, as_of, max(match_id) AS last_match_id
                FROM elo_ratings GROUP BY 1, 2, 3 HAVING count(*) > 1
            )
            SELECT count(*) AS groups,
                   count(*) FILTER (
                       WHERE s.rating IS DISTINCT FROM e.rating::text::numeric
                   ) AS wrong
            FROM tied t
            JOIN elo_ratings e ON e.team_id = t.team_id AND e.format = t.format
                              AND e.match_id = t.last_match_id
            JOIN elo_asof_summary s ON s.team_id = t.team_id AND s.format = t.format
                              AND s.effective_date = (t.as_of AT TIME ZONE 'UTC')::date
            """
        )
        groups, wrong = cur.fetchone()
    if groups == 0:
        pytest.skip("no same-date Elo ties in this corpus")
    assert wrong == 0, f"{wrong} of {groups} tied dates carry the wrong side of the tie"
    print(f"\n{groups} same-date Elo ties, all resolved to the last match of the day")


def test_the_bare_ordering_really_was_ambiguous(real_conn):
    """Guards the reason elo_as_of_direct carries `, match_id DESC`.

    If this ever reports zero, the tie-break has stopped being load-bearing
    and the extra ORDER BY term could be dropped - but it must be MEASURED,
    not assumed, which is exactly the mistake the original helper made.
    """
    with real_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM (SELECT 1 FROM elo_ratings "
            "GROUP BY team_id, format, as_of HAVING count(*) > 1) x"
        )
        tied_groups = cur.fetchone()[0]
    assert tied_groups > 0, (
        "no same-date Elo ties remain; the match_id tie-break is no longer "
        "load-bearing and this test should be revisited rather than deleted"
    )
    print(f"\n{tied_groups} (team, format, as_of) groups have more than one row")


def test_rebuild_is_idempotent(real_conn):
    """Two consecutive rebuilds produce byte-identical tables. Slow (two full
    rebuilds against the real corpus), and deselected by the repo's usual
    `-k "not idempotent"`."""
    rebuild_all(real_conn)
    first = {t.name: content_hash(real_conn, t) for t in DERIVED_TABLES}
    rebuild_all(real_conn)
    second = {t.name: content_hash(real_conn, t) for t in DERIVED_TABLES}
    assert first == second


# --- Staleness ------------------------------------------------------------


def _seed_summaries(conn, newest: date) -> None:
    """Minimal rows satisfying both summaries' foreign keys, with `newest`
    as the most recent breakpoint."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO venues (name) VALUES ('Staleness Ground') RETURNING venue_id")
        venue_id = cur.fetchone()[0]
        cur.execute("INSERT INTO teams (name) VALUES ('Staleness XI') RETURNING team_id")
        team_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO venue_asof_summary (venue_id, effective_date, chase_wins, chase_n, "
            "first_inns_runs, first_inns_n) VALUES (%s, %s, 6.0, 12, 1800, 12)",
            (venue_id, newest),
        )
        cur.execute(
            "INSERT INTO elo_asof_summary (team_id, format, effective_date, rating) "
            "VALUES (%s, 'T20', %s, 1512.5)",
            (team_id, newest),
        )
    for table in DERIVED_TABLES:
        record_sync_state(conn, table, synced=True)


def test_fresh_reference_data_is_accepted(conn):
    today = date(2026, 9, 15)
    _seed_summaries(conn, newest=today - timedelta(days=1))
    report = assert_reference_fresh(conn, today=today)
    assert report["age_days"] == 1
    assert set(report["hashes"]) == {t.name for t in DERIVED_TABLES}


def test_a_missing_sync_state_row_refuses_to_serve(conn):
    today = date(2026, 9, 15)
    _seed_summaries(conn, newest=today)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM reference_sync_state WHERE table_name = %s", (ELO_SUMMARY.name,))
    with pytest.raises(StaleReferenceData, match="no reference_sync_state row"):
        assert_reference_fresh(conn, today=today)


def test_an_edited_summary_row_refuses_to_serve(conn):
    """The content-hash check, which is the one with no caveat: a single
    changed value anywhere in either table must stop the worker."""
    today = date(2026, 9, 15)
    _seed_summaries(conn, newest=today)
    with conn.cursor() as cur:
        cur.execute("UPDATE venue_asof_summary SET chase_wins = chase_wins + 1")
    with pytest.raises(StaleReferenceData, match="content hash mismatch"):
        assert_reference_fresh(conn, today=today)


def test_a_dropped_summary_row_refuses_to_serve(conn):
    """A partial sync, simulated. Row count and hash both move."""
    today = date(2026, 9, 15)
    _seed_summaries(conn, newest=today)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM elo_asof_summary")
    with pytest.raises(StaleReferenceData, match="content hash mismatch"):
        assert_reference_fresh(conn, today=today)


def test_summaries_older_than_the_age_budget_refuse_to_serve(conn):
    """The "sync never ran" proxy. Matches are played almost daily, so a
    newest breakpoint two weeks back means the ritual was skipped."""
    today = date(2026, 9, 15)
    _seed_summaries(conn, newest=today - timedelta(days=30))
    with pytest.raises(StaleReferenceData, match="newest as-of breakpoint"):
        assert_reference_fresh(conn, today=today)


def test_one_summary_falling_behind_is_not_masked_by_the_other(conn):
    """The freshness bound takes the OLDER of the two tables' newest dates.
    A stale Elo rebuild alongside a current venue rebuild must still fail."""
    today = date(2026, 9, 15)
    _seed_summaries(conn, newest=today)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE elo_asof_summary SET effective_date = %s", (today - timedelta(days=60),)
        )
    for table in DERIVED_TABLES:
        record_sync_state(conn, table, synced=True)  # re-hash, so only age is wrong
    with pytest.raises(StaleReferenceData, match="newest as-of breakpoint"):
        assert_reference_fresh(conn, today=today)


def test_empty_summaries_refuse_to_serve(conn):
    for table in DERIVED_TABLES:
        record_sync_state(conn, table, synced=True)
    with pytest.raises(StaleReferenceData, match="empty"):
        assert_reference_fresh(conn, today=date(2026, 9, 15))


def test_the_hash_is_stable_across_row_insertion_order(conn):
    """It is ordered by the natural key, not by physical order, so a sync
    that inserts in a different sequence still verifies."""
    today = date(2026, 9, 15)
    _seed_summaries(conn, newest=today)
    before = content_hash(conn, VENUE_SUMMARY)
    with conn.cursor() as cur:
        cur.execute("SELECT venue_id FROM venue_asof_summary LIMIT 1")
        venue_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO venue_asof_summary VALUES (%s, %s, 2.0, 4, 600, 4)",
            (venue_id, today - timedelta(days=10)),
        )
        cur.execute(
            "DELETE FROM venue_asof_summary WHERE effective_date = %s",
            (today - timedelta(days=10),),
        )
    assert content_hash(conn, VENUE_SUMMARY) == before
