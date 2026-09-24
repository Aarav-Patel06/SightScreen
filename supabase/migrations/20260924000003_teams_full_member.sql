-- Full Member status, and the live rows that never transitioned.
--
-- Two changes, both feeding UI Phase 2 step 1:
--   1. teams.full_member - the flag the landing hero and the /matches
--                          filter order by
--   2. a status backfill - four rows stuck at 'live' since before the
--                          Session 3 worker fix

-- 1. Which teams are ICC Full Members --------------------------------------
--
-- Flagged by EXACT name. The obvious hazard with a prefix or ILIKE match is
-- that "India A", "India U19" and "India Women" would be swept in with
-- "India". That hazard was checked rather than assumed: a sweep of all 347
-- rows for (women|u-?19|u-?23|under|\sA$|emerging|academy|XI) returns only
-- Africa XI, Asia XI, ICC World XI, and three regex false positives
-- (Mexico, Sydney Thunder, Sylhet Thunder). No A-team, age-group or
-- women's side exists in this corpus at all. Exact names are therefore both
-- safe and honest about what is being asserted.
--
-- The three composite invitational sides above are deliberately FALSE: they
-- are selections, not members.
ALTER TABLE teams
  ADD COLUMN IF NOT EXISTS full_member BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE teams SET full_member = TRUE
WHERE name IN (
  'Australia', 'England', 'India', 'New Zealand', 'Pakistan', 'South Africa',
  'Sri Lanka', 'West Indies', 'Bangladesh', 'Zimbabwe', 'Afghanistan', 'Ireland'
);

-- ASSERTED AS AN INVARIANT, NOT AS A COUNT. The obvious check here is
-- `count(*) = 11`, and the first version of this migration used it. It was
-- wrong for a reason worth keeping: **migrations build schema, and this
-- database may hold no data yet.** `tests/features/test_asof_summary.py`
-- creates a scratch database and runs every migration into it; with an empty
-- `teams` the count is 0 and the whole migration chain fails. A migration
-- that only applies to a populated database is not a migration.
--
-- So the assertion is the thing the UPDATE above is actually responsible for:
-- no Full Member that IS present may be left unflagged. Vacuously true on an
-- empty table, and the real check on a loaded one.
--
-- ELEVEN, NOT TWELVE, on the real databases. There are twelve ICC Full
-- Members and the list above names all twelve, but **Afghanistan has no row
-- in this corpus** - `name ILIKE '%afghan%'` returns nothing, and every one
-- of the 347 teams present appears in at least one match, so there is no
-- orphan row either. Inserting a team with no matches to make the count reach
-- twelve would be inventing data to satisfy an assertion. The count belongs
-- in a test that runs against real data, and it is in
-- tests/db/test_full_member.py, which asserts the flagged SET against the
-- canonical twelve in both directions.
DO $$
DECLARE unflagged TEXT;
BEGIN
  SELECT string_agg(name, ', ' ORDER BY name) INTO unflagged
  FROM teams
  WHERE NOT full_member AND name IN (
    'Australia', 'England', 'India', 'New Zealand', 'Pakistan', 'South Africa',
    'Sri Lanka', 'West Indies', 'Bangladesh', 'Zimbabwe', 'Afghanistan', 'Ireland'
  );
  IF unflagged IS NOT NULL THEN
    RAISE EXCEPTION 'these Full Members are present but not flagged: %', unflagged;
  END IF;
  RAISE NOTICE 'full_member set on % team(s)', (SELECT count(*) FROM teams WHERE full_member);
END $$;

COMMENT ON COLUMN teams.full_member IS
  'ICC Full Member status as of 2026-09. Eleven of the twelve are flagged: Afghanistan is a Full Member but has no row in this corpus. Africa XI, Asia XI and ICC World XI are deliberately FALSE - they are selections, not members.';

-- 2. The four rows stuck at 'live' -----------------------------------------
--
-- UI-PHASE-2.md section 2.3 prescribes "a match with a winner is complete".
-- That rule does not fit these rows: ids 1, 2, 3 and 1000001 all have
-- winner IS NULL, while 9337/9339/13143 - the rows that DO have winners -
-- are already 'complete'. Applying it would change nothing.
--
-- The rule that does fit: a row still marked 'live' whose start_time is more
-- than 24 hours in the past cannot still be live. The four qualifying rows
-- started between 16 and 20 September.
--
-- DELIBERATELY NOT A RECURRING SWEEP. The recurring mechanism is Session 3's
-- LivePredictor.record_status plus the run_once change that observes status
-- on empty polls; before those, live_loop read match-end from an in-memory
-- snapshot and never wrote it back, which is why these four exist. A standing
-- age-based sweep would quietly paper over that worker failing again, so this
-- is a one-off correction of rows that predate the fix.
-- THIS MIGRATION RUNS ON BOTH DATABASES and the correct count differs
-- between them: the serving database has these 4 rows, the local corpus has
-- 0 (all 13,143 of its matches are 'complete'). So the guard is an upper
-- bound rather than an equality. That is the direction standing rule 15
-- actually protects: the danger in an UPDATE is touching MORE rows than
-- intended. More than four would mean something new is producing stuck rows,
-- which is a question, not a backfill.
DO $$
DECLARE stale INT;
BEGIN
  SELECT count(*) INTO stale FROM matches
  WHERE status = 'live' AND start_time < now() - interval '24 hours';
  IF stale > 4 THEN
    RAISE EXCEPTION
      'expected at most 4 stale live rows, found %. Refusing to guess: a '
      'larger count means the worker is still failing to record status, and '
      'backfilling would hide that.', stale;
  END IF;
  RAISE NOTICE 'correcting % stale live row(s)', stale;
END $$;

UPDATE matches SET status = 'complete'
WHERE status = 'live' AND start_time < now() - interval '24 hours';

-- Postcondition, asserted rather than assumed. Holds on both databases.
DO $$
DECLARE remaining INT;
BEGIN
  SELECT count(*) INTO remaining FROM matches
  WHERE status = 'live' AND start_time < now() - interval '24 hours';
  IF remaining <> 0 THEN
    RAISE EXCEPTION 'still % stale live row(s) after the backfill', remaining;
  END IF;
END $$;
