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
    python -m agent_tools.replica --check-url U  refuse a mis-pasted target URL

scripts/setup-replica.ps1 drives all four in order; run them by hand only when
that script has already told you which step failed.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
from psycopg.types.json import Json

from db.defaults import LOCAL_DB_NAME

VIEWS_SQL = Path(__file__).with_name("views.sql")

# match_states is deliberately absent - see the module docstring. Order
# matters: foreign keys point left.
REPLICATED_TABLES = ("venues", "teams", "players", "matches", "deliveries")

# The chunking key for each replicated table: its primary key, which is a
# dense integer in every case. Written out rather than taken as "the first
# column of COPY_COLUMNS" - true today, and it would break silently and
# wrongly the day someone reordered a column list.
CHUNK_KEYS = {
    "venues": "venue_id",
    "teams": "team_id",
    "players": "player_id",
    "matches": "match_id",
    "deliveries": "delivery_id",
}

# What the replica occupied when it was first built (Phase 6 session 1:
# deliveries 566 MB, matches 2.1 MB, players 1.3 MB, venues and teams under
# 60 kB each). A constant because the only way to know it is to build the
# thing, and --load should be able to say it before spending twenty minutes
# over a tunnel discovering the volume is smaller. Update it if the corpus
# grows enough to matter.
MEASURED_REPLICA_MB = 577

# The volume to provision, which is not the same number. Chunked commits keep
# WAL recyclable during the load, but checkpoints still hold a few hundred MB
# of it, a re-run leaves dead rows behind until autovacuum catches up, and
# neither is worth cutting fine on a disk that costs cents. Railway's default
# Postgres volume is 500 MB - less than MEASURED_REPLICA_MB, which is how the
# first load died at delivery 769,000.
REQUIRED_VOLUME_MB = 1_500

# Rows between progress lines during a chunked load.
PROGRESS_EVERY = 500_000

ROLE_NAME = "agent_ro"

# Provenance, not corpus. Written by --load, read by --verify, and readable by
# nobody else - see PROTECTED_TABLES below.
METADATA_TABLE = "load_metadata"

# Every table agent_ro must hold nothing on. The replicated five plus the
# stamp: a new table in this schema that the role could read would be a hole
# in layer 1, and the matrix should grow with the schema rather than lag it.
PROTECTED_TABLES = REPLICATED_TABLES + (METADATA_TABLE,)

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

-- Provenance for the copy. A replica with no recorded load date is
-- indistinguishable from a stale duplicate of the corpus, and the difference
-- only matters at the moment an answer looks wrong - which is exactly when
-- nobody can reconstruct it. Created by --bootstrap, filled by --load, read
-- back by --verify.
--
-- One row, not a history: the PRIMARY KEY on a column CHECKed to be TRUE is
-- the standard way to say "at most one". This records the last load, and a
-- load log is a different thing that nothing here needs.
CREATE TABLE IF NOT EXISTS load_metadata (
  singleton       BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
  source_database TEXT        NOT NULL,
  row_counts      JSONB       NOT NULL,
  loaded_at       TIMESTAMPTZ NOT NULL
);
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


# --- the target URL, refused before anything connects ---------------------

# The databases this script is allowed to build a replica in, and the only
# ones it will let --load TRUNCATE. `railway` is what Railway names the
# database in a Postgres service, reached through a local tunnel; the other
# is the docker-compose Postgres of db/defaults.py, which is where the agent
# is built while the hosting decision is deferred.
#
# An allowlist, not a blocklist, for the same reason config.py's pooler check
# is one: naming the mistakes already made catches those mistakes, not the
# class. Every name added here is a name --load is permitted to empty, which
# is the reason to keep it short.
REPLICA_DATABASES = ("railway", "cricket_agent_replica")

# Both targets are reached at localhost - one through a tunnel, one as a
# published docker port. A remote host here is always a paste error.
TARGET_HOSTS = ("localhost", "127.0.0.1")

TARGET_SCHEMES = ("postgresql", "postgres")


def validate_target_url(raw: str) -> str:
    """Refuse a mis-pasted replica target, naming the specific problem.

    Every rejection below is a paste error that has already cost real time,
    because each surfaced as a confusing connection failure rather than as
    itself: a database name truncated to `/ra`, a trailing newline off the
    clipboard, and the Railway INTERNAL host - which resolves only inside
    Railway's network and, from a laptop, hangs until it times out.

    One rejection here has never happened and is the most expensive of the
    lot. The target used to be a tunnel and the source a local database, so
    they could not be confused; pointing this at local docker puts both on
    localhost:5433, one database name apart, and --load TRUNCATEs its target.
    Naming the corpus as the target would destroy the Phase 0 bulk load. That
    case gets its own message, and load() re-checks it structurally.

    Whitespace is rejected rather than stripped. Accepting a string and
    silently repairing it is how the repaired version and the version in the
    clipboard drift apart; the paste is either right or it is not.
    """
    if raw != raw.strip():
        raise SystemExit(
            f"the target URL has surrounding whitespace ({raw!r}). It came off "
            "the clipboard with a newline or a space attached - re-copy it, or "
            "quote it, but do not let this script trim it for you."
        )
    if not raw:
        raise SystemExit("the target URL is empty.")

    parts = urlsplit(raw)
    if parts.scheme not in TARGET_SCHEMES:
        raise SystemExit(
            f"the target URL's scheme is {parts.scheme!r}, not one of "
            f"{TARGET_SCHEMES}."
        )

    host = parts.hostname or ""
    if host not in TARGET_HOSTS:
        if host.endswith(".railway.internal"):
            detail = (
                f"{host!r} is Railway's INTERNAL host. It resolves only from "
                "inside Railway's private network, so from this laptop it will "
                "hang rather than refuse. It is the value a deployed service "
                "uses, not one you can reach from here."
            )
        elif host.endswith(".rlwy.net") or host.endswith(".railway.app"):
            detail = (
                f"{host!r} is Railway's public TCP proxy. This script drives a "
                "local target - a tunnel or docker - which is what keeps the "
                "corpus copy off the public internet."
            )
        else:
            detail = (
                f"{host!r} is not a local host. Both supported targets "
                f"terminate on this machine, so the URL must name one of "
                f"{TARGET_HOSTS}."
            )
        raise SystemExit(f"the target URL points at the wrong host: {detail}")

    if parts.port is None:
        raise SystemExit(
            "the target URL has no port. A tunnel picks a fresh local port "
            "every session and docker publishes its own, so neither is safe "
            "to omit or to inherit from yesterday."
        )

    name = parts.path.lstrip("/")
    if not name:
        raise SystemExit(
            f"the target URL has no database name (got {raw!r}). It must be "
            f"one of {REPLICA_DATABASES}; a URL ending at the port connects to "
            "a database named after the login instead."
        )
    if name == LOCAL_DB_NAME:
        raise SystemExit(
            f"the target URL names {name!r}, which is the CORPUS - the database "
            "this script reads FROM. --load truncates its target before copying "
            "into it, so this would destroy the Phase 0 bulk load. The replica "
            f"is a separate database: {REPLICA_DATABASES}."
        )
    if name not in REPLICA_DATABASES:
        truncated = [full for full in REPLICA_DATABASES if full.startswith(name)]
        hint = (
            f" - {name!r} is {truncated[0]!r} cut short, which is a half-copied "
            "paste, not a different database"
            if truncated
            else ""
        )
        raise SystemExit(
            f"the target URL names database {name!r}, which is not one of "
            f"{REPLICA_DATABASES}{hint}."
        )

    if not parts.username:
        raise SystemExit(
            "the target URL has no username. psycopg would fall back to this "
            "machine's OS user, which fails as an authentication error and "
            "reads like a wrong password."
        )
    if not parts.password:
        raise SystemExit("the target URL has no password.")

    return raw


def _sanitise_source(url: str) -> str:
    """host:port/dbname, never the credentials.

    The load stamp is a row in a database, and a row in a database gets
    dumped, backed up and pasted into a bug report. A password has no
    business in any of those.
    """
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.hostname or '?'}{port}/{parts.path.lstrip('/') or '?'}"


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


def _ensure_database_exists(admin_url: str) -> None:
    """Create the replica database if it is not there yet.

    Railway creates `railway` along with the Postgres service, so this was
    never needed while the target was a tunnel. Local docker creates only
    `cricket_training`, and psycopg's failure for a missing database reads
    like a connection problem rather than a missing database - which is the
    class of confusing-failure this script exists to remove.

    The name is re-checked against REPLICA_DATABASES rather than trusted:
    this function runs CREATE DATABASE, and it should not be able to create
    anything validate_target_url would have refused.
    """
    parts = urlsplit(admin_url)
    name = parts.path.lstrip("/")
    if name not in REPLICA_DATABASES:
        raise SystemExit(
            f"refusing to create a database named {name!r}: not one of "
            f"{REPLICA_DATABASES}."
        )
    # CREATE DATABASE cannot run inside a transaction and cannot run from the
    # database being created, so it goes through the maintenance database.
    maintenance = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, ""))
    with psycopg.connect(maintenance, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if exists:
            return
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    print(f"bootstrap: created database {name}")


def bootstrap() -> None:
    """Schema, role, views, grants. Idempotent; safe to re-run."""
    password = _role_password()
    admin_url = _admin_url()
    _ensure_database_exists(admin_url)
    with psycopg.connect(admin_url, autocommit=True) as conn:
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


def _report_capacity(src, dest) -> None:
    """Say what this will cost on the destination, before it costs it.

    The single-transaction load filled a 500 MB Railway volume at delivery
    769,000 and reported it as `server closed the connection unexpectedly`
    after two and a half minutes. The volume size was knowable in the first
    second, so it is now printed in the first second.

    Reported, not enforced: PostgreSQL cannot see how large the filesystem
    under it is, so the only honest thing this can do is name the number and
    leave the comparison to whoever provisioned the disk.
    """
    source_bytes = src.execute(
        "SELECT sum(pg_total_relation_size(c.oid)) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY(%s)",
        (list(REPLICATED_TABLES),),
    ).fetchone()[0] or 0
    dest_name, dest_bytes = dest.execute(
        "SELECT current_database(), pg_database_size(current_database())"
    ).fetchone()
    mb = 1024 * 1024
    print(
        f"load: source occupies {source_bytes / mb:,.0f} MB; the projection of it "
        f"measured {MEASURED_REPLICA_MB} MB"
    )
    # The Railway caveat only when the target IS Railway. It is true and
    # useful there and pure noise against local docker, and a warning that
    # fires when it does not apply is how warnings stop being read.
    caveat = (
        " Railway's default Postgres volume is 500 MB and will not hold this."
        if dest_name == "railway"
        else ""
    )
    print(
        f"load: destination {dest_name!r} holds {dest_bytes / mb:,.0f} MB now and "
        f"needs ~{REQUIRED_VOLUME_MB:,} MB free, WAL included.{caveat}"
    )


def load(source_url: str, batch: int = 50_000) -> None:
    """COPY the corpus across, table by table, in FK order.

    Chunked and committed as it goes rather than copied in one transaction.
    The single-transaction version filled a 500 MB Railway volume at delivery
    769,000: an uncommitted COPY holds its WAL, so peak disk was the data plus
    260 MB of WAL that could not be recycled until a commit that never came.
    Committing every `batch` rows lets checkpoints recycle it, so peak disk
    tends towards the size the replica actually occupies.

    The cost is atomicity, and it is worth naming rather than burying: an
    interrupted load leaves `deliveries` partly filled. That is only safe
    because of the load stamp, which is written once, after the last chunk -
    so a partial load is an UNSTAMPED load, and --verify already exits 1 on
    those. The stamp stopped being provenance-only the moment this function
    stopped being atomic.

    Chunks are primary-key ranges rather than slices of one stream: a binary
    COPY stream carries a header and a trailer, so it cannot be cut in half
    and fed to two COPY statements, but each range is its own well-formed
    stream. Every replicated table has a dense integer primary key (measured:
    density 1.0000 for four of the five, 0.9914 for teams), so a range of
    `batch` ids is a chunk of about `batch` rows. Sparser keys would only cost
    round-trips on empty ranges, not correctness.

    `batch` is 50,000 because measuring says the size barely matters: 76
    chunks took 297 s, 16 took 290 s and 4 took 260 s, against the atomic
    loader's 196 s, all against local Postgres. So most of what chunking costs
    is present at ANY chunk count - it is the filtered range scans on the
    source replacing one sequential scan - and chunk count itself accounts for
    only the last 37 s. 50,000 holds the least WAL per chunk, which is the
    entire reason for chunking, and over a tunnel the transfer swamps all of
    it anyway.
    """
    # Names are checked by validate_target_url; this checks the thing itself.
    # Both databases now live on localhost:5433, so source and target are one
    # word apart on one line and the TRUNCATE below empties the target. The
    # name allowlist catches the paste; comparing resolved endpoints catches
    # two different spellings - localhost against 127.0.0.1 - of one database.
    admin_url = _admin_url()
    if _sanitise_source(source_url) == _sanitise_source(admin_url):
        raise SystemExit(
            "the source and the target are the same database "
            f"({_sanitise_source(source_url)}). --load truncates its target, so "
            "this would empty the corpus it is copying from."
        )

    counts: dict[str, int] = {}
    with psycopg.connect(source_url) as src, psycopg.connect(admin_url) as dest:
        _report_capacity(src, dest)

        # One TRUNCATE for all five, before any of them is copied. Per-table
        # inside the loop - which is what this was when the whole load shared
        # one transaction - a CASCADE would now wipe tables that an earlier
        # iteration had already committed.
        #
        # THE STAMP GOES WITH THEM, and that is not tidiness. Once the load
        # stopped being atomic, a stamp left over from a previous successful
        # load would outlive an interrupted one and go on vouching for 3.78M
        # rows that had just been truncated away - the exact stale-duplicate
        # reading the stamp was added to make impossible, reintroduced by the
        # thing that made the stamp load-bearing. Cleared here, "rows present
        # but no stamp" means "this load did not finish", and --verify says so.
        dest.execute(
            sql.SQL("TRUNCATE {} CASCADE").format(
                sql.SQL(", ").join(
                    sql.Identifier(t) for t in REPLICATED_TABLES + (METADATA_TABLE,)
                )
            )
        )
        dest.commit()

        for table in REPLICATED_TABLES:
            column_list = ", ".join(COPY_COLUMNS[table])
            key = CHUNK_KEYS[table]
            low, high = src.execute(
                sql.SQL("SELECT min({k}), max({k}) FROM {t}").format(
                    k=sql.Identifier(key), t=sql.Identifier(table)
                )
            ).fetchone()

            copied = 0
            next_report = PROGRESS_EVERY
            start = low
            while start is not None and start <= high:
                stop = start + batch
                with dest.cursor() as writer_cursor:
                    with src.cursor().copy(
                        sql.SQL(
                            "COPY (SELECT {cols} FROM {t} WHERE {k} >= %s AND {k} < %s) "
                            "TO STDOUT (FORMAT BINARY)"
                        ).format(
                            cols=sql.SQL(column_list),
                            t=sql.Identifier(table),
                            k=sql.Identifier(key),
                        ),
                        (start, stop),
                    ) as reader, writer_cursor.copy(
                        f"COPY {table} ({column_list}) FROM STDIN (FORMAT BINARY)"
                    ) as writer:
                        for block in reader:
                            writer.write(block)
                    # rowcount off the COPY rather than count(*) on the
                    # table after every chunk. Measured, because the obvious
                    # guess was wrong: it made no difference to the total
                    # (297 s against 295 s), so the redundant scans were not
                    # where the chunked load loses its time. Kept anyway -
                    # re-scanning a growing 3.78M-row table 76 times to
                    # print a progress line is still the wrong shape.
                    copied += writer_cursor.rowcount
                # The whole point: the chunk is durable and its WAL is
                # recyclable before the next one is read.
                dest.commit()

                if copied >= next_report:
                    # Over a tunnel this table takes tens of minutes. Silence
                    # for that long is indistinguishable from a hang, which is
                    # how the 500 MB volume read the first time.
                    print(f"load: {table:<12} {copied:>9,} rows so far")
                    next_report += PROGRESS_EVERY
                start = stop

            # One authoritative count per table, read back from the
            # destination rather than tallied off the stream - the tally above
            # drives progress lines, this is what gets stamped.
            rows = dest.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
            counts[table] = rows
            print(f"load: {table:<12} {rows:>9,} rows")

        # Last, and alone in its own transaction, which is exactly what makes
        # the chunked load above safe: an interrupted load leaves rows behind
        # but no stamp, and --verify fails on an unstamped replica. A stamp
        # written any earlier would vouch for data that never arrived.
        dest.execute(
            """
            INSERT INTO load_metadata (singleton, source_database, row_counts, loaded_at)
            VALUES (TRUE, %s, %s, now())
            ON CONFLICT (singleton) DO UPDATE SET
                source_database = EXCLUDED.source_database,
                row_counts      = EXCLUDED.row_counts,
                loaded_at       = EXCLUDED.loaded_at
            """,
            (_sanitise_source(source_url), Json(counts)),
        )
        dest.commit()
    print(f"load: stamped {sum(counts.values()):,} rows from {_sanitise_source(source_url)}")


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
    for table in PROTECTED_TABLES:
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


def _report_load_stamp(admin_url: str | None) -> list[str]:
    """Read back what --load recorded, and fail if it recorded nothing.

    Read with the OWNER connection, not agent_ro's: the role deliberately
    holds nothing on load_metadata (see PROTECTED_TABLES), so this is the only
    connection that can see it.

    A missing stamp is a real failure, not a cosmetic one. An unstamped
    replica is indistinguishable from a stale duplicate of the corpus, and
    that ambiguity is worth an exit code. Where the owner URL is absent the
    check is skipped rather than failed, because provenance is not a security
    layer and --verify's actual job still ran.
    """
    print("\nload stamp:")
    if not admin_url:
        print("  skipped  REPLICA_ADMIN_DB_URL unset; only the owner can read it")
        return []

    with psycopg.connect(admin_url, autocommit=True) as conn:
        try:
            row = conn.execute(
                f"SELECT source_database, row_counts, loaded_at FROM {METADATA_TABLE}"
            ).fetchone()
        except psycopg.errors.UndefinedTable:
            print(f"  MISSING  {METADATA_TABLE} does not exist - re-run --bootstrap")
            return [f"{METADATA_TABLE} does not exist: this replica predates the load stamp"]

    if row is None:
        print("  MISSING  the replica holds data but no record of where it came from")
        return [f"{METADATA_TABLE} is empty: --load has never completed against this replica"]

    source, counts, loaded_at = row
    total = sum(counts.values())
    age = (datetime.now(timezone.utc) - loaded_at).days
    # One stable machine-readable line: scripts/setup-replica.ps1 parses rows=
    # out of it for its summary. Keep the key=value shape if you edit this.
    print(f"  source={source} rows={total} loaded_at={loaded_at.isoformat()}")
    print(f"  ok       {total:,} rows from {source}, loaded {age} day(s) ago")
    for table in REPLICATED_TABLES:
        print(f"           {table:<12} {counts.get(table, 0):>9,}")
    return []


def verify(role_url: str, admin_url: str | None = None) -> int:
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
                f"nothing on {len(PROTECTED_TABLES)} base tables, and no role attributes"
            )
        failures.extend(privilege_failures)

    failures.extend(_report_load_stamp(admin_url))

    if failures:
        print(f"\nverify: {len(failures)} failure(s)")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "\nverify: layers 1 and 3 hold - views readable, base tables denied on "
        "privilege, no write privilege anywhere, and the 5s timeout cancels a "
        "query that outlives it, over a corpus whose load is stamped"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--check-url",
        metavar="URL",
        help="validate a target URL's shape and exit, without connecting to anything",
    )
    parser.add_argument(
        "--source",
        default=os.environ.get("LOCAL_DATABASE_URL"),
        help="corpus to copy FROM; defaults to LOCAL_DATABASE_URL",
    )
    args = parser.parse_args(argv)

    if args.check_url is not None:
        validate_target_url(args.check_url)
        print("check-url: shape ok")
        return 0

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
        return verify(role_url, os.environ.get("REPLICA_ADMIN_DB_URL"))
    if not (args.bootstrap or args.load or args.verify):
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
