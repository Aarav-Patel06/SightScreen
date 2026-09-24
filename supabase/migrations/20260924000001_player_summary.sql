-- Per-player aggregates, derived locally and synced (UI mini-phase session 4).
--
-- WHY A DERIVED TABLE RATHER THAN A QUERY. /player/[id] wants career
-- aggregates and splits by format and phase. All of that is computed from
-- `deliveries`, which is empty on Supabase by §2.1's design and must stay
-- that way, so the page cannot compute it at request time. The established
-- answer is a summary built where the corpus is and pushed across:
-- venue_asof_summary and elo_asof_summary already do exactly this, and at
-- 10,508 and 25,290 rows they are the same order of magnitude as these.
--
-- Measured before choosing, per UI-PHASE.md §7's open decision:
--   9,615  rows per (player, format)
--  21,451  rows per (player, format, phase)
--   8,080  players who have ever batted
--   6,054  players who have ever bowled
--  18,468  players named in the corpus
--
-- That last pair is the reason for player_index below: more than half the
-- players in the corpus have never batted or bowled in it. They appear
-- because a team sheet named them. An index listing all 18,468 would be
-- mostly rows that go nowhere.

-- --------------------------------------------------------------------------
-- player_index: one row per player WITH A RECORD. The /players table.
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS player_index (
  player_id        INT PRIMARY KEY REFERENCES players(player_id),
  canonical_name   TEXT NOT NULL,

  -- The resolver's own canonicalisation, precomputed.
  --
  -- §4.4 requires search to match both "Kohli" and "Virat Kohli", and says to
  -- reuse the resolver rather than write a second matcher. The resolver is
  -- Python (ingest/entity_resolution.py's normalize_name and surname_key) and
  -- the search box is TypeScript, so the choice was to port the logic or to
  -- carry its output. Carrying it means there is exactly one implementation:
  -- if the resolver's notion of a name changes, this column changes with it
  -- on the next rebuild, and the web layer never has an opinion.
  normalized_name  TEXT NOT NULL,
  surname_key      TEXT NOT NULL,

  matches          INT  NOT NULL,
  formats          TEXT NOT NULL,   -- e.g. 'T20' or 'ODI,T20', for the column
  bat_innings      INT  NOT NULL,
  bat_runs         INT  NOT NULL,
  bowl_innings     INT  NOT NULL,
  bowl_wickets     INT  NOT NULL
);

COMMENT ON TABLE player_index IS
  'Players with at least one delivery batted or bowled. Rebuilt by '
  'features.player_summary, synced by ingest.sync_reference_tables. '
  'Excludes the ~10,388 corpus players who only ever appear on a team sheet.';

CREATE INDEX IF NOT EXISTS player_index_surname ON player_index (surname_key);
CREATE INDEX IF NOT EXISTS player_index_normalized ON player_index (normalized_name);

-- --------------------------------------------------------------------------
-- player_career_summary: the detail page's numbers.
-- --------------------------------------------------------------------------
--
-- One row per (player, format, phase). `phase = 'all'` carries the career
-- line for that format, so the page reads one table rather than reconciling
-- an aggregate against the sum of its parts - which is how an aggregate and
-- its splits come to disagree.
CREATE TABLE IF NOT EXISTS player_career_summary (
  player_id        INT  NOT NULL REFERENCES players(player_id),
  format           TEXT NOT NULL,
  phase            TEXT NOT NULL,   -- 'all' | 'powerplay' | 'middle' | 'death'

  -- Batting. balls_faced excludes wides, which a batter is not deemed to
  -- have faced; a no-ball IS faced. `outs` counts dismissals of this batter,
  -- excluding the retired-* types, which end an innings without being one.
  bat_innings      INT NOT NULL,
  bat_balls        INT NOT NULL,
  bat_runs         INT NOT NULL,
  bat_outs         INT NOT NULL,
  bat_fours        INT NOT NULL,
  bat_sixes        INT NOT NULL,

  -- Bowling. balls counts legal deliveries only. runs_conceded is the
  -- batter's runs plus wides and no-balls - byes and leg-byes are NOT charged
  -- to the bowler. wickets counts only bowler-credited dismissals: a run out
  -- is not the bowler's.
  bowl_balls       INT NOT NULL,
  bowl_runs        INT NOT NULL,
  bowl_wickets     INT NOT NULL,

  PRIMARY KEY (player_id, format, phase)
);

COMMENT ON TABLE player_career_summary IS
  'Descriptive record only, never an ability estimate - see player_state, '
  'which is empty until Phase 5. Rebuilt by features.player_summary.';

COMMENT ON COLUMN player_career_summary.bowl_wickets IS
  'Bowler-credited dismissals only: bowled, caught, caught and bowled, lbw, '
  'stumped, hit wicket. A run out is not the bowler''s wicket.';

-- --------------------------------------------------------------------------
-- Anon read access, matching the pattern in 20260826180008 and 20260919000001.
-- These are aggregates of a public corpus; there is nothing to gate.
-- --------------------------------------------------------------------------
GRANT SELECT ON player_index TO anon, authenticated;
GRANT SELECT ON player_career_summary TO anon, authenticated;

ALTER TABLE player_index ENABLE ROW LEVEL SECURITY;
ALTER TABLE player_career_summary ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS player_index_anon_select ON player_index;
CREATE POLICY player_index_anon_select ON player_index
  FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS player_career_summary_anon_select ON player_career_summary;
CREATE POLICY player_career_summary_anon_select ON player_career_summary
  FOR SELECT TO anon, authenticated USING (true);

-- Writes stay with the service role, matching 20260919000001's treatment of
-- the other browser-readable tables.
--
-- NOT optional, and not symmetric between the two databases. Hosted Supabase
-- grants anon and authenticated ALL privileges on a new table in `public` by
-- default; local Postgres grants nothing. So a migration that only GRANTs
-- SELECT produces a table that is read-only locally and fully writable on
-- Supabase - which is 20260918000001's finding exactly, and the first
-- version of this migration reproduced it. tests/db/test_schema_parity.py's
-- `grants` snapshot caught it, which is what that test is for.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON player_index, player_career_summary FROM anon, authenticated;
