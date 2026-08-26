-- Reference tables (SPEC.md section 5.1) plus team/venue alias tables for
-- cross-source name reconciliation (section 4.4), mirroring player_aliases.
-- Approved in Phase 0 planning session 2.

CREATE TABLE venues (
  venue_id        SERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  city            TEXT,
  country         TEXT,
  UNIQUE (name, city)
);

CREATE TABLE teams (
  team_id         SERIAL PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  short_name      TEXT
);

CREATE TABLE players (
  player_id       SERIAL PRIMARY KEY,
  canonical_name  TEXT NOT NULL,
  batting_hand    TEXT,          -- 'RHB' | 'LHB' | NULL
  bowling_style   TEXT,          -- 'RF' | 'LFM' | 'OB' | 'SLA' | 'LB' | ...
  dob             DATE
);

CREATE TABLE player_aliases (
  alias_id        SERIAL PRIMARY KEY,
  player_id       INT REFERENCES players(player_id),
  source          TEXT NOT NULL,   -- 'cricsheet' | 'cricketdata' | ...
  source_name     TEXT NOT NULL,
  source_id       TEXT,
  UNIQUE (source, source_name)
);

CREATE TABLE team_aliases (
  alias_id        SERIAL PRIMARY KEY,
  team_id         INT REFERENCES teams(team_id),
  source          TEXT NOT NULL,
  source_name     TEXT NOT NULL,
  source_id       TEXT,
  UNIQUE (source, source_name)
);

CREATE TABLE venue_aliases (
  alias_id        SERIAL PRIMARY KEY,
  venue_id        INT REFERENCES venues(venue_id),
  source          TEXT NOT NULL,
  source_name     TEXT NOT NULL,
  source_id       TEXT,
  UNIQUE (source, source_name)
);
