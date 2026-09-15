"""Venue as-of aggregates (SPEC.md section 6.2, Phase 1 session 2; rewired
onto a precomputed summary in Phase 2 session 3).

Two rolling features `win_prob_2nd.py` needs, both computed the same way
`elo.py`'s `elo_as_of` already is (Phase 0 session 6): a strict `< as_of_date`
filter, so a match can never see itself or anything after it, and never a
mutable "current value" to accidentally overwrite.

`min_matches` (default 10): below this many prior matches at a venue, the
observed rate/average is mostly noise (2/5 vs. 3/5 swings 20 points on a
single outcome) - both functions return None rather than a global-average
substitute, which would need its own as-of discipline and adds a second
thing to get right for no clear benefit. None propagates to NaN in the
caller, which LightGBM's native missing-value handling is built for.

WHAT CHANGED IN PHASE 2 SESSION 3, and why there are now two of everything:

The original implementations recomputed from `matches`/`match_states`/
`deliveries` on every call. Those tables are local-only by section 2.1, so a
Railway worker could not run them at all. They now read
`venue_asof_summary`, which exists in BOTH databases, so training and serving
run the same function over the same table shape and differ only in which
connection they are handed.

The direct implementations are kept, renamed `*_direct`, and are NOT dead
code: they are the rebuild job's source of truth and the parity gate's
oracle (`tests/db/test_asof_parity.py`). Deleting them would make the gate
unrepeatable. They are never called on a serving path.

The predicate fragments below are shared by both, deliberately: if someone
ever changes `innings = 2` or `NOT is_super_over`, the direct query, the
rebuild and the oracle all change together instead of drifting apart.
"""

from __future__ import annotations

from datetime import date, datetime

# The cold-start floor, defined exactly once. Training and serving must not
# be able to disagree about it - 24-33% of matches hit this path - so every
# default in this module and in features/as_of.py points here rather than
# repeating the literal.
MIN_VENUE_MATCHES = 10

# --- Shared predicate fragments -------------------------------------------
# One match, one vote. The chase rate aggregates per MATCH via a subquery
# before averaging - not per ball, which would let a long chase out-vote a
# short one.
CHASE_PER_MATCH_SELECT = """
    SELECT DISTINCT ms.match_id, ms.batting_team_won
    FROM match_states ms
    JOIN matches m ON m.match_id = ms.match_id
    WHERE ms.innings = 2
      AND ms.batting_team_won IS NOT NULL
"""
CHASE_WIN_EXPR = "CASE WHEN batting_team_won THEN 1.0 ELSE 0.0 END"

# Sourced from SUM(runs_batter + runs_extras) over deliveries (innings=1, not
# super over) - NOT target_runs - 1, which is only valid for an un-revised
# target and wrong by dozens of runs for a DLS-revised one (Phase 0 session
# 6's own finding: matches.target_runs is the authoritative, possibly
# DLS-revised figure, never innings_1_total + 1).
FIRST_INNINGS_PER_MATCH_SELECT = """
    SELECT d.match_id, SUM(d.runs_batter + d.runs_extras) AS innings_total
    FROM deliveries d
    JOIN matches m ON m.match_id = d.match_id
    WHERE d.innings = 1
      AND NOT d.is_super_over
"""


def as_of_date_key(as_of_date) -> date:
    """Normalise whatever a caller passes into the one key type the summary
    tables use.

    Training passes a `datetime.date` (from `deliveries.match_date`); the
    live path passes `matches.start_time`, a `timestamptz`. Against the old
    `m.start_time < %s` those two agreed only because every start_time in
    this corpus is exactly midnight AND both databases happen to run in UTC -
    Postgres resolves `timestamptz < date` by casting the date to midnight
    *in the session timezone*, so a Supabase project on a non-UTC timezone
    would have silently shifted every serving lookup by hours. Comparing
    date to date removes the hazard instead of relying on the coincidence.
    """
    if isinstance(as_of_date, datetime):
        return as_of_date.date()
    return as_of_date


# --- Summary-backed: the functions training and serving both call ----------

_VENUE_SUMMARY_LOOKUP = """
    SELECT CASE WHEN chase_n > 0 THEN chase_wins / chase_n END,
           chase_n,
           CASE WHEN first_inns_n > 0 THEN first_inns_runs::numeric / first_inns_n END,
           first_inns_n
    FROM venue_asof_summary
    WHERE venue_id = %(venue_id)s AND effective_date < %(as_of_date)s
    ORDER BY effective_date DESC
    LIMIT 1
"""


def _lookup(conn, venue_id: int, as_of_date) -> tuple | None:
    with conn.cursor() as cur:
        cur.execute(
            _VENUE_SUMMARY_LOOKUP,
            {"venue_id": venue_id, "as_of_date": as_of_date_key(as_of_date)},
        )
        return cur.fetchone()


def venue_chase_win_rate_as_of(
    conn, venue_id: int, as_of_date, min_matches: int = MIN_VENUE_MATCHES
) -> float | None:
    """Fraction of prior matches (strictly before as_of_date) at this venue
    where the chasing (innings-2) team won. Returns None if fewer than
    min_matches qualifying matches exist.

    The division happens in SQL, not Python. Postgres computes avg(numeric)
    as numeric_div(sum, count), whose result scale depends on its operands'
    dscale; Decimal division in Python uses its own 28-significant-digit
    context and would not necessarily land on the same float. See the
    migration's note on why chase_wins is stored at scale 1.
    """
    row = _lookup(conn, venue_id, as_of_date)
    if row is None:
        return None
    rate, n, _, _ = row
    if n < min_matches:
        return None
    return float(rate)


def venue_avg_first_innings_as_of(
    conn, venue_id: int, as_of_date, min_matches: int = MIN_VENUE_MATCHES
) -> float | None:
    """Mean first-innings total at this venue, matches strictly before
    as_of_date. Returns None if fewer than min_matches qualifying matches
    exist."""
    row = _lookup(conn, venue_id, as_of_date)
    if row is None:
        return None
    _, _, avg_total, n = row
    if n < min_matches:
        return None
    return float(avg_total)


# --- Direct: the oracle, and the rebuild's source -------------------------
# Never called on a serving path. Reads match_states/deliveries/matches,
# which exist only in the local training database.

def venue_chase_win_rate_as_of_direct(
    conn, venue_id: int, as_of_date: date, min_matches: int = MIN_VENUE_MATCHES
) -> float | None:
    """The pre-session-3 implementation, unchanged in behaviour: recomputes
    from the corpus on every call. The parity gate asserts the summary-backed
    function above returns byte-identical values to this one for every
    (venue, date) pair in the corpus."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH per_match AS (
                {CHASE_PER_MATCH_SELECT}
                  AND m.venue_id = %(venue_id)s
                  AND m.start_time < %(as_of_date)s
            )
            SELECT avg({CHASE_WIN_EXPR}), count(*)
            FROM per_match
            """,
            {"venue_id": venue_id, "as_of_date": as_of_date},
        )
        rate, n = cur.fetchone()
    if n is None or n < min_matches:
        return None
    return float(rate)


def venue_avg_first_innings_as_of_direct(
    conn, venue_id: int, as_of_date: date, min_matches: int = MIN_VENUE_MATCHES
) -> float | None:
    """The pre-session-3 implementation, unchanged in behaviour."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH per_match AS (
                {FIRST_INNINGS_PER_MATCH_SELECT}
                  AND m.venue_id = %(venue_id)s
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
