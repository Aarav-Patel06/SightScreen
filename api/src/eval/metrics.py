"""Evaluation metrics (SPEC.md section 9.3, Phase 1 session 1).

Brier score, log loss, and a reliability (calibration) table, plus bucketed
views of both scores. A single averaged number hides exactly what section
9.3's targets are checking - its Brier targets are phase-specific (~0.25 at
the start of a chase, <=0.05 in the final 3 overs), so every score here can
be computed overall or split by bucket.
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-15  # clips predicted probabilities away from exactly 0/1 before log()


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=np.float64)
    y_prob = np.asarray(y_prob, dtype=np.float64)
    return float(np.mean((y_prob - y_true) ** 2))


def log_loss(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=np.float64)
    y_prob = np.clip(np.asarray(y_prob, dtype=np.float64), _EPS, 1 - _EPS)
    return float(-np.mean(y_true * np.log(y_prob) + (1 - y_true) * np.log(1 - y_prob)))


def reliability_table(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> list[dict]:
    """Per-decile (by default) calibration view: mean predicted probability
    vs. observed outcome rate vs. bin size, for section 9.3's "calibration
    error <3pp for any decile with n>200" acceptance check."""
    y_true = np.asarray(y_true, dtype=np.float64)
    y_prob = np.asarray(y_prob, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_idx = np.clip(np.digitize(y_prob, edges[1:-1], right=True), 0, n_bins - 1)

    rows = []
    for b in range(n_bins):
        mask = bin_idx == b
        n = int(mask.sum())
        rows.append(
            {
                "bin_low": float(edges[b]),
                "bin_high": float(edges[b + 1]),
                "n": n,
                "mean_predicted": float(y_prob[mask].mean()) if n else None,
                "observed_rate": float(y_true[mask].mean()) if n else None,
            }
        )
    return rows


def bucketed_metrics(y_true: np.ndarray, y_prob: np.ndarray, bucket: np.ndarray) -> dict[str, dict]:
    """Brier/log loss/n per distinct value of `bucket` (e.g. the phase
    column, or a named section-9.3 cutoff computed by the caller)."""
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    bucket = np.asarray(bucket)

    out: dict[str, dict] = {}
    for value in np.unique(bucket):
        mask = bucket == value
        out[str(value)] = {
            "n": int(mask.sum()),
            "brier": brier_score(y_true[mask], y_prob[mask]),
            "log_loss": log_loss(y_true[mask], y_prob[mask]),
        }
    return out


def brier_match_clustered_ci(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    match_id: np.ndarray,
    n_resamples: int = 2000,
    ci: float = 0.95,
    seed: int = 0,
) -> dict:
    """Match-clustered bootstrap confidence interval on the Brier score
    (SPEC.md section 9.3). Balls within a match are highly correlated -
    resampling balls instead of matches understates the standard error by
    roughly an order of magnitude and makes noise look like a significant
    improvement. Effective sample size is closer to the match count than
    the row count, so this resamples MATCHES with replacement and lets
    every resampled match contribute all of its own rows, never a
    ball-level resample.

    Implementation note: since a Brier score is a mean of per-row squared
    errors, a bootstrap resample's score is fully determined by how many
    times each match was drawn and that match's own (sum of squared
    error, row count) - computed once, not by re-touching raw rows inside
    the resampling loop.
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_prob = np.asarray(y_prob, dtype=np.float64)
    match_id = np.asarray(match_id)
    sq_err = (y_prob - y_true) ** 2

    unique_matches, inverse = np.unique(match_id, return_inverse=True)
    n_matches = len(unique_matches)
    sse_per_match = np.bincount(inverse, weights=sq_err, minlength=n_matches)
    n_per_match = np.bincount(inverse, minlength=n_matches)

    point = float(sq_err.sum() / len(sq_err))

    rng = np.random.default_rng(seed)
    boot_scores = np.empty(n_resamples)
    for i in range(n_resamples):
        draw_counts = np.bincount(rng.integers(0, n_matches, size=n_matches), minlength=n_matches)
        boot_scores[i] = (draw_counts * sse_per_match).sum() / (draw_counts * n_per_match).sum()

    alpha = (1 - ci) / 2
    lo, hi = np.quantile(boot_scores, [alpha, 1 - alpha])
    return {
        "point": point,
        "ci_low": float(lo),
        "ci_high": float(hi),
        "ci": ci,
        "n_matches": int(n_matches),
        "n_resamples": n_resamples,
        "bootstrap_se": float(boot_scores.std(ddof=1)),
    }


def paired_brier_match_clustered_ci(
    y_true: np.ndarray,
    y_prob_a: np.ndarray,
    y_prob_b: np.ndarray,
    match_id: np.ndarray,
    n_resamples: int = 2000,
    ci: float = 0.95,
    seed: int = 0,
) -> dict:
    """95% CI for (Brier_a - Brier_b), the PAIRED match-clustered bootstrap
    (SPEC.md section 9.3, Phase 1 session 2). Both models score the same
    rows, so each resample draws matches ONCE and evaluates both models on
    the identical resampled rows, rather than building two independent
    per-model intervals (brier_match_clustered_ci) and comparing them -
    the latter is the unpaired test and is too conservative to detect a
    real, small improvement, because match-level difficulty (an easy vs. a
    hard chase) inflates both models' individual variances independently
    instead of cancelling in the difference.

    The criterion this supports: model beats baseline iff the 95% CI of
    (brier_a - brier_b) - with a = baseline, b = model, by convention - lies
    entirely above zero.
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_prob_a = np.asarray(y_prob_a, dtype=np.float64)
    y_prob_b = np.asarray(y_prob_b, dtype=np.float64)
    match_id = np.asarray(match_id)
    sq_err_a = (y_prob_a - y_true) ** 2
    sq_err_b = (y_prob_b - y_true) ** 2

    unique_matches, inverse = np.unique(match_id, return_inverse=True)
    n_matches = len(unique_matches)
    sse_a = np.bincount(inverse, weights=sq_err_a, minlength=n_matches)
    sse_b = np.bincount(inverse, weights=sq_err_b, minlength=n_matches)
    n_per_match = np.bincount(inverse, minlength=n_matches)

    point_a = float(sq_err_a.sum() / len(sq_err_a))
    point_b = float(sq_err_b.sum() / len(sq_err_b))
    point_diff = point_a - point_b

    rng = np.random.default_rng(seed)
    diffs = np.empty(n_resamples)
    for i in range(n_resamples):
        draw_counts = np.bincount(rng.integers(0, n_matches, size=n_matches), minlength=n_matches)
        denom = (draw_counts * n_per_match).sum()
        brier_a = (draw_counts * sse_a).sum() / denom
        brier_b = (draw_counts * sse_b).sum() / denom
        diffs[i] = brier_a - brier_b

    alpha = (1 - ci) / 2
    lo, hi = np.quantile(diffs, [alpha, 1 - alpha])
    return {
        "point_diff": point_diff,
        "brier_a": point_a,
        "brier_b": point_b,
        "ci_low": float(lo),
        "ci_high": float(hi),
        "ci": ci,
        "n_matches": int(n_matches),
        "n_resamples": n_resamples,
        "significant": bool(lo > 0),
    }


def reliability_match_clustered(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    match_id: np.ndarray,
    n_bins: int = 10,
    n_resamples: int = 2000,
    ci: float = 0.95,
    seed: int = 0,
) -> list[dict]:
    """Per-decile calibration, measured correctly (Phase 1 session 3): a
    raw "<3pp, n>200 balls" check is unmeasurable, because balls within a
    match are correlated - 200 balls might be 5 effective observations.
    For each decile this reports the observed rate's match-clustered 95%
    CI (resampling matches with replacement, restricted to each drawn
    match's own rows that already fall in this decile) and whether the
    predicted mean falls inside it, plus BOTH n (rows) and n_matches
    (distinct matches contributing any row to this decile) - the
    "how thin is this actually" number a raw row count hides.
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_prob = np.asarray(y_prob, dtype=np.float64)
    match_id = np.asarray(match_id)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_idx = np.clip(np.digitize(y_prob, edges[1:-1], right=True), 0, n_bins - 1)

    rows = []
    for b in range(n_bins):
        mask = bin_idx == b
        n = int(mask.sum())
        if n == 0:
            rows.append({
                "bin_low": float(edges[b]), "bin_high": float(edges[b + 1]),
                "n": 0, "n_matches": 0, "mean_predicted": None, "observed_rate": None,
                "ci_low": None, "ci_high": None, "contains_predicted": None,
            })
            continue

        y_true_b, y_prob_b, match_id_b = y_true[mask], y_prob[mask], match_id[mask]
        unique_matches, inverse = np.unique(match_id_b, return_inverse=True)
        n_matches = len(unique_matches)
        sums = np.bincount(inverse, weights=y_true_b, minlength=n_matches)
        counts = np.bincount(inverse, minlength=n_matches)

        mean_predicted = float(y_prob_b.mean())
        observed_rate = float(y_true_b.mean())

        rng = np.random.default_rng(seed)
        boot = np.empty(n_resamples)
        for i in range(n_resamples):
            draw = np.bincount(rng.integers(0, n_matches, size=n_matches), minlength=n_matches)
            boot[i] = (draw * sums).sum() / (draw * counts).sum()
        alpha = (1 - ci) / 2
        lo, hi = np.quantile(boot, [alpha, 1 - alpha])

        rows.append({
            "bin_low": float(edges[b]), "bin_high": float(edges[b + 1]),
            "n": n, "n_matches": int(n_matches),
            "mean_predicted": mean_predicted, "observed_rate": observed_rate,
            "ci_low": float(lo), "ci_high": float(hi),
            "contains_predicted": bool(lo <= mean_predicted <= hi),
        })
    return rows
