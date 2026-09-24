"""Tool queries return the RIGHT number, not merely a number.

WHY THIS EXISTS, and why it is a separate file from the compile check.

`get_matchup` counted `wicket_type IS NOT NULL` over every ball the batter
faced, so **any** wicket falling on one of those deliveries was reported as
that bowler dismissing that batter. A non-striker run out was a dismissal.
It compiled perfectly. It passed a twenty-case paid eval, because the
citation rules assert that the answer quotes a number the tool produced -
never that the number is right.

So the existing gates answer three questions and not the fourth:

    was the tool called?          tests/agent/test_tool_coverage.py
    did it return something?      the same, output-asserting form
    does its SQL compile?         tests/db/test_tool_queries_compile.py
    IS THE NUMBER CORRECT?        here, and nowhere else

HOW THE EXPECTED VALUES WERE DERIVED. By hand, from `deliveries` on the
local corpus, with a query deliberately shaped differently from the tool's:
joined through `players` rather than reading the view, and counting
`player_out_id = batter_id` explicitly rather than trusting a FILTER the
tool also uses. Comparing a query against itself proves nothing. The
derivation is quoted at each constant so anyone can re-run it.

WHEN THIS FAILS AFTER A CORPUS RELOAD, that is correct. The numbers are
facts about a fixed corpus; if the corpus changes they change, and a human
should re-derive and re-commit them rather than the test quietly tracking
whatever the tool now says.

Not every tool and not every case - the point is that a silently wrong
aggregate has somewhere to fail, not exhaustive coverage.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"


# --------------------------------------------------------------------------
# V Kohli against A Zampa. Chosen because it is the pair that exposes the bug:
# 386 balls, and the old query returned 10 dismissals where the answer is 9.
#
# The tenth was an ODI on 2019-03-05 in which **V Shankar** was run out while
# Kohli was on strike to Zampa. Kohli was not dismissed. The number a matchup
# exists to report was wrong by one, in the direction that flatters the
# bowler, on a pair with a large enough sample that nobody would question it.
#
# Derived with:
#
#   SELECT count(*)                                            AS balls,
#          sum(d.runs_batter)                                  AS runs,
#          count(*) FILTER (WHERE d.player_out_id = d.batter_id) AS dismissals
#   FROM deliveries d
#   JOIN matches m   ON m.match_id  = d.match_id
#   JOIN players bat ON bat.player_id = d.batter_id
#   JOIN players bowl ON bowl.player_id = d.bowler_id
#   WHERE bat.canonical_name = 'V Kohli'
#     AND bowl.canonical_name = 'A Zampa'
#     AND NOT d.is_super_over
#     AND NOT m.has_reconciliation_anomaly;
#
# The two NOT clauses mirror agent_deliveries' own WHERE, so the hand
# calculation and the view describe the same population.
# --------------------------------------------------------------------------

KOHLI_V_ZAMPA_ALL = {"balls": 386, "runs": 425, "dismissals": 9}

# The same pair restricted to T20, so the optional format filter is covered
# by a value and not only by compiling.
KOHLI_V_ZAMPA_T20 = {"balls": 90, "runs": 123, "dismissals": 3}

# What the pre-fix query returned for the same pair. Asserted as a NEGATIVE:
# if the tool ever reports this again, the non-striker bug is back.
KOHLI_V_ZAMPA_WRONG_DISMISSALS = 10


def _env() -> dict[str, str | None]:
    if not ENV_PATH.exists():
        pytest.skip(f"{ENV_PATH} not found - the tool values need the replica")
    return dotenv_values(ENV_PATH)


@pytest.fixture(scope="module")
def replica():
    env = _env()
    url = env.get("AGENT_SQL_ROLE_DB_URL")
    if not url:
        pytest.skip("AGENT_SQL_ROLE_DB_URL must be set")
    try:
        conn = psycopg.connect(url, connect_timeout=30)
    except psycopg.OperationalError as exc:
        # "could not reach it" is not "the number is wrong". See the same
        # distinction in test_tool_queries_compile.py.
        pytest.skip(f"cannot reach the replica, so nothing was checked: {exc}")
    with conn:
        yield conn


def _matchup(conn, batter: str, bowler: str, fmt: str | None = None) -> dict:
    """Run the tool's own SQL, exactly as the route assembles it."""
    from agent_tools.routes import MATCHUP_SQL

    filters = ["batter = %(batter)s", "bowler = %(bowler)s"]
    params = {"batter": batter, "bowler": bowler}
    if fmt:
        filters.append("format = %(format)s")
        params["format"] = fmt

    sql = MATCHUP_SQL.format(where=" AND ".join(filters))
    with conn.cursor() as cur:
        cur.execute(sql, params)
        balls, runs, dismissals = cur.fetchone()
    conn.rollback()
    return {"balls": balls, "runs": runs, "dismissals": dismissals}


def test_get_matchup_returns_the_hand_computed_answer(replica):
    assert _matchup(replica, "V Kohli", "A Zampa") == KOHLI_V_ZAMPA_ALL


def test_get_matchup_honours_the_format_filter(replica):
    got = _matchup(replica, "V Kohli", "A Zampa", "T20")
    assert got == KOHLI_V_ZAMPA_T20
    assert got["balls"] < KOHLI_V_ZAMPA_ALL["balls"], "the filter did not narrow anything"


def test_a_non_striker_run_out_is_not_a_dismissal_by_this_bowler(replica):
    """The regression, named.

    On 2019-03-05 V Shankar was run out while Kohli faced Zampa. The old
    query counted it against Kohli. If this number is 10 again, the FILTER
    has drifted back to `wicket_type IS NOT NULL`.
    """
    got = _matchup(replica, "V Kohli", "A Zampa")
    assert got["dismissals"] != KOHLI_V_ZAMPA_WRONG_DISMISSALS, (
        "dismissals is back to the pre-fix count - a wicket that fell on a ball "
        "this batter faced is being attributed to this batter"
    )
    assert got["dismissals"] == KOHLI_V_ZAMPA_ALL["dismissals"]


def test_the_expected_values_still_describe_the_corpus(replica):
    """Re-derive from base tables and compare against the committed numbers.

    Not a duplicate of the assertions above: those check the TOOL against a
    constant, this checks the CONSTANT against the corpus. If a reload
    changes the data, this fails first and says the constants need
    re-deriving, rather than the tool tests failing and reading like a code
    regression.

    Deliberately shaped unlike the tool's query - base tables, explicit
    joins through `players`, no view.
    """
    with replica.cursor() as cur:
        try:
            cur.execute(
                """
                SELECT count(*), sum(d.runs_batter),
                       count(*) FILTER (WHERE d.player_out_id = d.batter_id)
                FROM deliveries d
                JOIN matches m    ON m.match_id = d.match_id
                JOIN players bat  ON bat.player_id = d.batter_id
                JOIN players bowl ON bowl.player_id = d.bowler_id
                WHERE bat.canonical_name = 'V Kohli'
                  AND bowl.canonical_name = 'A Zampa'
                  AND NOT d.is_super_over
                  AND NOT m.has_reconciliation_anomaly
                """
            )
            balls, runs, dismissals = cur.fetchone()
        except psycopg.errors.InsufficientPrivilege:
            # The agent role is denied the base tables by design - that is
            # the whole point of the view layer, and replica.py asserts it.
            # Said out loud rather than passed: this half of the check did
            # not run.
            replica.rollback()
            pytest.skip(
                "the agent role cannot read base tables (by design), so the "
                "constants could not be re-derived here - run the derivation "
                "in the file header against LOCAL_DATABASE_URL instead"
            )
    replica.rollback()

    assert {"balls": balls, "runs": runs, "dismissals": dismissals} == KOHLI_V_ZAMPA_ALL, (
        "the corpus no longer matches the committed expected values - re-derive "
        "them with the query in this file's header and commit the new numbers"
    )
