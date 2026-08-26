-- Match and delivery tables (SPEC.md section 5.2), plus Phase 0 session 3
-- decisions: denormalized batting_team_id/bowling_team_id/match_date,
-- is_super_over and wicket_count flags, and the matches.venue_id index /
-- idempotent-loader unique index that section 5 doesn't specify.

CREATE TABLE matches (
  match_id        SERIAL PRIMARY KEY,
  external_ids    JSONB NOT NULL DEFAULT '{}',   -- {'cricsheet': '...', 'cricketdata': '...'}
  competition     TEXT NOT NULL,
  format          TEXT NOT NULL,                 -- 'T20' | 'ODI'
  venue_id        INT REFERENCES venues(venue_id),
  start_time      TIMESTAMPTZ NOT NULL,
  team_a          INT REFERENCES teams(team_id),
  team_b          INT REFERENCES teams(team_id),
  toss_winner     INT REFERENCES teams(team_id),
  toss_decision   TEXT,                          -- 'bat' | 'field'
  winner          INT REFERENCES teams(team_id), -- NULL if no result
  result_method   TEXT,                          -- 'normal' | 'dls' | 'tie' | 'no_result'
  status          TEXT NOT NULL                  -- 'scheduled' | 'live' | 'complete'
);
CREATE INDEX ON matches (start_time);
CREATE INDEX ON matches (status);
-- Agent SQL: arbitrary filters on venue (Decision 3, session 3) - section 5
-- doesn't index this FK, but venue lives on matches, not deliveries.
CREATE INDEX ON matches (venue_id);
-- Backs the Cricsheet loader's idempotent re-run check (session 2).
CREATE UNIQUE INDEX ON matches ((external_ids->>'cricsheet')) WHERE external_ids->>'cricsheet' IS NOT NULL;

CREATE TABLE deliveries (
  delivery_id     BIGSERIAL PRIMARY KEY,
  match_id        INT NOT NULL REFERENCES matches(match_id),
  innings         SMALLINT NOT NULL,             -- 1|2 normal play, 3+ super over (see is_super_over)
  over_num        SMALLINT NOT NULL,             -- 0-indexed
  ball_in_over    SMALLINT NOT NULL,
  legal_ball_num  SMALLINT NOT NULL,             -- cumulative legal balls in innings
  batter_id       INT REFERENCES players(player_id),
  non_striker_id  INT REFERENCES players(player_id),
  bowler_id       INT REFERENCES players(player_id),
  -- Denormalized (Decision 2c, session 3): every WPA/training/agent query
  -- needs to know who's batting/bowling, and deriving it via
  -- matches+toss_winner+toss_decision on every read is real, avoidable cost
  -- on the largest table in the database. Populated from Cricsheet's own
  -- per-innings team field at load time, not toss inference.
  batting_team_id INT NOT NULL REFERENCES teams(team_id),
  bowling_team_id INT NOT NULL REFERENCES teams(team_id),
  -- Denormalized (Decision 2d, session 3): every temporal split and as_of()
  -- lookup filters on this; avoids a join to matches on every such scan.
  match_date      DATE NOT NULL,
  -- Cricsheet models a super over as additional innings entries (3, 4, ...).
  -- Flagged explicitly (Decision 2a, session 3) so a 1-over sudden-death
  -- mini-innings can never be silently mixed into normal-chase training
  -- data - match_states is never built for these deliveries at all.
  is_super_over   BOOLEAN NOT NULL DEFAULT FALSE,
  runs_batter     SMALLINT NOT NULL DEFAULT 0,
  runs_extras     SMALLINT NOT NULL DEFAULT 0,
  extra_type      TEXT,                          -- 'wide'|'noball'|'bye'|'legbye'|NULL
  wicket_type     TEXT,                          -- 'bowled'|'caught'|'lbw'|...|NULL
  player_out_id   INT REFERENCES players(player_id),
  -- Cricsheet's wickets field is an array - rare double dismissals on one
  -- ball (e.g. both batters run out) exist. wicket_type/player_out_id above
  -- only retain the primary dismissal; this at least flags when there was
  -- more than one, rather than losing the fact silently (Decision 2e).
  wicket_count    SMALLINT NOT NULL DEFAULT 0,
  UNIQUE (match_id, innings, over_num, ball_in_over)
);
CREATE INDEX ON deliveries (match_id, innings, legal_ball_num);
CREATE INDEX ON deliveries (batter_id);
CREATE INDEX ON deliveries (bowler_id);

COMMENT ON COLUMN deliveries.innings IS
  '1|2 for normal play; 3+ for super-over innings (see is_super_over).';
COMMENT ON COLUMN deliveries.ball_in_over IS
  '1-indexed position within the over, counting every delivery including wides/no-balls. NOT the legal-ball "x.y" over notation - that scheme collides on every over containing an illegal delivery and would violate the UNIQUE(match_id, innings, over_num, ball_in_over) constraint above.';
