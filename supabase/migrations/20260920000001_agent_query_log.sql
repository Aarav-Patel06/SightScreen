-- Server-side log of every agent SQL tool call (Phase 6 session 1,
-- Decision 5). Supabase only - the corpus replica is deliberately not in
-- supabase/migrations/, and the agent's read-only role cannot write
-- anywhere, which is the whole point of it. The api already holds a
-- privileged Supabase connection, so the log lives where the writes can
-- happen.
--
-- What this exists for: the caller gets ONE uninformative payload whatever
-- the cause - {"error": "query rejected", "ref": "<uuid>"} - with no table
-- name, no column name and no layer identity, because an error saying
-- `relation "users" does not exist` is free reconnaissance and a message
-- that varies by cause tells an attacker which layer they tripped. That is
-- only tolerable if the full detail is recorded somewhere we can read.
-- query_ref is the join between the two.
--
-- What it must never hold: a connection string, the shared secret, or any
-- credential. It records SQL and verdicts.

CREATE TABLE agent_query_log (
  log_id       BIGSERIAL PRIMARY KEY,
  query_ref    UUID NOT NULL UNIQUE,
  -- The SQL as the guard produced it for an admitted query, or as the
  -- caller sent it for a rejected one. The rejected form is the more
  -- interesting of the two and is the one that would be lost if only
  -- successful queries were logged.
  sql_text     TEXT NOT NULL,
  verdict      TEXT NOT NULL,   -- 'ok' | 'rejected' | 'database_error'
  -- Which layer refused it. NULL when the verdict is 'ok'. This is the
  -- field the caller is never told.
  rejected_by  TEXT,
  row_count    INT,
  duration_ms  INT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The two questions anyone will actually ask of this table: "what has been
-- rejected recently" and "what is slow".
CREATE INDEX ON agent_query_log (created_at DESC);
CREATE INDEX ON agent_query_log (verdict, created_at DESC);

-- RLS on with no policy, matching 20260918000001. The anon and
-- authenticated roles must never read this: it is a record of exactly which
-- probes were attempted and which layer stopped each one, which is a map of
-- the defences. Only the service role, which carries rolbypassrls, can see
-- it.
ALTER TABLE agent_query_log ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE agent_query_log IS
  'Server-side record of agent SQL tool calls. The caller receives a uniform uninformative rejection carrying only query_ref; this table is where the cause is recorded. Never web-readable.';
COMMENT ON COLUMN agent_query_log.rejected_by IS
  'The guard layer that refused the query, or ''readonly_role_or_timeout'' when the database refused it. Deliberately never returned to the caller - a per-layer error message is an oracle.';
