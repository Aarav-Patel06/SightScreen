-- Phase 0 session 6, Decision 3: Cricsheet's recorded target (from the
-- chasing innings' own `target` block) is the authoritative source for
-- match_states.target/runs_required - NOT innings_1_total + 1, which is
-- only correct for a normal, undecided-by-method match. A DLS-revised
-- target can differ from innings_1_total + 1 by dozens of runs (confirmed
-- against a real match: innings 1 scored 165, DLS target was 70 off 6
-- overs). This was previously only used transiently during session 5's
-- load-time validation, never persisted - match_states needs it as a
-- real column.
ALTER TABLE matches
  ADD COLUMN target_runs SMALLINT,   -- NULL for innings-1-only records (shouldn't occur for a 2-innings match)
  ADD COLUMN target_overs REAL;      -- true balls = floor(overs)*6 + round((overs - floor(overs)) * 10)
