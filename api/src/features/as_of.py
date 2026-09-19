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

from datetime import date, datetime, timezone

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

    2. SYNC FRESHNESS. How long ago reference_sync_state says the sync last
       pushed to THIS database. Still a proxy rather than a proof - a worker
       reading only Supabase cannot prove the local copy hasn't moved since,
       because stale data and its stale hash are mutually consistent - but it
       measures the right clock.

       CORRECTED 2026-09-15 (Phase 2 session 4). This check originally used
       max(effective_date), the newest MATCH date in the summaries, on the
       reasoning that "cricket is played almost daily". That conflated two
       different clocks. The corpus is a periodically-refreshed archive, so
       its newest match is routinely weeks old even when the sync ran minutes
       ago. Caught by the first real container start: data synced 14 hours
       earlier was refused as "22 days old" and the deployment could not
       proceed. synced_at answers the question actually being asked - is this
       database's copy current - and does not false-positive between Cricsheet
       refreshes. The corpus age is still reported, because it is worth
       seeing; it just no longer refuses.
    """
    # UTC, not date.today(). `synced_at` is a TIMESTAMPTZ written by the
    # sync job and compared here as a date; `date.today()` is the LOCAL
    # date, so anywhere west of UTC the two disagree for part of every
    # evening. Found 2026-09-19 at 02:08 UTC / 22:08 EDT, when a sync
    # performed minutes earlier reported `age_days: -1` - a freshness check
    # claiming the data arrives tomorrow. Invisible in CI and on Railway
    # because both run in UTC, which is exactly why it survived three
    # phases: the only machine that can see it is a developer laptop, in
    # the evening.
    today = today or datetime.now(timezone.utc).date()
    report: dict = {
        "hashes": {},
        "newest_breakpoint": None,
        "corpus_age_days": None,
        "synced_at": None,
        "age_days": None,
    }

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
    report["newest_breakpoint"] = newest
    report["corpus_age_days"] = (today - newest).days

    synced_at = _oldest_sync(conn)
    if synced_at is None:
        raise StaleReferenceData(
            "reference_sync_state has no synced_at - the summaries were rebuilt but "
            "never pushed. Run `python -m ingest.sync_reference_tables`."
        )
    age_days = (today - synced_at).days
    report["synced_at"] = synced_at
    report["age_days"] = age_days

    if age_days > max_age_days:
        raise StaleReferenceData(
            f"reference tables were last synced {synced_at} ({age_days} days ago, limit "
            f"{max_age_days}). Rebuild the summaries locally and re-run "
            f"ingest.sync_reference_tables."
        )
    if age_days > warn_age_days:
        print(
            f"WARNING: reference tables were last synced {age_days} days ago "
            f"({synced_at}); they become unservable at {max_age_days} days."
        )
    return report


def _newest_breakpoint(conn) -> date | None:
    """The OLDER of the two summaries' newest dates, so that either one
    falling behind is not masked by the other. Reported, not enforced."""
    newest: list[date] = []
    for table in DERIVED_TABLES:
        with conn.cursor() as cur:
            cur.execute(f"SELECT max(effective_date) FROM {table.name}")
            value = cur.fetchone()[0]
        if value is None:
            return None
        newest.append(value)
    return min(newest)


def _oldest_sync(conn) -> date | None:
    """The OLDEST synced_at across the derived tables - same principle: one
    table falling behind must trip the check, not be averaged away."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT min(synced_at) FROM reference_sync_state WHERE table_name = ANY(%s)",
            ([table.name for table in DERIVED_TABLES],),
        )
        value = cur.fetchone()[0]
    # .astimezone(UTC) before .date(): psycopg renders a TIMESTAMPTZ in the
    # SESSION's timezone, so without this the answer depends on a Postgres
    # setting rather than on the data. Same class of bug as session 3's
    # extra_float_digits - a value that changes with a session variable.
    return value.astimezone(timezone.utc).date() if value is not None else None


__all__ = [
    "MAX_REFERENCE_AGE_DAYS",
    "WARN_REFERENCE_AGE_DAYS",
    "StaleReferenceData",
    "assert_reference_fresh",
    "compute_as_of_features",
]
