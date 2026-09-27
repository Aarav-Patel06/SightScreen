"""The margin and method of a result, read from Cricsheet's `outcome`.

One case per outcome shape found in the corpus on 2026-09-27 (see migration
20260927000001 for the counts). NULL means not recorded - a margin is never
guessed and never zero-filled.
"""

from __future__ import annotations

import pytest

from ingest.cricsheet import outcome_detail

TEAMS = {"England": 29, "India": 20}


def detail(outcome: dict) -> dict:
    return outcome_detail({"outcome": outcome}, TEAMS)


EMPTY = {"win_by_runs": None, "win_by_wickets": None, "outcome_method": None,
         "tie_winner": None, "tie_decided_by": None}


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ({"winner": "England", "by": {"runs": 31}}, {**EMPTY, "win_by_runs": 31}),
        ({"winner": "India", "by": {"wickets": 4}}, {**EMPTY, "win_by_wickets": 4}),
        ({"winner": "India", "by": {"runs": 18}, "method": "D/L"},
         {**EMPTY, "win_by_runs": 18, "outcome_method": "D/L"}),
        ({"winner": "India", "by": {"wickets": 8}, "method": "VJD"},
         {**EMPTY, "win_by_wickets": 8, "outcome_method": "VJD"}),
        ({"winner": "England", "method": "Awarded"}, {**EMPTY, "outcome_method": "Awarded"}),
        ({"winner": "England", "method": "Lost fewer wickets"},
         {**EMPTY, "outcome_method": "Lost fewer wickets"}),
        ({"result": "no result"}, EMPTY),
        ({"result": "tie"}, EMPTY),
        ({"result": "tie", "method": "D/L"}, {**EMPTY, "outcome_method": "D/L"}),
        ({"result": "tie", "eliminator": "India"},
         {**EMPTY, "tie_winner": 20, "tie_decided_by": "super_over"}),
        ({"result": "tie", "eliminator": "England", "method": "D/L"},
         {**EMPTY, "outcome_method": "D/L", "tie_winner": 29, "tie_decided_by": "super_over"}),
        ({"result": "tie", "bowl_out": "England"},
         {**EMPTY, "tie_winner": 29, "tie_decided_by": "bowl_out"}),
    ],
)
def test_every_outcome_shape_in_the_corpus(outcome, expected):
    assert detail(outcome) == expected


def test_an_eliminator_that_is_not_one_of_the_teams_is_not_guessed():
    assert detail({"result": "tie", "eliminator": "Somebody Else"}) == EMPTY
