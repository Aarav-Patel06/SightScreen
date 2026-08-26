-- Derived per-ball state table (SPEC.md section 5.3). Rebuilt from
-- deliveries by api/src/features/match_state.py - never hand-edited, never
-- incrementally patched. Adds match_date (Decision 2d, session 3): this
-- table has no temporal column at all otherwise, and it's what training
-- actually queries, so every temporal split would need a join to matches
-- just to filter chronologically without it.

CREATE TABLE match_states (
  delivery_id           BIGINT PRIMARY KEY REFERENCES deliveries(delivery_id),
  match_id              INT NOT NULL,
  innings               SMALLINT NOT NULL,
  -- state BEFORE this delivery is bowled
  score                 SMALLINT NOT NULL,
  wickets               SMALLINT NOT NULL,
  balls_bowled          SMALLINT NOT NULL,
  balls_remaining       SMALLINT NOT NULL,
  target                SMALLINT,                -- NULL in innings 1
  runs_required         SMALLINT,                -- NULL in innings 1
  current_run_rate      REAL,
  required_run_rate     REAL,
  rrr_minus_crr         REAL,
  partnership_runs      SMALLINT,
  partnership_balls     SMALLINT,
  balls_since_wicket    SMALLINT,
  phase                 TEXT NOT NULL,           -- 'powerplay'|'middle'|'death'
  batter_runs_so_far    SMALLINT,
  batter_balls_faced    SMALLINT,
  dls_resources_pct     REAL,
  -- outcome label (filled after match completes); NULL for ties/no-results -
  -- eval/splits.py (Phase 1) is where those rows get excluded from
  -- training, not a schema constraint here (Decision 2f, session 3) - these
  -- rows are still needed for replay/WP-curve/agent-query purposes.
  batting_team_won      BOOLEAN,
  match_date            DATE NOT NULL
);
CREATE INDEX ON match_states (match_id, innings);
CREATE INDEX ON match_states (match_date);
