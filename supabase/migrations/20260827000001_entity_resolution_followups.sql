-- Three schema gaps surfaced while designing entity resolution (Phase 0
-- session 4), before any resolver code exists. Cheap now (these tables are
-- still empty); expensive to retrofit after session 5 loads real rows.

-- Approved this session: innings > 2 already means is_super_over should be
-- true and vice versa; enforce it rather than trusting the loader forever.
ALTER TABLE deliveries ADD CONSTRAINT is_super_over_matches_innings
  CHECK (is_super_over = (innings > 2));

-- Decision 4: idempotent resolution needs to look up by Cricsheet's own
-- stable person ID, not just (source, source_name) - the same registry ID
-- can legitimately appear with slightly different name spellings across
-- different match files. Without this, registry-ID lookup can't be a true
-- exact-match short-circuit.
CREATE UNIQUE INDEX ON player_aliases (source, source_id) WHERE source_id IS NOT NULL;

-- The single best_candidate_id/best_score columns can't hold "candidate
-- matches with scores" (plural) or say which match a name first appeared
-- in - both required for a reviewer to actually decide. reason gives the
-- review CLI a one-line explanation (below_threshold /
-- same_surname_collision / temporal_implausible / ambiguous_margin)
-- instead of just a number.
ALTER TABLE unresolved_entities
  DROP COLUMN best_candidate_id,
  DROP COLUMN best_score,
  ADD COLUMN candidates JSONB NOT NULL DEFAULT '[]',        -- [{"candidate_id":.., "candidate_name":.., "score":..}, ...]
  ADD COLUMN first_seen_match_id INT REFERENCES matches(match_id),
  ADD COLUMN reason TEXT NOT NULL DEFAULT 'below_threshold';
