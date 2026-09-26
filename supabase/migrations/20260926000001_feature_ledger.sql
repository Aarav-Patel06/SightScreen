-- feature_ledger: the per-match inputs of the as-of summaries, so they can be
-- rebuilt where the corpus is not (the daily Cricsheet Action).
--
-- WHY. elo_asof_summary and venue_asof_summary are rebuilt from `matches`,
-- `match_states` and `deliveries`, none of which reach Supabase (SPEC.md
-- section 2.1). A GitHub runner that ingests a new match therefore cannot
-- advance them - and advancing them incrementally is not exact: Elo carries
-- float64 in memory but the summary stores float4, and a match Cricsheet
-- publishes late lands BEFORE matches already counted.
--
-- So the job keeps, per match, exactly the columns the two rebuilds read, and
-- re-runs the unchanged rebuild code over all of them each time:
--
--   features.elo.recompute_format  reads  format, start_time, match_id,
--                                         team_a, team_b, winner,
--                                         result_method, status
--   the venue summary              reads  venue_id, start_time, and two
--                                         per-match values produced by the
--                                         shared fragments in
--                                         features/venue_stats.py:
--     chase_batting_team_won  = CHASE_PER_MATCH_SELECT's batting_team_won
--                               (NULL: no such row - tie, no result, no chase)
--     first_innings_runs      = FIRST_INNINGS_PER_MATCH_SELECT's innings_total
--                               (NULL: no innings-1 deliveries)
--
-- One row per corpus match (~13k, ~2MB) plus one per match the daily job
-- adds. Built and published from the corpus by `features.feature_ledger
-- publish`; appended to by `ingest.daily_cricsheet`.
--
-- No foreign keys, deliberately. It is a derived snapshot like the summaries,
-- and most of its match_ids have no Supabase `matches` row at all - the full
-- corpus never crosses (section 2.1).
--
-- match_id is the corpus id for corpus matches, and the Supabase-assigned id
-- (>= 1,000,000, 20260919000001) for a match the daily job added first; the
-- local catch-up adopts that id, so Elo's same-day tie-break
-- (ORDER BY start_time, match_id) is identical on both sides.

CREATE TABLE IF NOT EXISTS feature_ledger (
  match_id                INT         PRIMARY KEY,
  cricsheet_id            TEXT        NOT NULL UNIQUE,
  format                  TEXT        NOT NULL,
  start_time              TIMESTAMPTZ NOT NULL,
  team_a                  INT,
  team_b                  INT,
  winner                  INT,
  result_method           TEXT,
  status                  TEXT        NOT NULL,
  venue_id                INT,
  chase_batting_team_won  BOOLEAN,
  first_innings_runs      BIGINT
);

-- Server-only, like the summaries it feeds (20260918000001's pattern A).
REVOKE ALL ON TABLE public.feature_ledger FROM anon, authenticated;
ALTER TABLE public.feature_ledger ENABLE ROW LEVEL SECURITY;
-- No policy. RLS with no policy denies everything, which is the intent.
