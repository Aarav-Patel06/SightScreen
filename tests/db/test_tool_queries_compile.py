"""Every tool's SQL, compiled against the database it actually runs on.

WHY THIS EXISTS. `get_live_prediction` selected `win_probability` - a column
that has never existed on `predictions`, which carries a `payload JSONB`
instead. The query raised `UndefinedColumn` on every call it had ever
received. It survived a twenty-case paid eval, a full adversarial SQL-guard
suite, a per-endpoint auth suite covering all five tools, and a live-replica
bootstrap verifier, because not one of them ever sent a tool's SQL to a
database:

  * `test_sql_guard_adversarial.py` calls `check()`, a string transformer.
  * `test_tool_auth.py` parametrises all five endpoints and every call
    short-circuits at auth, so no SQL runs.
  * `replica.py`'s verifier runs `SELECT * FROM agent_<view> LIMIT 1`, which
    proves the views are readable and cannot notice a tool asking for a
    column they do not have.
  * The eval's two `get_live_prediction` cases assert `must_call` and nothing
    about the result, so a 500 satisfies them.

PREPARE, not execute. The server parses and plans the statement - which is
where an unknown column or table is caught - without reading a row or
depending on any particular data being present. So this is fast, safe against
both databases, and tests the thing that was wrong rather than the thing that
happened to be missing.

WHAT THIS DOES NOT COVER, said plainly because the file's name overstates its
reach: it proves a statement RESOLVES, not that it returns the right answer.
`get_matchup` counted a non-striker's run out as the batter's dismissal for
months while compiling perfectly. A compile test is a floor.

Third database. `tests/db/test_schema_parity.py` deliberately knows only the
two that migrations govern - its own docstring explains that the replica is a
rebuildable projection and not a third member of that regime - so this lives
beside it rather than inside it.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"


def _env() -> dict[str, str | None]:
    if not ENV_PATH.exists():
        pytest.skip(f"{ENV_PATH} not found - the tool queries need real databases")
    return dotenv_values(ENV_PATH)


def _tool_statements():
    """(tool, database, sql, parameter count) for every statement a tool runs.

    Imported from the route module rather than retyped. A copy here would
    compile cleanly for as long as it took the real one to change.
    """
    from agent_tools.routes import (
        LIVE_PREDICTION_SQL,
        MATCHUP_SQL,
        RESOLVE_SQL,
        RESOLVE_VIEWS,
    )

    cases = [
        # Both arms of the optional format filter: the version with it is a
        # different statement, and only one of the two is exercised per call.
        (
            "get_matchup",
            "replica",
            MATCHUP_SQL.format(where="batter = %(batter)s AND bowler = %(bowler)s"),
            {"batter": "x", "bowler": "y"},
        ),
        (
            "get_matchup[format]",
            "replica",
            MATCHUP_SQL.format(
                where="batter = %(batter)s AND bowler = %(bowler)s AND format = %(format)s"
            ),
            {"batter": "x", "bowler": "y", "format": "T20"},
        ),
        ("get_live_prediction", "supabase", LIVE_PREDICTION_SQL, (1,)),
    ]
    for kind, (view, id_column) in RESOLVE_VIEWS.items():
        cases.append(
            (
                f"resolve_entity[{kind}]",
                "replica",
                RESOLVE_SQL.format(id_column=id_column, view=view),
                None,
            )
        )
    return cases


CASES = _tool_statements()


@pytest.fixture(scope="module")
def urls() -> dict[str, str]:
    env = _env()
    replica = env.get("AGENT_SQL_ROLE_DB_URL")
    supabase = env.get("SUPABASE_SESSION_POOLER_URL")
    if not replica or not supabase:
        pytest.skip("AGENT_SQL_ROLE_DB_URL and SUPABASE_SESSION_POOLER_URL must both be set")
    return {"replica": replica, "supabase": supabase}


@pytest.mark.parametrize("tool,database,sql,params", CASES, ids=[c[0] for c in CASES])
def test_the_statement_compiles(urls, tool, database, sql, params):
    """The server must be able to parse and plan it.

    Rolled back rather than committed, and PREPARE rather than EXECUTE, so
    nothing is read and nothing is written. A failure here is a column or a
    table the schema does not have.
    """
    try:
        conn = psycopg.connect(urls[database], connect_timeout=30)
    except psycopg.OperationalError as exc:
        # "could not reach it" is not "the query is wrong", and conflating
        # them makes this go red on a DNS blip until people stop reading it
        # (standing rule 14). Skipped loudly rather than passed silently, so
        # a run that proved nothing says so - standing rule 10.
        pytest.skip(f"cannot reach the {database} database, so nothing was checked: {exc}")

    with conn:
        try:
            with conn.cursor() as cur:
                # psycopg's own prepare path needs the parameter shape to
                # match, so the statement is prepared with its real
                # placeholders bound to throwaway values.
                cur.execute(f"SELECT 1 FROM ({sql}) AS _compile_probe WHERE false", params)
        except psycopg.errors.UndefinedColumn as exc:
            pytest.fail(f"{tool}: selects a column that does not exist - {exc}")
        except psycopg.errors.UndefinedTable as exc:
            pytest.fail(f"{tool}: reads a table or view that does not exist - {exc}")
        except psycopg.errors.UndefinedFunction as exc:
            pytest.fail(f"{tool}: uses a function or cast that does not resolve - {exc}")
        finally:
            conn.rollback()


def test_the_registry_covers_every_tool_that_runs_sql():
    """A tool added without a statement here would be untested by exactly the
    mechanism that let win_probability through.

    get_player_form runs no SQL - it returns a structured refusal - and
    query_ball_data's SQL is written by the model, so neither can be
    compiled ahead of time. Both are named rather than silently absent.
    """
    from agent_eval import tools as tools_module

    NO_SQL = {"get_player_form"}
    MODEL_AUTHORED = {"query_ball_data"}

    covered = {case[0].split("[")[0] for case in CASES}
    expected = set(tools_module.TOOL_NAMES) - NO_SQL - MODEL_AUTHORED

    assert covered == expected, (
        f"tools with fixed SQL that this file does not compile: {sorted(expected - covered)}; "
        f"compiled but not a known tool: {sorted(covered - expected)}"
    )
