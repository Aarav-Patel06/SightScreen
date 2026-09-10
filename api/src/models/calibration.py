"""Calibration candidates (SPEC.md sections 6.2/9.3, Phase 1 session 3).

Session 2's global isotonic map assumed miscalibration is a function of
predicted probability alone. It isn't: a raw score of 0.7 arises both from
a genuinely-uncertain early-chase state and a usually-reliable late-chase
state, and one monotone map merges those into a single, mis-corrected bin.
This module gives every candidate the same fit/predict interface so they
can be compared on equal footing, selected on a held-out chunk of
validation (never the same data used to fit them - see
`split_for_calibration`), and only the winner is ever touched against test.

Five candidates (SPEC.md section 6.2's original "just fit IsotonicRegression"
instruction is superseded by this comparison, not silently kept):
  a) IdentityCalibrator      - no correction; a serious candidate, not a strawman
  b) GlobalIsotonicCalibrator - session 2's approach, kept as the reference
  c) PhaseStratifiedCalibrator(GlobalIsotonicCalibrator)
  d) GlobalPlattCalibrator
  e) PhaseStratifiedCalibrator(GlobalPlattCalibrator)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from eval.splits import SecondInningsDataset

# Below this many rows, a phase's own stratum is too thin to fit on its own -
# falls back to a calibrator fit on the pooled (all-phase) data instead of
# returning an unfit or noisy map. Session 1/2's baseline/bin-fallback floors
# (30, 10) were for much finer bins; this floor is for a full 1-D calibration
# curve/logistic fit, which needs more data per fit than a lookup bin does -
# a judgment call, reported (not assumed) whether it ever actually binds.
MIN_ROWS_PER_STRATUM = 1000

_EPS = 1e-6  # keeps logit() finite at raw scores of exactly 0.0/1.0


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


class Calibrator(Protocol):
    def fit(self, raw: np.ndarray, y: np.ndarray, phase: np.ndarray | None = None) -> "Calibrator": ...
    def predict(self, raw: np.ndarray, phase: np.ndarray | None = None) -> np.ndarray: ...


class IdentityCalibrator:
    """Candidate (a): no correction. Real, not a strawman - LightGBM
    trained on log loss over millions of rows is often already calibrated."""

    def fit(self, raw: np.ndarray, y: np.ndarray, phase: np.ndarray | None = None) -> "IdentityCalibrator":
        return self

    def predict(self, raw: np.ndarray, phase: np.ndarray | None = None) -> np.ndarray:
        return np.asarray(raw, dtype=np.float64)


class GlobalIsotonicCalibrator:
    """Candidate (b): session 2's approach, kept as the reference point."""

    def __init__(self) -> None:
        self._iso = IsotonicRegression(out_of_bounds="clip")

    def fit(self, raw: np.ndarray, y: np.ndarray, phase: np.ndarray | None = None) -> "GlobalIsotonicCalibrator":
        self._iso.fit(raw, y)
        return self

    def predict(self, raw: np.ndarray, phase: np.ndarray | None = None) -> np.ndarray:
        return self._iso.predict(raw)


class GlobalPlattCalibrator:
    """Candidate (d): standard Platt scaling - a 1-D logistic regression of
    the true label on the logit of the raw score. Two parameters, lower
    variance than isotonic's fully nonparametric monotone map, at the cost
    of assuming a sigmoid-shaped correction."""

    def __init__(self) -> None:
        self._model = LogisticRegression()

    def fit(self, raw: np.ndarray, y: np.ndarray, phase: np.ndarray | None = None) -> "GlobalPlattCalibrator":
        self._model.fit(_logit(np.asarray(raw, dtype=np.float64)).reshape(-1, 1), y)
        return self

    def predict(self, raw: np.ndarray, phase: np.ndarray | None = None) -> np.ndarray:
        X = _logit(np.asarray(raw, dtype=np.float64)).reshape(-1, 1)
        return self._model.predict_proba(X)[:, 1]


class PhaseStratifiedCalibrator:
    """Candidates (c)/(e): one `base_cls()` instance per phase. A phase
    with fewer than MIN_ROWS_PER_STRATUM rows in the fit chunk falls back
    to a calibrator fit on the POOLED (all-phase) fit data, rather than
    fitting on too little data or leaving that phase uncalibrated."""

    def __init__(self, base_cls: type, min_rows_per_stratum: int = MIN_ROWS_PER_STRATUM) -> None:
        self._base_cls = base_cls
        self._min_rows = min_rows_per_stratum
        self._by_phase: dict[str, Calibrator] = {}
        self._pooled_fallback: Calibrator | None = None
        self._fallback_phases: set[str] = set()

    def fit(self, raw: np.ndarray, y: np.ndarray, phase: np.ndarray | None = None) -> "PhaseStratifiedCalibrator":
        assert phase is not None, "PhaseStratifiedCalibrator requires phase"
        raw, y, phase = np.asarray(raw), np.asarray(y), np.asarray(phase)

        self._pooled_fallback = self._base_cls().fit(raw, y)
        for p in np.unique(phase):
            mask = phase == p
            if mask.sum() < self._min_rows:
                self._fallback_phases.add(str(p))
                continue
            self._by_phase[str(p)] = self._base_cls().fit(raw[mask], y[mask])
        return self

    def predict(self, raw: np.ndarray, phase: np.ndarray | None = None) -> np.ndarray:
        assert phase is not None, "PhaseStratifiedCalibrator requires phase"
        raw, phase = np.asarray(raw, dtype=np.float64), np.asarray(phase)
        out = np.empty(len(raw), dtype=np.float64)
        for p in np.unique(phase):
            mask = phase == p
            calibrator = self._by_phase.get(str(p), self._pooled_fallback)
            out[mask] = calibrator.predict(raw[mask])
        return out


CANDIDATES: dict[str, "Calibrator | type"] = {
    "identity": IdentityCalibrator,
    "global_isotonic": GlobalIsotonicCalibrator,
    "phase_isotonic": lambda: PhaseStratifiedCalibrator(GlobalIsotonicCalibrator),
    "global_platt": GlobalPlattCalibrator,
    "phase_platt": lambda: PhaseStratifiedCalibrator(GlobalPlattCalibrator),
}


def split_for_calibration(
    val_ds: SecondInningsDataset, split_date: date
) -> tuple[SecondInningsDataset, SecondInningsDataset]:
    """Fit chunk (<=split_date) / select chunk (>split_date) WITHIN an
    already train/val/test-gated validation set - fitting candidate maps
    and selecting between them on the same data is circular. This is
    calibration-method-selection logic, not train/val/test split logic, so
    it stays here rather than in eval/splits.py (section 9.1 governs the
    latter, not a secondary split of an already-gated set)."""
    fields = ("delivery_id", "match_id", "match_date", "required_run_rate", "wickets_in_hand",
              "balls_remaining", "runs_required", "phase", "label")
    fit_mask = val_ds.match_date <= np.datetime64(split_date)
    select_mask = ~fit_mask
    fit_ds = SecondInningsDataset(**{f: getattr(val_ds, f)[fit_mask] for f in fields})
    select_ds = SecondInningsDataset(**{f: getattr(val_ds, f)[select_mask] for f in fields})
    return fit_ds, select_ds
