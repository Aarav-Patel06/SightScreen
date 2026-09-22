-- Daily spend ceiling for the public /ask agent (Phase 6 session 2).
--
-- Batched with 20260920000001_agent_query_log.sql, which has also not been
-- pushed. Worth stating plainly: every eval run in session 2 printed
-- "agent_query_log write failed: UndefinedTable" - once per tool call,
-- dozens of times - and it was correct to ignore, because the route degrades
-- without failing the call and the migration was known to be unpushed. But a
-- known-harmless error appearing on every single run is exactly the cover a
-- real one hides under. The lesson is not "chase every line"; it is that an
-- error you have decided to tolerate should be FIXED or SILENCED, because
-- tolerating it trains everyone to read past that spot.
--
-- Why a table rather than a counter in memory: Vercel functions are
-- stateless and horizontally scaled, so two concurrent requests see two
-- different process memories. The cap has to live where both can see it, and
-- Supabase is already wired into the web app's server side.
--
-- Why cost and not a message count: one eval conversation in session 2 made
-- 17 tool calls across 8 turns. "50 messages" and "50 conversations" differ
-- by more than an order of magnitude in spend, and the thing being protected
-- is a bill.

CREATE TABLE agent_usage (
  day                 DATE PRIMARY KEY,
  conversations       INT    NOT NULL DEFAULT 0,
  input_tokens        BIGINT NOT NULL DEFAULT 0,
  output_tokens       BIGINT NOT NULL DEFAULT 0,
  cache_read_tokens   BIGINT NOT NULL DEFAULT 0,
  cache_write_tokens  BIGINT NOT NULL DEFAULT 0,
  -- NUMERIC, not REAL, per the float-boundary decision of 2026-09-15: this
  -- value is written by the route and read back by the next request to
  -- decide whether the cap is hit, so it crosses the boundary where `real`'s
  -- TEXT rendering depends on the session's extra_float_digits. A cap that
  -- compared 1.9999 against 2.0 differently depending on which pooler
  -- session answered would be the same class of bug as elo_asof_summary's.
  cost_usd            NUMERIC NOT NULL DEFAULT 0,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RLS on with no policy, matching agent_query_log and 20260918000001. The
-- anon and authenticated roles must never read this: it is a public signal
-- of how close the budget is to exhaustion, which tells anyone who wants to
-- exhaust it exactly how much further to go. Only the service role, which
-- carries rolbypassrls, can see or write it.
ALTER TABLE agent_usage ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE agent_usage IS
  'One row per UTC day of /ask agent spend. Read before each conversation to enforce the daily cap, updated after. Never web-readable - remaining budget is a hint to whoever is trying to exhaust it.';
COMMENT ON COLUMN agent_usage.cost_usd IS
  'NUMERIC deliberately: written by one request and compared against the cap by the next, so it crosses the boundary where real''s text rendering depends on extra_float_digits (see SPEC.md section 15, 2026-09-15).';
