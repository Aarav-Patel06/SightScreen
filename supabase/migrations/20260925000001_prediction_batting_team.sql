-- Which side a win probability belongs to, stored with the probability.
--
-- `payload.p` is the BATTING side's chance (the model is trained on
-- match_states.batting_team_won). Nothing on the serving database said which
-- side that was: batting order lives only in `deliveries`, which stays local
-- by design and has 0 rows on Supabase. So every surface guessed, and the
-- match page guessed `matches.team_a` - the side Cricsheet lists first, which
-- is the side that batted FIRST. On 340 of the 342 matches with predictions
-- the page named the bowling side: match 8429 read "England to win 1%" while
-- India needed 32 off 1.
--
-- NULL means unknown, and the UI renders "batting side" for it rather than a
-- name. It is never filled by inference from the toss or from table order.
-- Every writer already holds the value (the as-of features need it); this
-- column stops them discarding it. Existing rows are backfilled from local
-- `deliveries` by `python -m ingest.backfill_batting_team`, not here - the
-- source table is on the other database.
--
-- Readable by anon through the table-level GRANT SELECT in 20260826180008.

ALTER TABLE predictions
  ADD COLUMN IF NOT EXISTS batting_team_id INT REFERENCES teams(team_id);

COMMENT ON COLUMN predictions.batting_team_id IS
  'The side batting when this prediction was made; payload.p is its win '
  'probability. NULL = unknown - render "batting side", never a guess.';

-- ---------------------------------------------------------------------------
-- matches.status, set from the data.
--
-- The worker never wrote 'complete': CricketDataClient.list_live_matches
-- skipped every ended snapshot, so the loop never saw a match again once the
-- provider said it was over. Fixed in the worker; this sets every existing
-- row from what the data already says. A match is complete if it has a
-- result, or if its last innings-2 prediction shows the chase decided.
--
-- Measured before writing this (read-only, 2026-09-25): 0 rows qualify on
-- either database - the local corpus has 13,143 'complete' rows and nothing
-- else, Supabase 344. The one-off in 20260924000003 already corrected the
-- four stuck rows. It is still run, so the rule is recorded and re-runnable.
DO $$
DECLARE changing INT;
BEGIN
  SELECT count(*) INTO changing FROM matches m
  WHERE m.status <> 'complete' AND (
    m.winner IS NOT NULL OR m.result_method IS NOT NULL OR EXISTS (
      SELECT 1 FROM (
        SELECT payload FROM predictions p
        WHERE p.match_id = m.match_id AND p.prediction_type = 'win_prob' AND p.innings = 2
        ORDER BY p.prediction_id DESC LIMIT 1
      ) last
      WHERE (last.payload->>'runs_required')::int <= 0
         OR (last.payload->>'wickets')::int >= 10
         OR (last.payload->>'balls_remaining')::int <= 0));
  RAISE NOTICE 'marking % match row(s) complete from the data', changing;
END $$;

UPDATE matches m SET status = 'complete'
WHERE m.status <> 'complete' AND (
  m.winner IS NOT NULL OR m.result_method IS NOT NULL OR EXISTS (
    SELECT 1 FROM (
      SELECT payload FROM predictions p
      WHERE p.match_id = m.match_id AND p.prediction_type = 'win_prob' AND p.innings = 2
      ORDER BY p.prediction_id DESC LIMIT 1
    ) last
    WHERE (last.payload->>'runs_required')::int <= 0
       OR (last.payload->>'wickets')::int >= 10
       OR (last.payload->>'balls_remaining')::int <= 0));
