-- Elo as a queryable time series (SPEC.md section 6.1) - one row per team
-- per format per completed match. A single current-rating column would be
-- a leakage bug; every later phase's elo_diff feature must look up a
-- rating as of a specific past date via the index below, never "current".
-- Approved in Phase 0 planning session 2.

CREATE TABLE elo_ratings (
  elo_id      BIGSERIAL PRIMARY KEY,
  team_id     INT NOT NULL REFERENCES teams(team_id),
  format      TEXT NOT NULL,                          -- 'T20' | 'ODI'
  match_id    INT NOT NULL REFERENCES matches(match_id),  -- match that produced this rating
  as_of       TIMESTAMPTZ NOT NULL,                    -- = matches.start_time, denormalised for range queries
  rating      REAL NOT NULL,
  UNIQUE (team_id, format, match_id)
);
-- As-of lookup: rating for a team/format at a date.
-- SELECT rating FROM elo_ratings WHERE team_id=$1 AND format=$2
--   AND as_of < $3 ORDER BY as_of DESC LIMIT 1
CREATE INDEX ON elo_ratings (team_id, format, as_of);
