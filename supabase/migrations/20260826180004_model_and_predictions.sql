-- Model and prediction tables (SPEC.md section 5.4). Migrated now per the
-- Phase 0 checklist ("every table in section 5"), even though nothing
-- writes to these until Phase 3/5/6 - not scope creep, just early.

CREATE TABLE model_versions (
  model_version   TEXT PRIMARY KEY,              -- 'winprob2-2026-09-14'
  model_type      TEXT NOT NULL,
  trained_at      TIMESTAMPTZ NOT NULL,
  train_end_date  DATE NOT NULL,
  test_brier      REAL,
  test_log_loss   REAL,
  is_active       BOOLEAN NOT NULL DEFAULT FALSE,
  is_shadow       BOOLEAN NOT NULL DEFAULT FALSE,
  artifact_path   TEXT NOT NULL
);

CREATE TABLE predictions (
  prediction_id   BIGSERIAL PRIMARY KEY,
  match_id        INT NOT NULL REFERENCES matches(match_id),
  delivery_id     BIGINT REFERENCES deliveries(delivery_id),  -- NULL for pre-match
  model_version   TEXT NOT NULL REFERENCES model_versions(model_version),
  prediction_type TEXT NOT NULL,      -- 'win_prob'|'score_proj'|'batter_runs'|'bowler_econ'
  subject_id      INT,                -- player_id for player predictions
  payload         JSONB NOT NULL,     -- {'p': 0.73} or {'mean': 51, 'p50plus': 0.38}
  match_phase     TEXT NOT NULL,      -- 'pre_toss'|'post_toss'|'innings1'|'break'|'innings2'
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Live serving: latest predictions for one match_id.
CREATE INDEX ON predictions (match_id, created_at);
-- Section 8 calibration/drift jobs group by exactly this.
CREATE INDEX ON predictions (model_version, prediction_type);

CREATE TABLE prediction_outcomes (
  prediction_id   BIGINT PRIMARY KEY REFERENCES predictions(prediction_id),
  actual          JSONB NOT NULL,
  brier           REAL,
  log_loss        REAL,
  resolved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
