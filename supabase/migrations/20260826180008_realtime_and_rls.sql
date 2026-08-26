-- Realtime + RLS (SPEC.md section 7.4). Without RLS anon-SELECT policies,
-- Realtime silently delivers nothing to anonymous clients - a failure mode
-- that looks exactly like a broken subscription (section 13).
--
-- Self-bootstrapping (Phase 0 session 3, Decision 1): anon/authenticated
-- roles and the supabase_realtime publication already exist on Supabase but
-- not on local Postgres. Rather than splitting into shared-vs-Supabase-only
-- migration sets (which supabase db push has no built-in way to apply
-- selectively per target) or a separate docker-compose init script (a
-- second artifact to keep in sync, only runs once against an empty data
-- volume), this block creates them if missing so the exact same migration
-- file applies unmodified to both databases - one source of truth, per
-- section 5's "schemas never diverge" requirement.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    CREATE PUBLICATION supabase_realtime;
  END IF;
END $$;

-- Anonymous SELECT on every table the browser reads directly (section 7.4
-- names predictions/matches at minimum; players/venues/teams added here too
-- since the browser needs them to render any match/player page - judgment
-- call from Phase 0 session 3, flagged for review). No write policies for
-- anon/authenticated anywhere - all writes go through the service-role key,
-- which bypasses RLS by design. deliveries/match_states/elo_ratings/etc.
-- get no RLS policy in Phase 0: nothing reads them from a browser yet, and
-- the full corpus never leaves local Postgres (section 2.1).
GRANT USAGE ON SCHEMA public TO anon, authenticated;

ALTER TABLE public.predictions ENABLE ROW LEVEL SECURITY;
CREATE POLICY predictions_anon_select ON public.predictions FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.predictions TO anon, authenticated;

ALTER TABLE public.matches ENABLE ROW LEVEL SECURITY;
CREATE POLICY matches_anon_select ON public.matches FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.matches TO anon, authenticated;

ALTER TABLE public.players ENABLE ROW LEVEL SECURITY;
CREATE POLICY players_anon_select ON public.players FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.players TO anon, authenticated;

ALTER TABLE public.venues ENABLE ROW LEVEL SECURITY;
CREATE POLICY venues_anon_select ON public.venues FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.venues TO anon, authenticated;

ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;
CREATE POLICY teams_anon_select ON public.teams FOR SELECT TO anon, authenticated USING (true);
GRANT SELECT ON public.teams TO anon, authenticated;

-- Only predictions needs live push (section 7.4's literal wording) - matches
-- etc. are read via normal queries, not subscriptions.
ALTER PUBLICATION supabase_realtime ADD TABLE public.predictions;
