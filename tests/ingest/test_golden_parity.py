"""The golden comparator fails on ANY difference (ingest/golden_parity.py).

The nightly check's whole value is that it cannot be satisfied by "close":
a probability one part in a billion away, a missing ball and an extra ball
are each a failure. Pure, so it runs in CI.
"""

from __future__ import annotations

from ingest.golden_parity import differences

REFERENCE = {"winprob2-20260927": {"2-0-1": 0.4512345678901234, "2-0-2": 0.25}}


def test_an_exact_reproduction_passes():
    assert differences(REFERENCE, {"winprob2-20260927": dict(REFERENCE["winprob2-20260927"])}) == []


def test_a_value_one_part_in_a_billion_away_fails():
    altered = dict(REFERENCE["winprob2-20260927"])
    altered["2-0-1"] *= 1 + 1e-9
    assert differences(REFERENCE, {"winprob2-20260927": altered}) == [
        f"winprob2-20260927 ball 2-0-1: reference {0.4512345678901234!r}, production {altered['2-0-1']!r}"]


def test_a_missing_ball_an_extra_ball_and_a_missing_version_fail():
    computed = {"winprob2-20260927": {"2-0-1": 0.4512345678901234, "2-0-3": 0.5}}
    assert sorted(differences(REFERENCE, computed)) == [
        "winprob2-20260927 ball 2-0-2: missing", "winprob2-20260927 ball 2-0-3: not in the reference"]
    assert differences(REFERENCE, {}) == ["winprob2-20260927: not recomputed"]
