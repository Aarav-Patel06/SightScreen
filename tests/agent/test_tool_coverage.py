"""Every tool is exercised by a case that would notice it breaking.

WHY THIS EXISTS. `get_live_prediction` selected a column that has never
existed and raised on every call it ever received, through a twenty-case
paid eval run. It was not missing from the eval set: two live cases name it
in `must_call`. Both assert `must_call` **and nothing else**, so the tool
500ing, or returning an error payload, or returning nothing at all, still
satisfies them. The assertion was "the model reached for this tool", which
was true, rather than "this tool answered", which was not.

So the shape mirrored here from `test_ablation_attribution.py`'s
`test_each_ablatable_clause_has_a_live_case_that_exercises_its_rule` is
deliberately the STRONGER one. "Has a case" would pass today and would have
passed throughout the bug's life. What is required instead is a case that
asserts something about what the tool RETURNED - a citation rooted at it, or
words the answer must contain, which a broken tool cannot produce.

THE HONEST LIMIT. A case whose assertion depends on the tool's output is
still not a unit test of that tool: it needs a paid model run to fire, and
`get_live_prediction` additionally needs a live match, which does not exist
on demand. That is why the compile check lives separately, in
`tests/db/test_tool_queries_compile.py`, and runs against the real schema
with no model and no match. This file makes the eval set honest about its
own coverage; that one catches the class of bug that got through.
"""

from __future__ import annotations

import pytest

from agent_eval import tools as tools_module
from agent_eval.cases import load

EVAL_SET = load()

# Tools that cannot have an output-asserting case, each for a stated reason.
# Listing them here rather than skipping them silently is the point: an
# exemption somebody has to type is one somebody has to justify.
#
# get_player_form runs no SQL and exists to refuse - its cases assert
# `must_disclose_unavailable`, which IS an output assertion, so it is not
# actually exempt and is not listed.
# Empty, and set() rather than {} because {} is a dict. Every one of the five
# tools currently has an output-asserting case or can have one:
# query_ball_data's four cases cite `query_ball_data.row_count`, and
# get_player_form's assert `must_disclose_unavailable`, which is an output
# assertion in the only form a refusal can have.
NO_FIXED_OUTPUT: set[str] = set()


def _asserts_something_about_output(case, tool: str) -> bool:
    """Would this case notice if `tool` returned nothing useful?

    A citation rooted at the tool is the strongest form - the answer has to
    quote a value the tool produced. `must_say_any` and the disclosure flags
    also require the model to have learnt something it could only learn from
    a working tool.
    """
    if any(path.split(".")[0] == tool for path in case.cite):
        return True
    if case.cite_values_from_first_row > 0:
        return True
    if case.must_say_any:
        return True
    return case.must_disclose_unavailable or case.must_disclose_truncation


@pytest.mark.parametrize("tool", sorted(tools_module.TOOL_NAMES))
def test_each_tool_has_a_live_case_that_calls_it(tool):
    """The weak form, kept because it is the precondition for the strong one
    and its failure message is clearer."""
    assert any(tool in case.must_call for case in EVAL_SET.live), (
        f"no live case calls {tool!r}, so nothing in the eval set would notice it breaking"
    )


@pytest.mark.parametrize("tool", sorted(set(tools_module.TOOL_NAMES) - NO_FIXED_OUTPUT))
def test_each_tool_has_a_case_that_would_notice_it_returning_nothing(tool):
    """The form that bites, and the one this file exists for.

    `get_live_prediction` fails this today. Its two cases assert `must_call`
    alone, which a tool raising UndefinedColumn satisfies perfectly.
    """
    callers = [case for case in EVAL_SET.live if tool in case.must_call]
    assert callers, f"no live case calls {tool!r}"

    asserting = [case for case in callers if _asserts_something_about_output(case, tool)]
    assert asserting, (
        f"{tool!r} is called by {[c.id for c in callers]}, and none of them assert "
        f"anything about what it returned. A tool that raises on every call "
        f"satisfies `must_call`. Add a `cite` rooted at {tool!r}, or a "
        f"`must_say_any`, to at least one of them."
    )


def test_the_tool_list_is_not_a_fourth_hand_maintained_copy():
    """Four enumerations of the five tools exist: TOOLS/ENDPOINTS in
    agent_eval.tools, the router's own routes, cases.TOOL_NAMES, and
    test_tool_auth.ENDPOINTS. Only the first two are pinned together, and
    only in one direction.

    This pins the third. A tool added to the router and the definitions but
    not to the loader's validation list would make every `must_call` naming
    it fail to load, which reads as a broken eval set rather than a missing
    registration.
    """
    from agent_eval import cases as cases_module

    assert set(cases_module.TOOL_NAMES) == set(tools_module.TOOL_NAMES), (
        "agent_eval.cases.TOOL_NAMES has drifted from agent_eval.tools.TOOL_NAMES"
    )


def test_the_router_serves_nothing_the_tool_list_does_not_declare():
    """The missing direction of test_agent_artifacts' endpoint check.

    That one asserts every declared tool has a route. This asserts the
    reverse: a route added without a TOOLS entry is a tool the model is
    never told about, the eval never calls, and nothing here covers - which
    is a quieter version of exactly this file's subject.
    """
    from agent_tools import routes

    served = {r.path for r in routes.router.routes if r.path.startswith("/agent/")}
    declared = set(tools_module.ENDPOINTS.values())

    assert served == declared, (
        f"routes with no tool definition: {sorted(served - declared)}; "
        f"definitions with no route: {sorted(declared - served)}"
    )
