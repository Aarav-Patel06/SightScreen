-- Make the browser roles read-only in PRIVILEGE, not just in policy
-- (Phase 2 session 5, Decision 1, second finding).
--
-- 20260826180008's comment states the intent: "No write policies for
-- anon/authenticated anywhere; all writes go through the service-role key,
-- which bypasses RLS by design." That is what the POLICIES say. It is not
-- what the GRANTS say on Supabase.
--
-- Measured 2026-09-18 on the five browser-readable tables:
--
--   role                LOCAL     SUPABASE
--   anon                SELECT    DELETE, INSERT, REFERENCES, SELECT,
--                                 TRIGGER, TRUNCATE, UPDATE
--   authenticated       SELECT    (same)
--
-- Hosted Supabase grants the full set by default. Writes are refused today
-- only because no INSERT/UPDATE/DELETE policy exists - the privilege is
-- present and a single permissive `FOR ALL` policy, added by someone
-- reaching for a quick fix, would let an anonymous holder of the publishable
-- key delete rows from `predictions`. The key is public by design, so that
-- is not a hypothetical attacker; it is anyone who opens devtools.
--
-- Defence in depth means the privilege and the policy must BOTH say no. The
-- companion migration 20260918000001 handles the tables that should not be
-- readable at all; this one handles the five that should be readable and
-- nothing more.
--
-- SELECT is deliberately preserved - these five are what the match page
-- reads (SPEC.md 7.4, 12.1). Safe for the services for the same verified
-- reason as 20260918000001: `postgres` and `service_role` both have
-- rolbypassrls = true and hold their privileges independently of anon.

DO $$
DECLARE
  target text;
BEGIN
  FOR target IN SELECT unnest(ARRAY[
    'predictions',
    'matches',
    'players',
    'venues',
    'teams'
  ])
  LOOP
    EXECUTE format(
      'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
      'ON TABLE public.%I FROM anon, authenticated',
      target
    );
  END LOOP;
END $$;
