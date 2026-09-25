"""The deployed-vs-local parity check, now with a reachable failure path.

WHY THIS FILE EXISTS. `PARITY_TOLERANCE = 1e-9` is the only thing asserting
that the deployed service and the local recomputation compute the same
probability from the same ball. If they drift, every row the service writes is
wrong in a way nothing downstream can see: the payload is well formed, the
Brier score is a number, /accuracy renders.

It had no test, because it lived inline in `run()`'s loop and could only be
reached by driving a full replay against a service returning wrong numbers.
That is the audit's own criterion for a guard that is really a comment - so the
comparison was extracted into `parity_verdict` and these are its proofs.

The tolerance is 1e-9 rather than 0 because the two sides serialise through
JSON, and the point of the number is to catch a DIFFERENT MODEL, not a
different last bit.
"""

import pytest

from ingest.replay_log import PARITY_TOLERANCE, parity_verdict


def _ball(over=4, ball_in_over=3):
    return {"innings": 2, "over_num": over, "ball_in_over": ball_in_over}


def test_exact_agreement_is_zero():
    """What the real run produced: max diff 0.000e+00 over 372 balls."""
    p = 0.5305938225791994
    diff, worst = parity_verdict([(_ball(), p, p), (_ball(5, 1), 0.25, 0.25)])
    assert diff == 0.0
    assert worst is None, "nothing to report when nothing differs"


def test_a_json_round_trip_stays_inside_the_tolerance():
    """The tolerance is not zero for a reason. Catching a float's last bit
    would make this fire on every run and it would be switched off."""
    import json

    p = 0.5305938225791994
    round_tripped = json.loads(json.dumps(p))
    diff, _worst = parity_verdict([(_ball(), round_tripped, p)])
    assert diff <= PARITY_TOLERANCE


def test_a_different_model_is_caught():
    """The failure. A service running yesterday's artifact returns plausible
    numbers that are simply not this model's."""
    diff, worst = parity_verdict(
        [(_ball(1, 1), 0.500000, 0.500000), (_ball(12, 4), 0.731, 0.688)]
    )
    assert diff > PARITY_TOLERANCE
    ball, remote_p, local_p = worst
    assert ball == _ball(12, 4), "report the ball that was worst, not the last one"
    assert (remote_p, local_p) == (0.731, 0.688)


def test_a_difference_just_above_the_tolerance_is_caught():
    """Non-vacuity at the boundary: the constant has to be load-bearing."""
    p = 0.5
    diff, worst = parity_verdict([(_ball(), p + 1e-8, p)])
    assert diff > PARITY_TOLERANCE
    assert worst is not None


def test_the_worst_ball_is_reported_not_the_first_or_the_last():
    """The message quotes one ball, so it has to be the informative one."""
    _diff, worst = parity_verdict(
        [
            (_ball(1, 1), 0.10, 0.11),   # 0.01
            (_ball(9, 2), 0.40, 0.75),   # 0.35  <- worst
            (_ball(19, 6), 0.90, 0.88),  # 0.02
        ]
    )
    assert worst[0] == _ball(9, 2)


def test_direction_does_not_matter():
    """Deployed lower than local is the same failure as deployed higher."""
    over, under = parity_verdict([(_ball(), 0.9, 0.4)]), parity_verdict([(_ball(), 0.4, 0.9)])
    assert over[0] == pytest.approx(under[0]) == pytest.approx(0.5)


def test_an_empty_match_is_not_a_parity_failure():
    """Every ball already logged means nothing was posted and nothing was
    compared. That must read as 'nothing to do', not as agreement proven."""
    diff, worst = parity_verdict([])
    assert diff == 0.0 and worst is None
