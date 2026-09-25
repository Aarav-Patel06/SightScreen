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
    decision = refit_decision(Rows(_records(100)), log=messages.append, allow_refit=True)

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
    assert refit_decision(rows, log=lambda _m: None, allow_refit=True)["ran"] is False


def test_a_wide_enough_window_runs_and_still_prefers_identity():
    """Above the floor the branch executes - and on a well-calibrated model
    no candidate should beat identity by a margin excluding zero."""
    messages: list[str] = []
    decision = refit_decision(Rows(_records(MIN_WINDOW_MATCHES + 40)), log=messages.append, allow_refit=True)

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
    decision = refit_decision(Rows(_records(MIN_WINDOW_MATCHES + 40, seed=7)), log=lambda _m: None, allow_refit=True)
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
    decision = refit_decision(rows, log=lambda _m: None, allow_refit=True)
    assert decision["ran"] is False
    assert decision["winner"] == "identity"


def _summary(tmp_path, report: dict) -> str:
    """Render the Actions step summary for a report."""
    import os

    from eval.calibration_monitor import write_step_summary

    target = tmp_path / "summary.md"
    os.environ["GITHUB_STEP_SUMMARY"] = str(target)
    try:
        write_step_summary(report)
    finally:
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
    return target.read_text(encoding="utf-8")


def _report(refit: dict) -> dict:
    return {
        "model_version": "winprob2-20260910",
        "populations": {
            "backfill": {
                "n": 12081,
                "n_matches": 100,
                "brier": 0.102,
                "brier_ci_low": 0.0745,
                "brier_ci_high": 0.1311,
                "n_deciles_failed": 4,
                "n_deciles_populated": 10,
                "unresolved": {"predictions": 0, "matches": 0},
                "refit": refit,
            }
        },
    }


def test_the_summary_distinguishes_skipped_from_evaluated(tmp_path):
    """Two green runs that mean different things must not look identical.

    A run that skipped the refit because the window was thin and a run that
    evaluated every candidate and rejected them are both green ticks in the
    Actions list. The first says "not enough data"; the second says "we
    checked, and doing nothing still wins". Telling them apart should not
    require opening the log.
    """
    skipped = _summary(tmp_path, _report({"ran": False, "winner": "identity", "reason": "100 matches, floor is 500"}))
    assert "SKIPPED" in skipped
    assert "not enough data" in skipped

    evaluated = _summary(
        tmp_path, _report({"ran": True, "winner": "identity", "reason": "no candidate beat identity"})
    )
    assert "SKIPPED" not in evaluated
    assert "refit RAN" in evaluated
    assert "kept identity" in evaluated


def test_the_summary_names_a_promotion_candidate(tmp_path):
    promoted = _summary(
        tmp_path, _report({"ran": True, "winner": "phase_isotonic", "reason": "beat identity"})
    )
    assert "promotion candidate: phase_isotonic" in promoted


def test_the_summary_reports_an_unscored_population_as_a_count(tmp_path):
    report = _report({"ran": False, "winner": "identity", "reason": "thin"})
    report["populations"]["live"] = {
        "n": 0,
        "n_matches": 0,
        "unresolved": {"predictions": 25, "matches": 1},
    }
    rendered = _summary(tmp_path, report)
    assert "25 logged, none scored yet" in rendered


def test_the_summary_is_a_no_op_off_actions(tmp_path):
    """A laptop run must not need an env var to succeed."""
    from eval.calibration_monitor import write_step_summary

    write_step_summary(_report({"ran": False, "winner": "identity", "reason": "thin"}))


# --- the gate, added 2026-09-24 ------------------------------------------
#
# WHY THE FIVE TESTS ABOVE GAINED `allow_refit=True`. They were written to
# exercise the FLOOR, and the floor now sits behind an explicit flag, so
# without it they would all stop at the gate and stop testing what they were
# written to test. The assertions themselves are unchanged.
#
# The gate exists because the floor turned out to be the wrong KIND of
# protection. `n_matches >= 500` is a threshold on a number that grows
# whenever anyone loads data: UI Phase 2 step 1 added 240 matches to populate
# a landing page and took the window from 100 to 340, cutting the margin from
# 400 matches to 160. The next backfill of that size would have promoted a
# calibrator from a nightly cron with nobody deciding anything.


def test_the_gate_holds_even_when_the_floor_is_satisfied():
    """The scenario that would have fired incidentally.

    This is the whole point: plenty of data, floor cleared, and it still does
    not run - because running is a decision and nobody made one.
    """
    messages: list[str] = []
    rows = Rows(_records(MIN_WINDOW_MATCHES + 40))
    assert rows.n_matches > MIN_WINDOW_MATCHES  # the floor would NOT stop this

    decision = refit_decision(rows, log=messages.append)

    assert decision["ran"] is False
    assert decision["winner"] == "identity"
    assert decision["gated"] is True
    assert any("GATED" in m for m in messages)


def test_the_gate_reports_that_a_refit_would_now_be_eligible():
    """Silence here would recreate the problem one level up.

    A gate that declines identically whether there are 10 matches or 10,000
    hides the fact that the evidence threshold has been reached. The report
    has to say so, or the decision never gets made at all.
    """
    thin = refit_decision(Rows(_records(100)), log=lambda _m: None)
    fat = refit_decision(Rows(_records(MIN_WINDOW_MATCHES + 40)), log=lambda _m: None)

    assert thin["would_be_eligible"] is False
    assert fat["would_be_eligible"] is True
    assert "not be eligible" in thin["reason"]
    assert "BE ELIGIBLE" in fat["reason"]


def test_the_gate_names_the_flag_that_opens_it():
    """A refusal that does not say how to proceed is a dead end."""
    decision = refit_decision(Rows(_records(100)), log=lambda _m: None)
    assert "--allow-refit" in decision["reason"]


def test_passing_the_flag_reaches_the_floor_logic_again():
    """The gate must not become a second, permanent floor."""
    decision = refit_decision(Rows(_records(100)), log=lambda _m: None, allow_refit=True)
    assert decision.get("gated") is None
    assert f"floor is {MIN_WINDOW_MATCHES}" in decision["reason"]
