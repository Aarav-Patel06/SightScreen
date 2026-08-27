"""match_states builder tests (SPEC.md section 5.3, Phase 0 session 6).

Local-only, like the schema-parity and exact-match tests - needs the real
bulk-loaded corpus in LOCAL_DATABASE_URL, which CI doesn't have.

The off-by-one test is the load-bearing one: every value here was
hand-verified against tests/fixtures/cricsheet/1410501.json (Sussex v
Gloucestershire, 2024-09-14, a normal all-out-for-106 T20 innings - no
DLS, no reduction, no super over) by literally counting deliveries in the
source JSON up to (not including) each checkpoint ball. If the builder's
window frame were `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`
instead of `1 PRECEDING` - the classic off-by-one - every assertion here
fails immediately (confirmed by hand once, not kept as permanent code).
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

FIXTURE_NORMAL = "1410501"  # Sussex v Gloucestershire, all out 106, target 107
FIXTURE_REDUCED = "1494102"  # Hong Kong v Malaysia, reduced to 5 overs/side


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        pytest.skip(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


@pytest.fixture(scope="module")
def conn():
    # autocommit=True: every test here is read-only against the same shared
    # connection - without it, each SELECT leaves a transaction open
    # (never committed), and its lock on match_states then blocks
    # test_rebuild_is_idempotent's TRUNCATE (on a separate connection)
    # indefinitely. Confirmed as a real hang, not a hypothetical.
    connection = psycopg.connect(_env()["LOCAL_DATABASE_URL"], autocommit=True)
    yield connection
    connection.close()


def _state_at(conn, cricsheet_id: str, innings: int, over_num: int, ball_in_over: int) -> tuple:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ms.score, ms.wickets, ms.balls_bowled, ms.balls_remaining, ms.target,
                   ms.runs_required, ms.partnership_runs, ms.partnership_balls,
                   ms.balls_since_wicket, ms.phase
            FROM match_states ms
            JOIN matches m ON m.match_id = ms.match_id
            JOIN deliveries d ON d.delivery_id = ms.delivery_id
            WHERE m.external_ids->>'cricsheet' = %s AND d.innings = %s
              AND d.over_num = %s AND d.ball_in_over = %s
            """,
            (cricsheet_id, innings, over_num, ball_in_over),
        )
        row = cur.fetchone()
    if row is None:
        pytest.skip(f"match {cricsheet_id} not loaded - run the full corpus load first")
    return row


# --- Decision 1: the off-by-one, hand-verified against source -------------

def test_first_ball_of_innings_is_zero_state(conn):
    score, wickets, balls_bowled, balls_remaining, target, *_rest, phase = _state_at(
        conn, FIXTURE_NORMAL, innings=1, over_num=0, ball_in_over=1
    )
    assert (score, wickets, balls_bowled, balls_remaining, target) == (0, 0, 0, 120, None)
    assert phase == "powerplay"


def test_state_right_after_first_wicket(conn):
    # Source: over=2 ball_in_over=4 dismisses DP Hughes (bowled). The very
    # next delivery (over=2 ball_in_over=5) must show wickets=1, and the
    # partnership/balls-since-wicket must have just reset to zero - not the
    # pre-wicket partnership carried forward, and not incremented past zero
    # by the dismissal ball itself.
    score, wickets, balls_bowled, _br, _t, _rr, partnership_runs, partnership_balls, balls_since_wicket, _phase = (
        _state_at(conn, FIXTURE_NORMAL, innings=1, over_num=2, ball_in_over=5)
    )
    assert (score, wickets, balls_bowled) == (13, 1, 15)
    assert (partnership_runs, partnership_balls, balls_since_wicket) == (0, 0, 0)


def test_state_at_last_ball_of_innings(conn):
    # Source: over=18 ball_in_over=1 is the 10th-wicket ball (all out 106).
    # State BEFORE it must reflect 9 wickets down, not 10 - the defining
    # off-by-one check: shift this by one ball forward and it would
    # (wrongly) show 10 wickets already fallen before the ball that causes it.
    score, wickets, balls_bowled, *_rest = _state_at(
        conn, FIXTURE_NORMAL, innings=1, over_num=18, ball_in_over=1
    )
    assert (score, wickets, balls_bowled) == (106, 9, 108)


def test_target_matches_recorded_source_value(conn):
    # Source innings 1 total = 106, target.runs = 107 (106 + 1, non-DLS,
    # non-reduced - target.overs = 20 confirms it). Checked on an innings-2
    # ball to confirm the persisted matches.target_runs (session 6
    # migration + backfill), not a derived-in-SQL value, is what flows
    # through - target must be NULL on every innings-1 row regardless.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ms.target FROM match_states ms
            JOIN matches m ON m.match_id = ms.match_id
            JOIN deliveries d ON d.delivery_id = ms.delivery_id
            WHERE m.external_ids->>'cricsheet' = %s AND d.innings = 2
            ORDER BY d.over_num, d.ball_in_over LIMIT 1
            """,
            (FIXTURE_NORMAL,),
        )
        row = cur.fetchone()
    if row is None:
        pytest.skip("fixture not loaded")
    assert row[0] == 107


# --- Decision 5: phase boundaries, nominal and reduced --------------------

def test_phase_boundaries_nominal_t20(conn):
    # Overs 0-5 powerplay, 6-14 middle, 15-19 death for an unreduced 20-over
    # innings - the exact boundaries named in the session 6 request.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.over_num, ms.phase FROM match_states ms
            JOIN matches m ON m.match_id = ms.match_id
            JOIN deliveries d ON d.delivery_id = ms.delivery_id
            WHERE m.external_ids->>'cricsheet' = %s AND d.innings = 1 AND d.ball_in_over = 1
            ORDER BY d.over_num
            """,
            (FIXTURE_NORMAL,),
        )
        rows = dict(cur.fetchall())
    if not rows:
        pytest.skip("fixture not loaded")
    assert rows[0] == "powerplay"
    assert rows[5] == "powerplay"
    assert rows[6] == "middle"
    assert rows[14] == "middle"
    assert rows[15] == "death"


def test_phase_boundaries_scale_for_reduced_overs(conn):
    # FIXTURE_REDUCED's chase is reduced to 5 overs (target.overs=5, 30
    # balls) - powerplay should end at round(30*0.30/6)=1.5->2 overs, not
    # the nominal-20-over boundary of 6. Confirms phase is computed against
    # the innings' own scheduled length, not a hardcoded per-format over
    # number (session 6 Decision 5).
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.over_num, ms.phase FROM match_states ms
            JOIN matches m ON m.match_id = ms.match_id
            JOIN deliveries d ON d.delivery_id = ms.delivery_id
            WHERE m.external_ids->>'cricsheet' = %s AND d.innings = 2 AND d.ball_in_over = 1
            ORDER BY d.over_num
            """,
            (FIXTURE_REDUCED,),
        )
        rows = dict(cur.fetchall())
    if not rows:
        pytest.skip("fixture not loaded")
    # 30 scheduled balls: powerplay ends at round(30*0.3)=9 balls (over 1,
    # since over 0 = balls 0-5, over 1 = balls 6-11 - ball 9 falls in over 1)
    assert rows[0] == "powerplay"
    assert rows.get(1) in ("powerplay", "middle")  # boundary ball, both are defensible; must not be "death"
    assert rows[max(rows)] != "powerplay"  # the last over of a 5-over chase must not still read powerplay


# --- Decision 4: batting_team_won and its exclusions -----------------------

def test_batting_team_won_null_for_ties_and_no_results(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM match_states ms
            JOIN matches m ON m.match_id = ms.match_id
            WHERE m.result_method IN ('tie', 'no_result') AND ms.batting_team_won IS NOT NULL
            """
        )
        bad = cur.fetchone()[0]
    assert bad == 0


def test_has_reconciliation_anomaly_and_is_dls_decided_denormalized_correctly(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM match_states ms
            JOIN matches m ON m.match_id = ms.match_id
            WHERE ms.has_reconciliation_anomaly != m.has_reconciliation_anomaly
               OR ms.is_dls_decided != (m.result_method = 'dls')
            """
        )
        mismatched = cur.fetchone()[0]
    assert mismatched == 0


def test_super_over_deliveries_produce_no_match_states_rows(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM match_states ms
            JOIN deliveries d ON d.delivery_id = ms.delivery_id
            WHERE d.is_super_over
            """
        )
        count = cur.fetchone()[0]
    assert count == 0


# --- Row-count parity ------------------------------------------------------

def test_row_count_parity_with_deliveries_minus_super_overs(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM match_states")
        match_states_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM deliveries WHERE NOT is_super_over")
        expected = cur.fetchone()[0]
    assert match_states_count == expected, (
        f"match_states has {match_states_count} rows, expected {expected} "
        "(deliveries WHERE NOT is_super_over) - the only deliberate exclusion"
    )


# --- Idempotency: rebuild is a pure function of deliveries + matches ------

def test_rebuild_is_idempotent(conn):
    """Runs the real rebuild twice against the real corpus and diffs a
    checksum of every column - byte-identical output required (section 2:
    never hand-edited, never incrementally patched). Slow (two full
    rebuilds); this is the one test in this file that mutates match_states,
    left in its rebuilt-once state afterward (matching normal usage).
    """
    from features.match_state import rebuild

    def _fingerprint() -> str:
        with conn.cursor() as cur:
            cur.execute("SELECT md5(string_agg(md5(match_states.*::text), '' ORDER BY delivery_id)) FROM match_states")
            return cur.fetchone()[0]

    rebuild()
    first = _fingerprint()
    rebuild()
    second = _fingerprint()
    assert first == second
