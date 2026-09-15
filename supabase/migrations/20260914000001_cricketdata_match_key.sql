-- Phase 2 session 2: the live adapter maps a CricketData match UUID to our
-- canonical match_id through matches.external_ids->>'cricketdata' (the same
-- mechanism 20260826180002 established for Cricsheet, per SPEC.md section
-- 5.2's own column comment).
--
-- That migration created the partial unique index for the 'cricsheet' key
-- only. Without the equivalent here, the live path's lookup is an unindexed
-- scan of the whole matches table on every poll, and - worse - nothing stops
-- two workers (or one worker restarted mid-poll) inserting a second row for
-- the same provider match. The index is what actually makes
-- CricketDataClient._ensure_match_row idempotent, rather than idempotent
-- only while nothing runs concurrently.

CREATE UNIQUE INDEX IF NOT EXISTS matches_external_cricketdata_key
  ON matches ((external_ids->>'cricketdata'))
  WHERE external_ids->>'cricketdata' IS NOT NULL;
