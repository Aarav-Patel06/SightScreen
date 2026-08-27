"""Baseline correctness tests (SPEC.md section 9.2, Phase 1 session 1).

Synthetic datasets only - these test the baselines' own logic (shape,
fallback behaviour, the self-reference guard), not the real corpus.
"""

from __future__ import annotations

import numpy as np
import pytest

from eval.baselines import HistoricalBaseRateBaseline, LogisticBaseline
from eval.splits import SecondInningsDataset


def _make_dataset(n: int, match_id_start: int = 0, seed: int = 0) -> SecondInningsDataset:
    rng = np.random.default_rng(seed)
    return SecondInningsDataset(
        match_id=np.arange(match_id_start, match_id_start + n, dtype=np.int64),
        match_date=np.array(["2023-01-01"] * n, dtype="datetime64[D]"),
        required_run_rate=rng.uniform(4, 12, size=n).astype(np.float32),
        wickets_in_hand=rng.integers(0, 11, size=n).astype(np.int8),
        balls_remaining=rng.integers(1, 120, size=n).astype(np.int16),
        runs_required=rng.integers(1, 150, size=n).astype(np.int16),
        phase=rng.choice(["powerplay", "middle", "death"], size=n).astype(object),
        label=rng.integers(0, 2, size=n).astype(np.int8),
    )


# --- Baseline 1: logistic ---------------------------------------------------


def test_logistic_baseline_fit_predict_shape_and_range():
    train = _make_dataset(500, seed=1)
    test = _make_dataset(100, match_id_start=1000, seed=2)

    model = LogisticBaseline().fit(train)
    probs = model.predict_proba(test)

    assert probs.shape == (100,)
    assert np.all((probs >= 0) & (probs <= 1))


def test_logistic_baseline_scaler_is_never_refit_on_eval_data():
    train = _make_dataset(500, seed=1)
    test = _make_dataset(100, match_id_start=1000, seed=2)

    model = LogisticBaseline().fit(train)
    scaler_mean_before = model._scaler.mean_.copy()
    model.predict_proba(test)
    assert np.array_equal(scaler_mean_before, model._scaler.mean_)


def test_logistic_baseline_raises_if_predict_called_before_fit():
    test = _make_dataset(10, seed=3)
    with pytest.raises(RuntimeError):
        LogisticBaseline().predict_proba(test)


# --- Baseline 2: historical base rate --------------------------------------


def test_base_rate_baseline_exact_bin_used_when_well_populated():
    # Every row identical bin (runs_required=5 -> bin 0, balls_remaining=6
    # -> overs bin 1, wickets_in_hand=5), 200 observations - comfortably
    # above the min-bin-observations floor, all labelled 1.
    n = 200
    train = SecondInningsDataset(
        match_id=np.arange(n, dtype=np.int64),
        match_date=np.array(["2023-01-01"] * n, dtype="datetime64[D]"),
        required_run_rate=np.full(n, 5.0, dtype=np.float32),
        wickets_in_hand=np.full(n, 5, dtype=np.int8),
        balls_remaining=np.full(n, 6, dtype=np.int16),
        runs_required=np.full(n, 5, dtype=np.int16),
        phase=np.full(n, "death", dtype=object),
        label=np.ones(n, dtype=np.int8),
    )
    model = HistoricalBaseRateBaseline().fit(train)

    test = SecondInningsDataset(
        match_id=np.array([99999], dtype=np.int64),
        match_date=np.array(["2025-01-01"], dtype="datetime64[D]"),
        required_run_rate=np.array([5.0], dtype=np.float32),
        wickets_in_hand=np.array([5], dtype=np.int8),
        balls_remaining=np.array([6], dtype=np.int16),
        runs_required=np.array([5], dtype=np.int16),
        phase=np.array(["death"], dtype=object),
        label=np.array([1], dtype=np.int8),
    )
    probs = model.predict_proba(test)
    assert probs[0] == 1.0  # exact bin, 100% observed rate, well above the floor


def test_base_rate_baseline_falls_back_when_bin_is_sparse():
    # One rare exact combination (5 rows, below the 30-observation floor)
    # sitting inside a much larger (balls_remaining, wickets) group whose
    # overall rate differs sharply - the fallback must kick in and use the
    # broader group's rate, not the tiny, noisy exact-bin rate.
    rng = np.random.default_rng(4)
    n_broad = 200
    broad = SecondInningsDataset(
        match_id=np.arange(n_broad, dtype=np.int64),
        match_date=np.array(["2023-01-01"] * n_broad, dtype="datetime64[D]"),
        required_run_rate=rng.uniform(4, 12, size=n_broad).astype(np.float32),
        wickets_in_hand=np.full(n_broad, 5, dtype=np.int8),
        balls_remaining=np.full(n_broad, 6, dtype=np.int16),
        runs_required=rng.integers(20, 30, size=n_broad).astype(np.int16),  # bin 2 or 3, not the rare bin
        phase=np.full(n_broad, "death", dtype=object),
        label=np.zeros(n_broad, dtype=np.int8),  # broad group's true rate: 0.0
    )
    n_rare = 5
    rare = SecondInningsDataset(
        match_id=np.arange(n_broad, n_broad + n_rare, dtype=np.int64),
        match_date=np.array(["2023-01-01"] * n_rare, dtype="datetime64[D]"),
        required_run_rate=np.full(n_rare, 5.0, dtype=np.float32),
        wickets_in_hand=np.full(n_rare, 5, dtype=np.int8),
        balls_remaining=np.full(n_rare, 6, dtype=np.int16),
        runs_required=np.full(n_rare, 5, dtype=np.int16),  # rare exact bin: 5 rows only
        phase=np.full(n_rare, "death", dtype=object),
        label=np.ones(n_rare, dtype=np.int8),  # rare bin's own (noisy) rate: 1.0
    )
    train = SecondInningsDataset(
        **{
            name: np.concatenate([getattr(broad, name), getattr(rare, name)])
            for name in ("match_id", "match_date", "required_run_rate", "wickets_in_hand",
                         "balls_remaining", "runs_required", "phase", "label")
        }
    )
    model = HistoricalBaseRateBaseline().fit(train)

    test = SecondInningsDataset(
        match_id=np.array([99999], dtype=np.int64),
        match_date=np.array(["2025-01-01"], dtype="datetime64[D]"),
        required_run_rate=np.array([5.0], dtype=np.float32),
        wickets_in_hand=np.array([5], dtype=np.int8),
        balls_remaining=np.array([6], dtype=np.int16),
        runs_required=np.array([5], dtype=np.int16),
        phase=np.array(["death"], dtype=object),
        label=np.array([0], dtype=np.int8),
    )
    probs = model.predict_proba(test)
    # The exact bin (5 rows, all labelled 1) is below the observation floor,
    # so this must fall back to the (balls_remaining, wickets) group, which
    # pools the rare rows back in with the 200 broad rows: 5/205, not the
    # sparse exact bin's own noisy rate (1.0).
    assert probs[0] == pytest.approx(5 / 205)
    assert probs[0] != 1.0


def test_base_rate_baseline_raises_on_reference_pool_self_overlap():
    """The structural leakage guard: predict_proba() on the exact dataset
    fit() was called with (or anything sharing a match_id with it) must
    raise, not silently return an optimistic, self-referential number."""
    train = _make_dataset(50, seed=5)
    model = HistoricalBaseRateBaseline().fit(train)
    with pytest.raises(ValueError):
        model.predict_proba(train)


def test_base_rate_baseline_raises_before_fit():
    test = _make_dataset(10, seed=6)
    with pytest.raises(RuntimeError):
        HistoricalBaseRateBaseline().predict_proba(test)
