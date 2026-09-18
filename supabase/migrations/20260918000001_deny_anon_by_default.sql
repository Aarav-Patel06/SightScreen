-- Close the anon read hole on Supabase (Phase 2 session 5, Decision 1).
--
-- 20260826180008 granted anon SELECT on exactly five tables - predictions,
-- matches, players, venues, teams - and its comment states the intent
-- plainly: "deliveries/match_states/elo_ratings/etc. get no RLS policy in
-- Phase 0: nothing reads them from a browser yet."
--
-- That intent was true on LOCAL Postgres and false on Supabase. Hosted
-- Supabase grants `anon` and `authenticated` SELECT on tables in `public` by
-- default, so every table created WITHOUT an explicit policy was readable by
-- anyone holding the publishable key - which is, by design, public.
--
-- Measured on 2026-09-18 by web/scripts/check-anon-access.mjs's negative
-- control, comparing both databases:
--
--   table                  local anon SELECT   Supabase anon SELECT
--   deliveries             no                  YES
--   elo_asof_summary       no                  YES   (25,290 rows)
--   elo_ratings            no                  YES
--   match_states           no                  YES
--   model_versions         no                  YES
--   player_aliases         no                  YES   (18,468 rows)
--   player_state           no                  YES
--   prediction_outcomes    no                  YES
--   reference_sync_state   no                  YES
--   team_aliases           no                  YES
--   unresolved_entities    no                  YES
--   venue_aliases          no                  YES
--   venue_asof_summary     no                  YES   (10,508 rows)
--
-- No credentials were exposed and the corpus is not on Supabase, but the
-- entire derived feature set and the alias tables were world-readable, and
-- the exposure would have grown the moment Session 5 or Phase 3 wrote live
-- deliveries, match_states or prediction_outcomes there.
--
-- Two defences, deliberately both:
--
--   REVOKE removes the privilege that actually granted access. On its own it
--   is undone by anyone re-running a blanket GRANT.
--
--   ENABLE ROW LEVEL SECURITY with NO policy denies by default and keeps
--   denying through a future GRANT, because a granted privilege on an
--   RLS-enabled table with no matching policy still yields no rows.
--
-- Safe for the services: verified on 2026-09-18 that both `postgres` (the
-- role the session pooler connects as) and `service_role` have
-- rolbypassrls = true, so the Railway worker, the FastAPI service and every
-- admin script are unaffected. Locally `postgres` is superuser and bypasses
-- for the same reason. Verified by pg_roles, not assumed.
--
-- Adding a table to this list is the DEFAULT for anything new. Opening one to
-- the browser is the deliberate act, and it takes three statements -
-- GRANT SELECT, ENABLE ROW LEVEL SECURITY, and a FOR SELECT TO anon policy -
-- exactly as 20260826180008 does for the five browser-readable tables.

DO $$
DECLARE
  target text;
BEGIN
  FOR target IN SELECT unnest(ARRAY[
    'deliveries',
    'elo_asof_summary',
    'elo_ratings',
    'match_states',
    'model_versions',
    'player_aliases',
    'player_state',
    'prediction_outcomes',
    'reference_sync_state',
    'team_aliases',
    'unresolved_entities',
    'venue_aliases',
    'venue_asof_summary'
  ])
  LOOP
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon, authenticated', target);
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', target);
  END LOOP;
END $$;
