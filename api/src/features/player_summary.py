"""Per-player aggregates, rebuilt from the corpus (UI mini-phase session 4).

    python -m features.player_summary rebuild

DESCRIPTIVE ONLY, AND THE DISTINCTION IS THE POINT. Everything here is a
record of what happened. None of it is an estimate of what a player can do
now - that is `player_state`, which is empty until Phase 5, and the two are
different questions. `get_player_form` already refuses to conflate them in
the agent; /player/[id] makes the same refusal in the interface.

Built here rather than queried at request time because it is computed from
`deliveries`, which is empty on Supabase by §2.1's design. Synced by
ingest.sync_reference_tables alongside the as-of summaries, using the same
replace-and-verify with a content hash.

THE CRICKET RULES THIS ENCODES, stated because they are the part a reviewer
cannot check by reading SQL:

  * A batter faces every delivery except a wide. A no-ball IS faced.
  * A batter's runs are `runs_batter` only - byes and leg-byes off their bat
    are extras, not theirs.
  * A dismissal counts as an out unless it is a `retired *` type. Retiring
    ends an innings without being a dismissal, and counting it would deflate
    an average that is already the most-quoted number on the page.
  * A bowler bowls legal deliveries only: wides and no-balls are not balls
    of the over.
  * A bowler concedes the batter's runs plus wides and no-balls. Byes and
    leg-byes are NOT charged to them - those are the keeper's.
  * A bowler is credited with bowled, caught, caught and bowled, lbw,
    stumped and hit wicket. A run out is not their wicket, and neither is
    obstructing the field.
"""

from __future__ import annotations

import argparse
import sys

import psycopg
from dotenv import dotenv_values

from features.asof_summary import ENV_PATH, DerivedTable, record_sync_state

# Dismissals credited to the bowler. Measured distinct values in the corpus:
# caught, bowled, lbw, run out, caught and bowled, stumped, retired hurt,
# hit wicket, retired out, obstructing the field, retired not out,
# hit the ball twice.
BOWLER_CREDITED = ("bowled", "caught", "caught and bowled", "lbw", "stumped", "hit wicket")

# Types that end an innings without being a dismissal.
NOT_AN_OUT = ("retired hurt", "retired out", "retired not out")

PLAYER_INDEX = DerivedTable(
    "player_index",
    (
        "player_id",
        "canonical_name",
        "normalized_name",
        "surname_key",
        "matches",
        "formats",
        "bat_innings",
        "bat_runs",
        "bowl_innings",
        "bowl_wickets",
    ),
    "player_id",
    "player_id::text || '|' || canonical_name || '|' || normalized_name || '|' || "
    "surname_key || '|' || matches::text || '|' || formats || '|' || "
    "bat_innings::text || '|' || bat_runs::text || '|' || "
    "bowl_innings::text || '|' || bowl_wickets::text",
)

PLAYER_CAREER = DerivedTable(
    "player_career_summary",
    (
        "player_id",
        "format",
        "phase",
        "bat_innings",
        "bat_balls",
        "bat_runs",
        "bat_outs",
        "bat_fours",
        "bat_sixes",
        "bowl_balls",
        "bowl_runs",
        "bowl_wickets",
    ),
    "player_id, format, phase",
    "player_id::text || '|' || format || '|' || phase || '|' || "
    "bat_innings::text || '|' || bat_balls::text || '|' || bat_runs::text || '|' || "
    "bat_outs::text || '|' || bat_fours::text || '|' || bat_sixes::text || '|' || "
    "bowl_balls::text || '|' || bowl_runs::text || '|' || bowl_wickets::text",
)

PLAYER_DERIVED_TABLES = (PLAYER_INDEX, PLAYER_CAREER)


# `phase` lives on match_states, which exists for every non-super-over
# delivery. The LEFT JOIN keeps super-over balls in the career totals rather
# than dropping them; they land in the 'all' row and are excluded from the
# phase splits, which is the honest treatment - a super over has no
# powerplay.
_CAREER_SQL = f"""
WITH balls AS (
    SELECT d.match_id, d.innings, d.batter_id, d.bowler_id,
           m.format,
           ms.phase,
           d.runs_batter, d.runs_extras, d.extra_type,
           d.wicket_type, d.player_out_id,
           (d.extra_type IS NULL OR d.extra_type NOT IN ('wide', 'noball')) AS is_legal,
           (d.extra_type IS DISTINCT FROM 'wide')                            AS is_faced
    FROM deliveries d
    JOIN matches m ON m.match_id = d.match_id
    LEFT JOIN match_states ms ON ms.delivery_id = d.delivery_id
),
-- Every (player, format, phase) bucket either side has appeared in, so a
-- pure bowler still gets a row with zeroed batting and vice versa.
buckets AS (
    SELECT batter_id AS player_id, format, phase FROM balls WHERE batter_id IS NOT NULL
    UNION
    SELECT bowler_id, format, phase FROM balls WHERE bowler_id IS NOT NULL
    UNION
    SELECT batter_id, format, 'all' FROM balls WHERE batter_id IS NOT NULL
    UNION
    SELECT bowler_id, format, 'all' FROM balls WHERE bowler_id IS NOT NULL
),
batting AS (
    SELECT b.batter_id AS player_id, b.format, p.phase,
           count(DISTINCT (b.match_id, b.innings))            AS innings,
           count(*) FILTER (WHERE b.is_faced)                 AS balls,
           coalesce(sum(b.runs_batter), 0)                    AS runs,
           count(*) FILTER (
               WHERE b.player_out_id = b.batter_id
                 AND b.wicket_type IS NOT NULL
                 AND b.wicket_type <> ALL (%(not_out)s)
           )                                                  AS outs,
           count(*) FILTER (WHERE b.runs_batter = 4)          AS fours,
           count(*) FILTER (WHERE b.runs_batter = 6)          AS sixes
    FROM balls b
    CROSS JOIN LATERAL (VALUES (b.phase), ('all')) AS p(phase)
    WHERE b.batter_id IS NOT NULL AND p.phase IS NOT NULL
    GROUP BY 1, 2, 3
),
bowling AS (
    SELECT b.bowler_id AS player_id, b.format, p.phase,
           count(*) FILTER (WHERE b.is_legal)                 AS balls,
           coalesce(sum(
               b.runs_batter
               + CASE WHEN b.extra_type IN ('wide', 'noball') THEN b.runs_extras ELSE 0 END
           ), 0)                                              AS runs,
           count(*) FILTER (WHERE b.wicket_type = ANY (%(credited)s)) AS wickets
    FROM balls b
    CROSS JOIN LATERAL (VALUES (b.phase), ('all')) AS p(phase)
    WHERE b.bowler_id IS NOT NULL AND p.phase IS NOT NULL
    GROUP BY 1, 2, 3
)
INSERT INTO player_career_summary (
    player_id, format, phase,
    bat_innings, bat_balls, bat_runs, bat_outs, bat_fours, bat_sixes,
    bowl_balls, bowl_runs, bowl_wickets
)
SELECT k.player_id, k.format, k.phase,
       coalesce(bat.innings, 0), coalesce(bat.balls, 0), coalesce(bat.runs, 0),
       coalesce(bat.outs, 0), coalesce(bat.fours, 0), coalesce(bat.sixes, 0),
       coalesce(bowl.balls, 0), coalesce(bowl.runs, 0), coalesce(bowl.wickets, 0)
FROM buckets k
LEFT JOIN batting bat
       ON bat.player_id = k.player_id AND bat.format = k.format AND bat.phase = k.phase
LEFT JOIN bowling bowl
       ON bowl.player_id = k.player_id AND bowl.format = k.format AND bowl.phase = k.phase
WHERE k.phase IS NOT NULL;
"""

_INDEX_SQL = """
INSERT INTO player_index (
    player_id, canonical_name, normalized_name, surname_key,
    matches, formats, bat_innings, bat_runs, bowl_innings, bowl_wickets
)
SELECT s.player_id,
       p.canonical_name,
       '',  -- filled in by the Python pass below
       '',
       s.matches,
       s.formats,
       s.bat_innings,
       s.bat_runs,
       s.bowl_innings,
       s.bowl_wickets
FROM (
    SELECT c.player_id,
           string_agg(DISTINCT c.format, ',' ORDER BY c.format) AS formats,
           sum(c.bat_innings)::int  AS bat_innings,
           sum(c.bat_runs)::int     AS bat_runs,
           count(*) FILTER (WHERE c.bowl_balls > 0)::int AS bowl_innings,
           sum(c.bowl_wickets)::int AS bowl_wickets,
           0 AS matches
    FROM player_career_summary c
    WHERE c.phase = 'all'
    GROUP BY c.player_id
) s
JOIN players p ON p.player_id = s.player_id
-- A record means a delivery batted or bowled. More than half the corpus's
-- 18,468 players have neither: they were named on a team sheet and never
-- faced or sent down a ball. Listing them would be an index of mostly
-- nothing.
WHERE s.bat_innings > 0 OR s.bowl_innings > 0;
"""

_MATCHES_SQL = """
UPDATE player_index pi
SET matches = m.n
FROM (
    SELECT player_id, count(DISTINCT match_id)::int AS n
    FROM (
        SELECT batter_id AS player_id, match_id FROM deliveries WHERE batter_id IS NOT NULL
        UNION
        SELECT bowler_id, match_id FROM deliveries WHERE bowler_id IS NOT NULL
    ) appearances
    GROUP BY player_id
) m
WHERE m.player_id = pi.player_id;
"""


def rebuild(conn) -> tuple[int, int]:
    """Rebuild both tables. Returns (career rows, index rows)."""
    from ingest.entity_resolution import normalize_name, surname_key

    with conn.cursor() as cur:
        # Separate statements: psycopg prepares a parameterised execute, and a
        # prepared statement cannot carry more than one command.
        cur.execute("TRUNCATE player_career_summary")
        cur.execute(_CAREER_SQL, {"credited": list(BOWLER_CREDITED), "not_out": list(NOT_AN_OUT)})
        cur.execute("TRUNCATE player_index")
        cur.execute(_INDEX_SQL)
        cur.execute(_MATCHES_SQL)

        # The search columns, through the resolver itself rather than a SQL
        # reimplementation of it. §4.4 asks for one matcher, and this is how
        # the web layer gets the resolver's answer without owning a copy of
        # its rules.
        cur.execute("SELECT player_id, canonical_name FROM player_index")
        rows = cur.fetchall()
        cur.executemany(
            "UPDATE player_index SET normalized_name = %s, surname_key = %s WHERE player_id = %s",
            [
                (normalize_name(name), surname_key(normalize_name(name)), player_id)
                for player_id, name in rows
            ],
        )

        cur.execute("SELECT count(*) FROM player_career_summary")
        career = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM player_index")
        index = cur.fetchone()[0]
    conn.commit()

    # The local copy has moved and Supabase has not been proven to match, so
    # each row records synced_at = NULL. sync_reference_tables stamps it only
    # after re-reading what it pushed. Without these rows the sync refuses
    # the table outright, which is how this was found.
    for table in PLAYER_DERIVED_TABLES:
        record_sync_state(conn, table, synced=False)
    conn.commit()

    return career, index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="features.player_summary", description=__doc__)
    parser.add_argument("command", choices=["rebuild"])
    parser.parse_args(argv)

    env = dotenv_values(ENV_PATH)
    url = env.get("LOCAL_DATABASE_URL")
    if not url:
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")

    with psycopg.connect(url, connect_timeout=30) as conn:
        career, index = rebuild(conn)

    print(f"player_career_summary: {career:,} rows")
    print(f"player_index:          {index:,} rows")
    print("\nnow run: python -m ingest.sync_reference_tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
