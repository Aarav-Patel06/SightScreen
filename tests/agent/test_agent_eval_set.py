"""The eval set itself, checked before anything runs against it.

SPEC.md §11 makes a 20-question set Phase 6's acceptance criterion, and §10.4
makes the citation behaviour a gate rather than a hope. Both claims are about
the CONTENTS of the set, so they are asserted here - an eval set is the one
artifact whose coverage cannot be established by running it.
"""

from __future__ import annotations

import pytest

from agent_eval.cases import (
    CATEGORIES,
    REQUIRED_CASE_COUNT,
    EvalSetError,
    LiveCase,
    load,
)

EVAL_SET = load()


def test_the_set_loads():
    assert EVAL_SET.live
    assert EVAL_SET.canned


def test_the_set_has_the_twenty_questions_the_acceptance_criterion_names():
    assert len(EVAL_SET.live) == REQUIRED_CASE_COUNT


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_required_category_is_covered(category):
    """§11 names four areas. A set that drifted to nineteen SQL cases and one
    lookup would still be twenty questions and would prove much less."""
    assert EVAL_SET.by_category(category), f"no cases in category {category!r}"


def test_every_case_explains_why_it_exists():
    """A case with no stated reason is a case nobody can safely delete, and
    one nobody can tell has stopped testing what it was added for."""
    for case in EVAL_SET.live + EVAL_SET.canned:
        assert case.why.strip(), f"case {case.id!r} has no why"


def test_every_live_case_asserts_something():
    """The failure this prevents: a question with no assertions runs, costs
    money, and reports green whatever the agent says."""
    for case in EVAL_SET.live:
        asserted = any(
            getattr(case, f)
            for f in LiveCase.__dataclass_fields__
            if f not in {"id", "category", "question", "why"}
        )
        assert asserted, f"live case {case.id!r} asserts nothing"


# --- §10.4 and Gap 3, asserted as coverage -------------------------------


def test_the_gap_three_trap_is_in_the_set():
    """`docs/phase6-session1.md` §5: "If session 2 ships the agent loop
    without enforcing citations in its evals, this gap is open again and the
    tool-layer refusal is exactly the theatre it was accused of being."
    """
    traps = [c for c in EVAL_SET.by_category("form") if c.must_disclose_unavailable]
    assert traps, "no form case requires the unavailability to be disclosed"


def test_the_trap_does_not_depend_on_the_word_form():
    """If the behaviour only appears when the question says "form", the agent
    learned a keyword and not the rule."""
    traps = [c for c in EVAL_SET.by_category("form") if c.must_disclose_unavailable]
    assert any("form" not in c.question.lower() for c in traps), (
        "every unavailability trap contains the word 'form'"
    )


def test_the_descriptive_case_is_in_the_set_too():
    """Gap 3 cuts both ways. "How many runs has X scored recently?" is
    descriptive and query_ball_data should answer it; refusing would make the
    SQL tool useless. Without this case the eval only rewards refusing, and
    an agent that refused everything would score perfectly.
    """
    descriptive = [
        c
        for c in EVAL_SET.by_category("form")
        if "get_player_form" in c.must_not_call
    ]
    assert descriptive, "no form case requires a descriptive question to be ANSWERED"


def test_the_required_failing_example_is_in_the_set():
    """The one the set is incomplete without: a fluent, correct-looking
    average presented as an ability estimate, asserted as a failure.

    A set of passing examples alone cannot distinguish a working checker from
    one that returns `pass` unconditionally.
    """
    trap = EVAL_SET.canned_for("form-trap-is-jaiswal-in-form")
    assert any(c.expect == "fail" for c in trap), (
        "the Gap 3 trap has no failing canned example"
    )
    assert any(c.expect == "pass" for c in trap), (
        "the Gap 3 trap has no passing canned example to contrast against"
    )


def test_every_canned_rule_is_specified_in_both_directions():
    """Each live case with canned examples must have at least one of each
    verdict. A rule demonstrated only by what satisfies it is a rule whose
    check has never been observed to fire."""
    by_case: dict[str, set[str]] = {}
    for canned in EVAL_SET.canned:
        by_case.setdefault(canned.of_case, set()).add(canned.expect)
    for case_id, verdicts in by_case.items():
        assert verdicts == {"pass", "fail"}, (
            f"canned cases for {case_id!r} only cover {sorted(verdicts)}"
        )


def test_zero_successful_injections_is_actually_asserted():
    """§11: "Zero successful injections". Every adversarial case must expect a
    rejection, or the criterion is aspirational."""
    adversarial = EVAL_SET.by_category("adversarial_sql")
    assert adversarial
    for case in adversarial:
        assert case.expect_rejection, f"adversarial case {case.id!r} does not expect a rejection"


# --- the loader refuses malformed sets ------------------------------------


@pytest.mark.parametrize(
    "mutation, fragment",
    [
        ({"schema_version": 99}, "schema_version"),
        ({"live_cases": [{"id": "x", "category": "nope", "question": "q", "why": "w"}]}, "category"),
        ({"live_cases": [{"id": "x", "category": "form", "question": "q", "why": "w", "typo": 1}]}, "unknown field"),
        ({"live_cases": [{"id": "x", "category": "form", "question": "q", "why": "w", "must_call": ["nope"]}]}, "not a tool"),
    ],
)
def test_a_malformed_set_fails_to_load(tmp_path, mutation, fragment):
    """Loudly, not quietly. A set that loads with assertions dropped reports
    green while testing less than it claims."""
    import json

    base = {"schema_version": 1, "live_cases": [], "canned_cases": []}
    base.update(mutation)
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(base), encoding="utf-8")

    with pytest.raises(EvalSetError) as err:
        load(path)
    assert fragment in str(err.value)
