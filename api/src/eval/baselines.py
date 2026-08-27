"""Baselines every future model is compared against (SPEC.md section 9.2,
Phase 1 session 1).

Baseline 1 is a plain 3-feature logistic regression. Baseline 2 is a
literal lookup of historical outcomes, binned by (runs_required,
balls_remaining, wickets_in_hand) - "more likely to leak than the logistic,
since it's literally a lookup of outcomes" (your framing). Its leakage
guard is structural, not a label you have to remember to pass correctly:
`fit()` remembers the exact match_ids it was fit on, and `predict_proba()`
raises if the dataset it's asked to score shares even one match_id with
that reference pool - so evaluating this baseline against its own
reference pool (train) fails loudly instead of silently returning an
optimistic number.
"""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from eval.splits import SecondInningsDataset

# Decision 5: bin widths, stated as tunable judgment calls, not derived
# from section 9.2. Runs required clipped at 100+ into one bucket - a chase
# needing more than that is rare and functionally "already lost" regardless
# of finer resolution. balls_remaining binned to whole overs.
_RUNS_BIN_WIDTH = 10
_RUNS_BIN_CAP = 100
_MIN_BIN_OBSERVATIONS = 30


def _runs_bin(runs_required: np.ndarray) -> np.ndarray:
    clipped = np.clip(runs_required, 0, _RUNS_BIN_CAP)
    return (clipped // _RUNS_BIN_WIDTH).astype(np.int64)


def _overs_bin(balls_remaining: np.ndarray) -> np.ndarray:
    return (balls_remaining // 6).astype(np.int64)


class LogisticBaseline:
    """Section 9.2 baseline 1: required_run_rate, wickets_in_hand,
    balls_remaining - nothing else. Scaler is fit on train only and reused
    (never refit) for every later prediction, so val/test never influence
    the feature scaling."""

    def __init__(self) -> None:
        self._scaler = StandardScaler()
        self._model = LogisticRegression()
        self._fitted = False

    @staticmethod
    def _features(dataset: SecondInningsDataset) -> np.ndarray:
        return np.column_stack(
            [dataset.required_run_rate, dataset.wickets_in_hand, dataset.balls_remaining]
        )

    def fit(self, train: SecondInningsDataset) -> "LogisticBaseline":
        X = self._scaler.fit_transform(self._features(train))
        self._model.fit(X, train.label)
        self._fitted = True
        return self

    def predict_proba(self, dataset: SecondInningsDataset) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("call fit() before predict_proba()")
        X = self._scaler.transform(self._features(dataset))  # never refit here
        return self._model.predict_proba(X)[:, 1]


def _grouped_rates(key: np.ndarray, label: np.ndarray) -> dict[int, tuple[float, int]]:
    """key -> (observed rate, n) for every distinct key value present."""
    unique_keys, inverse = np.unique(key, return_inverse=True)
    counts = np.bincount(inverse)
    sums = np.bincount(inverse, weights=label.astype(np.float64))
    rates = sums / counts
    return {int(k): (float(r), int(n)) for k, r, n in zip(unique_keys, rates, counts)}


class HistoricalBaseRateBaseline:
    """Section 9.2 baseline 2: observed chase-success rate from prior
    matches, binned by (runs_required, balls_remaining, wickets_in_hand).
    Reference pool is exactly the dataset passed to fit() - never
    predictions's own data (guarded, see module docstring). Sparse bins
    back off, most to least specific: exact 3-way bin (>=30 observations)
    -> (balls_remaining bin, wickets) -> balls_remaining bin alone ->
    global rate."""

    def __init__(self, min_bin_observations: int = _MIN_BIN_OBSERVATIONS) -> None:
        self._min_n = min_bin_observations
        self._fitted = False

    def fit(self, train: SecondInningsDataset) -> "HistoricalBaseRateBaseline":
        runs_bin = _runs_bin(train.runs_required)
        overs_bin = _overs_bin(train.balls_remaining)
        wickets = train.wickets_in_hand.astype(np.int64)

        fine_key = runs_bin * 10_000 + overs_bin * 100 + wickets
        mid_key = overs_bin * 100 + wickets
        coarse_key = overs_bin

        self._fine = _grouped_rates(fine_key, train.label)
        self._mid = _grouped_rates(mid_key, train.label)
        self._coarse = _grouped_rates(coarse_key, train.label)
        self._global_rate = float(train.label.mean())
        self._train_match_ids = set(int(m) for m in np.unique(train.match_id))
        self._fitted = True
        return self

    def _lookup(self, table: dict[int, tuple[float, int]], key: int) -> float | None:
        entry = table.get(key)
        if entry is None:
            return None
        rate, n = entry
        return rate if n >= self._min_n else None

    def predict_proba(self, dataset: SecondInningsDataset) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("call fit() before predict_proba()")
        overlap = self._train_match_ids & set(int(m) for m in np.unique(dataset.match_id))
        if overlap:
            raise ValueError(
                f"predict_proba() called on a dataset sharing {len(overlap)} match_id(s) with "
                "the reference pool this baseline was fit on - this baseline is a literal "
                "lookup of historical outcomes and must never be evaluated against its own "
                "reference pool (it would just be reading back the answer)"
            )

        runs_bin = _runs_bin(dataset.runs_required)
        overs_bin = _overs_bin(dataset.balls_remaining)
        wickets = dataset.wickets_in_hand.astype(np.int64)
        fine_key = runs_bin * 10_000 + overs_bin * 100 + wickets
        mid_key = overs_bin * 100 + wickets
        coarse_key = overs_bin

        out = np.empty(len(dataset), dtype=np.float64)
        for i in range(len(dataset)):
            rate = self._lookup(self._fine, int(fine_key[i]))
            if rate is None:
                rate = self._lookup(self._mid, int(mid_key[i]))
            if rate is None:
                rate = self._lookup(self._coarse, int(coarse_key[i]))
            if rate is None:
                rate = self._global_rate
            out[i] = rate
        return out
