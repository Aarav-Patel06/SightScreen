-- The accuracy page's storage (SPEC.md sections 8.1 and 8.5, Phase 3 s2).
--
-- Four changes, each with its own reason:
--   1. predictions.source  - the backfilled/live distinction, recorded by
--                            the producer rather than inferred later
--   2. calibration_runs    - where the daily monitor puts its report, and
--                            where /accuracy reads it from
--   3. anon policies       - the page is public; two tables it needs are
--                            currently readable by nobody but the service
--   4. a sequence bump     - two id spaces are sharing one column

-- 1. Which population a prediction belongs to ------------------------------
--
-- The accuracy page must never mix these. The 100 replayed matches are the
-- section 9.1 TEST split, which Phase 1 used to select the shipped
-- calibrator - so they are in-sample with respect to model selection and
-- there is no held-out data behind their numbers at all. A live prediction
-- was made before the result existed. Those are different claims and they
-- get different panels.
--
-- Recorded by the producer, NOT derived. The obvious derivation - compare
-- created_at to the match's start_time - misclassifies a replay of a match
-- played today, and would be exactly the kind of retrofit that gets found
-- later by someone asking "is this measuring itself?".
ALTER TABLE predictions
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'backfill';

ALTER TABLE predictions
  DROP CONSTRAINT IF EXISTS predictions_source_check;
ALTER TABLE predictions
  ADD CONSTRAINT predictions_source_check CHECK (source IN ('backfill', 'live'));

COMMENT ON COLUMN predictions.source IS
  'backfill = replayed from the corpus after the fact; live = predicted before the result existed. Written by the producer, never inferred.';

-- Existing rows: everything is backfill except what the live worker wrote.
-- A worker-created match is identifiable by its provider id - the replay
-- mirror copies corpus rows and never sets one.
UPDATE predictions SET source = 'live'
WHERE match_id IN (
  SELECT match_id FROM matches WHERE external_ids ? 'cricketdata'
);

-- 2. The monitor's output --------------------------------------------------
--
-- The page renders this rather than computing it. The reliability figures
-- are match-clustered bootstraps - 2,000 resamples across 10 deciles - and
-- eval/metrics.py already implements them. Recomputing in TypeScript per
-- request would be both slow and a second implementation of the statistics,
-- which is the divergence this project keeps getting bitten by.
CREATE TABLE IF NOT EXISTS calibration_runs (
  run_id        BIGSERIAL PRIMARY KEY,
  computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  model_version TEXT NOT NULL REFERENCES model_versions(model_version),
  -- The whole section 8.1 report: per-population reliability tables, Brier
  -- with clustered CIs, per-phase buckets, baseline comparisons, the refit
  -- decision and why. JSONB because its shape is the monitor's business and
  -- will grow; the page narrows it in web/lib/accuracy.ts.
  report        JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS calibration_runs_computed_at_idx
  ON calibration_runs (computed_at DESC);

COMMENT ON TABLE calibration_runs IS
  'One row per daily calibration monitor run (SPEC.md 8.1). The accuracy page reads the most recent.';

-- 3. The page is public ----------------------------------------------------
--
-- Three statements each, exactly as 20260826180008 does for the five
-- browser-readable tables: GRANT, ENABLE RLS, and a SELECT policy. Opening
-- a table to the browser is a deliberate act and this is what it looks like.
--
-- prediction_outcomes was revoked by 20260918000001's deny-by-default
-- sweep. It holds a Brier and a log loss per prediction and an actual
-- outcome - all of which the accuracy page exists to show publicly.
GRANT SELECT ON prediction_outcomes TO anon, authenticated;
ALTER TABLE prediction_outcomes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prediction_outcomes_anon_select ON prediction_outcomes;
CREATE POLICY prediction_outcomes_anon_select ON prediction_outcomes
  FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON calibration_runs TO anon, authenticated;
ALTER TABLE calibration_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS calibration_runs_anon_select ON calibration_runs;
CREATE POLICY calibration_runs_anon_select ON calibration_runs
  FOR SELECT TO anon, authenticated USING (true);

-- Writes stay with the service role, matching 20260918000002's treatment of
-- the other browser-readable tables.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON prediction_outcomes, calibration_runs FROM anon, authenticated;

-- 4. Two id spaces were sharing one column ---------------------------------
--
-- `matches.match_id` receives rows from two places. The replay mirror
-- copies corpus rows with their LOCAL ids (6970, 8154, ...). The live
-- worker's _ensure_match_row inserts without an id, so the SERIAL assigns
-- one starting from 1 - and local match_id 3 is a 2017 Pakistan-Australia
-- ODI while Supabase match_id 3 is a CPL 2026 match. Anything that reads a
-- Supabase match_id and looks it up in the corpus gets a different match,
-- silently. models/resolve_outcomes.py is exactly such a thing, and
-- resolving live predictions is the next thing anyone would do.
--
-- Two defences. This is the structural one: start the sequence above the
-- corpus, so a newly tracked live match can never be assigned an id that
-- means something else locally. The behavioural one is the identity check
-- in resolve_outcomes.py, which protects the three rows that already exist
-- down at 1, 2 and 3 - deliberately NOT renumbered here, because their
-- predictions reference them and a rewrite would be a bigger risk than the
-- guard it replaces.
SELECT setval('matches_match_id_seq', GREATEST(1000000, (SELECT max(match_id) FROM matches)), true);
