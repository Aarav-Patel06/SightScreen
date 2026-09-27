-- The rest of Cricsheet's `outcome`, so a completed match can say what
-- actually happened: "England won by 31 runs", "by 4 wickets", "Match tied ·
-- Punjab won the super over".
--
-- WHY. `matches` kept only `winner` and `result_method`, so the match page
-- could say who won but not by how much, and fell back to the last state the
-- model saw ("chase needed 32 off 1"), which is not a result. Measured across
-- the corpus's outcome shapes on 2026-09-27: by.wickets 6,336; by.runs 5,771;
-- D/L-decided 573; VJD 5; no result 312; ties 142 - 81 then decided by a
-- super over (`eliminator`), 2 by a bowl-out, 59 left tied; 'Awarded' 3;
-- 'Lost fewer wickets' 1.
--
-- All nullable, and NULL means "not recorded", never zero. Populated by the
-- corpus loader (ingest/cricsheet.py) for every new match, mirrored to
-- Supabase by the replay mirror and the daily Cricsheet job, and backfilled
-- once by `python -m ingest.backfill_outcomes`.

ALTER TABLE matches
  -- outcome.by.runs / outcome.by.wickets. At most one is set.
  ADD COLUMN IF NOT EXISTS win_by_runs     INT,
  ADD COLUMN IF NOT EXISTS win_by_wickets  INT,
  -- outcome.method verbatim: 'D/L', 'VJD', 'Awarded', 'Lost fewer wickets'.
  -- result_method stays as it is; it collapses every method to 'dls' and the
  -- training split and Elo read it, so it is not the column to widen.
  ADD COLUMN IF NOT EXISTS outcome_method  TEXT,
  -- A tie that was then decided: outcome.eliminator (a super over) or
  -- outcome.bowl_out. `winner` stays NULL for a tie, as it always has.
  ADD COLUMN IF NOT EXISTS tie_winner      INT REFERENCES teams(team_id),
  ADD COLUMN IF NOT EXISTS tie_decided_by  TEXT;

ALTER TABLE matches DROP CONSTRAINT IF EXISTS matches_tie_decided_by_check;
ALTER TABLE matches ADD CONSTRAINT matches_tie_decided_by_check
  CHECK (tie_decided_by IS NULL OR tie_decided_by IN ('super_over', 'bowl_out'));
