"""The name fold behind the Phase 5 precondition scan.

The scan itself needs the corpus and is a command, not a test. The fold is
pure and is where a mistake would hide: too loose and it merges two real
people, too tight and it misses the split it exists to find.
"""

from __future__ import annotations

import pytest

from ingest.check_player_identity import active_splits, fold_name


def test_the_real_split_folds_together():
    """The actual pair in the corpus, differing only by a hyphen."""
    assert fold_name("A Davidson Soler") == fold_name("A Davidson-Soler")


@pytest.mark.parametrize(
    "a, b",
    [
        ("CJ Tavar\u00e9", "CJ Tavare"),
        ("Maria Casti\u00f1eiras", "Maria Castineiras"),
        ("S Fouch\u00e9", "S Fouche"),
        ("M O\u2019Brien", "M O'Brien"),
        ("A  B  Smith", "A B Smith"),
        ("de Kock, Q", "de Kock Q"),
    ],
)
def test_folding_collapses_accents_quotes_and_punctuation(a, b):
    assert fold_name(a) == fold_name(b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("V Kohli", "R Kohli"),
        ("RG Sharma", "R Sharma"),
        ("MS Dhoni", "MSK Dhoni"),
        ("A Davidson", "A Davidson Soler"),
    ],
)
def test_folding_does_not_merge_different_people(a, b):
    """The cost of a false positive here is asserting two humans are one,
    which is why this script reports and never merges."""
    assert fold_name(a) != fold_name(b)


def test_folding_tolerates_null():
    assert fold_name(None) == ""


# --- which splits actually matter ----------------------------------------


def test_an_inert_twin_is_not_an_active_split():
    """269 deliveries against 0 cannot split an ability posterior: the
    zero-delivery side contributes to no aggregate at all."""
    group = [(432, "A Davidson Soler", 269, "524943f1"), (433, "A Davidson-Soler", 0, "71d6fd2c")]
    assert active_splits([group]) == []


def test_two_active_sides_are_an_active_split():
    """The case that would give §6.5 two half-histories and two wrong
    posteriors, with a widened sd rendering as a confidence band."""
    group = [(1, "A Player", 400, "aaa"), (2, "A-Player", 350, "bbb")]
    assert active_splits([group]) == [group]


def test_three_sides_with_one_active_is_still_not_a_split():
    group = [(1, "A Player", 400, "aaa"), (2, "A-Player", 0, "bbb"), (3, "A  Player", 0, "ccc")]
    assert active_splits([group]) == []
