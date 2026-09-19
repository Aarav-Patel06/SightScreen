"""Float columns and the local/Supabase boundary (Phase 2 session 4
housekeeping).

Session 3 found that a float's TEXT output depends on the session's
extra_float_digits, that psycopg decodes in text mode, and that Supabase's
pooler serves 0 where local Postgres serves 1 - so the same stored float4 was
decoded as 1496.445 locally and 1496.44 remotely.

Two claims are pinned here rather than written in a comment, which is the
lesson that finding actually taught:

  1. matches.target_overs stays REAL, on the grounds that its values carry
     too few significant digits for the two settings to differ. That is a
     claim about the data, so the data is asked.

  2. No NEW float column appears in a table that crosses the boundary. A
     comment explaining which columns were converted protects nothing against
     the next migration.

Local-only, like the other tests in this directory - it reads the real
corpus.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

# Tables whose rows are written on one side of the boundary and read on the
# other, or read back by the process that wrote them through a pooler.
# A float column appearing in one of these is a bug.
CROSSING_TABLES = ("model_versions", "prediction_outcomes", "player_state")

# Tables that are local-only or whose float columns are documented as benign.
# Listed explicitly so that adding a table forces a decision rather than
# silently inheriting one.
EXEMPT = {
    "match_states": "local-only; nothing serving-side inserts into it (session 4 sweep)",
    "elo_ratings": "local-only; elo_asof_summary carries the served copy",
    "unresolved_entities": "best_score is a RapidFuzz diagnostic read by a human",
    "matches": "target_overs only; pinned benign by the test below",
    "deliveries": "local-only corpus",
}

FLOAT_TYPES = ("real", "double precision")


def _conn():
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        pytest.skip(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return psycopg.connect(env["LOCAL_DATABASE_URL"], autocommit=True)


@pytest.fixture(scope="module")
def conn():
    connection = _conn()
    yield connection
    connection.close()


def test_no_float_columns_in_tables_that_cross_the_boundary(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ANY(%s)
              AND data_type = ANY(%s)
            ORDER BY table_name, column_name
            """,
            (list(CROSSING_TABLES), list(FLOAT_TYPES)),
        )
        offenders = cur.fetchall()
    assert not offenders, (
        "float columns found in tables that cross the local/Supabase boundary: "
        f"{offenders}. Their text rendering depends on extra_float_digits, which "
        "differs between local Postgres and Supabase's pooler - use NUMERIC. "
        "See supabase/migrations/20260915000002_numeric_metrics.sql."
    )


def test_every_float_column_is_either_converted_or_explicitly_exempt(conn):
    """A new migration adding a REAL column to an unlisted table fails here,
    which is the point: the sweep should not need repeating by hand."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT table_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND data_type = ANY(%s)
            ORDER BY table_name
            """,
            (list(FLOAT_TYPES),),
        )
        tables = {row[0] for row in cur.fetchall()}
    unreviewed = tables - set(EXEMPT) - set(CROSSING_TABLES)
    assert not unreviewed, (
        f"tables with float columns that nobody has classified: {sorted(unreviewed)}. "
        "Decide whether the values cross the boundary; convert to NUMERIC if they do, "
        "or add the table to EXEMPT with the reason."
    )


def test_target_overs_renders_identically_at_both_float_settings(conn):
    """The claim matches.target_overs is exempt on.

    Its values are 20, 50, 19.3, 21 - at most three significant digits - so
    float4's shortest-exact output at extra_float_digits=1 equals its
    six-significant-digit output at 0. If a value ever arrives that needs more
    precision (a DLS-revised 19.3333), this fails and target_overs must be
    converted like the rest.
    """
    with conn.cursor() as cur:
        cur.execute("SET extra_float_digits = 1")
        cur.execute(
            "SELECT DISTINCT target_overs, target_overs::text FROM matches "
            "WHERE target_overs IS NOT NULL ORDER BY 1"
        )
        at_one = {row[0]: row[1] for row in cur.fetchall()}
        cur.execute("SET extra_float_digits = 0")
        cur.execute(
            "SELECT DISTINCT target_overs, target_overs::text FROM matches "
            "WHERE target_overs IS NOT NULL ORDER BY 1"
        )
        at_zero = {row[0]: row[1] for row in cur.fetchall()}
        cur.execute("SET extra_float_digits = 1")

    if not at_one:
        pytest.skip("no target_overs values in the corpus")
    differing = {value: (at_one[value], at_zero.get(value)) for value in at_one
                 if at_one[value] != at_zero.get(value)}
    assert not differing, (
        f"target_overs values render differently at extra_float_digits 0 vs 1: {differing}. "
        "The exemption in 20260915000002_numeric_metrics.sql no longer holds - convert the "
        "column to NUMERIC."
    )
    print(f"\n{len(at_one)} distinct target_overs values, identical at both settings")


# --- the fact the match_states exemption rests on -------------------------

SRC = REPO_ROOT / "api" / "src"

# Modules whose code RUNS inside a deployed container. Not the import
# closure: serving/app.py imports features/match_state.py for MatchStateRow,
# and that module also holds REBUILD_SQL - an INSERT INTO match_states that
# only rebuild() ever executes, against local Postgres. An import-graph scan
# flags it and is wrong to; what matters is which functions execute.
SERVING_AUTHORED = (
    "serving",
    "ingest/cricketdata.py",
    # Phase 3 session 1. These two do not run in a container, but they DO
    # write to Supabase - which is what this scan is actually about. The
    # name says "serving" for historical reasons; the membership rule is
    # "writes to the hosted database". Adding them closed a real gap: the
    # scan globs `serving/**` plus one named file, so a new module anywhere
    # else was invisible to it, and Phase 3 added exactly that. A gate whose
    # coverage does not grow with the code stops being a gate.
    "ingest/replay_log.py",
    "models/resolve_outcomes.py",
    # Phase 3 session 2: the daily calibration monitor. Runs in a GitHub
    # Action rather than a container, and writes one calibration_runs row
    # per run - which makes it a Supabase writer, which is what this scan is
    # about regardless of where the process lives.
    "eval/calibration_monitor.py",
)

# Every module above must still be findable. Without this, a rename quietly
# empties the scanned set and every assertion below passes vacuously - the
# failure mode Phase 2 session 5 found in CI, where a check reported
# `skipped` for ten runs while the thing it guarded drifted.
REQUIRED_SCANNED = (
    "serving/app.py",
    "serving/live_loop.py",
    "ingest/cricketdata.py",
    "ingest/replay_log.py",
    "models/resolve_outcomes.py",
    "eval/calibration_monitor.py",
)

# Tables a serving process may write on Supabase. Adding one is a decision -
# see the assertion messages.
ALLOWED_SERVING_WRITES = {
    "matches",              # cricketdata.py's _ensure_match_row, for a live match
    "predictions",          # serving/app.py and the Phase 3 logger, one row per ball
    "unresolved_entities",  # entity_resolution queues rather than auto-creating
    "prediction_outcomes",  # models/resolve_outcomes.py, one row per resolved prediction
    "calibration_runs",     # eval/calibration_monitor.py, one row per daily run
}

# Functions that mutate corpus or derived tables. Serving code must not call
# them: they all take a `conn`, so config.py's section 2.1 check - which only
# forbids LOCAL_DATABASE_URL - would not stop one being handed the Supabase
# connection. This is the subtler version of the same mistake.
CORPUS_MUTATORS = (
    "REBUILD_SQL",
    "recompute_format",
    "rebuild_venue_summary",
    "rebuild_elo_summary",
    "rebuild_all",
)

_WRITE_PATTERNS = (
    re.compile(r"INSERT\s+INTO\s+([a-z_][a-z0-9_]*)", re.I),
    re.compile(r"UPDATE\s+([a-z_][a-z0-9_]*)\s+SET", re.I),
    re.compile(r"DELETE\s+FROM\s+([a-z_][a-z0-9_]*)", re.I),
    re.compile(r"TRUNCATE\s+(?:TABLE\s+)?([a-z_][a-z0-9_]*)", re.I),
)


def _serving_sources() -> dict[str, str]:
    files: dict[str, str] = {}
    for entry in SERVING_AUTHORED:
        target = SRC / entry
        paths = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in paths:
            files[str(path.relative_to(SRC)).replace("\\", "/")] = path.read_text(encoding="utf-8")
    return files


def test_every_module_the_scan_claims_to_cover_is_actually_read():
    """The scan is only as good as the file list, and a list goes stale."""
    scanned = set(_serving_sources())
    missing = [name for name in REQUIRED_SCANNED if name not in scanned]
    assert not missing, (
        f"{missing} are named in REQUIRED_SCANNED but were not read by _serving_sources(). "
        "Either the module moved and SERVING_AUTHORED needs updating, or it was deleted "
        "and REQUIRED_SCANNED does. Until then every write-target assertion below is "
        "weaker than it looks."
    )


def _real_tables(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        return {row[0] for row in cur.fetchall()}


def _write_targets(source: str, tables: set[str]) -> set[str]:
    """Matched tokens intersected with real table names.

    The intersection is what removes prose false positives - an argparse help
    string reading "truncate and rebuild match_states" otherwise reports a
    table called `and`.
    """
    found: set[str] = set()
    for pattern in _WRITE_PATTERNS:
        found.update(match.group(1).lower() for match in pattern.finditer(source))
    return found & tables


def test_no_serving_code_writes_match_states_or_deliveries(conn):
    """The fact the match_states exemption rests on, enforced.

    match_states keeps its REAL columns ONLY because nothing on a serving path
    writes it, so those values never round-trip through a pooler whose
    extra_float_digits differs from local Postgres'. That was a property of
    the code when the sweep was done, not a rule - and Session 5, which adds
    live match_states and deliveries for the UI, is exactly when someone adds
    the second write without remembering why it mattered. The failure would be
    silent rounding in model features, which no metric would surface.

    If this fails: convert match_states' current_run_rate, required_run_rate,
    rrr_minus_crr and dls_resources_pct to NUMERIC first (3.78M local rows,
    and eval/splits.py starts receiving Decimal), then move the table out of
    EXEMPT above - or revert the write.
    """
    tables = _real_tables(conn)
    offenders = {
        name: sorted(targets)
        for name, source in _serving_sources().items()
        if (targets := _write_targets(source, tables) & {"match_states", "deliveries"})
    }
    assert not offenders, (
        f"serving code now writes corpus tables: {offenders}. See "
        "supabase/migrations/20260915000002_numeric_metrics.sql for why that breaks the "
        "match_states exemption."
    )


def test_the_full_set_of_serving_writes_is_reviewed(conn):
    """Any new write target in serving code fails here, so it is a decision
    rather than something inherited."""
    tables = _real_tables(conn)
    found = {
        name: targets
        for name, source in _serving_sources().items()
        if (targets := _write_targets(source, tables))
    }
    all_targets: set[str] = set().union(*found.values()) if found else set()
    unreviewed = all_targets - ALLOWED_SERVING_WRITES
    assert not unreviewed, (
        f"unreviewed serving-side write targets {sorted(unreviewed)} (in {found}). "
        "Decide whether a deployed container should write these at all, then add them "
        "to ALLOWED_SERVING_WRITES with the reason."
    )
    assert "matches" in all_targets, (
        "no write to matches found, so this test is vacuous - cricketdata.py's "
        "_ensure_match_row should be in the scanned set"
    )


def test_serving_code_does_not_call_the_corpus_rebuilders():
    """Closes the hole the write-scan cannot see.

    recompute_format, rebuild_venue_summary and friends all take a `conn`.
    Nothing stops one being handed the Supabase connection from serving code,
    and config.py's boundary check would not notice - it only forbids
    LOCAL_DATABASE_URL.
    """
    offenders = {
        name: sorted(found)
        for name, source in _serving_sources().items()
        if (found := {mutator for mutator in CORPUS_MUTATORS if mutator in source})
    }
    assert not offenders, (
        f"serving code references corpus rebuilders {offenders}. Those functions accept "
        "any connection, so calling one from a serving process would rebuild against "
        "Supabase - training never touches Supabase (SPEC.md section 2.1)."
    )
