"""The corpus replica: bootstrap, load, and prove layer 1 (Decision 1).

§2.1 put all 3.78M deliveries in local Postgres. Railway cannot reach a
laptop and Supabase holds the serving data, so the agent had nothing to
query. Decision 1 gives the corpus a third home: its own Postgres service,
holding deliveries + matches + players + teams + venues and NOT match_states.

Why a separate service rather than Supabase Pro or a T20-only subset,
measured rather than argued:

    full corpus                        ~1.19 GB  (deliveries 682 MB +
                                                  match_states 516 MB + ref)
    this replica, no match_states       577 MB   MEASURED, built locally
    T20-only deliveries                 457 MB
    Supabase free space remaining       465 MB

The 577 MB is the built artefact, not an estimate: deliveries 566 MB,
matches 2.1 MB, players 1.3 MB, venues and teams under 60 kB each. The plan
plate said ~690 MB, which was conservative by ~16% - it assumed the source's
full index set, and this projection carries three indexes rather than six.
577 MB is still 112 MB more than the whole of Supabase's remaining free
space, so the decision is unchanged; the margin is just smaller than the
number it was argued from, and that is worth saying plainly.

T20-only does not avoid the upgrade: 457 against 465 MB leaves ~8 MB before
predictions grow at 79 kB/match, and dropping the batter_id/bowler_id indexes
to make it fit turns every matchup question into a sequential scan of 2.4M
rows that the 5 s timeout then kills. §2.1's "roughly halves the row count
and fits comfortably" is wrong twice: T20 is 64% of deliveries, not ~50%,
and it does not fit.

The deciding argument is blast radius, not money. Filling the SERVING
database to ~79% to host an analytical replica risks the live product - at
the 500 MB cap, predictions stop being written and both the match page and
the accuracy page break. A separate replica isolates that: if it fills, the
agent degrades and serving is untouched.

**The replica is not a third member of the schema-parity regime.** It is a
derived, rebuildable projection - the same category as the as-of summaries,
just larger. Its schema is created here, not by supabase/migrations/, so
apply_migrations.py keeps two targets and test_schema_parity.py keeps
comparing exactly two databases.

    python -m agent_tools.replica --bootstrap   schema, role, views, grants
    python -m agent_tools.replica --load        COPY the corpus across
    python -m agent_tools.replica --verify      prove layer 1 empirically
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

VIEWS_SQL = Path(__file__).with_name("views.sql")

# match_states is deliberately absent - see the module docstring. Order
# matters: foreign keys point left.
REPLICATED_TABLES = ("venues", "teams", "players", "matches", "deliveries")

ROLE_NAME = "agent_ro"

# Mirrors supabase/migrations/20260826180001 and ...0002, minus every column
# the agent has no business reading and minus the alias tables entirely.
# Deliberately NOT generated from the migrations: this is a projection with
# its own shape, and generating it would recreate the parity coupling the
# replica exists to avoid.
SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS venues (
  venue_id  INT PRIMARY KEY,
  name      TEXT NOT NULL,
  city      TEXT,
  country   TEXT
);

CREATE TABLE IF NOT EXISTS teams (
  team_id    INT PRIMARY KEY,
  name       TEXT NOT NULL,
  short_name TEXT
);

CREATE TABLE IF NOT EXISTS players (
  player_id      INT PRIMARY KEY,
  canonical_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
  match_id                   INT PRIMARY KEY,
  competition                TEXT NOT NULL,
  format                     TEXT NOT NULL,
  venue_id                   INT REFERENCES venues(venue_id),
  start_time                 TIMESTAMPTZ NOT NULL,
  team_a                     INT REFERENCES teams(team_id),
  team_b                     INT REFERENCES teams(team_id),
  toss_winner                INT REFERENCES teams(team_id),
  toss_decision              TEXT,
  winner                     INT REFERENCES teams(team_id),
  result_method              TEXT,
  status                     TEXT NOT NULL,
  has_reconciliation_anomaly BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS deliveries (
  delivery_id     BIGINT PRIMARY KEY,
  match_id        INT NOT NULL REFERENCES matches(match_id),
  innings         SMALLINT NOT NULL,
  over_num        SMALLINT NOT NULL,
  ball_in_over    SMALLINT NOT NULL,
  legal_ball_num  SMALLINT NOT NULL,
  batter_id       INT REFERENCES players(player_id),
  non_striker_id  INT REFERENCES players(player_id),
  bowler_id       INT REFERENCES players(player_id),
  batting_team_id INT NOT NULL REFERENCES teams(team_id),
  bowling_team_id INT NOT NULL REFERENCES teams(team_id),
  match_date      DATE NOT NULL,
  is_super_over   BOOLEAN NOT NULL DEFAULT FALSE,
  runs_batter     SMALLINT NOT NULL DEFAULT 0,
  runs_extras     SMALLINT NOT NULL DEFAULT 0,
  extra_type      TEXT,
  wicket_type     TEXT,
  player_out_id   INT REFERENCES players(player_id),
  wicket_count    SMALLINT NOT NULL DEFAULT 0
);

-- The two indexes Decision 1 refused to trade away. Without them every
-- matchup question is a sequential scan of 2.4M rows and the 5 s statement
-- timeout kills it - which is why "T20-only, heap-only, on the free tier"
-- was not a cheaper option but a broken one.
CREATE INDEX IF NOT EXISTS deliveries_batter_idx ON deliveries (batter_id);
CREATE INDEX IF NOT EXISTS deliveries_bowler_idx ON deliveries (bowler_id);
CREATE INDEX IF NOT EXISTS deliveries_match_idx  ON deliveries (match_id, innings, legal_ball_num);
"""

COPY_COLUMNS: dict[str, tuple[str, ...]] = {
    "venues": ("venue_id", "name", "city", "country"),
    "teams": ("team_id", "name", "short_name"),
    "players": ("player_id", "canonical_name"),
    "matches": (
        "match_id",
        "competition",
        "format",
        "venue_id",
        "start_time",
        "team_a",
        "team_b",
        "toss_winner",
        "toss_decision",
        "winner",
        "result_method",
        "status",
        "has_reconciliation_anomaly",
    ),
    "deliveries": (
        "delivery_id",
        "match_id",
        "innings",
        "over_num",
        "ball_in_over",
        "legal_ball_num",
        "batter_id",
        "non_striker_id",
        "bowler_id",
        "batting_team_id",
        "bowling_team_id",
        "match_date",
        "is_super_over",
        "runs_batter",
        "runs_extras",
        "extra_type",
        "wicket_type",
        "player_out_id",
        "wicket_count",
    ),
}


def _admin_url() -> str:
    url = os.environ.get("REPLICA_ADMIN_DB_URL")
    if not url:
        raise SystemExit(
            "REPLICA_ADMIN_DB_URL is not set. It is the replica's OWNER "
            "connection string, used only by this script - never by the "
            "serving process, which holds agent_ro instead."
        )
    return url


def _role_password() -> str:
    password = os.environ.get("AGENT_RO_PASSWORD")
    if not password:
        raise SystemExit("AGENT_RO_PASSWORD is not set.")
    return password


def bootstrap() -> None:
    """Schema, role, views, grants. Idempotent; safe to re-run."""
    password = _role_password()
    with psycopg.connect(_admin_url(), autocommit=True) as conn:
        conn.execute(SCHEMA_DDL)

        # Composed rather than parameterised: PostgreSQL wants a literal for
        # PASSWORD, so a placeholder is a syntax error. sql.Literal quotes it
        # properly; the value never reaches a log line.
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE_NAME,)
        ).fetchone()
        verb = sql.SQL("ALTER ROLE") if exists else sql.SQL("CREATE ROLE")
        conn.execute(
            sql.SQL("{verb} {role} WITH LOGIN NOINHERIT PASSWORD {password}").format(
                verb=verb,
                role=sql.Identifier(ROLE_NAME),
                password=sql.Literal(password),
            )
        )

        conn.execute(VIEWS_SQL.read_text(encoding="utf-8"))
    print("bootstrap: schema, role and views applied")


def load(source_url: str, batch: int = 50_000) -> None:
    """COPY the corpus across, table by table, in FK order.

    Streams rather than materialising: `deliveries` is 3.78M rows and this
    runs on a laptop.
    """
    with psycopg.connect(source_url) as src, psycopg.connect(_admin_url()) as dest:
        for table in REPLICATED_TABLES:
            columns = COPY_COLUMNS[table]
            column_list = ", ".join(columns)
            dest.execute(sql.SQL("TRUNCATE {} CASCADE").format(sql.Identifier(table)))
            copied = 0
            with src.cursor().copy(
                f"COPY (SELECT {column_list} FROM {table}) TO STDOUT (FORMAT BINARY)"
            ) as reader, dest.cursor().copy(
                f"COPY {table} ({column_list}) FROM STDIN (FORMAT BINARY)"
            ) as writer:
                for block in reader:
                    writer.write(block)
                    copied += len(block)
            # Row counts, not byte counts, are what anyone checking this cares
            # about - read them back rather than inferring from the stream.
            rows = dest.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
            print(f"load: {table:<12} {rows:>9,} rows")
        dest.commit()


# --- layer 1, proven rather than asserted --------------------------------

# The non-vacuity proof for layer 1, and it cannot be written as a unit test:
# the claim is about GRANTS, not about Python. Each of these must fail for
# agent_ro, and the reason must be permissions - not "relation does not
# exist", which would mean the check passed for the wrong reason.
MUST_BE_DENIED = (
    "SELECT * FROM deliveries LIMIT 1",
    "SELECT * FROM matches LIMIT 1",
    "SELECT * FROM players LIMIT 1",
    "SELECT * FROM teams LIMIT 1",
    "SELECT * FROM venues LIMIT 1",
)

MUST_BE_ALLOWED = (
    "SELECT * FROM agent_deliveries LIMIT 1",
    "SELECT * FROM agent_matches LIMIT 1",
    "SELECT * FROM agent_players LIMIT 1",
    "SELECT * FROM agent_teams LIMIT 1",
    "SELECT * FROM agent_venues LIMIT 1",
)

MUST_NOT_WRITE = (
    "INSERT INTO agent_deliveries (batter) VALUES ('x')",
    "UPDATE agent_players SET name = 'x'",
    "DELETE FROM agent_matches",
    "CREATE TABLE evil (id INT)",
)

INSUFFICIENT_PRIVILEGE = "42501"

# A view built over a join is not auto-updatable, so PostgreSQL refuses a
# write against it at rewrite time (55000, object_not_in_prerequisite_state)
# BEFORE it ever consults the grants. The write is refused either way, but
# "refused because the view has a join in it" is a much weaker guarantee than
# "refused because the privilege is absent" - it would stop protecting
# anything the day someone added an INSTEAD OF trigger. So where 55000 comes
# back, the absence of the privilege is established from the catalogue
# instead, and the check only passes if BOTH are true.
NOT_UPDATABLE = "55000"

WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")

VIEWS = ("agent_deliveries", "agent_matches", "agent_players", "agent_teams", "agent_venues")


def _privilege_matrix(conn) -> list[str]:
    """Ask the catalogue what agent_ro actually holds.

    This is the claim layer 1 rests on, stated directly rather than inferred
    from whichever error a particular statement happens to raise first.
    """
    failures: list[str] = []
    for view in VIEWS:
        allowed = conn.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT')", (ROLE_NAME, view)
        ).fetchone()[0]
        if not allowed:
            failures.append(f"{ROLE_NAME} lacks SELECT on {view}")
        for privilege in WRITE_PRIVILEGES:
            held = conn.execute(
                "SELECT has_table_privilege(%s, %s, %s)", (ROLE_NAME, view, privilege)
            ).fetchone()[0]
            if held:
                failures.append(f"{ROLE_NAME} holds {privilege} on {view}")
    for table in REPLICATED_TABLES:
        for privilege in ("SELECT",) + WRITE_PRIVILEGES:
            held = conn.execute(
                "SELECT has_table_privilege(%s, %s, %s)", (ROLE_NAME, table, privilege)
            ).fetchone()[0]
            if held:
                failures.append(f"{ROLE_NAME} holds {privilege} on base table {table}")

    attributes = conn.execute(
        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolinherit "
        "FROM pg_roles WHERE rolname = %s",
        (ROLE_NAME,),
    ).fetchone()
    names = ("rolsuper", "rolcreaterole", "rolcreatedb", "rolbypassrls", "rolinherit")
    for name, value in zip(names, attributes):
        if value:
            failures.append(f"{ROLE_NAME} has {name}")
    return failures


STATEMENT_TIMEOUT = "5s"
QUERY_CANCELED = "57014"


def _statement_timeout_proof(role_url: str) -> list[str]:
    """Layer 3's non-vacuity proof, which also has to be run, not asserted.

    Same shape as the three Python proofs in the adversarial suite: the query
    is killed with the layer on, and survives with it off. The "off" half is
    the part that matters - without it, "we set a timeout" is a line of code
    nobody has watched do anything.

    SET LOCAL is a no-op outside a transaction, which is why this opens one
    explicitly rather than relying on the connection's autocommit default.
    """
    failures: list[str] = []
    print(f"\nstatement_timeout ({STATEMENT_TIMEOUT}):")

    with psycopg.connect(role_url) as conn:  # autocommit off: SET LOCAL needs a txn
        with conn.transaction():
            conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            try:
                conn.execute("SELECT pg_sleep(10)")
            except psycopg.Error as err:
                if err.sqlstate == QUERY_CANCELED:
                    print("  ok       pg_sleep(10) cancelled with the timeout set")
                else:
                    failures.append(f"timeout fired the wrong error: {err.sqlstate}")
                    print(f"  WRONG    pg_sleep(10) -> {err.sqlstate}")
            else:
                failures.append("pg_sleep(10) completed with a 5s timeout set")
                print("  BREACH   pg_sleep(10) completed")

    # The other half. A 1-second sleep with no timeout must succeed - if it
    # did not, the check above would pass for a reason unrelated to the
    # timeout and prove nothing.
    with psycopg.connect(role_url, autocommit=True) as conn:
        try:
            conn.execute("SELECT pg_sleep(1)")
            print("  ok       pg_sleep(1) completed with no timeout set")
        except psycopg.Error as err:
            failures.append(f"the control case failed: {err.sqlstate}")
            print(f"  WRONG    control pg_sleep(1) -> {err.sqlstate}")
    return failures


def verify(role_url: str) -> int:
    """Connect AS agent_ro and establish what it can and cannot do.

    Returns a process exit code. Prints every check, because a security
    verifier that only prints on failure gives you nothing to read when it
    passes.
    """
    failures: list[str] = []
    with psycopg.connect(role_url, autocommit=True) as conn:
        who = conn.execute("SELECT current_user").fetchone()[0]
        print(f"connected as: {who}")
        if who != ROLE_NAME:
            failures.append(f"connected as {who!r}, not {ROLE_NAME!r}")

        for query in MUST_BE_ALLOWED:
            try:
                conn.execute(query).fetchall()
                print(f"  ALLOWED  ok        {query}")
            except psycopg.Error as err:
                failures.append(f"should have been allowed: {query} ({err.sqlstate})")
                print(f"  ALLOWED  FAILED    {query} -> {err.sqlstate}")

        for query in MUST_BE_DENIED + MUST_NOT_WRITE:
            try:
                conn.execute(query)
            except psycopg.Error as err:
                if err.sqlstate == INSUFFICIENT_PRIVILEGE:
                    print(f"  DENIED   ok        {query}")
                elif err.sqlstate == NOT_UPDATABLE:
                    # Backed by the privilege matrix below, not by this code.
                    print(f"  DENIED   ok (55000){query}")
                else:
                    # A denial for the wrong reason is not a denial. If the
                    # table were merely absent this would pass silently and
                    # stop protecting anything the day it was added.
                    failures.append(
                        f"denied for the wrong reason: {query} -> {err.sqlstate}"
                    )
                    print(f"  DENIED   WRONG     {query} -> {err.sqlstate}")
            else:
                failures.append(f"NOT denied: {query}")
                print(f"  DENIED   BREACH    {query}")

        failures.extend(_statement_timeout_proof(role_url))

        print("\nprivileges, from the catalogue:")
        privilege_failures = _privilege_matrix(conn)
        for failure in privilege_failures:
            print(f"  BREACH   {failure}")
        if not privilege_failures:
            print(
                f"  ok       {ROLE_NAME} holds SELECT on {len(VIEWS)} views, "
                f"nothing on {len(REPLICATED_TABLES)} base tables, and no role attributes"
            )
        failures.extend(privilege_failures)

    if failures:
        print(f"\nverify: {len(failures)} failure(s)")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "\nverify: layers 1 and 3 hold - views readable, base tables denied on "
        "privilege, no write privilege anywhere, and the 5s timeout cancels a "
        "query that outlives it"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--source",
        default=os.environ.get("LOCAL_DATABASE_URL"),
        help="corpus to copy FROM; defaults to LOCAL_DATABASE_URL",
    )
    args = parser.parse_args(argv)

    if args.bootstrap:
        bootstrap()
    if args.load:
        if not args.source:
            raise SystemExit("--load needs --source or LOCAL_DATABASE_URL")
        load(args.source)
    if args.verify:
        role_url = os.environ.get("AGENT_SQL_ROLE_DB_URL")
        if not role_url:
            raise SystemExit("--verify needs AGENT_SQL_ROLE_DB_URL")
        return verify(role_url)
    if not (args.bootstrap or args.load or args.verify):
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
