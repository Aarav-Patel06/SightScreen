-- Layer 1 of the SQL guard (SPEC.md §10.3): the read-only role, and the five
-- views that are the only thing it can read.
--
-- Applied to the CORPUS REPLICA only, by agent_tools/replica.py. It is not in
-- supabase/migrations/ on purpose: the replica is a derived, rebuildable
-- projection, not a third member of the schema-parity regime, and putting it
-- in the migrations directory would make apply_migrations.py and
-- test_schema_parity.py start comparing three databases instead of two.
--
-- §10.3 calls layer 1 "the layer that actually protects you; the rest are
-- defence in depth". That is only true if the views are a real boundary, so
-- each one does something a `SELECT * FROM <table>` could not:
--
--   * it exposes NAMES, not just ids, so the agent never needs the alias
--     tables - which hold unresolved junk and a human review audit trail;
--   * it applies the project's own correctness filters, so the agent cannot
--     compute a statistic over data the project already knows is bad;
--   * it omits surrogate keys and provider ids, so nothing leaks the loader's
--     dedupe key or lets an id be correlated across sources;
--   * it omits the three always-NULL player attribute columns entirely,
--     because an always-empty column invites queries that come back empty for
--     an invisible reason (SPEC.md §15, 2026-09-20).
--
-- Idempotent: it is re-run on every replica bootstrap. CREATE OR REPLACE VIEW
-- cannot change a column list, so the views are dropped first.

DROP VIEW IF EXISTS agent_deliveries;
DROP VIEW IF EXISTS agent_matches;
DROP VIEW IF EXISTS agent_players;
DROP VIEW IF EXISTS agent_teams;
DROP VIEW IF EXISTS agent_venues;

-- match_ref, not match_id (Gap 1). query_ball_data reads CORPUS ids and
-- get_live_prediction reads SUPABASE ids, which are different SERIAL spaces
-- over the same matches. Handing a bare int from one tool to the other
-- returns a different match, silently - the Phase 3 ball-key collision again,
-- one layer up. A prefixed text ref cannot be passed to a tool that takes an
-- int, so the mistake becomes a type error instead of a wrong answer.
CREATE VIEW agent_deliveries AS
SELECT
    'corpus:' || d.match_id            AS match_ref,
    m.competition,
    m.format,
    m.start_time,
    d.match_date,
    v.name                             AS venue,
    d.innings,
    d.over_num,
    d.ball_in_over,
    d.legal_ball_num,
    bat.canonical_name                 AS batter,
    nons.canonical_name                AS non_striker,
    bowl.canonical_name                AS bowler,
    batting.name                       AS batting_team,
    bowling.name                       AS bowling_team,
    d.runs_batter,
    d.runs_extras,
    d.runs_batter + d.runs_extras      AS runs_total,
    d.extra_type,
    d.wicket_type,
    dismissed.canonical_name           AS player_out,
    d.wicket_count
FROM deliveries d
JOIN matches m          ON m.match_id = d.match_id
LEFT JOIN venues v      ON v.venue_id = m.venue_id
LEFT JOIN players bat   ON bat.player_id = d.batter_id
LEFT JOIN players nons  ON nons.player_id = d.non_striker_id
LEFT JOIN players bowl  ON bowl.player_id = d.bowler_id
LEFT JOIN players dismissed ON dismissed.player_id = d.player_out_id
JOIN teams batting      ON batting.team_id = d.batting_team_id
JOIN teams bowling      ON bowling.team_id = d.bowling_team_id
-- A super over is a 1-over sudden-death mini-innings. Averaged in with normal
-- play it inflates every strike rate it touches, which is why match_states is
-- never built for these deliveries at all (20260826180002).
WHERE NOT d.is_super_over
-- The same exclusion eval/splits.py applies to training data. An agent
-- answering a question about the corpus should not be held to a weaker
-- standard than the model is.
  AND NOT m.has_reconciliation_anomaly;

CREATE VIEW agent_matches AS
SELECT
    'corpus:' || m.match_id            AS match_ref,
    m.competition,
    m.format,
    m.start_time,
    v.name                             AS venue,
    a.name                             AS team_a,
    b.name                             AS team_b,
    tw.name                            AS toss_winner,
    m.toss_decision,
    w.name                             AS winner,
    m.result_method
FROM matches m
LEFT JOIN venues v  ON v.venue_id = m.venue_id
LEFT JOIN teams a   ON a.team_id = m.team_a
LEFT JOIN teams b   ON b.team_id = m.team_b
LEFT JOIN teams tw  ON tw.team_id = m.toss_winner
LEFT JOIN teams w   ON w.team_id = m.winner
WHERE m.status = 'complete'
  AND NOT m.has_reconciliation_anomaly;

-- batting_hand, bowling_style and dob are deliberately absent: they are NULL
-- for all 18,468 rows and always have been, because Cricsheet does not
-- publish them (SPEC.md §15). Exposing them would let the model write
-- "WHERE batting_hand = 'LHB'" and report an empty result as a finding.
CREATE VIEW agent_players AS
SELECT player_id, canonical_name AS name FROM players;

CREATE VIEW agent_teams AS
SELECT team_id, name, short_name FROM teams;

CREATE VIEW agent_venues AS
SELECT venue_id, name, city, country FROM venues;

-- The role itself is created by replica.py BEFORE this file is applied, not
-- here. A password cannot be passed as a query parameter - PostgreSQL wants a
-- literal - and this file is checked in, so the credential has to be composed
-- in Python from the environment. The grants below are what matter and they
-- are here, in one reviewable place.
--
-- The role is NOINHERIT with no CREATEDB/CREATEROLE/SUPERUSER. Note what is
-- NOT granted: USAGE on schema public, and SELECT on five views. No table
-- privileges at all, so a query naming `deliveries` fails on permissions even
-- if every Python layer were deleted. That is what makes layer 1 independent
-- of layers 2-5 rather than a restatement of them.

-- Revoke first, so a re-run after a schema change cannot leave a stale grant
-- behind. REVOKE ALL on ALL TABLES covers views too.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM agent_ro;
REVOKE ALL ON SCHEMA public FROM agent_ro;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM agent_ro;

GRANT USAGE ON SCHEMA public TO agent_ro;
GRANT SELECT ON agent_deliveries TO agent_ro;
GRANT SELECT ON agent_matches TO agent_ro;
GRANT SELECT ON agent_players TO agent_ro;
GRANT SELECT ON agent_teams TO agent_ro;
GRANT SELECT ON agent_venues TO agent_ro;

-- Views execute with their OWNER's rights, not the caller's: PostgreSQL 15+
-- defaults security_invoker to false. These views are owned by the bootstrap
-- superuser, which is precisely the mechanism that lets agent_ro read through
-- them without holding a grant on deliveries. Setting security_invoker = true
-- here would break every one of them - stated explicitly because it looks
-- like a hardening improvement and is the opposite.
--
-- Guarded on the server version because the option did not exist before
-- PostgreSQL 15. On 14 and earlier a view ALWAYS executes with owner rights
-- and there is nothing to set, so skipping is correct rather than merely
-- tolerable - but an unguarded ALTER would abort the whole bootstrap on an
-- older image, and which image a hosted provider hands you is not something
-- this file gets to assume.
DO $$
BEGIN
    IF current_setting('server_version_num')::int >= 150000 THEN
        ALTER VIEW agent_deliveries SET (security_invoker = false);
        ALTER VIEW agent_matches    SET (security_invoker = false);
        ALTER VIEW agent_players    SET (security_invoker = false);
        ALTER VIEW agent_teams      SET (security_invoker = false);
        ALTER VIEW agent_venues     SET (security_invoker = false);
    ELSE
        RAISE NOTICE 'server is %, pre-15: views are owner-rights by default, nothing to set',
            current_setting('server_version');
    END IF;
END
$$;

-- Belt and braces against a future table being created and silently readable:
-- strip the PUBLIC pseudo-role's default CREATE on schema public (already the
-- default in PG15+, restated because the replica may be bootstrapped on an
-- older image) and make sure nothing new is granted to agent_ro by default.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM agent_ro;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
