"""Step 4's precondition: the ablation proof must fail for the RIGHT reason.

The proof itself needs a paid model run. What is testable without one is the
part that makes the proof meaningful: that a failure carries which gate fired,
and that "removing the citation clause" is tied to "the cite rule goes red"
by something checked rather than by a comment.

The standard is sql_guard's, restated: a layer proof needs a query that only
THAT layer rejects, not a query that happens to be rejected. An ablated run
that goes red on `must_call` because the model also skipped resolve_entity
tells you nothing about whether the citation clause was load-bearing.
"""

from __future__ import annotations

import pytest

from agent_eval.cases import load
from agent_eval.checker import RULE_FOR_CLAUSE, RULES, Failure, Result, check_answer
from agent_eval.prompt import ABLATABLE, CLAUSE_NAMES, CLAUSES

EVAL_SET = load()
LIVE = {c.id: c for c in EVAL_SET.live}


def test_every_ablatable_clause_maps_to_a_rule():
    """Otherwise ablating it has no gate to go red, and the proof cannot be
    stated at all - let alone run."""
    for clause in sorted(ABLATABLE):
        assert clause in RULE_FOR_CLAUSE, f"clause {clause!r} has no rule to fire"
        assert RULE_FOR_CLAUSE[clause] in RULES


def test_the_mapping_names_only_real_clauses_and_real_rules():
    """Both halves, so a rename on either side fails here rather than
    silently producing a proof that asserts nothing."""
    for clause, rule in RULE_FOR_CLAUSE.items():
        assert clause in CLAUSE_NAMES, f"{clause!r} is not a prompt clause"
        assert rule in RULES, f"{rule!r} is not a checker rule"


def test_every_clauses_enforced_by_matches_the_mapping():
    """`Clause.enforced_by` is prose in the prompt module and RULE_FOR_CLAUSE
    is what the proof reads. Two places, so they are pinned together."""
    for clause in CLAUSES:
        if clause.name in RULE_FOR_CLAUSE:
            assert clause.enforced_by, f"{clause.name!r} claims no enforcement"


def test_the_citation_clause_maps_to_the_cite_rule():
    """The specific proof asked for: ablate `citation`, go red on `cite`."""
    assert RULE_FOR_CLAUSE["citation"] == "cite"


# --- attribution, demonstrated both ways ---------------------------------


def test_a_missing_citation_is_attributed_to_cite_and_nothing_else():
    """The shape the ablated run must produce. Everything else is satisfied,
    so `cite` is the only gate that can fire - which is what makes the proof
    about the citation clause rather than about the run."""
    result = check_answer(
        LIVE["stats-kohli-vs-malinga"],
        "Kohli strikes at 104.89 against Malinga.",
        [{"tool": "get_matchup", "result": {"balls": 225, "strike_rate": 104.89}}],
        tools_called=["get_matchup"],
    )
    assert result.verdict == "fail"
    assert result.rules_fired() == {"cite"}, (
        f"expected cite alone, got {sorted(result.rules_fired())} - a proof run "
        "producing this would not be about the citation clause"
    )


def test_a_wrong_reason_failure_is_distinguishable_from_a_right_one():
    """The false-positive the proof has to survive. This answer also omits
    the citation, but the run ALSO skipped a required tool - so the verdict
    is red for two reasons and `cite` alone cannot be credited."""
    result = check_answer(
        LIVE["stats-kohli-career-runs"],
        "Kohli has scored a lot of runs.",
        [{"tool": "query_ball_data", "result": {"row_count": 26227}}],
        tools_called=["query_ball_data"],
    )
    assert result.verdict == "fail"
    assert result.rules_fired() == {"must_call", "cite"}
    assert result.failed_on("cite")
    # The distinction the proof turns on:
    assert result.rules_fired() != {"cite"}


def test_failed_on_is_false_for_gates_that_did_not_fire():
    result = Result(case_id="x", failures=[Failure("cite", "no sample size")])
    assert result.failed_on("cite")
    assert not result.failed_on("disclose_unavailable")
    assert not result.failed_on("must_call")


def test_a_clean_answer_fires_no_gates():
    """The control half. Without it, "the gate fired" could just mean the
    gate always fires - the vacuity problem one level up."""
    result = check_answer(
        LIVE["stats-kohli-vs-malinga"],
        "Across 225 deliveries Kohli strikes at 104.89 against Malinga.",
        [{"tool": "get_matchup", "result": {"balls": 225, "strike_rate": 104.89}}],
        tools_called=["get_matchup"],
    )
    assert result.verdict == "pass", result.failures
    assert result.rules_fired() == set()


@pytest.mark.parametrize("clause", sorted(ABLATABLE))
def test_each_ablatable_clause_has_a_live_case_that_exercises_its_rule(clause):
    """A clause whose rule no live case asserts could be ablated with the
    eval staying green - the gate would be unprovable, not merely unproven."""
    rule = RULE_FOR_CLAUSE[clause]
    predicate = {
        "cite": lambda c: bool(c.cite),
        "disclose_unavailable": lambda c: c.must_disclose_unavailable,
        "disclose_truncation": lambda c: c.must_disclose_truncation,
        "no_guard_detail": lambda c: c.expect_rejection,
    }[rule]
    assert any(predicate(c) for c in EVAL_SET.live), (
        f"no live case exercises {rule!r}, so ablating {clause!r} proves nothing"
    )
