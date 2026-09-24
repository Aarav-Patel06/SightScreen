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


# --- the three-rep subset ------------------------------------------------


def test_every_citation_case_is_in_the_all_reps_must_pass_subset():
    """"2-of-3 is a failure, not a flake" only bites if the case gets three
    reps. `_is_honesty` selects that subset, and it checked only the `cite`
    field - so when stats-venue-scoring moved to cite_values_from_first_row
    it silently fell back to ONE rep. Nothing failed; it just stopped being
    measured the way it was meant to be.
    """
    from agent_eval.runner import _is_honesty

    for case in EVAL_SET.live:
        asserts_a_citation = bool(case.cite) or bool(case.cite_values_from_first_row)
        if asserts_a_citation:
            assert _is_honesty(case), (
                f"{case.id!r} asserts a citation but is not in the all-reps-must-pass "
                "subset, so an intermittent failure there would not be caught"
            )


def test_the_subset_size_is_what_the_cost_estimate_assumed():
    """38 conversations per pass: 9 cases at 3 reps, 11 at 1. A change that
    moved a case between subsets would quietly change both the cost and the
    strictness.

    Was 8 and 12, for 36. `live-prediction-by-id` gained a citation on
    2026-09-24 and moved across, which cost two more conversations a pass
    and is the point: until then that case asserted `must_call` alone, and
    `get_live_prediction` had been raising UndefinedColumn on every call it
    ever received while the case stayed green. This gate firing on the
    change is it working - the cost of coverage should be a decision, not a
    surprise on an invoice.
    """
    from agent_eval.runner import HONESTY_REPS, OTHER_REPS, _is_honesty

    honesty = [c for c in EVAL_SET.live if _is_honesty(c)]
    other = [c for c in EVAL_SET.live if not _is_honesty(c)]
    assert len(honesty) == 9, [c.id for c in honesty]
    assert len(other) == 11
    assert len(honesty) * HONESTY_REPS + len(other) * OTHER_REPS == 38


# --- the adversarial set cannot be satisfied by refusing everything ------


def test_every_adversarial_case_names_the_payload_it_forbids():
    """`expect_rejection` alone asserts nothing now: it needs to know WHICH
    string must not reach the database. A case without one would pass on any
    behaviour at all."""
    for case in EVAL_SET.by_category("adversarial_sql"):
        assert case.injection_fragment, (
            f"{case.id!r} expects a rejection but names no payload, so nothing is checked"
        )


def test_some_adversarial_case_requires_a_real_answer():
    """The both-directions guard, inside the adversarial set.

    "The payload never executed" is satisfied perfectly by an agent that
    refuses every question. Without at least one case whose legitimate half
    must still be answered, total paralysis scores 6/6 here - the same
    asymmetry that made the Gap 3 trap need a descriptive twin.
    """
    paired = [c for c in EVAL_SET.by_category("adversarial_sql") if c.benign_half_must_succeed]
    assert paired, (
        "no adversarial case requires its legitimate half to be answered, so an agent "
        "that refuses everything would score perfectly on this category"
    )


def test_a_refuse_everything_agent_fails_the_paired_cases():
    """Proven rather than asserted: feed the checker a total refusal and
    watch the paired cases go red."""
    from agent_eval.checker import check_answer

    for case in EVAL_SET.by_category("adversarial_sql"):
        result = check_answer(case, "I cannot run that query.", [], tools_called=[])
        if case.benign_half_must_succeed:
            assert result.failed_on("benign_half"), (
                f"{case.id!r} accepted a blanket refusal"
            )
        else:
            assert result.verdict == "pass", (
                f"{case.id!r} has no legitimate half, so refusal must be a pass"
            )


# --- the staleness marker ------------------------------------------------


def test_the_results_staleness_marker_agrees_with_the_fingerprints():
    """`results.json` carries a `stale` flag. This asserts it tells the truth.

    The flag exists because the file's own numbers stay accurate when it goes
    stale: "35 of 36" is not a wrong answer, it is an answer to a question set
    that has since changed, and every other field around it is still correct.
    That is the same shape as a synced-but-empty column - it looks current
    because its neighbours are - so the file says so in words rather than
    leaving it to a CI job somebody has to read.

    A marker that is itself wrong would be worse than none, in both
    directions: a file marked stale after a re-run trains people to ignore
    the flag, and a file marked fresh while stale is the original bug with a
    reassuring label on it.
    """
    import json

    from agent_eval.cases import fingerprint as cases_fingerprint
    from agent_eval.prompt import fingerprint as prompt_fingerprint
    from agent_eval.runner import RESULTS_PATH

    data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    actually_stale = (
        data.get("prompt_fingerprint") != prompt_fingerprint()
        or data.get("cases_fingerprint") != cases_fingerprint()
    )
    marked_stale = bool(data.get("stale"))

    if actually_stale:
        assert marked_stale, (
            "results.json no longer matches the current prompt or cases, and does "
            "not say so. Add `stale: true` with a reason, or re-run "
            "`python -m agent_eval.runner --all`."
        )
        assert data.get("stale_reason"), "`stale: true` with no `stale_reason` says nothing useful"
        assert data.get("stale_clears_when"), (
            "a staleness marker needs to say what would clear it, or it becomes permanent"
        )
    else:
        assert not marked_stale, (
            "results.json matches both fingerprints but is still marked stale - "
            "a marker that outlives its cause trains people to ignore the next one"
        )
