-- Float columns that cross the local/Supabase boundary become NUMERIC
-- (Phase 2 session 4 housekeeping; the finding is session 3's, recorded in
-- SPEC.md section 15 and docs/phase2-session3-asof-sync.md).
--
-- A float's TEXT output depends on the session's extra_float_digits, and
-- psycopg decodes in text mode. Supabase's pooler hands out sessions with 0
-- where local Postgres uses the PG12+ default of 1, so the SAME stored
-- float4 is decoded as 1496.445 on one side and 1496.44 on the other.
-- NUMERIC has no such session knob.
--
-- Converted here are only the columns where a value is written on one side
-- of the boundary and read on the other. Left alone, deliberately:
--
--   match_states.current_run_rate / required_run_rate / rrr_minus_crr /
--   dls_resources_pct  - these look like the scariest case because they are
--   model features, but nothing on a serving path inserts into match_states
--   (the live worker's only Supabase write is INSERT INTO matches). They are
--   written by the local rebuild and read by local training, so they never
--   make the trip. Converting would mean altering 3.78M local rows AND
--   handing Decimal instead of float to eval/splits.py's numpy arrays - a
--   training dtype change wearing a deployment fix's clothing. If Session 5
--   starts writing live match_states to Supabase, convert first.
--
--   matches.target_overs - does cross, but provably benign: its values carry
--   at most three significant digits (20, 50, 19.3, 21), so float4's text
--   output is identical at extra_float_digits 0 and 1. Pinned by
--   tests/db/test_float_boundary.py rather than asserted in a comment, so a
--   six-significant-digit value would fail rather than silently round.
--
--   elo_ratings.rating - local only; elo_asof_summary carries the served
--   copy and is already NUMERIC.
--
--   unresolved_entities.best_score - a RapidFuzz diagnostic read by a human
--   in review_queue.py. Three significant figures is not a correctness
--   question here.
--
-- The casts go via ::text::numeric, NOT ::numeric. Postgres's float4 ->
-- numeric conversion is hardcoded to FLT_DIG (6) significant digits and
-- ignores extra_float_digits entirely - it turns 1601.9048 into 1601.9.
-- Session 3 found that the hard way. These tables are empty or near-empty
-- today so nothing would have been lost, but the correct cast is the one
-- that stays correct when someone re-runs this against real data.

SET extra_float_digits = 1;

-- Accuracy page (Phase 3) reads these from Supabase; training writes them.
ALTER TABLE model_versions
  ALTER COLUMN test_brier    TYPE NUMERIC USING test_brier::text::numeric,
  ALTER COLUMN test_log_loss TYPE NUMERIC USING test_log_loss::text::numeric;

-- Written by section 8's scoring job, read by the accuracy page and the
-- drift job - both from Supabase.
ALTER TABLE prediction_outcomes
  ALTER COLUMN brier    TYPE NUMERIC USING brier::text::numeric,
  ALTER COLUMN log_loss TYPE NUMERIC USING log_loss::text::numeric;

-- The worst case and the cheapest fix. A Kalman update (section 6.5) reads
-- its own prior from Supabase and writes the posterior back, so rounding on
-- every read compounds across updates rather than staying cosmetic. The
-- table is empty until Phase 5, so converting now costs nothing and
-- converting later costs a data migration.
ALTER TABLE player_state
  ALTER COLUMN bat_ability_mean  TYPE NUMERIC USING bat_ability_mean::text::numeric,
  ALTER COLUMN bat_ability_sd    TYPE NUMERIC USING bat_ability_sd::text::numeric,
  ALTER COLUMN bowl_ability_mean TYPE NUMERIC USING bowl_ability_mean::text::numeric,
  ALTER COLUMN bowl_ability_sd   TYPE NUMERIC USING bowl_ability_sd::text::numeric;

-- Carries KNOWN TRAIN/SERVE SKEW to the serving side, so the skew is visible
-- from the database the accuracy page reads rather than only in SPEC.md.
ALTER TABLE model_versions
  ADD COLUMN IF NOT EXISTS notes TEXT;
