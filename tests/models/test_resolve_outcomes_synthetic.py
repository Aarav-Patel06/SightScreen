"""Exclusion logic on seeded rows, so it runs everywhere (Phase 3 session 1).

Companion to test_resolve_outcomes.py, which asserts the same rules against
real tied and no-result matches in the local corpus. That file is the reality
check - it proves the corpus actually contains those shapes - but it cannot
run in CI, where Postgres has the schema and no data, so on its own it would
be a check that never runs where it matters most.

These seed the shapes directly through the truncating `conn` fixture and
therefore run in CI, locally, and anywhere else. Between the two files the
rule is covered by something that always executes and by something that
confronts real data.
"""

from __future__ import annotations

import pytest

from eval.splits import second_innings_labels

TEAM_BATTING = 1
TEAM_BOWLING = 2


def _seed_reference(conn) -> tuple[int, int, int]:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO venues (name) VALUES ('Resolve Ground') RETURNING venue_id")
        venue_id = cur.fetchone()[0]
        cur.execute("INSERT INTO teams (name) VALUES ('Chasers') RETURNING team_id")
        batting = cur.fetchone()[0]
        cur.execute("INSERT INTO teams (name) VALUES ('Defenders') RETURNING team_id")
        bowling = cur.fetchone()[0]
    return venue_id, batting, bowling


def _seed_match(
    conn,
    venue_id: int,
    batting: int,
    bowling: int,
    *,
    winner: int | None,
    result_method: str,
    anomaly: bool = False,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO matches (external_ids, competition, format, venue_id, start_time,
                                 team_a, team_b, winner, result_method, status,
                                 has_reconciliation_anomaly)
            VALUES ('{}', 'Test Cup', 'T20', %s, '2026-03-01T00:00:00Z', %s, %s, %s, %s,
                    'complete', %s)
            RETURNING match_id
            """,
            (venue_id, batting, bowling, winner, result_method, anomaly),
        )
        return cur.fetchone()[0]


def _seed_ball(
    conn,
    match_id: int,
    batting: int,
    bowling: int,
    *,
    over_num: int,
    ball_in_over: int,
    batting_team_won: bool | None,
    anomaly: bool = False,
    required_run_rate: float | None = 8.0,
) -> None:
    """One innings-2 delivery and the match_states row before it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO deliveries (match_id, innings, over_num, ball_in_over, legal_ball_num,
                                    batting_team_id, bowling_team_id, match_date,
                                    runs_batter, runs_extras)
            VALUES (%s, 2, %s, %s, %s, %s, %s, '2026-03-01', 1, 0)
            RETURNING delivery_id
            """,
            (match_id, over_num, ball_in_over, over_num * 6 + ball_in_over, batting, bowling),
        )
        delivery_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO match_states (delivery_id, match_id, innings, score, wickets,
                                      balls_bowled, balls_remaining, target, runs_required,
                                      required_run_rate, phase, batting_team_won, match_date,
                                      has_reconciliation_anomaly, is_dls_decided)
            VALUES (%s, %s, 2, 10, 1, 6, 114, 160, 150, %s, 'powerplay', %s, '2026-03-01',
                    %s, false)
            """,
            (delivery_id, match_id, required_run_rate, batting_team_won, anomaly),
        )


def test_a_completed_chase_is_labelled(conn):
    venue_id, batting, bowling = _seed_reference(conn)
    match_id = _seed_match(conn, venue_id, batting, bowling, winner=batting, result_method="normal")
    _seed_ball(conn, match_id, batting, bowling, over_num=1, ball_in_over=1, batting_team_won=True)

    labels = second_innings_labels(conn, [match_id])
    assert labels == {(match_id, 2, 1, 1): 1}


def test_a_defended_total_is_labelled_zero(conn):
    venue_id, batting, bowling = _seed_reference(conn)
    match_id = _seed_match(conn, venue_id, batting, bowling, winner=bowling, result_method="normal")
    _seed_ball(conn, match_id, batting, bowling, over_num=1, ball_in_over=1, batting_team_won=False)

    assert second_innings_labels(conn, [match_id]) == {(match_id, 2, 1, 1): 0}


@pytest.mark.parametrize("result_method", ["tie", "no_result"])
def test_a_match_with_no_winner_yields_no_label(conn, result_method):
    """The quiet one. `matches.winner` is NULL for a tie AND a no-result, so
    anything deriving the label itself from `winner = batting_team` records
    both as a loss for the chasing side - a wrong label on a real match with
    nothing to flag it. match_states.batting_team_won is NULL here, and this
    asserts the NULL survives all the way to "no outcome row"."""
    venue_id, batting, bowling = _seed_reference(conn)
    match_id = _seed_match(conn, venue_id, batting, bowling, winner=None, result_method=result_method)
    _seed_ball(conn, match_id, batting, bowling, over_num=1, ball_in_over=1, batting_team_won=None)

    assert second_innings_labels(conn, [match_id]) == {}


def test_a_flagged_match_yields_no_label(conn):
    venue_id, batting, bowling = _seed_reference(conn)
    match_id = _seed_match(
        conn, venue_id, batting, bowling, winner=batting, result_method="normal", anomaly=True
    )
    _seed_ball(
        conn, match_id, batting, bowling, over_num=1, ball_in_over=1,
        batting_team_won=True, anomaly=True,
    )

    assert second_innings_labels(conn, [match_id]) == {}


def test_a_ball_without_a_required_run_rate_is_excluded(conn):
    """The fourth clause, found in Phase 1 by querying the corpus: 992 rows
    pass the documented three and still have a NULL required_run_rate."""
    venue_id, batting, bowling = _seed_reference(conn)
    match_id = _seed_match(conn, venue_id, batting, bowling, winner=batting, result_method="normal")
    _seed_ball(
        conn, match_id, batting, bowling, over_num=1, ball_in_over=1,
        batting_team_won=True, required_run_rate=None,
    )
    _seed_ball(conn, match_id, batting, bowling, over_num=1, ball_in_over=2, batting_team_won=True)

    # Only the ball with a usable feature is resolvable; its neighbour in the
    # same over, same match, same outcome is not.
    assert second_innings_labels(conn, [match_id]) == {(match_id, 2, 1, 2): 1}


def test_a_mixed_batch_resolves_only_the_resolvable(conn):
    venue_id, batting, bowling = _seed_reference(conn)
    good = _seed_match(conn, venue_id, batting, bowling, winner=batting, result_method="normal")
    tied = _seed_match(conn, venue_id, batting, bowling, winner=None, result_method="tie")
    _seed_ball(conn, good, batting, bowling, over_num=1, ball_in_over=1, batting_team_won=True)
    _seed_ball(conn, tied, batting, bowling, over_num=1, ball_in_over=1, batting_team_won=None)

    labels = second_innings_labels(conn, [good, tied])
    assert {match_id for match_id, _i, _o, _b in labels} == {good}


def test_an_extra_and_the_next_ball_are_separate_keys(conn):
    """Two deliveries in one over that differ only in ball_in_over must be
    two labels. This is the corpus side of the collision the live path had."""
    venue_id, batting, bowling = _seed_reference(conn)
    match_id = _seed_match(conn, venue_id, batting, bowling, winner=batting, result_method="normal")
    _seed_ball(conn, match_id, batting, bowling, over_num=3, ball_in_over=3, batting_team_won=True)
    _seed_ball(conn, match_id, batting, bowling, over_num=3, ball_in_over=4, batting_team_won=True)

    labels = second_innings_labels(conn, [match_id])
    assert set(labels) == {(match_id, 2, 3, 3), (match_id, 2, 3, 4)}


def test_no_match_ids_is_not_a_query(conn):
    assert second_innings_labels(conn, []) == {}
