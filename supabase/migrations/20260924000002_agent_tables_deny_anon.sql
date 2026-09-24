-- Restore the second defence on the two agent tables.
--
-- WHAT WAS WRONG. `agent_query_log` (20260920000001) and `agent_usage`
-- (20260921000001) were created without revoking Supabase's default
-- privileges, so on the hosted database `anon` and `authenticated` held all
-- seven privileges on both - SELECT, INSERT, UPDATE, DELETE, TRUNCATE,
-- REFERENCES, TRIGGER - while locally they held none.
--
-- That is precisely the divergence 20260918000001 exists to close, and its
-- own comment explains the mechanism: "Hosted Supabase grants `anon` and
-- `authenticated` SELECT on tables in `public` by default, so every table
-- created WITHOUT an explicit policy was readable by anyone holding the
-- publishable key - which is, by design, public."
--
-- NOTHING WAS EXPOSED, and the reason matters. Both tables have RLS enabled
-- with zero policies, and a granted privilege on an RLS-enabled table with
-- no matching policy still yields no rows. Verified on 2026-09-24 with the
-- real publishable key: `GET /rest/v1/agent_usage` returned `[]` to anon and
-- rows to the service key.
--
-- So this is a latent defect rather than a live one - and 20260918000001
-- already argued why that is not good enough: "Two defences, deliberately
-- both. REVOKE removes the privilege that actually granted access. On its
-- own it is undone by anyone re-running a blanket GRANT. ENABLE ROW LEVEL
-- SECURITY with NO policy denies by default and keeps denying through a
-- future GRANT." One of those two was doing all the work here. A single
-- permissive policy added later - for a dashboard, say - would have turned
-- a latent defect into a live one with no other change.
--
-- `agent_usage` is the one that matters most: it holds the daily spend cap.
-- A writable cap is a cap.
--
-- HOW IT SURVIVED. tests/db/test_schema_parity.py's `grants` snapshot
-- compares exactly `anon` and `authenticated` across both databases and
-- would have failed on the commit that introduced it. It did not run: those
-- two migrations were among 25 commits that sat unpushed for four days, and
-- CI is triggered by `push`. See standing rule 17. The check was correctly
-- built, correctly wired, and never fired.
--
-- Found on 2026-09-24, when those commits were finally pushed and the job
-- ran for the first time.

REVOKE ALL ON TABLE public.agent_query_log FROM anon, authenticated;
REVOKE ALL ON TABLE public.agent_usage FROM anon, authenticated;

-- Belt and braces, matching 20260918000001's treatment: RLS is already on
-- for both, but asserting it here means this migration alone is sufficient
-- rather than depending on the two that created them.
ALTER TABLE public.agent_query_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_usage ENABLE ROW LEVEL SECURITY;
