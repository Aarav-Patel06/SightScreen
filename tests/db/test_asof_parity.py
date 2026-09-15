"""THE GATE (SPEC.md section 15's 2026-09-10 decision, Phase 2 session 3).

The summary-backed as-of helpers must return byte-identical values to the
direct-query helpers they replace, for every (entity, date) pair across the
full corpus. Not a sample. This is the same class of bug as the ::numeric
rounding mismatch in features/match_state.py: a silent divergence between
training and serving that no metric would reveal, because both sides would
keep producing plausible numbers.

Three layers, each closing a different hole:

  LAYER 1  Exhaustive, set-based, local. Compares the summary against the
           direct aggregate over the FULL CROSS PRODUCT - every venue x
           every corpus date, every (team, format) x every corpus date -
           not merely the pairs that happen to occur. ~3.8M comparisons.

  LAYER 2  The Python helpers, over every lookup training actually performs
           (12,088 venue, 25,899 elo). This is where min_matches, the
           float() conversion and the None path get exercised - the exact
           places the ::numeric bug class lives.

           Stated plainly: layer 2's oracle is the set-based SQL form of the
           direct helper, materialised once, not 12,088 separate Python
           calls. Those are full corpus scans and would run for hours, and a
           gate that takes hours gets deselected, which is how gates rot.
           The equivalence is closed two ways - the oracle is built from the
           same module-level SQL fragments venue_stats.py's *_direct
           functions execute, and a deterministic 200-pair sample runs the
           actual Python *_direct functions against it.

  LAYER 3  Both databases. Full row-by-row diff of both summary tables,
           local vs Supabase. Given identical table contents and identical
           SQL - the helpers are the same function object in both cases,
           handed a different connection - the returned values are identical
           by construction. That reduction is the argument; it is written
           here rather than left implicit. A small sample additionally runs
           the helper against Supabase over the pooler to prove the read
           path itself works.

Local-only, like tests/db/test_schema_parity.py: it needs the full corpus
(which CI's empty Postgres does not have) and live credentials to both
databases (which CI does not have). Run manually after every
`python -m features.asof_summary rebuild` + `python -m
ingest.sync_reference_tables`:

    pytest tests/db/test_asof_parity.py -v
"""

from __future__ import annotations

import random
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

from features.asof_summary import DERIVED_TABLES
from features.elo import STARTING_RATING, elo_as_of, elo_as_of_direct
from features.venue_stats import (
    CHASE_PER_MATCH_SELECT,
    CHASE_WIN_EXPR,
    FIRST_INNINGS_PER_MATCH_SELECT,
    MIN_VENUE_MATCHES,
    venue_avg_first_innings_as_of,
    venue_avg_first_innings_as_of_direct,
    venue_chase_win_rate_as_of,
    venue_chase_win_rate_as_of_direct,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

SAMPLE_SIZE = 200
SAMPLE_SEED = 20260915  # fixed, so the same pairs are checked every run


def _env() -> dict[str, str]:
    if not ENV_PATH.exists():
        pytest.skip(f"{ENV_PATH} not found - this gate needs live credentials")
    return dotenv_values(ENV_PATH)


@pytest.fixture(scope="module")
def conn():
    env = _env()
    if not env.get("LOCAL_DATABASE_URL"):
        pytest.skip(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    # autocommit: a shared read-only connection across every test in this
    # module, per docs/phase0-closeout.md's autocommit lesson.
    connection = psycopg.connect(env["LOCAL_DATABASE_URL"], autocommit=True)
    _pin_float_output(connection)
    _require_summaries(connection)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def supabase_conn():
    env = _env()
    if not env.get("SUPABASE_SESSION_POOLER_URL"):
        pytest.skip(f"SUPABASE_SESSION_POOLER_URL must be set in {ENV_PATH}")
    connection = psycopg.connect(env["SUPABASE_SESSION_POOLER_URL"], autocommit=True)
    yield connection
    connection.close()


def _pin_float_output(connection) -> None:
    """Both oracles read elo_ratings.rating, a float4, whose TEXT rendering -
    and therefore the Python float psycopg decodes - depends on this setting.
    The rebuild pins it for the same reason; the gate has to agree with the
    rebuild or it is measuring the session, not the data."""
    with connection.cursor() as cur:
        cur.execute("SET extra_float_digits = 1")


def _require_summaries(connection) -> None:
    for table in DERIVED_TABLES:
        with connection.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table.name}")
            if cur.fetchone()[0] == 0:
                pytest.skip(
                    f"{table.name} is empty - run `python -m features.asof_summary rebuild`"
                )


# --- Layer 1: exhaustive, set-based ---------------------------------------
# The grid is the full cross product, deliberately. Restricting it to pairs
# that occur would never test a lookup that lands BETWEEN two breakpoints,
# which is the one thing the summary's "latest row strictly before" rule has
# to get right.

_VENUE_GRID_SQL = f"""
WITH chase_per_match AS (
    {CHASE_PER_MATCH_SELECT}
), chase_dated AS (
    SELECT m.venue_id, (m.start_time AT TIME ZONE 'UTC')::date AS d,
           chase_per_match.batting_team_won
    FROM chase_per_match JOIN matches m ON m.match_id = chase_per_match.match_id
    WHERE m.venue_id IS NOT NULL
), first_inns_per_match AS (
    {FIRST_INNINGS_PER_MATCH_SELECT}
    GROUP BY d.match_id
), first_inns_dated AS (
    SELECT m.venue_id, (m.start_time AT TIME ZONE 'UTC')::date AS d,
           first_inns_per_match.innings_total
    FROM first_inns_per_match JOIN matches m ON m.match_id = first_inns_per_match.match_id
    WHERE m.venue_id IS NOT NULL
), grid AS (
    SELECT v.venue_id, dates.d
    FROM (SELECT DISTINCT venue_id FROM matches WHERE venue_id IS NOT NULL) v
    CROSS JOIN (SELECT DISTINCT (start_time AT TIME ZONE 'UTC')::date AS d FROM matches) dates
), oracle AS (
    SELECT g.venue_id, g.d,
           avg({CHASE_WIN_EXPR}) FILTER (WHERE c.venue_id IS NOT NULL) AS chase_rate,
           count(c.venue_id) AS chase_n
    FROM grid g
    LEFT JOIN chase_dated c ON c.venue_id = g.venue_id AND c.d < g.d
    GROUP BY 1, 2
), oracle_fi AS (
    SELECT g.venue_id, g.d,
           avg(f.innings_total) FILTER (WHERE f.venue_id IS NOT NULL) AS fi_avg,
           count(f.venue_id) AS fi_n
    FROM grid g
    LEFT JOIN first_inns_dated f ON f.venue_id = g.venue_id AND f.d < g.d
    GROUP BY 1, 2
), actual AS (
    SELECT o.venue_id, o.d, o.chase_rate, o.chase_n, ofi.fi_avg, ofi.fi_n,
           CASE WHEN s.chase_n > 0 THEN s.chase_wins / s.chase_n END AS s_chase_rate,
           coalesce(s.chase_n, 0) AS s_chase_n,
           CASE WHEN s.first_inns_n > 0
                THEN s.first_inns_runs::numeric / s.first_inns_n END AS s_fi_avg,
           coalesce(s.first_inns_n, 0) AS s_fi_n
    FROM oracle o
    JOIN oracle_fi ofi USING (venue_id, d)
    LEFT JOIN LATERAL (
        SELECT chase_wins, chase_n, first_inns_runs, first_inns_n
        FROM venue_asof_summary vs
        WHERE vs.venue_id = o.venue_id AND vs.effective_date < o.d
        ORDER BY vs.effective_date DESC LIMIT 1
    ) s ON TRUE
)
SELECT * FROM actual
WHERE chase_rate IS DISTINCT FROM s_chase_rate
   OR chase_n   IS DISTINCT FROM s_chase_n
   OR fi_avg    IS DISTINCT FROM s_fi_avg
   OR fi_n      IS DISTINCT FROM s_fi_n
"""

_VENUE_GRID_SIZE_SQL = """
SELECT (SELECT count(DISTINCT venue_id) FROM matches WHERE venue_id IS NOT NULL)
     * (SELECT count(DISTINCT (start_time AT TIME ZONE 'UTC')::date) FROM matches)
"""

_ELO_GRID_SQL = """
WITH grid AS (
    SELECT tf.team_id, tf.format, dates.d
    FROM (SELECT DISTINCT team_id, format FROM elo_ratings) tf
    CROSS JOIN (SELECT DISTINCT (start_time AT TIME ZONE 'UTC')::date AS d FROM matches) dates
)
SELECT g.team_id, g.format, g.d, o.rating AS oracle_rating, s.rating AS summary_rating
FROM grid g
LEFT JOIN LATERAL (
    SELECT rating FROM elo_ratings e
    WHERE e.team_id = g.team_id AND e.format = g.format AND e.as_of < g.d
    ORDER BY e.as_of DESC, e.match_id DESC LIMIT 1
) o ON TRUE
LEFT JOIN LATERAL (
    SELECT rating FROM elo_asof_summary s2
    WHERE s2.team_id = g.team_id AND s2.format = g.format AND s2.effective_date < g.d
    ORDER BY s2.effective_date DESC LIMIT 1
) s ON TRUE
-- ::text::numeric, matching asof_summary.ELO_SUMMARY_REBUILD_SQL exactly.
-- A bare `real IS DISTINCT FROM numeric` would compare through float8 and
-- report all 1.9M rows as differing over identical values, because float4
-- 1471.7908 widens to 1471.79077148... while the numeric is exactly
-- 1471.7908. What this layer tests is the lookup semantics - the tie-break,
-- the date collapse, the strictly-before rule - not float rendering, which
-- layer 2 covers by comparing the Python values the helpers return.
WHERE o.rating::text::numeric IS DISTINCT FROM s.rating
"""

_ELO_GRID_SIZE_SQL = """
SELECT (SELECT count(*) FROM (SELECT DISTINCT team_id, format FROM elo_ratings) x)
     * (SELECT count(DISTINCT (start_time AT TIME ZONE 'UTC')::date) FROM matches)
"""


def test_layer1_venue_summary_matches_direct_for_every_venue_date_pair(conn):
    with conn.cursor() as cur:
        cur.execute(_VENUE_GRID_SIZE_SQL)
        pairs = cur.fetchone()[0]
        cur.execute(_VENUE_GRID_SQL)
        mismatches = cur.fetchall()
    assert not mismatches, (
        f"{len(mismatches)} of {pairs} (venue, date) pairs disagree between "
        f"venue_asof_summary and the direct aggregate. First 20:\n"
        + "\n".join(str(r) for r in mismatches[:20])
    )
    print(f"\nlayer 1 venue: {pairs} pairs, 0 mismatches")


def test_layer1_elo_summary_matches_elo_ratings_for_every_team_format_date(conn):
    with conn.cursor() as cur:
        cur.execute(_ELO_GRID_SIZE_SQL)
        pairs = cur.fetchone()[0]
        cur.execute(_ELO_GRID_SQL)
        mismatches = cur.fetchall()
    assert not mismatches, (
        f"{len(mismatches)} of {pairs} (team, format, date) triples disagree between "
        f"elo_asof_summary and elo_ratings. First 20:\n"
        + "\n".join(str(r) for r in mismatches[:20])
    )
    print(f"\nlayer 1 elo: {pairs} triples, 0 mismatches")


# --- Layer 2: the Python helpers over every real lookup -------------------

_VENUE_LOOKUPS_SQL = f"""
WITH chase_per_match AS (
    {CHASE_PER_MATCH_SELECT}
), chase_dated AS (
    SELECT m.venue_id, (m.start_time AT TIME ZONE 'UTC')::date AS d,
           chase_per_match.batting_team_won
    FROM chase_per_match JOIN matches m ON m.match_id = chase_per_match.match_id
    WHERE m.venue_id IS NOT NULL
), first_inns_per_match AS (
    {FIRST_INNINGS_PER_MATCH_SELECT}
    GROUP BY d.match_id
), first_inns_dated AS (
    SELECT m.venue_id, (m.start_time AT TIME ZONE 'UTC')::date AS d,
           first_inns_per_match.innings_total
    FROM first_inns_per_match JOIN matches m ON m.match_id = first_inns_per_match.match_id
    WHERE m.venue_id IS NOT NULL
), lookups AS (
    SELECT DISTINCT venue_id, (start_time AT TIME ZONE 'UTC')::date AS d
    FROM matches WHERE venue_id IS NOT NULL
)
SELECT l.venue_id, l.d,
       avg({CHASE_WIN_EXPR}) FILTER (WHERE c.venue_id IS NOT NULL),
       count(c.venue_id),
       (SELECT avg(f.innings_total) FROM first_inns_dated f
        WHERE f.venue_id = l.venue_id AND f.d < l.d),
       (SELECT count(*) FROM first_inns_dated f
        WHERE f.venue_id = l.venue_id AND f.d < l.d)
FROM lookups l
LEFT JOIN chase_dated c ON c.venue_id = l.venue_id AND c.d < l.d
GROUP BY l.venue_id, l.d
"""

_ELO_LOOKUPS_SQL = """
WITH lookups AS (
    SELECT DISTINCT team_id, format, d FROM (
        SELECT team_a AS team_id, format, (start_time AT TIME ZONE 'UTC')::date AS d
        FROM matches WHERE team_a IS NOT NULL
        UNION ALL
        SELECT team_b, format, (start_time AT TIME ZONE 'UTC')::date
        FROM matches WHERE team_b IS NOT NULL
    ) u
)
SELECT l.team_id, l.format, l.d, o.rating
FROM lookups l
LEFT JOIN LATERAL (
    SELECT rating FROM elo_ratings e
    WHERE e.team_id = l.team_id AND e.format = l.format AND e.as_of < l.d
    ORDER BY e.as_of DESC, e.match_id DESC LIMIT 1
) o ON TRUE
"""


@pytest.fixture(scope="module")
def venue_oracle(conn) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(_VENUE_LOOKUPS_SQL)
        return cur.fetchall()


def test_layer2_venue_helpers_match_the_oracle_for_every_training_lookup(conn, venue_oracle):
    """`==`, not pytest.approx. "Close" is what this gate exists to reject."""
    bad = []
    for venue_id, d, chase_rate, chase_n, fi_avg, fi_n in venue_oracle:
        expected_rate = float(chase_rate) if chase_n >= MIN_VENUE_MATCHES else None
        expected_avg = float(fi_avg) if fi_n >= MIN_VENUE_MATCHES else None
        actual_rate = venue_chase_win_rate_as_of(conn, venue_id, d)
        actual_avg = venue_avg_first_innings_as_of(conn, venue_id, d)
        if actual_rate != expected_rate or actual_avg != expected_avg:
            bad.append((venue_id, d, expected_rate, actual_rate, expected_avg, actual_avg))
    assert not bad, (
        f"{len(bad)} of {len(venue_oracle)} venue lookups diverge. First 20:\n"
        + "\n".join(str(r) for r in bad[:20])
    )
    print(f"\nlayer 2 venue: {len(venue_oracle)} lookups, 0 divergences")


def test_layer2_elo_helper_matches_the_oracle_for_every_training_lookup(conn):
    with conn.cursor() as cur:
        cur.execute(_ELO_LOOKUPS_SQL)
        rows = cur.fetchall()
    bad = []
    for team_id, format_, d, rating in rows:
        expected = STARTING_RATING if rating is None else rating
        actual = elo_as_of(conn, team_id, format_, d)
        if actual != expected:
            bad.append((team_id, format_, d, expected, actual))
    assert not bad, (
        f"{len(bad)} of {len(rows)} elo lookups diverge. First 20:\n"
        + "\n".join(str(r) for r in bad[:20])
    )
    print(f"\nlayer 2 elo: {len(rows)} lookups, 0 divergences")


def test_layer2_the_python_direct_helpers_agree_with_the_sql_oracle(conn, venue_oracle):
    """Closes the loop layer 2's materialised oracle would otherwise leave
    open: that the batched SQL really is what the per-call Python functions
    compute. Deterministic sample, because these ARE the full corpus scans."""
    sample = random.Random(SAMPLE_SEED).sample(venue_oracle, SAMPLE_SIZE)
    for venue_id, d, chase_rate, chase_n, fi_avg, fi_n in sample:
        expected_rate = float(chase_rate) if chase_n >= MIN_VENUE_MATCHES else None
        expected_avg = float(fi_avg) if fi_n >= MIN_VENUE_MATCHES else None
        assert venue_chase_win_rate_as_of_direct(conn, venue_id, d) == expected_rate
        assert venue_avg_first_innings_as_of_direct(conn, venue_id, d) == expected_avg


def test_layer2_elo_direct_and_summary_agree_on_a_sample(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT team_id, format, (as_of AT TIME ZONE 'UTC')::date + 1 "
            "FROM elo_ratings"
        )
        rows = cur.fetchall()
    for team_id, format_, d in random.Random(SAMPLE_SEED).sample(rows, SAMPLE_SIZE):
        assert elo_as_of(conn, team_id, format_, d) == elo_as_of_direct(conn, team_id, format_, d)


# --- Layer 3: both databases ----------------------------------------------

def _rows(connection, table) -> list[tuple]:
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(table.columns)} FROM {table.name} ORDER BY {table.order_by}"
        )
        return cur.fetchall()


@pytest.mark.parametrize("table", DERIVED_TABLES, ids=lambda t: t.name)
def test_layer3_summary_tables_are_identical_in_both_databases(conn, supabase_conn, table):
    local, remote = _rows(conn, table), _rows(supabase_conn, table)
    if not remote:
        pytest.skip(
            f"{table.name} is empty on Supabase - run "
            f"`python -m ingest.sync_reference_tables`"
        )
    assert len(local) == len(remote), (
        f"{table.name}: {len(local)} rows locally, {len(remote)} on Supabase"
    )
    differing = [(a, b) for a, b in zip(local, remote) if a != b]
    assert not differing, (
        f"{table.name}: {len(differing)} of {len(local)} rows differ between the "
        f"databases. First 20:\n" + "\n".join(f"  local {a}\n  supa  {b}" for a, b in differing[:20])
    )


@pytest.mark.parametrize("table", DERIVED_TABLES, ids=lambda t: t.name)
def test_layer3_recorded_content_hash_matches_both_copies(conn, supabase_conn, table):
    from features.asof_summary import content_hash

    local_hash, local_rows = content_hash(conn, table)
    remote_hash, remote_rows = content_hash(supabase_conn, table)
    if remote_rows == 0:
        pytest.skip(f"{table.name} is empty on Supabase")
    assert local_hash == remote_hash, (
        f"{table.name}: local hash {local_hash} over {local_rows} rows, "
        f"Supabase {remote_hash} over {remote_rows}"
    )
    with supabase_conn.cursor() as cur:
        cur.execute(
            "SELECT content_hash FROM reference_sync_state WHERE table_name = %s", (table.name,)
        )
        recorded = cur.fetchone()
    assert recorded is not None, f"no reference_sync_state row for {table.name} on Supabase"
    assert recorded[0] == remote_hash


def test_layer3_the_helper_returns_the_same_values_over_the_pooler(conn, supabase_conn):
    """Proves the read path itself works against Supabase - the helper is the
    same function object, so the only thing under test here is that the
    connection, types and pooler round-trip don't change the answer."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM venue_asof_summary")
        if cur.fetchone()[0] == 0:
            pytest.skip("venue_asof_summary is empty")
        cur.execute(
            "SELECT DISTINCT venue_id, (start_time AT TIME ZONE 'UTC')::date + 1 FROM matches "
            "WHERE venue_id IS NOT NULL"
        )
        pairs = cur.fetchall()
    with supabase_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM venue_asof_summary")
        if cur.fetchone()[0] == 0:
            pytest.skip("venue_asof_summary is empty on Supabase - run the sync")

    for venue_id, d in random.Random(SAMPLE_SEED).sample(pairs, SAMPLE_SIZE):
        assert venue_chase_win_rate_as_of(supabase_conn, venue_id, d) == (
            venue_chase_win_rate_as_of(conn, venue_id, d)
        )
        assert venue_avg_first_innings_as_of(supabase_conn, venue_id, d) == (
            venue_avg_first_innings_as_of(conn, venue_id, d)
        )
