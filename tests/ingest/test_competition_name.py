"""Competition names, folded on the way in.

Cricsheet's source JSON spells one competition two ways. Measured across all
22,737 retained files, not inferred:

    11  ICC Men's T20 World Cup Asia Qualifier Final      (ASCII apostrophe)
     4  ICC Men\u2019s T20 World Cup Asia Qualifier Final  (U+2019)
    16  ICC Men's T20 World Cup Qualifier A               (ASCII apostrophe)
     4  ICC Men\u2019s T20 World Cup Qualifier A           (U+2019)

The corpus matched those counts exactly, which is the useful part of the
finding: the loader was transcribing its source correctly and the defect is
upstream. There is no mojibake and there never was - U+FFFD across
competitions, venues, teams and players is zero.

`competition` is the only entity in the schema with no alias table to absorb
an upstream spelling difference. Teams, venues and players all resolve
through one, which is why the same inconsistency produced zero split venues
and zero split teams.
"""

from __future__ import annotations

import pytest

from ingest.cricsheet import _competition_name

SPLIT_NAME_ASCII = "ICC Men's T20 World Cup Qualifier A"
SPLIT_NAME_TYPOGRAPHIC = "ICC Men\u2019s T20 World Cup Qualifier A"


def test_the_two_real_spellings_fold_to_one():
    """The whole point. These are the actual strings in the actual files."""
    assert _competition_name({"event": {"name": SPLIT_NAME_TYPOGRAPHIC}}) == SPLIT_NAME_ASCII
    assert _competition_name({"event": {"name": SPLIT_NAME_ASCII}}) == SPLIT_NAME_ASCII


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Men\u2018s Cup", "Men's Cup"),
        ("\u201cThe Hundred\u201d", '"The Hundred"'),
        ("Asia \u2013 Pacific Cup", "Asia - Pacific Cup"),
        ("Asia \u2014 Pacific Cup", "Asia - Pacific Cup"),
        ("Indian\u00a0Premier League", "Indian Premier League"),
        ("  Big Bash League  ", "Big Bash League"),
    ],
)
def test_typographic_punctuation_is_folded(raw, expected):
    assert _competition_name({"event": {"name": raw}}) == expected


def test_accents_are_left_alone():
    """Punctuation only. The corpus holds three legitimately accented player
    names (CJ Tavare, Maria Castineiras, S Fouche, all with real diacritics)
    and folding accents here would start a precedent for silently rewriting
    them - a data change far beyond the evidence, which is about apostrophes.
    """
    assert _competition_name({"event": {"name": "Coupe de Cricket \u00c9lite"}}) == (
        "Coupe de Cricket \u00c9lite"
    )


def test_a_missing_event_still_yields_unknown():
    """The pre-existing default, unchanged. 'Unknown' is a real value in the
    corpus and the fold must not turn it into something else."""
    assert _competition_name({}) == "Unknown"
    assert _competition_name({"event": {}}) == "Unknown"


def test_an_already_clean_name_is_returned_unchanged():
    """Standing rule 11's shape, applied to this fold: check what the
    transformation does to input it is not meant to touch."""
    for name in ("Indian Premier League", "Big Bash League", "SA20", "Unknown"):
        assert _competition_name({"event": {"name": name}}) == name
