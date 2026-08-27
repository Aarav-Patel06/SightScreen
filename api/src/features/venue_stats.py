"""Venue as-of aggregates (SPEC.md section 6.2, Phase 1 session 2).

Two rolling features `win_prob_2nd.py` needs, both computed the same way
`elo.py`'s `elo_as_of` already is (Phase 0 session 6): a strict `< as_of_date`
filter, so a match can never see itself or anything after it, and never a
mutable "current value" to accidentally overwrite - every call recomputes
from `matches`/`match_states`/`deliveries` directly.

`min_matches` (default 10): below this many prior matches at a venue, the
observed rate/average is mostly noise (2/5 vs. 3/5 swings 20 points on a
single outcome) - both functions return None rather than a global-average
substitute, which would need its own as-of discipline and adds a second
thing to get right for no clear benefit. None propagates to NaN in the
caller, which LightGBM's native missing-value handling is built for.
"""

from __future__ import annotations

from datetime import date


def venue_chase_win_rate_as_of(
    conn, venue_id: int, as_of_date: date, min_matches: int = 10
) -> float | None:
    """Fraction of prior matches (strictly before as_of_date) at this venue
    where the chasing (innings-2) team won. Aggregated per MATCH - one vote
    each, via a per-match subquery before averaging - not per ball, which
    would let a long chase out-vote a short one. Returns None if fewer than
    min_matches qualifying matches exist."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH per_match AS (
                SELECT DISTINCT ms.match_id, ms.batting_team_won
                FROM match_states ms
                JOIN matches m ON m.match_id = ms.match_id
                WHERE m.venue_id = %(venue_id)s
                  AND ms.innings = 2
                  AND ms.batting_team_won IS NOT NULL
                  AND m.start_time < %(as_of_date)s
            )
            SELECT avg(CASE WHEN batting_team_won THEN 1.0 ELSE 0.0 END), count(*)
            FROM per_match
            """,
            {"venue_id": venue_id, "as_of_date": as_of_date},
        )
        rate, n = cur.fetchone()
    if n is None or n < min_matches:
        return None
    return float(rate)


def venue_avg_first_innings_as_of(
    conn, venue_id: int, as_of_date: date, min_matches: int = 10
) -> float | None:
    """Mean first-innings total at this venue, matches strictly before
    as_of_date. Sourced from SUM(runs_batter + runs_extras) over deliveries
    (innings=1, not super over) - NOT target_runs - 1, which is only valid
    for an un-revised target and wrong by dozens of runs for a DLS-revised
    one (Phase 0 session 6's own finding: matches.target_runs is the
    authoritative, possibly DLS-revised figure, never innings_1_total + 1).
    Returns None if fewer than min_matches qualifying matches exist."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH per_match AS (
                SELECT d.match_id, SUM(d.runs_batter + d.runs_extras) AS innings_total
                FROM deliveries d
                JOIN matches m ON m.match_id = d.match_id
                WHERE m.venue_id = %(venue_id)s
                  AND d.innings = 1
                  AND NOT d.is_super_over
                  AND m.start_time < %(as_of_date)s
                GROUP BY d.match_id
            )
            SELECT avg(innings_total), count(*) FROM per_match
            """,
            {"venue_id": venue_id, "as_of_date": as_of_date},
        )
        avg_total, n = cur.fetchone()
    if n is None or n < min_matches:
        return None
    return float(avg_total)
