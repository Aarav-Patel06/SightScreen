"""The reconstructed ball key must be unique within an over (Phase 3 s1).

Found by a live match, not by review. `predictions` is keyed on
(match_id, innings, over_num, ball_in_over) and written ON CONFLICT DO
NOTHING, so two deliveries sharing a key means the second one's prediction
is silently discarded - a live win-probability curve missing a ball, with
nothing anywhere reporting a problem.

`reconstruct` derives ball_in_over from the LEGAL ball count, so a wide and
the legal ball after it both came out as the same ball of the over.
supabase/SCHEMA.md had already written down why that numbering is wrong:
"the legal-ball x.y notation would collide with the UNIQUE(match_id,
innings, over_num, ball_in_over) constraint on every over with an illegal
delivery."
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ingest.cricketdata import (
    InningsSnapshot,
    MatchSnapshot,
    _renumber_within_over,
    reconstruct,
)


def _snapshot(*innings: tuple[int, int, int]) -> MatchSnapshot:
    return MatchSnapshot(
        provider_id="p1",
        name="A vs B",
        format="T20",
        status="live",
        venue="V",
        teams=("A", "B"),
        innings=tuple(
            InningsSnapshot(runs=r, wickets=w, balls=b, batting_team="A")
            for r, w, b in innings
        ),
        started=True,
        ended=False,
        reduced_overs=None,
        dls_target=None,
        observed_at=datetime.now(timezone.utc),
    )


def _accumulate(steps: list[MatchSnapshot]):
    """What CricketDataClient._ingest does: reconstruct, then renumber
    against everything already held."""
    acc: list = []
    previous = steps[0]
    for nxt in steps[1:]:
        acc.extend(_renumber_within_over(acc, reconstruct(previous, nxt)))
        previous = nxt
    return acc


def _keys(deliveries) -> list[tuple[int, int, int]]:
    return [(d.innings, d.over_num, d.ball_in_over) for d in deliveries]


def test_a_wide_does_not_collide_with_the_ball_after_it():
    # runs move without the legal-ball count moving: that is a wide.
    steps = [
        _snapshot((10, 0, 2)),
        _snapshot((11, 0, 2)),  # wide
        _snapshot((12, 0, 3)),  # legal ball
        _snapshot((13, 0, 4)),  # legal ball
    ]
    keys = _keys(_accumulate(steps))
    assert len(keys) == len(set(keys)), f"colliding ball keys: {keys}"


def test_several_extras_in_one_over_stay_distinct():
    steps = [_snapshot((10, 0, 0))]
    runs, balls = 10, 0
    for legal in (False, True, False, True, True, False, True):
        runs += 1
        balls += 1 if legal else 0
        steps.append(_snapshot((runs, 0, balls)))
    keys = _keys(_accumulate(steps))
    assert len(keys) == len(set(keys)), f"colliding ball keys: {keys}"
    assert len(keys) >= 7


def test_numbering_counts_every_delivery_not_just_legal_ones():
    """The corpus convention: ball_in_over counts wides and no-balls too."""
    steps = [
        _snapshot((0, 0, 0)),
        _snapshot((1, 0, 1)),  # legal
        _snapshot((2, 0, 1)),  # wide
        _snapshot((3, 0, 2)),  # legal
    ]
    deliveries = _accumulate(steps)
    assert [d.ball_in_over for d in deliveries] == [1, 2, 3]


def test_keys_stay_unique_across_an_over_boundary():
    steps = [_snapshot((0, 0, 0))]
    runs = 0
    for ball in range(1, 14):
        runs += 1
        steps.append(_snapshot((runs, 0, ball)))
    deliveries = _accumulate(steps)
    keys = _keys(deliveries)
    assert len(keys) == len(set(keys))
    assert len({over for _i, over, _b in keys}) >= 2, "test never crossed an over"


def test_innings_two_restarts_the_numbering():
    steps = [
        _snapshot((120, 10, 120)),
        _snapshot((120, 10, 120), (1, 0, 1)),
        _snapshot((120, 10, 120), (2, 0, 2)),
    ]
    keys = _keys(_accumulate(steps))
    assert len(keys) == len(set(keys))
    assert {innings for innings, _o, _b in keys} == {2}


@pytest.mark.parametrize("wicket_on_extra", [True, False])
def test_a_wicket_does_not_change_the_uniqueness_property(wicket_on_extra):
    steps = [
        _snapshot((10, 0, 2)),
        _snapshot((11, 1 if wicket_on_extra else 0, 2)),
        _snapshot((12, 1, 3)),
    ]
    keys = _keys(_accumulate(steps))
    assert len(keys) == len(set(keys))
