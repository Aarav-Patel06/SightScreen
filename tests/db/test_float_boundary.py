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
