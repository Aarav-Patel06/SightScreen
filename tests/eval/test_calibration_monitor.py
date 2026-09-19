"""The calibration monitor's judgement (SPEC.md §8.1, Phase 3 session 2).

Not the arithmetic - `eval/metrics.py` owns that and has its own tests.
These cover the decisions, which are where a monitor goes quietly wrong:
promoting a calibrator that won by noise, refitting on data too thin to
select on, or reporting a baseline as beaten when its interval includes zero.
"""

from __future__ import annotations

import numpy as np
import pytest

from eval.calibration_monitor import (
    MIN_SELECT_MATCHES,
    MIN_WINDOW_MATCHES,
    Rows,
    refit_decision,
)
from eval.metrics import calibration_report


def _records(n_matches: int, balls_per_match: int = 20, seed: int = 0) -> list[tuple]:
    """Synthetic logged predictions: a well-calibrated model, by construction."""
    rng = np.random.default_rng(seed)
    out = []
    for match in range(n_matches):
        p = float(rng.uniform(0.15, 0.85))
        won = int(rng.random() < p)
        day = np.datetime64("2026-01-01") + np.timedelta64(match, "D")
        for ball in range(balls_per_match):
            out.append(
                (
                    match,
                    p,
                    won,
                    "middle",
                    120 - ball,
                    80,
                    3,
                    None,
                    day.astype("datetime64[D]").item(),
                )
            )
    return out


def test_a_thin_window_does_not_refit_and_says_why():
    """The case that is live today: 100 matches, floor 500."""
    messages: list[str] = []
    decision = refit_decision(Rows(_records(100)), log=messages.append)

    assert decision["ran"] is False
    assert decision["winner"] == "identity"
    assert "floor is 500" in decision["reason"]
    # The wording matters as much as the behaviour: a scheduled job that
    # declines to act must not read as a no-op failure at 3am.
    assert "expected outcome, not a failure" in decision["reason"]
    assert any("SKIPPED" in m for m in messages)


def test_the_floor_is_on_matches_not_rows():
    """20,000 balls across 100 matches is still 100 observations.

    §9.3: treating balls as independent understates the standard error by
    about an order of magnitude, which is exactly how a refit gets selected
    on noise.
    """
    rows = Rows(_records(100, balls_per_match=200))
    assert len(rows) == 20_000
    assert rows.n_matches == 100
    assert refit_decision(rows, log=lambda _m: None)["ran"] is False


def test_a_wide_enough_window_runs_and_still_prefers_identity():
    """Above the floor the branch executes - and on a well-calibrated model
    no candidate should beat identity by a margin excluding zero."""
    messages: list[str] = []
    decision = refit_decision(Rows(_records(MIN_WINDOW_MATCHES + 40)), log=messages.append)

    assert decision["ran"] is True
    assert decision["n_select_matches"] >= MIN_SELECT_MATCHES
    assert decision["winner"] == "identity"
    assert "identity" in decision["reason"]
    # Every candidate was actually evaluated, not skipped.
    assert set(decision["comparison"]) >= {"identity", "global_isotonic", "global_platt"}


def test_a_candidate_is_only_promoted_on_a_significant_margin():
    """§8.1 step 7, and the thing run_calibration_selection.py does NOT do.

    Its selection is `min(comparison, key=brier)` - a plain argmin with no
    significance test - so a candidate ahead by 0.0001 would win. Here a
    candidate must beat identity by a paired match-clustered margin whose CI
    excludes zero, so ties and noise leave identity in place.
    """
    decision = refit_decision(Rows(_records(MIN_WINDOW_MATCHES + 40, seed=7)), log=lambda _m: None)
    for name, entry in decision["comparison"].items():
        if name == "identity" or "failed" in entry:
            continue
        if entry["beats_identity"]:
            assert entry["ci_low"] > 0, f"{name} promoted with an interval touching zero"


def test_deciles_are_counted_only_when_populated():
    """`contains_predicted` is None on an empty bin, and None is falsy - so
    summing it directly would count every empty decile as a failure."""
    rows = Rows(_records(60))
    report = calibration_report(rows.y, rows.p, rows.match_id, n_resamples=200)
    assert report["n_deciles_populated"] <= 10
    assert 0 <= report["n_deciles_failed"] <= report["n_deciles_populated"]


def test_rows_exposes_effective_sample_size():
    rows = Rows(_records(12, balls_per_match=50))
    assert len(rows) == 600
    assert rows.n_matches == 12


@pytest.mark.parametrize("n_matches", [0, 1, 5])
def test_tiny_windows_do_not_crash(n_matches):
    """A brand new deployment has almost no data, and the monitor still has
    to produce a report rather than an exception."""
    rows = Rows(_records(n_matches)) if n_matches else Rows([])
    decision = refit_decision(rows, log=lambda _m: None)
    assert decision["ran"] is False
    assert decision["winner"] == "identity"
