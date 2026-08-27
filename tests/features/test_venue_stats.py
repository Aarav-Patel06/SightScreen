"""Venue as-of leakage tests (SPEC.md section 6.2, Phase 1 session 2).

Uses the writable cricket_training_test database (tests/conftest.py's `conn`
fixture) - these tests INSERT synthetic matches, which must never touch the
real, hand-verified cricket_training corpus.

The poison-pill pattern, the real deliverable: compute a value at some
as_of_date, insert a synthetic FUTURE match with a deliberately extreme,
unmistakable outcome, recompute at the SAME as_of_date, and assert
byte-identical results. This mechanically proves "can't see a match on or
after the prediction date" - an assertion, not a re-read of the WHERE clause.
"""

from __future__ import annotations

from datetime import date

import pytest

from features.venue_stats import venue_avg_first_innings_as_of, venue_chase_win_rate_as_of


def _insert_team(conn, name: str) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING team_id", (name,))
        return cur.fetchone()[0]


def _insert_venue(conn, name: str) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO venues (name) VALUES (%s) RETURNING venue_id", (name,))
        return cur.fetchone()[0]


def _insert_match(conn, venue_id: int, team_a: int, team_b: int, start_time: str, winner: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO matches (competition, format, venue_id, start_time, team_a, team_b,
                                  winner, result_method, status)
            VALUES ('test', 'T20', %s, %s, %s, %s, %s, 'normal', 'complete')
            RETURNING match_id
            """,
            (venue_id, start_time, team_a, team_b, winner),
        )
        return cur.fetchone()[0]


def _insert_match_states_row(
    conn,
    match_id: int,
    batting_team_won: bool,
    match_date: str,
    innings: int = 2,
    batting_team_id: int = 1,
    bowling_team_id: int = 2,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO deliveries (match_id, innings, over_num, ball_in_over, legal_ball_num,
                                     batting_team_id, bowling_team_id, match_date,
                                     runs_batter, runs_extras)
            VALUES (%s, %s, 0, 1, 1, %s, %s, %s, 1, 0)
            RETURNING delivery_id
            """,
            (match_id, innings, batting_team_id, bowling_team_id, match_date),
        )
        delivery_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO match_states (delivery_id, match_id, innings, score, wickets,
                                       balls_bowled, balls_remaining, phase, batting_team_won,
                                       match_date, has_reconciliation_anomaly, is_dls_decided)
            VALUES (%s, %s, %s, 0, 0, 0, 120, 'powerplay', %s, %s, false, false)
            """,
            (delivery_id, match_id, innings, batting_team_won, match_date),
        )


def _insert_first_innings_delivery(
    conn, match_id: int, runs: int, match_date: str = "2020-01-01",
    batting_team_id: int = 1, bowling_team_id: int = 2,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO deliveries (match_id, innings, over_num, ball_in_over, legal_ball_num,
                                     batting_team_id, bowling_team_id, match_date,
                                     runs_batter, runs_extras)
            VALUES (%s, 1, 0, 1, 1, %s, %s, %s, %s, 0)
            """,
            (match_id, batting_team_id, bowling_team_id, match_date, runs),
        )


def _make_prior_matches(conn, venue_id: int, team_a: int, team_b: int, n: int, chase_won: bool):
    for i in range(n):
        match_date = f"2020-01-{i + 1:02d}"
        match_id = _insert_match(conn, venue_id, team_a, team_b, match_date, team_a)
        _insert_match_states_row(
            conn, match_id, chase_won, match_date, batting_team_id=team_a, bowling_team_id=team_b
        )
        _insert_first_innings_delivery(
            conn, match_id, 150, match_date=match_date, batting_team_id=team_a, bowling_team_id=team_b
        )


# --- venue_chase_win_rate_as_of --------------------------------------------


def test_venue_chase_win_rate_returns_none_below_min_matches(conn):
    venue_id = _insert_venue(conn, "Test Ground A")
    team_a, team_b = _insert_team(conn, "Team A"), _insert_team(conn, "Team B")
    _make_prior_matches(conn, venue_id, team_a, team_b, n=9, chase_won=True)  # one short of 10

    result = venue_chase_win_rate_as_of(conn, venue_id, date(2021, 1, 1), min_matches=10)
    assert result is None


def test_venue_chase_win_rate_returns_none_with_no_history(conn):
    venue_id = _insert_venue(conn, "Empty Ground")
    result = venue_chase_win_rate_as_of(conn, venue_id, date(2025, 1, 1))
    assert result is None


def test_venue_chase_win_rate_uses_exact_bin_once_min_matches_met(conn):
    venue_id = _insert_venue(conn, "Test Ground B")
    team_a, team_b = _insert_team(conn, "Team C"), _insert_team(conn, "Team D")
    _make_prior_matches(conn, venue_id, team_a, team_b, n=10, chase_won=True)

    result = venue_chase_win_rate_as_of(conn, venue_id, date(2021, 1, 1), min_matches=10)
    assert result == 1.0


def test_venue_chase_win_rate_poison_pill_future_match_never_seen(conn):
    """The real proof: adding a future match with an unmistakable, opposite
    outcome must not change the answer at the earlier as_of_date."""
    venue_id = _insert_venue(conn, "Poison Pill Ground")
    team_a, team_b = _insert_team(conn, "Team E"), _insert_team(conn, "Team F")
    _make_prior_matches(conn, venue_id, team_a, team_b, n=10, chase_won=True)

    as_of_date = date(2021, 1, 1)
    before = venue_chase_win_rate_as_of(conn, venue_id, as_of_date, min_matches=10)

    # Poison pill: 20 future matches, ALL chase losses (opposite of every
    # prior match) - if this leaked, the rate would collapse toward 0.
    for i in range(20):
        m = _insert_match(conn, venue_id, team_a, team_b, f"2021-06-{i + 1:02d}", team_b)
        _insert_match_states_row(
            conn, m, False, f"2021-06-{i + 1:02d}", batting_team_id=team_a, bowling_team_id=team_b
        )

    after = venue_chase_win_rate_as_of(conn, venue_id, as_of_date, min_matches=10)
    assert after == before == 1.0


def test_venue_chase_win_rate_boundary_match_does_not_count(conn):
    """A match dated exactly at as_of_date must not contribute - strictly
    before, matching elo_as_of's contract."""
    venue_id = _insert_venue(conn, "Boundary Ground")
    team_a, team_b = _insert_team(conn, "Team G"), _insert_team(conn, "Team H")
    _make_prior_matches(conn, venue_id, team_a, team_b, n=10, chase_won=True)

    as_of_date = date(2021, 1, 1)
    # A match starting exactly on as_of_date, with the opposite outcome.
    boundary_match = _insert_match(conn, venue_id, team_a, team_b, str(as_of_date), team_b)
    _insert_match_states_row(
        conn, boundary_match, False, str(as_of_date), batting_team_id=team_a, bowling_team_id=team_b
    )

    result = venue_chase_win_rate_as_of(conn, venue_id, as_of_date, min_matches=10)
    assert result == 1.0  # unaffected by the same-day match


# --- venue_avg_first_innings_as_of -----------------------------------------


def test_venue_avg_first_innings_returns_none_below_min_matches(conn):
    venue_id = _insert_venue(conn, "Test Ground C")
    team_a, team_b = _insert_team(conn, "Team I"), _insert_team(conn, "Team J")
    _make_prior_matches(conn, venue_id, team_a, team_b, n=5, chase_won=True)

    result = venue_avg_first_innings_as_of(conn, venue_id, date(2021, 1, 1), min_matches=10)
    assert result is None


def test_venue_avg_first_innings_poison_pill_future_match_never_seen(conn):
    venue_id = _insert_venue(conn, "Poison Pill Ground 2")
    team_a, team_b = _insert_team(conn, "Team K"), _insert_team(conn, "Team L")
    _make_prior_matches(conn, venue_id, team_a, team_b, n=10, chase_won=True)  # each: 150 first-innings runs

    as_of_date = date(2021, 1, 1)
    before = venue_avg_first_innings_as_of(conn, venue_id, as_of_date, min_matches=10)
    assert before == pytest.approx(150.0)

    # Poison pill: a wildly different future first-innings total.
    future_match = _insert_match(conn, venue_id, team_a, team_b, "2021-06-01", team_a)
    _insert_first_innings_delivery(
        conn, future_match, 400, match_date="2021-06-01", batting_team_id=team_a, bowling_team_id=team_b
    )

    after = venue_avg_first_innings_as_of(conn, venue_id, as_of_date, min_matches=10)
    assert after == before
