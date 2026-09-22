"""The system prompt's structure, and the seam the non-vacuity proof needs.

§10.4 is only a gate if it can be shown to fail. `sql_guard.check` earned
that with `skip=`; `prompt.build` earns it with `ablate=`. This file asserts
the seam works and that the served path never uses it - the same pair of
guarantees `test_the_production_entry_point_never_skips_a_layer` gives
layer 4.
"""

from __future__ import annotations

import pytest

from agent_eval.prompt import (
    ABLATABLE,
    CLAUSE_NAMES,
    CLAUSES,
    build,
    emit_typescript,
    fingerprint,
)


def test_the_production_prompt_ablates_nothing():
    """The default. A seam for disabling a safety clause must not be
    reachable by forgetting an argument."""
    full = build()
    for clause in CLAUSES:
        assert clause.text in full, f"clause {clause.name!r} missing from the default prompt"


@pytest.mark.parametrize("name", sorted(ABLATABLE))
def test_each_ablatable_clause_can_actually_be_removed(name):
    """If ablation silently did nothing, the non-vacuity proof would 'pass'
    by testing the unmodified prompt - which is the exact failure the proof
    exists to detect, one level up."""
    clause = next(c for c in CLAUSES if c.name == name)
    assert clause.text in build()
    assert clause.text not in build(ablate=frozenset({name}))


def test_ablating_one_clause_leaves_the_others_intact():
    reduced = build(ablate=frozenset({"citation"}))
    for clause in CLAUSES:
        if clause.name != "citation":
            assert clause.text in reduced


def test_an_unknown_ablation_name_is_refused():
    """A typo would ablate nothing and the proof would report green."""
    with pytest.raises(ValueError) as err:
        build(ablate=frozenset({"citaton"}))
    assert "citaton" in str(err.value)


def test_every_ablatable_clause_is_a_real_clause():
    assert ABLATABLE <= set(CLAUSE_NAMES)


def test_every_clause_names_what_enforces_it():
    """A clause with no eval assertion behind it is an intention, not a
    control - §10.4's own words about the citation rule. `enforced_by` makes
    that reviewable instead of assumed."""
    for clause in CLAUSES:
        assert clause.enforced_by, f"clause {clause.name!r} names no enforcement"


def test_the_citation_clause_is_ablatable():
    """The one the whole seam exists for. If this clause were not ablatable,
    §10.4 would have no non-vacuity proof available at all."""
    assert "citation" in ABLATABLE


def test_the_fingerprint_changes_when_the_prompt_changes(monkeypatch):
    """CI compares a committed eval result's fingerprint against a fresh one
    to enforce that the model-calling half was re-run. A fingerprint that
    did not move on a prompt edit would let stale results look current."""
    before = fingerprint()
    import agent_eval.prompt as prompt_module

    edited = tuple(
        prompt_module.Clause(c.name, c.text + " Also be concise.", c.enforced_by)
        if c.name == "citation"
        else c
        for c in prompt_module.CLAUSES
    )
    monkeypatch.setattr(prompt_module, "CLAUSES", edited)
    assert fingerprint() != before


def test_the_emitted_typescript_is_generated_not_edited():
    out = emit_typescript()
    assert "GENERATED" in out
    assert "do not edit" in out
    assert "SYSTEM_PROMPT" in out
    assert "PROMPT_FINGERPRINT" in out


# --- the tool descriptions carry the citation rule too -------------------


def test_the_production_tools_ablate_nothing():
    """The same guarantee build() gives the prompt. A seam for removing a
    safety instruction must not be reachable by forgetting an argument."""
    from agent_eval.tools import TOOLS, build_tools

    assert build_tools() == TOOLS


def test_ablating_citation_strips_it_from_the_tool_descriptions_too():
    """The first ablation proof FAILED TO FIRE because of this. The citation
    requirement is written in the system prompt AND in the tool descriptions,
    so removing the clause left get_matchup still saying "`balls` is the
    sample size and must appear alongside any rate you quote from it" - and
    the agent cited, correctly, from an instruction the proof thought it had
    removed. An ablation that leaves half the instruction standing is not an
    ablation.
    """
    from agent_eval.tools import build_tools

    full = {t["name"]: t["description"] for t in build_tools()}
    ablated = {t["name"]: t["description"] for t in build_tools(ablate_citation=True)}
    assert "sample size" in full["get_matchup"]
    assert "sample size" not in ablated["get_matchup"]
    assert "sample size" in full["query_ball_data"]
    assert "sample size" not in ablated["query_ball_data"]


def test_a_reworded_description_makes_the_ablation_raise_not_no_op():
    """A silent no-op would make the proof pass by testing nothing - the
    same failure mode build() guards with its unknown-clause ValueError."""
    import pytest

    from agent_eval import tools as tools_module

    original = tools_module.TOOLS
    try:
        tools_module.TOOLS = tuple(
            {**t, "description": "reworded"} if t["name"] == "get_matchup" else t
            for t in original
        )
        with pytest.raises(ValueError, match="get_matchup"):
            tools_module.build_tools(ablate_citation=True)
    finally:
        tools_module.TOOLS = original
