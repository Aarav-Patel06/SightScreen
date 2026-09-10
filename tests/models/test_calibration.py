"""Calibration candidate correctness tests (SPEC.md sections 6.2/9.3, Phase 1
session 3). Synthetic data only - these test each candidate's own logic
(fit/predict shape, monotonicity, stratification dispatch, the sparse-phase
fallback), not real predictions.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from eval.splits import SecondInningsDataset
from models.calibration import (
    MIN_ROWS_PER_STRATUM,
    GlobalIsotonicCalibrator,
    GlobalPlattCalibrator,
    IdentityCalibrator,
    PhaseStratifiedCalibrator,
    split_for_calibration,
)


def _make_dataset(n: int, seed: int = 0) -> SecondInningsDataset:
    rng = np.random.default_rng(seed)
    return SecondInningsDataset(
        delivery_id=np.arange(n, dtype=np.int64),
        match_id=np.arange(n, dtype=np.int64),
        match_date=np.array(["2024-01-01"] * n, dtype="datetime64[D]"),
        required_run_rate=rng.uniform(4, 12, size=n).astype(np.float32),
        wickets_in_hand=rng.integers(0, 11, size=n).astype(np.int8),
        balls_remaining=rng.integers(1, 120, size=n).astype(np.int16),
        runs_required=rng.integers(1, 150, size=n).astype(np.int16),
        phase=rng.choice(["powerplay", "middle", "death"], size=n).astype(object),
        label=rng.integers(0, 2, size=n).astype(np.int8),
    )


# --- IdentityCalibrator ------------------------------------------------------


def test_identity_calibrator_returns_raw_unchanged():
    raw = np.array([0.1, 0.5, 0.9])
    y = np.array([0, 1, 1])
    calibrator = IdentityCalibrator().fit(raw, y)
    assert np.array_equal(calibrator.predict(raw), raw)


# --- GlobalIsotonicCalibrator ------------------------------------------------


def test_global_isotonic_is_monotone_non_decreasing():
    rng = np.random.default_rng(1)
    raw = rng.uniform(0, 1, size=2000)
    y = (rng.uniform(0, 1, size=2000) < raw).astype(np.int8)
    calibrator = GlobalIsotonicCalibrator().fit(raw, y)

    test_points = np.linspace(0, 1, 50)
    predicted = calibrator.predict(test_points)
    assert np.all(np.diff(predicted) >= -1e-9)


# --- GlobalPlattCalibrator ---------------------------------------------------


def test_global_platt_is_monotone_and_sigmoid_shaped():
    rng = np.random.default_rng(2)
    raw = rng.uniform(0, 1, size=2000)
    y = (rng.uniform(0, 1, size=2000) < raw).astype(np.int8)
    calibrator = GlobalPlattCalibrator().fit(raw, y)

    test_points = np.linspace(0.01, 0.99, 50)
    predicted = calibrator.predict(test_points)
    assert np.all(np.diff(predicted) >= -1e-9)
    assert predicted.min() >= 0.0 and predicted.max() <= 1.0


def test_global_platt_recovers_a_known_recalibration():
    # Raw scores are systematically overconfident (true rate is raw/2);
    # Platt should learn to pull predictions down toward the true rate.
    rng = np.random.default_rng(3)
    raw = rng.uniform(0.1, 0.9, size=5000)
    y = (rng.uniform(0, 1, size=5000) < raw / 2).astype(np.int8)
    calibrator = GlobalPlattCalibrator().fit(raw, y)

    predicted = calibrator.predict(np.array([0.8]))
    assert predicted[0] < 0.6  # pulled down from the raw, overconfident 0.8


# --- PhaseStratifiedCalibrator -----------------------------------------------


def test_phase_stratified_dispatches_to_the_right_sub_calibrator():
    rng = np.random.default_rng(4)
    n = 6000
    phase = rng.choice(["powerplay", "middle", "death"], size=n).astype(object)
    raw = rng.uniform(0, 1, size=n)
    # Each phase has a DIFFERENT true recalibration - powerplay needs a big
    # pull-down, death needs almost none. A stratified fit should apply the
    # right correction per phase; a global one couldn't get both right.
    true_rate = np.where(phase == "powerplay", raw * 0.3, np.where(phase == "middle", raw * 0.6, raw * 0.95))
    y = (rng.uniform(0, 1, size=n) < true_rate).astype(np.int8)

    calibrator = PhaseStratifiedCalibrator(GlobalPlattCalibrator).fit(raw, y, phase=phase)

    pp_pred = calibrator.predict(np.array([0.8]), phase=np.array(["powerplay"]))
    death_pred = calibrator.predict(np.array([0.8]), phase=np.array(["death"]))
    assert pp_pred[0] < death_pred[0]  # powerplay pulled down much more than death


def test_phase_stratified_falls_back_below_min_rows_per_stratum():
    rng = np.random.default_rng(5)
    n_common = MIN_ROWS_PER_STRATUM * 3
    n_sparse = MIN_ROWS_PER_STRATUM // 4  # deliberately below the floor

    phase = np.array(["middle"] * n_common + ["death"] * n_sparse, dtype=object)
    raw = rng.uniform(0, 1, size=n_common + n_sparse)
    y = (rng.uniform(0, 1, size=n_common + n_sparse) < raw).astype(np.int8)

    calibrator = PhaseStratifiedCalibrator(GlobalPlattCalibrator, min_rows_per_stratum=MIN_ROWS_PER_STRATUM)
    calibrator.fit(raw, y, phase=phase)

    assert "middle" in calibrator._by_phase
    assert "death" not in calibrator._by_phase  # too sparse - fell back
    assert "death" in calibrator._fallback_phases

    # The sparse phase must still produce a real, finite prediction (via
    # the pooled fallback), not raise or return garbage.
    out = calibrator.predict(np.array([0.5]), phase=np.array(["death"]))
    assert np.isfinite(out[0])


# --- split_for_calibration ----------------------------------------------------


def test_split_for_calibration_is_strictly_date_bounded():
    n = 200
    rng = np.random.default_rng(6)
    dates = np.array(
        ["2024-03-01"] * (n // 2) + ["2024-09-01"] * (n // 2), dtype="datetime64[D]"
    )
    ds = SecondInningsDataset(
        delivery_id=np.arange(n, dtype=np.int64),
        match_id=np.arange(n, dtype=np.int64),
        match_date=dates,
        required_run_rate=rng.uniform(4, 12, size=n).astype(np.float32),
        wickets_in_hand=rng.integers(0, 11, size=n).astype(np.int8),
        balls_remaining=rng.integers(1, 120, size=n).astype(np.int16),
        runs_required=rng.integers(1, 150, size=n).astype(np.int16),
        phase=rng.choice(["powerplay", "middle", "death"], size=n).astype(object),
        label=rng.integers(0, 2, size=n).astype(np.int8),
    )
    fit_ds, select_ds = split_for_calibration(ds, date(2024, 7, 1))
    assert len(fit_ds) == n // 2
    assert len(select_ds) == n // 2
    assert fit_ds.match_date.max() <= np.datetime64(date(2024, 7, 1))
    assert select_ds.match_date.min() > np.datetime64(date(2024, 7, 1))
