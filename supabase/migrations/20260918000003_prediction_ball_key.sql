-- A natural ball key on predictions, so the log can be written twice
-- (SPEC.md section 15's 2026-09-18 entry, Phase 3 session 1).
--
-- The problem this closes, measured rather than argued: driving match 9339
-- through the deployed service posted 125 balls and produced 128 rows, three
-- byte-identical pairs with consecutive prediction_ids - a retried POST
-- against an endpoint with no unique key. Invisible on a curve plotted in
-- prediction_id order. Not invisible to a calibration bin, which counts the
-- duplicate twice against one outcome.
--
-- Why not delivery_id, which section 5.4 already provides for exactly this:
-- it carries a FOREIGN KEY into `deliveries`, and `deliveries` is empty on
-- Supabase by section 2.1's design and must stay that way. Any non-NULL
-- delivery_id here would violate that FK. The column stays, unused, meaning
-- what it says.
--
-- (innings, over_num, ball_in_over) instead, because:
--   * it mirrors deliveries' own UNIQUE (match_id, innings, over_num,
--     ball_in_over) - the same identity the corpus already uses;
--   * it is provider-independent. ingest/live_client.py's `Delivery` carries
--     exactly these three fields, so the live worker and a replay produce
--     the same key without either of them knowing a local delivery_id;
--   * balls_bowled alone cannot work. Extras repeat it with a different
--     payload - match 9339 is 125 deliveries across 111 legal balls.
--
-- The index is PARTIAL on innings IS NOT NULL. Rows written before this
-- migration have no ball key and cannot be given one: their payload carries
-- balls_bowled, which is exactly the ambiguous value above. They are left
-- alone, and every Phase 3 reader filters on innings IS NOT NULL. See the
-- note at the bottom for what those rows are.

ALTER TABLE predictions
  ADD COLUMN IF NOT EXISTS innings      SMALLINT,
  ADD COLUMN IF NOT EXISTS over_num     SMALLINT,
  ADD COLUMN IF NOT EXISTS ball_in_over SMALLINT;

COMMENT ON COLUMN predictions.innings IS
  'Ball key, with over_num/ball_in_over. NULL on rows written before Phase 3 session 1.';
COMMENT ON COLUMN predictions.over_num IS
  'Ball key: 0-indexed over, matching deliveries.over_num.';
COMMENT ON COLUMN predictions.ball_in_over IS
  '1-indexed position within the over counting every delivery, matching deliveries.ball_in_over.';

-- model_version is in the key deliberately: two models scoring the same ball
-- are two legitimate predictions, and section 8.4's shadow deployment depends
-- on being able to write both. prediction_type likewise - a win_prob and a
-- score_proj for one ball do not collide.
CREATE UNIQUE INDEX IF NOT EXISTS predictions_ball_key_uniq
  ON predictions (match_id, model_version, prediction_type, innings, over_num, ball_in_over)
  WHERE innings IS NOT NULL;

-- What the pre-Phase-3 rows are, recorded here so nobody later mistakes them
-- for a prediction log (counted 2026-09-18):
--   match 9337 - 125 rows, 125 distinct. The session 5 acceptance replay.
--   match 9339 - 128 rows, 125 distinct. The same, plus the 3 retry duplicates
--                that motivated this migration.
--   match 13143 - 121 rows, 13 DISTINCT. This is not a replay at all: it is
--                the 12-ball deploy smoke test, run about ten times across
--                sessions 4a, 4b and 5. It must never reach a calibration bin.
-- None of them have outcomes, and Phase 3's resolution job only resolves
-- matches named in its manifest, so they stay inert.
