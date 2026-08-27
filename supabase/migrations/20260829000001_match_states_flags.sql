-- Phase 0 session 6, Decision 4: both flags denormalized from `matches`
-- onto `match_states` for the same reason match_date was in session 3 -
-- avoid a join to matches on every hot per-training-row filter.
--
-- has_reconciliation_anomaly: eval/splits.py (Phase 1) must exclude these
-- from training - the label may be technically present but the underlying
-- data has an unreconciled discrepancy (session 5).
--
-- is_dls_decided: the result is real and must NOT be excluded by default -
-- this flag exists so a DLS-sensitivity re-run is a WHERE clause, not a
-- rebuild.
ALTER TABLE match_states
  ADD COLUMN has_reconciliation_anomaly BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN is_dls_decided BOOLEAN NOT NULL DEFAULT FALSE;
