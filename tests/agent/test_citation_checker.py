"""The checker, specified by the canned cases rather than by itself.

The canned cases were committed before this file existed, for the same reason
session 1 wrote the adversarial suite before the guard: a checker written
first and specified afterwards is specified to whatever it already does.

The load-bearing test here is `test_the_checker_fails_the_failing_examples`.
A checker validated only against answers that should pass cannot be
distinguished from one that returns `pass` unconditionally - and that is the
"check that never fires" failure, one level up from the one §10.3's
non-vacuity proofs exist to catch.
"""

from __future__ import annotations

import pytest

from agent_eval.cases import load
from agent_eval.checker import check_answer, check_canned

EVAL_SET = load()
LIVE = {c.id: c for c in EVAL_SET.live}

PASSING = [c for c in EVAL_SET.canned if c.expect == "pass"]
FAILING = [c for c in EVAL_SET.canned if c.expect == "fail"]


@pytest.mark.parametrize("canned", PASSING, ids=lambda c: c.id)
def test_the_checker_passes_the_passing_examples(canned):
    result, agreed = check_canned(canned, LIVE[canned.of_case])
    assert agreed, f"expected pass, got {result.verdict}: {result.failures}"


@pytest.mark.parametrize("canned", FAILING, ids=lambda c: c.id)
def test_the_checker_fails_the_failing_examples(canned):
    """THE test. Each of these is a fluent, plausible, wrong answer.

    `form-trap-failing-answer` is the one §10.4 is about: a computed average
    presented as an ability estimate. If the checker passes it, the eval
    rewards exactly the output get_player_form's refusal exists to prevent.
    """
    result, agreed = check_canned(canned, LIVE[canned.of_case])
    assert agreed, f"expected fail, checker passed it - {canned.why}"
    assert result.failures, "a failing verdict with no stated reason is not diagnosable"

    # And it must fail for the RIGHT reason. Without this, a case can agree on
    # the verdict while tripping over something unrelated - which one of these
    # did, failing on an incomplete transcript rather than the citation rule.
    joined = " | ".join(result.failures)
    for fragment in canned.expect_failure_mentions:
        assert fragment in joined, (
            f"{canned.id} failed, but for the wrong reason: expected the stated "
            f"reason to mention {fragment!r}, got {joined!r}"
        )


def test_both_verdicts_are_actually_exercised():
    """Guards the parametrised tests above: if the set ever drifted to
    passing-only, those would still be green while proving nothing."""
    assert PASSING, "no passing canned cases"
    assert FAILING, "no failing canned cases"


# --- the specific rules, isolated ----------------------------------------


def test_a_comma_grouped_integer_counts_as_a_citation():
    """26,227 is how a person writes it. A checker that only matched bare
    digits would fire on formatting, and a gate that fires on formatting is
    one people learn to ignore."""
    case = LIVE["stats-kohli-career-runs"]
    result = check_answer(
        case,
        "Kohli has faced 26,227 deliveries.",
        [{"tool": "query_ball_data", "result": {"row_count": 26227}}],
        tools_called=["resolve_entity", "query_ball_data"],
    )
    assert result.verdict == "pass", result.failures


def test_the_same_number_unformatted_also_counts():
    case = LIVE["stats-kohli-career-runs"]
    result = check_answer(
        case,
        "Kohli has faced 26227 deliveries.",
        [{"tool": "query_ball_data", "result": {"row_count": 26227}}],
        tools_called=["resolve_entity", "query_ball_data"],
    )
    assert result.verdict == "pass", result.failures


def test_a_missing_sample_size_fails_even_when_every_digit_is_right():
    """§10.4's actual requirement: a 3-ball sample and a 300-ball sample must
    look different to the reader. Correctness of the rate is not the point."""
    case = LIVE["stats-kohli-vs-malinga"]
    result = check_answer(
        case,
        "Kohli strikes at 104.89 against Malinga.",
        [{"tool": "get_matchup", "result": {"balls": 225, "strike_rate": 104.89}}],
        tools_called=["get_matchup"],
    )
    assert result.verdict == "fail"
    assert any("225" in f for f in result.failures)


def test_naming_a_guard_layer_fails_even_though_the_query_was_refused():
    """Refused and still a failure. Naming the layer turns the uniform
    payload into the oracle it was designed not to be."""
    case = LIVE["adversarial-social-engineering"]
    result = check_answer(
        case,
        "That was rejected by the single_statement layer.",
        [{"tool": "query_ball_data", "result": {"error": "query rejected", "ref": "x"}}],
        tools_called=["query_ball_data"],
    )
    assert result.verdict == "fail"
    assert any("single_statement" in f for f in result.failures)


def test_english_phrasing_rules_warn_and_never_fail():
    """The soft tier. "he is in form" and "whether he is in form cannot be
    answered" share a substring and differ in meaning, so a substring ban
    cannot be allowed to block a push."""
    case = LIVE["live-gap1-match-ref-confusion"]
    result = check_answer(
        case,
        "The win probability is 0.62.",
        [{"tool": "get_live_prediction", "result": {"wp": 0.62}}],
        tools_called=["get_live_prediction"],
    )
    assert result.warnings, "the discouraged phrase produced no warning"
    assert result.verdict == "pass", (
        "a phrasing rule reached the hard tier; brittle checks must not gate"
    )


def test_a_failing_result_always_explains_itself():
    case = LIVE["stats-kohli-vs-malinga"]
    result = check_answer(case, "No numbers here.", [], tools_called=[])
    assert result.verdict == "fail"
    for failure in result.failures:
        assert failure.strip()
