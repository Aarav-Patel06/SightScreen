-- TEMPLATE. Not a migration - nothing applies this file. Copy the blocks you
-- need into a real one.
--
-- It exists because the rule below has now been learned three times, and the
-- first two times it was written down somewhere nobody re-read.
--
-- ==========================================================================
-- THE ONE THING TO GET RIGHT: REVOKE BEFORE YOU GRANT
-- ==========================================================================
--
-- Hosted Supabase grants `anon` and `authenticated` ALL SEVEN privileges on
-- every new table in `public`, by default, at CREATE TABLE time. Local
-- Postgres grants nothing. So a migration that says
--
--     CREATE TABLE thing (...);
--     GRANT SELECT ON thing TO anon, authenticated;
--
-- produces a table that is read-only on your laptop and fully WRITABLE by
-- anyone holding the publishable key - which is, by design, public. The
-- GRANT looks like it is defining the access. It is not; it is adding one
-- privilege to six that are already there.
--
-- This is not hypothetical and it is not rare:
--
--   2026-09-18  Thirteen tables found world-readable on Supabase and not
--               locally. Closed by 20260918000001, which argued the rule at
--               length in its own header.
--   2026-09-20  agent_query_log created. Same defect.
--   2026-09-21  agent_usage created. Same defect - and that table holds the
--               daily spend cap, so the privileges included UPDATE.
--   2026-09-24  player_index and player_career_summary created. Same defect,
--               by someone who had read 20260918000001 that same week.
--
-- The argument was correct and stated clearly, in a file nobody opens when
-- writing the next migration. That is the same failure as a check that never
-- runs: correctness that is not in the path does not protect anything. Hence
-- this file, which sits in the directory you are already in.
--
-- TWO DEFENCES, DELIBERATELY BOTH (20260918000001's words):
--
--   REVOKE removes the privilege that actually grants access. On its own it
--   is undone by anyone re-running a blanket GRANT.
--
--   ENABLE ROW LEVEL SECURITY with no matching policy denies by default and
--   keeps denying through a future GRANT, because a granted privilege on an
--   RLS-enabled table with no policy still yields no rows.
--
-- Neither alone. In the 2026-09-24 case RLS was the only thing holding, and
-- nothing was exposed - but one permissive policy added later for a
-- dashboard would have turned a latent defect into a live one with no other
-- change, and nobody would have connected the two commits.
--
-- HOW YOU FIND OUT YOU GOT IT WRONG: tests/db/test_schema_parity.py's
-- `grants` snapshot compares `anon` and `authenticated` across both
-- databases and fails on any asymmetry. Run it. It is the gate that caught
-- all three of the above, on the day it was finally allowed to run.


-- ==========================================================================
-- A) A table nothing in a browser should ever see. THE DEFAULT.
-- ==========================================================================
-- Most tables. Anything a server reads with the secret key, anything derived,
-- anything operational. Say no, then say no again.

CREATE TABLE IF NOT EXISTS example_private (
  example_id  BIGSERIAL PRIMARY KEY,
  payload     JSONB NOT NULL
);

REVOKE ALL ON TABLE public.example_private FROM anon, authenticated;
ALTER TABLE public.example_private ENABLE ROW LEVEL SECURITY;
-- No policy. RLS with no policy denies everything, which is the intent.


-- ==========================================================================
-- B) A table the browser reads. REVOKE FIRST, then grant back exactly SELECT.
-- ==========================================================================
-- The order matters for readability, not for the result: revoking first makes
-- the statement "nothing, except this" instead of "this, plus whatever
-- Supabase already decided".

CREATE TABLE IF NOT EXISTS example_public (
  example_id  BIGSERIAL PRIMARY KEY,
  label       TEXT NOT NULL
);

REVOKE ALL ON TABLE public.example_public FROM anon, authenticated;
GRANT SELECT ON TABLE public.example_public TO anon, authenticated;

ALTER TABLE public.example_public ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS example_public_anon_select ON public.example_public;
CREATE POLICY example_public_anon_select ON public.example_public
  FOR SELECT TO anon, authenticated USING (true);

-- Belt and braces even here: the GRANT above is SELECT-only, so this is a
-- no-op today. It stops being a no-op the moment anyone adds a privilege
-- above without thinking about this line.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON TABLE public.example_public FROM anon, authenticated;


-- ==========================================================================
-- BEFORE YOU COMMIT
-- ==========================================================================
--
--   1. Apply to BOTH databases. supabase/apply_migrations.py does both;
--      applying to one is how they diverge.
--
--   2. Run the parity gate. It is the only thing that checks this:
--        cd api && pytest ../tests/db/test_schema_parity.py -q
--
--   3. If the table is browser-readable, prove it from the browser's side:
--        cd web && npm run check:anon
--      That script's negative control is the honest half - it asserts that
--      tables which MUST be denied actually are.
--
--   4. If a Supabase-visible table was added, web/lib/types.ts is now stale.
--      The pre-push hook checks this; enable it once per clone with
--        git config core.hooksPath .githooks
