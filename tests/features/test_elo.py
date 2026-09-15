"""Elo ratings tests (SPEC.md section 6.1, Phase 0 session 6).

Local-only, like test_match_state.py - needs the real bulk-loaded corpus in
LOCAL_DATABASE_URL, which CI doesn't have. The one exception is the
poison-pill test (Phase 1 session 2), which uses the writable
cricket_training_test database (tests/conftest.py's `test_db_url` fixture)
instead, since it inserts synthetic matches.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

MIN_MATCHES_FOR_RIVALRY_CHECK = 20


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        pytest.skip(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


@pytest.fixture(scope="module")
def conn():
    # autocommit=True - see test_match_state.py's fixture for why a shared
    # read/write connection across many tests must not leave transactions
    # open (a real hang, found and fixed this session).
    connection = psycopg.connect(_env()["LOCAL_DATABASE_URL"], autocommit=True)
    yield connection
    connection.close()


def test_rebuild_is_idempotent(conn):
    from features.elo import rebuild

    def _fingerprint() -> str:
        # elo_id (BIGSERIAL) is excluded deliberately - DELETE doesn't reset
        # the sequence, so it gets fresh values every rebuild even when
        # every meaningful column (team_id, format, match_id, as_of,
        # rating) is byte-identical. Confirmed as a real false-positive
        # this test caught before this fix.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT md5(string_agg(md5((team_id, format, match_id, as_of, rating)::text), '' "
                "ORDER BY team_id, format, match_id)) FROM elo_ratings"
            )
            return cur.fetchone()[0]

    rebuild()
    first = _fingerprint()
    rebuild()
    second = _fingerprint()
    assert first == second


def test_elo_as_of_returns_default_before_first_match(conn):
    from features.elo import STARTING_RATING, elo_as_of

    with conn.cursor() as cur:
        cur.execute("SELECT team_id, format, min(as_of) FROM elo_ratings GROUP BY team_id, format LIMIT 1")
        row = cur.fetchone()
    if row is None:
        pytest.skip("elo_ratings empty - run features.elo rebuild first")
    team_id, format_, first_as_of = row

    # Strictly before the first-ever rating for this team/format - no row
    # can exist yet, so the default must come back, not NULL or an error.
    rating = elo_as_of(conn, team_id, format_, first_as_of)
    assert rating == STARTING_RATING


def test_elo_as_of_never_sees_a_future_match(conn):
    from features.elo import elo_as_of

    with conn.cursor() as cur:
        cur.execute(
            "SELECT team_id, format, as_of FROM elo_ratings ORDER BY as_of DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        pytest.skip("elo_ratings empty")
    team_id, format_, latest_as_of = row

    # Querying as-of exactly the latest match's own timestamp must not see
    # that match's own rating (it's the state produced *by* that match, not
    # available before it) - confirms the strict "<" in elo_as_of, not "<=".
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM elo_ratings WHERE team_id=%s AND format=%s AND as_of < %s",
            (team_id, format_, latest_as_of),
        )
        rows_strictly_before = cur.fetchone()[0]
        cur.execute(
            "SELECT rating FROM elo_ratings WHERE team_id=%s AND format=%s AND as_of=%s",
            (team_id, format_, latest_as_of),
        )
        rating_at_latest = cur.fetchone()[0]

    result = elo_as_of(conn, team_id, format_, latest_as_of)
    if rows_strictly_before == 0:
        # nothing strictly before - must fall back to the 1500 default, not
        # leak the rating this very match produced.
        assert result == 1500.0
        assert result != rating_at_latest
    else:
        assert result != rating_at_latest


def test_lopsided_rivalry_direction(conn):
    # Sanity check, not an exact-value check (session 0's own framing): a
    # team with a clearly dominant real-world record should end with a
    # rating clearly above 1500, and a team with a clearly poor record
    # clearly below - derived from the real corpus, not a hardcoded pair.
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH team_matches AS (
                SELECT team_a AS team_id, winner, format FROM matches WHERE format='T20' AND status='complete'
                UNION ALL
                SELECT team_b AS team_id, winner, format FROM matches WHERE format='T20' AND status='complete'
            ),
            win_rates AS (
                SELECT team_id, count(*) AS n,
                       avg(CASE WHEN winner = team_id THEN 1.0 ELSE 0.0 END) AS win_rate
                FROM team_matches
                GROUP BY team_id
                HAVING count(*) >= %s
            )
            SELECT team_id, win_rate FROM win_rates ORDER BY win_rate DESC LIMIT 1
            """,
            (MIN_MATCHES_FOR_RIVALRY_CHECK,),
        )
        best = cur.fetchone()
        cur.execute(
            """
            WITH team_matches AS (
                SELECT team_a AS team_id, winner, format FROM matches WHERE format='T20' AND status='complete'
                UNION ALL
                SELECT team_b AS team_id, winner, format FROM matches WHERE format='T20' AND status='complete'
            ),
            win_rates AS (
                SELECT team_id, count(*) AS n,
                       avg(CASE WHEN winner = team_id THEN 1.0 ELSE 0.0 END) AS win_rate
                FROM team_matches
                GROUP BY team_id
                HAVING count(*) >= %s
            )
            SELECT team_id, win_rate FROM win_rates ORDER BY win_rate ASC LIMIT 1
            """,
            (MIN_MATCHES_FOR_RIVALRY_CHECK,),
        )
        worst = cur.fetchone()
    if best is None or worst is None:
        pytest.skip("not enough T20 match history for a rivalry sanity check")

    best_team_id, best_win_rate = best
    worst_team_id, worst_win_rate = worst

    with conn.cursor() as cur:
        cur.execute(
            "SELECT rating FROM elo_ratings WHERE team_id=%s AND format='T20' ORDER BY as_of DESC LIMIT 1",
            (best_team_id,),
        )
        best_final_rating = cur.fetchone()[0]
        cur.execute(
            "SELECT rating FROM elo_ratings WHERE team_id=%s AND format='T20' ORDER BY as_of DESC LIMIT 1",
            (worst_team_id,),
        )
        worst_final_rating = cur.fetchone()[0]

    assert best_win_rate > worst_win_rate  # sanity on the query itself
    assert best_final_rating > 1500.0
    assert worst_final_rating < 1500.0
    assert best_final_rating > worst_final_rating


def test_elo_as_of_poison_pill_future_match_never_seen(test_db_url):
    """Phase 1 session 2's poison-pill pattern, applied to elo_as_of: session
    6 argued this by construction (append-only rows, processed in strict
    chronological order); this makes it an assertion instead of a
    code-review promise. Needs its own writable connection to
    cricket_training_test (tests/conftest.py's `test_db_url`, NOT its `conn`
    fixture - `conn` would resolve to THIS FILE's own module-scoped fixture
    above, which points at the real corpus) - this test inserts synthetic
    matches and calls features.elo.recompute_format(conn, ...) directly
    (NOT the rebuild() CLI wrapper, which ignores any conn and always
    connects to LOCAL_DATABASE_URL itself - calling that here would rebuild
    Elo against the real corpus, exactly what this test must not do).

    Truncates every table it touches itself, including `teams` - a table a
    plain TRUNCATE matches/deliveries/elo_ratings would leave dirty across
    runs, and did (a real UNIQUE-violation failure on a second run, found
    and fixed this session, not hypothetical).

    Phase 2 session 3: elo_as_of reads elo_asof_summary, so the summary is
    rebuilt after each recompute_format - INCLUDING after the pill is
    planted. Skipping that second rebuild would make this test pass for the
    wrong reason: the pill would be invisible because it was never
    aggregated, rather than because the as-of rule excluded it.
    """
    import psycopg

    from features.asof_summary import rebuild_elo_summary
    from features.elo import elo_as_of, recompute_format

    write_conn = psycopg.connect(test_db_url, autocommit=True)
    with write_conn.cursor() as cur:
        cur.execute(
            "TRUNCATE matches, deliveries, elo_ratings, elo_asof_summary, "
            "venue_asof_summary, teams RESTART IDENTITY CASCADE"
        )
        cur.execute("INSERT INTO teams (name) VALUES ('Poison Pill A'), ('Poison Pill B') "
                    "RETURNING team_id")
        team_a, team_b = [row[0] for row in cur.fetchall()]
        for i in range(5):
            cur.execute(
                """
                INSERT INTO matches (competition, format, start_time, team_a, team_b, winner,
                                      result_method, status)
                VALUES ('test', 'T20', %s, %s, %s, %s, 'normal', 'complete')
                """,
                (f"2020-01-{i + 1:02d}", team_a, team_b, team_a),
            )

    recompute_format(write_conn, "T20")
    rebuild_elo_summary(write_conn)
    as_of_date = date(2021, 1, 1)
    before = elo_as_of(write_conn, team_a, "T20", as_of_date)

    # Poison pill: a lopsided future run of results in the OPPOSITE
    # direction - if this leaked, team_a's rating would move sharply.
    with write_conn.cursor() as cur:
        for i in range(20):
            cur.execute(
                """
                INSERT INTO matches (competition, format, start_time, team_a, team_b, winner,
                                      result_method, status)
                VALUES ('test', 'T20', %s, %s, %s, %s, 'normal', 'complete')
                """,
                (f"2021-06-{i + 1:02d}", team_a, team_b, team_b),
            )
    recompute_format(write_conn, "T20")
    rebuild_elo_summary(write_conn)  # the pill is now IN the summary
    after = elo_as_of(write_conn, team_a, "T20", as_of_date)

    assert after == before
    write_conn.close()


def test_renamed_franchise_has_unbroken_elo_history(conn):
    # Delhi Capitals' team_id carries the Delhi Daredevils era too (session
    # 4/5 alias seeding + session 6 backfill merge) - Elo must see one
    # continuous history under that single team_id, not a reset at the
    # rename boundary. Confirms Decision 7's "no Elo-specific code needed"
    # claim empirically, not just by construction.
    with conn.cursor() as cur:
        cur.execute("SELECT team_id FROM teams WHERE name = 'Delhi Capitals'")
        row = cur.fetchone()
    if row is None:
        pytest.skip("Delhi Capitals not present in this corpus")
    team_id = row[0]

    with conn.cursor() as cur:
        cur.execute(
            "SELECT min(as_of), max(as_of), count(*) FROM elo_ratings WHERE team_id=%s AND format='T20'",
            (team_id,),
        )
        earliest, latest, n = cur.fetchone()
    if n == 0:
        pytest.skip("no T20 elo_ratings rows for Delhi Capitals")

    # The Daredevils era predates the 2018 rename - an unbroken history
    # under this team_id must reach back before then, not just start there.
    assert earliest.year < 2018, (
        f"Delhi Capitals' earliest T20 Elo row is {earliest} - expected it to reach back into the "
        "Delhi Daredevils era (pre-2018), confirming the rename didn't reset history"
    )
    assert latest.year >= 2018
