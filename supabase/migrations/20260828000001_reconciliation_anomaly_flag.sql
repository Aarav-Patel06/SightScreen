-- A rejected match loses data that needs a re-parse to recover; a flagged
-- match is just a WHERE clause (same reasoning as is_super_over, section 5
-- session 3). Session 5 found that innings-total-vs-target mismatches
-- aren't always corruption - rare match-condition edge cases (e.g. a
-- slow-over-rate penalty run that isn't reflected in any delivery event)
-- can produce a genuine, small, real discrepancy in otherwise-valid data.
-- Flag and load instead of rejecting; eval/splits.py (Phase 1) must
-- exclude these from training, the same way it excludes ties/no-results
-- (section 5 session 3, Decision 2f).
ALTER TABLE matches ADD COLUMN has_reconciliation_anomaly BOOLEAN NOT NULL DEFAULT FALSE;
