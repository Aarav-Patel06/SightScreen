-- Below-threshold entity resolution queue (SPEC.md section 4.4 step 4).
-- Generic across players/teams/venues rather than one table per kind -
-- approved in Phase 0 planning session 2.

CREATE TABLE unresolved_entities (
  unresolved_id     SERIAL PRIMARY KEY,
  entity_kind       TEXT NOT NULL,                    -- 'player' | 'team' | 'venue'
  source            TEXT NOT NULL,
  source_name       TEXT NOT NULL,
  source_id         TEXT,
  best_candidate_id INT,                               -- closest existing row found, if any
  best_score        REAL,                              -- RapidFuzz score for that candidate
  status            TEXT NOT NULL DEFAULT 'pending',    -- 'pending' | 'resolved' | 'ignored'
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (entity_kind, source, source_name)
);
