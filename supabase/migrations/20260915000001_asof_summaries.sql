-- As-of summaries for the serving path (SPEC.md section 2.1 / section 15's
-- 2026-09-10 decision, Phase 2 session 3).
--
-- The two venue features and elo_diff are as-of aggregates over the full
-- corpus: venue_chase_win_rate reads match_states, venue_avg_first_innings
-- reads deliveries, and elo_as_of read elo_ratings. All four source tables
-- are local-only by section 2.1 and stay that way - so a Railway worker had
-- nothing to read, which is what blocked deployment.
--
-- These two tables are the derived, serving-side answer. Both are a time
-- series, not a snapshot: one row per (entity, date) carrying the cumulative
-- state as of that point, so an as-of lookup is
--   WHERE <entity> = $1 AND effective_date < $2
--   ORDER BY effective_date DESC LIMIT 1
-- which is exactly equivalent to the direct query's "m.start_time < $2".
-- The latest breakpoint strictly before $2 accumulates precisely the matches
-- with start_time < $2, because any match in between would itself be a
-- breakpoint.
--
-- They exist in BOTH databases. Training reading one thing while serving
-- reads another is the divergence this table exists to prevent, so
-- features/venue_stats.py and features/elo.py read these tables in both
-- environments and differ only in which connection they are handed.
--
-- No RLS policy, following elo_ratings' precedent in
-- 20260826180008_realtime_and_rls.sql: nothing reads these from a browser.

CREATE TABLE venue_asof_summary (
  venue_id        INT     NOT NULL REFERENCES venues(venue_id),
  effective_date  DATE    NOT NULL,
  -- Cumulative counters, NOT a precomputed rate. The min_matches floor is
  -- applied at read time by the one helper (section 15 / Decision 2): it is
  -- a hyperparameter, and baking it in here would freeze it and create a
  -- third place the rule is written down.
  --
  -- chase_wins is NUMERIC, not an integer count, and the scale is
  -- load-bearing. Postgres computes avg(numeric) as numeric_div(sum, count),
  -- and numeric_div's result scale depends on the dscale of its operands.
  -- The rebuild accumulates sum(CASE WHEN .. THEN 1.0 ELSE 0.0 END), giving
  -- dscale 1, so chase_wins / chase_n reproduces avg() digit for digit.
  -- Storing an integer 3 instead of 3.0 would silently change the last
  -- digits - the same class of bug as the ::numeric ROUND mismatch in
  -- features/match_state.py.
  chase_wins      NUMERIC NOT NULL,
  chase_n         BIGINT  NOT NULL,
  -- Separate counters because the two features aggregate DIFFERENT match
  -- subsets: the chase rate needs match_states.batting_team_won IS NOT NULL,
  -- the first-innings average needs innings-1 non-super-over deliveries. A
  -- shared n would be wrong for one of them.
  first_inns_runs BIGINT  NOT NULL,
  first_inns_n    BIGINT  NOT NULL,
  PRIMARY KEY (venue_id, effective_date)
);

CREATE TABLE elo_asof_summary (
  team_id         INT     NOT NULL REFERENCES teams(team_id),
  format          TEXT    NOT NULL,             -- 'T20' | 'ODI'
  effective_date  DATE    NOT NULL,
  -- NUMERIC, not REAL, and this is a serving-correctness decision rather
  -- than a modelling one. A float's TEXT output depends on the session's
  -- extra_float_digits, and Supabase's pooler hands out sessions with 0
  -- while local Postgres uses the 12+ default of 1. psycopg decodes in text
  -- mode, so the identical stored float4 came back to Python as 1496.445
  -- locally and 1496.44 from Supabase - a silent training/serving
  -- divergence of exactly the kind this table exists to prevent. Caught by
  -- tests/db/test_asof_parity.py's layer 3 on its first run.
  --
  -- numeric has no such session knob. The rebuild casts float4 -> numeric
  -- under a pinned extra_float_digits, giving the shortest exact decimal,
  -- which is precisely the string psycopg already parsed on the training
  -- path - so float(Decimal('1496.445')) == float('1496.445') and the
  -- values the model was trained against are unchanged.
  rating          NUMERIC NOT NULL,
  PRIMARY KEY (team_id, format, effective_date)
);

-- elo_ratings itself is NOT synced and stays local: its match_id is
-- NOT NULL REFERENCES matches(match_id), and matches never goes to Supabase
-- (section 2.1 - and Supabase's matches is the live worker's own write
-- target with a SERIAL match_id, so syncing 13k corpus rows would interleave
-- corpus and live ids in one space). Collapsing elo_ratings to this
-- (team, format, date) grain needs a tie-break, since a team playing twice
-- on one date produces two rows with an identical as_of; see
-- features/asof_summary.py.

CREATE TABLE reference_sync_state (
  table_name    TEXT PRIMARY KEY,
  -- md5(string_agg(md5(row::text), '' ORDER BY <natural key>)), the pattern
  -- already used by tests/features/test_elo.py's idempotency check.
  content_hash  TEXT        NOT NULL,
  row_count     BIGINT      NOT NULL,
  rebuilt_at    TIMESTAMPTZ NOT NULL,
  -- NULL locally after a rebuild; stamped by ingest/sync_reference_tables.py
  -- once the pushed copy's recomputed hash matches.
  synced_at     TIMESTAMPTZ
);
