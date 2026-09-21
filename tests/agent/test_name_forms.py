"""Deterministic name matching, and the class of failure it removes.

Measured against the live resolver 2026-09-21, before this existed:

    "Virat Kohli"      -> A Kohli 83.3, S Kohli 83.3, T Kohli 83.3,
                          V Kohli 83.3        four-way tie
    "Lasith Malinga"   -> E Malinga 87.5, T Malinga 87.5,
                          SL Malinga 82.4     wrong ones ranked HIGHER
    "Yashasvi Jaiswal" -> AV Jaiswal 82.4, YBK Jaiswal 77.8   same

Those three are the tests that matter. The rest cover the shapes that make
the transformation non-obvious - multi-part surnames, names already stored as
initials, and given names the speaker never said.
"""

from __future__ import annotations

import pytest

from agent_tools.name_forms import (
    canonical_forms,
    initials_compatible,
    initials_of,
    narrow,
    split_name,
)


# --- the three measured failures -----------------------------------------


def test_virat_kohli_narrows_to_one():
    """The four-way tie, resolved without any similarity score."""
    candidates = ["A Kohli", "S Kohli", "T Kohli", "V Kohli", "Parth Kohli", "Vivek Kohli"]
    assert narrow("Virat Kohli", candidates) == ["V Kohli"]


def test_lasith_malinga_keeps_only_the_ls():
    """Narrows four to two, and two is the HONEST answer: SL and LN Malinga
    both really do have an L, so they are genuinely ambiguous on initials and
    the agent should ask rather than guess. E and T Malinga are gone."""
    candidates = ["E Malinga", "T Malinga", "LN Malinga", "SL Malinga"]
    assert narrow("Lasith Malinga", candidates) == ["LN Malinga", "SL Malinga"]


def test_yashasvi_jaiswal_drops_the_wrong_one_that_outranked_it():
    candidates = ["AV Jaiswal", "YBK Jaiswal", "Mickil Jaiswal"]
    assert narrow("Yashasvi Jaiswal", candidates) == ["YBK Jaiswal"]


def test_the_stored_initials_may_include_names_nobody_says():
    """Why exact-match-on-generated-form is not enough on its own. Generating
    "L Malinga" and requiring equality would miss "SL Malinga" entirely."""
    assert "L Malinga" in canonical_forms("Lasith Malinga")
    assert "SL Malinga" not in canonical_forms("Lasith Malinga")
    assert initials_compatible("Lasith Malinga", "SL Malinga")


# --- form generation ------------------------------------------------------


@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("Virat Kohli", ["V Kohli"]),
        ("Mahendra Singh Dhoni", ["MS Dhoni", "M Dhoni"]),
        ("Rohit Sharma", ["R Sharma"]),
        ("AB de Villiers", ["AB de Villiers", "A de Villiers"]),
    ],
)
def test_canonical_forms(spoken, expected):
    assert canonical_forms(spoken) == expected


def test_a_bare_surname_generates_nothing():
    """"Kohli" alone carries no given-name evidence, so there is no form to
    generate and nothing to narrow on - which is correct, and is what makes
    a surname-only question genuinely ambiguous."""
    assert canonical_forms("Kohli") == []


# --- multi-part surnames, the case flagged as risky ----------------------


@pytest.mark.parametrize(
    "spoken, given, surname",
    [
        ("AB de Villiers", ["AB"], "de Villiers"),
        ("Abraham de Villiers", ["Abraham"], "de Villiers"),
        ("Faf du Plessis", ["Faf"], "du Plessis"),
        ("Colin de Grandhomme", ["Colin"], "de Grandhomme"),
        ("Virat Kohli", ["Virat"], "Kohli"),
    ],
)
def test_particles_belong_to_the_surname(spoken, given, surname):
    assert split_name(spoken) == (given, surname)


def test_an_already_initialled_token_keeps_all_its_letters():
    """"AB de Villiers" must yield AB, not A - otherwise the generated form
    is not the one stored."""
    assert initials_of(["AB"]) == "AB"
    assert initials_of(["Abraham", "Benjamin"]) == "AB"


def test_a_particle_is_never_consumed_as_the_whole_name():
    assert split_name("de Villiers") == (["de"], "Villiers")


# --- the word branch, which the initial branch must not swallow ----------


def test_a_stored_full_given_name_is_compared_as_a_word():
    """"Vivek Kohli" is stored with a WORD, and "Virat" is a different word.
    An initials test would keep it, because both start with V."""
    assert not initials_compatible("Virat Kohli", "Vivek Kohli")
    assert initials_compatible("Vivek Kohli", "Vivek Kohli")


def test_a_different_surname_never_matches():
    assert not initials_compatible("Virat Kohli", "V Sharma")


def test_subsequence_is_order_preserving():
    """Initials are ordered, and accepting any permutation would start
    merging different people. Said "Lasith Separamadu" -> LS; stored "SL" -
    L then S does not occur in that order, so it must not match."""
    assert initials_compatible("Lasith Malinga", "SL Malinga")
    assert not initials_compatible("Lasith Separamadu Malinga", "SL Malinga")
    assert initials_compatible("Separamadu Lasith Malinga", "SL Malinga")


# --- the fallback contract ------------------------------------------------


def test_no_compatible_candidate_returns_empty_not_everything():
    """The caller falls back to fuzzy on []. Returning the full list on no
    match would put this exactly back where it started - which is how the
    first version of the surname bucket produced "Rahat Ali" for
    "Viraat Kolhi"."""
    assert narrow("Virat Kohli", ["A Sharma", "B Singh"]) == []


def test_a_bare_surname_query_keeps_every_namesake():
    """The clarification case's input. "Kohli" matches every Kohli, which is
    what a genuinely ambiguous question should do."""
    candidates = ["A Kohli", "S Kohli", "T Kohli", "V Kohli"]
    assert narrow("Kohli", candidates) == candidates
