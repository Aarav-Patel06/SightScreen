"""The single as-of feature entry point (SPEC.md section 6.2, Phase 2
session 3, Decision 3).

One function, two backends. Training hands it a connection to the local
training Postgres; the Railway worker hands it a Supabase connection. That
is the ONLY difference between the two paths - there is deliberately no
serving-side reimplementation, because a second implementation is how
training and serving diverge without any metric noticing.

    models/win_prob_2nd.py::_match_level_features  ->  compute_as_of_features
    ingest/replay.py                               ->  compute_as_of_features
    Phase 4 Railway worker                         ->  compute_as_of_features
                                                            |
                              +-----------------------------+------------------+
                              v                                                v
             features/elo.py::elo_as_of                features/venue_stats.py::
               -> elo_asof_summary                       venue_*_as_of
               -> STARTING_RATING if no row              -> venue_asof_summary
                                                         -> None below min_matches

This module previously lived in ingest/replay.py. It moved here so training
does not have to import from `ingest`, which would be a backwards dependency
edge for a feature computation.
"""

from __future__ import annotations

from datetime import date

from features.asof_summary import DERIVED_TABLES, content_hash
from features.elo import elo_as_of
from features.venue_stats import (
    MIN_VENUE_MATCHES,
    as_of_date_key,
    venue_avg_first_innings_as_of,
    venue_chase_win_rate_as_of,
)

# A summary whose newest breakpoint is this old means the rebuild-and-sync
# ritual has not run. Cricket is played somewhere most days, so a two-week
# gap is not a quiet fortnight - it is a missed sync.
MAX_REFERENCE_AGE_DAYS = 14
WARN_REFERENCE_AGE_DAYS = 7


class StaleReferenceData(RuntimeError):
    """Raised at worker startup when the reference tables cannot be trusted.

    Deliberately fatal rather than degrading: serving a win probability from
    reference data that does not match what the model was trained against
    produces a number that looks fine and is wrong, which is worse than not
    serving one.
    """


def compute_as_of_features(
    conn,
    venue_id: int | None,
    batting_team_id: int,
    bowling_team_id: int,
    format_: str,
    match_date,
    min_venue_matches: int = MIN_VENUE_MATCHES,
) -> dict:
    """The SAME function models/win_prob_2nd.py calls for training -
    guarantees a cold-start venue (24-33% of matches, Phase 1's measured
    rate) gets the identical None -> NaN fallback the model was trained
    with, by construction rather than by re-implementing the rule twice.

    `match_date` is normalised to a `datetime.date` here, once, because
    training passes a date and the live path passes a `timestamptz`; see
    venue_stats.as_of_date_key for why that difference was a latent bug.
    """
    key = as_of_date_key(match_date)
    bat_elo = elo_as_of(conn, batting_team_id, format_, key)
    bowl_elo = elo_as_of(conn, bowling_team_id, format_, key)
    if venue_id is None:
        venue_rate, venue_avg = None, None
    else:
        venue_rate = venue_chase_win_rate_as_of(conn, venue_id, key, min_matches=min_venue_matches)
        venue_avg = venue_avg_first_innings_as_of(conn, venue_id, key, min_matches=min_venue_matches)
    return {
        "elo_diff": bat_elo - bowl_elo,
        "venue_chase_win_rate": venue_rate,
        "venue_avg_first_innings": venue_avg,
    }


def assert_reference_fresh(
    conn,
    *,
    max_age_days: int = MAX_REFERENCE_AGE_DAYS,
    warn_age_days: int = WARN_REFERENCE_AGE_DAYS,
    today: date | None = None,
) -> dict:
    """Refuses to serve on stale or corrupt reference data. Call at startup.

    Two checks, and they catch different things. Being precise about the
    limits, because one of them has a real one:

    1. CONTENT HASH. Recompute each derived table's hash from the rows
       actually present and compare against what the sync recorded. Airtight,
       and detectable from this connection alone: it catches a partial or
       failed sync, a truncation, a manual edit, corruption.

    2. CORPUS FRESHNESS. The newest breakpoint in the summaries, versus
       today. This is the only signal for "rebuilt locally, sync never run" -
       and it is a proxy, not a proof. A worker that can see only Supabase
       CANNOT prove the sync ran, because stale data and its stale hash are
       mutually consistent. It infers staleness from the fact that matches
       are played almost daily. Said plainly here rather than implied.
    """
    today = today or date.today()
    report: dict = {"hashes": {}, "newest_breakpoint": None, "age_days": None}

    for table in DERIVED_TABLES:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content_hash, row_count FROM reference_sync_state WHERE table_name = %s",
                (table.name,),
            )
            recorded = cur.fetchone()
        if recorded is None:
            raise StaleReferenceData(
                f"no reference_sync_state row for {table.name} - the rebuild/sync "
                f"ritual has never completed against this database"
            )
        actual_hash, actual_rows = content_hash(conn, table)
        if actual_hash != recorded[0]:
            raise StaleReferenceData(
                f"{table.name} content hash mismatch: recorded {recorded[0]} over "
                f"{recorded[1]} rows, found {actual_hash} over {actual_rows} rows. "
                f"The copy in this database is not the one the sync verified."
            )
        report["hashes"][table.name] = actual_hash

    newest = _newest_breakpoint(conn)
    if newest is None:
        raise StaleReferenceData("the as-of summaries are empty - nothing to serve from")
    age_days = (today - newest).days
    report["newest_breakpoint"] = newest
    report["age_days"] = age_days

    if age_days > max_age_days:
        raise StaleReferenceData(
            f"newest as-of breakpoint is {newest} ({age_days} days old, limit "
            f"{max_age_days}). Rebuild the summaries locally and re-run "
            f"ingest.sync_reference_tables."
        )
    if age_days > warn_age_days:
        print(
            f"WARNING: as-of summaries are {age_days} days old (newest breakpoint "
            f"{newest}); they become unservable at {max_age_days} days."
        )
    return report


def _newest_breakpoint(conn) -> date | None:
    """The OLDER of the two summaries' newest dates, so that either one
    falling behind trips the check rather than being masked by the other."""
    newest: list[date] = []
    for table in DERIVED_TABLES:
        with conn.cursor() as cur:
            cur.execute(f"SELECT max(effective_date) FROM {table.name}")
            value = cur.fetchone()[0]
        if value is None:
            return None
        newest.append(value)
    return min(newest)


__all__ = [
    "MAX_REFERENCE_AGE_DAYS",
    "WARN_REFERENCE_AGE_DAYS",
    "StaleReferenceData",
    "assert_reference_fresh",
    "compute_as_of_features",
]
