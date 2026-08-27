"""eval/metrics.py known-value tests (SPEC.md section 9.3, Phase 1 session 1)."""

from __future__ import annotations

import numpy as np
import pytest

from eval.metrics import (
    brier_match_clustered_ci,
    brier_score,
    bucketed_metrics,
    log_loss,
    paired_brier_match_clustered_ci,
    reliability_table,
)


def test_constant_half_prediction_on_balanced_labels_gives_brier_quarter():
    y_true = np.array([0, 1, 0, 1], dtype=np.int8)
    y_prob = np.full(4, 0.5)
    assert brier_score(y_true, y_prob) == 0.25


def test_perfect_prediction_gives_zero_brier():
    y_true = np.array([0, 1, 1, 0], dtype=np.int8)
    y_prob = np.array([0.0, 1.0, 1.0, 0.0])
    assert brier_score(y_true, y_prob) == 0.0


def test_log_loss_matches_hand_computed_value():
    y_true = np.array([1, 0], dtype=np.int8)
    y_prob = np.array([0.8, 0.3])
    expected = -np.mean([np.log(0.8), np.log(0.7)])
    assert log_loss(y_true, y_prob) == expected


def test_log_loss_does_not_blow_up_at_the_extremes():
    # Without clipping, log(0) is -inf and the mean becomes nan/inf - the
    # exact failure this clipping exists to prevent.
    y_true = np.array([1, 0], dtype=np.int8)
    y_prob = np.array([1.0, 0.0])
    result = log_loss(y_true, y_prob)
    assert np.isfinite(result)
    assert result < 1e-6


def test_reliability_table_bins_sum_to_total_n():
    rng = np.random.default_rng(0)
    y_prob = rng.uniform(0, 1, size=1000)
    y_true = (rng.uniform(0, 1, size=1000) < y_prob).astype(np.int8)
    table = reliability_table(y_true, y_prob, n_bins=10)
    assert len(table) == 10
    assert sum(row["n"] for row in table) == 1000


def test_reliability_table_recovers_good_calibration():
    # y_prob IS the true probability of y_true=1 by construction, so every
    # bin's observed rate should land close to its mean predicted value.
    rng = np.random.default_rng(1)
    y_prob = rng.uniform(0, 1, size=20000)
    y_true = (rng.uniform(0, 1, size=20000) < y_prob).astype(np.int8)
    table = reliability_table(y_true, y_prob, n_bins=10)
    for row in table:
        if row["n"] > 200:
            assert abs(row["mean_predicted"] - row["observed_rate"]) < 0.03


def test_bucketed_metrics_returns_one_entry_per_bucket_value():
    y_true = np.array([0, 1, 0, 1, 1], dtype=np.int8)
    y_prob = np.array([0.1, 0.9, 0.5, 0.5, 0.5])
    bucket = np.array(["powerplay", "powerplay", "death", "death", "death"])
    result = bucketed_metrics(y_true, y_prob, bucket)
    assert set(result.keys()) == {"powerplay", "death"}
    assert result["powerplay"]["n"] == 2
    assert result["death"]["n"] == 3
    assert result["powerplay"]["brier"] == brier_score(
        y_true[bucket == "powerplay"], y_prob[bucket == "powerplay"]
    )


# --- Match-clustered bootstrap CI (SPEC.md section 9.3) --------------------


def test_match_clustered_ci_point_estimate_matches_plain_brier():
    rng = np.random.default_rng(0)
    n_matches, rows_per_match = 50, 20
    match_id = np.repeat(np.arange(n_matches), rows_per_match)
    y_prob = rng.uniform(0, 1, size=n_matches * rows_per_match)
    y_true = (rng.uniform(0, 1, size=n_matches * rows_per_match) < y_prob).astype(np.int8)

    result = brier_match_clustered_ci(y_true, y_prob, match_id, n_resamples=200, seed=1)
    assert result["point"] == brier_score(y_true, y_prob)
    assert result["ci_low"] <= result["point"] <= result["ci_high"]
    assert result["n_matches"] == n_matches


def test_match_clustered_se_is_larger_than_naive_ball_level_se():
    """The whole point of clustering: resampling matches (each contributing
    a highly-correlated block of rows - same label, similar features) must
    give a materially wider interval than resampling individual balls as if
    they were independent observations. If this ever narrows to roughly the
    naive ball-level SE, the clustering isn't doing anything."""
    rng = np.random.default_rng(2)
    n_matches, rows_per_match = 60, 80
    # Match-level "difficulty" so rows within a match are correlated, not iid:
    # the model predicts each match's own true rate (varies match to match),
    # and labels are drawn from that same rate, so a match's mean squared
    # error (p(1-p), extreme near 0 or 1) varies between matches on top of
    # ordinary within-match sampling noise.
    match_true_rate = rng.uniform(0.1, 0.9, size=n_matches)
    match_id = np.repeat(np.arange(n_matches), rows_per_match)
    y_true = np.concatenate(
        [rng.binomial(1, p, size=rows_per_match) for p in match_true_rate]
    ).astype(np.int8)
    y_prob = np.repeat(match_true_rate, rows_per_match)

    clustered = brier_match_clustered_ci(y_true, y_prob, match_id, n_resamples=500, seed=3)
    clustered_se = clustered["bootstrap_se"]

    # Naive ball-level bootstrap, ignoring match structure entirely.
    sq_err = (y_prob - y_true) ** 2
    rng2 = np.random.default_rng(4)
    n = len(sq_err)
    naive_scores = np.array(
        [sq_err[rng2.integers(0, n, size=n)].mean() for _ in range(500)]
    )
    naive_se = naive_scores.std(ddof=1)

    assert clustered_se > naive_se * 1.5  # materially wider, not just noisier by chance


def test_match_clustered_ci_uses_all_of_a_resampled_matchs_rows():
    # A single-match, single-row-value dataset: every resample must
    # reproduce the same score exactly, since there's only one match to draw
    # (with replacement) and it always contributes the same rows.
    match_id = np.zeros(10, dtype=np.int64)
    y_true = np.array([1, 0] * 5, dtype=np.int8)
    y_prob = np.full(10, 0.5)
    result = brier_match_clustered_ci(y_true, y_prob, match_id, n_resamples=50, seed=5)
    assert result["ci_low"] == result["ci_high"] == result["point"] == 0.25


# --- Paired match-clustered bootstrap (SPEC.md section 9.3, session 2) ----


def test_paired_ci_point_diff_matches_plain_brier_difference():
    rng = np.random.default_rng(10)
    n_matches, rows_per_match = 80, 25
    match_id = np.repeat(np.arange(n_matches), rows_per_match)
    y_true = rng.integers(0, 2, size=n_matches * rows_per_match).astype(np.int8)
    y_prob_a = rng.uniform(0, 1, size=n_matches * rows_per_match)  # "baseline" - worse, uncorrelated
    y_prob_b = np.clip(y_true + rng.normal(0, 0.1, size=n_matches * rows_per_match), 0, 1)  # "model" - better

    result = paired_brier_match_clustered_ci(y_true, y_prob_a, y_prob_b, match_id, n_resamples=300, seed=11)
    assert result["point_diff"] == pytest.approx(brier_score(y_true, y_prob_a) - brier_score(y_true, y_prob_b))
    assert result["brier_a"] == pytest.approx(brier_score(y_true, y_prob_a))
    assert result["brier_b"] == pytest.approx(brier_score(y_true, y_prob_b))


def test_paired_ci_detects_a_real_improvement_as_significant():
    # Model b is unambiguously, consistently better than model a on every
    # row - the paired CI must find this significant (entirely above zero).
    rng = np.random.default_rng(12)
    n_matches, rows_per_match = 200, 30
    match_id = np.repeat(np.arange(n_matches), rows_per_match)
    y_true = rng.integers(0, 2, size=n_matches * rows_per_match).astype(np.int8)
    y_prob_a = np.full(n_matches * rows_per_match, 0.5)  # naive - Brier 0.25
    y_prob_b = np.clip(y_true + rng.normal(0, 0.05, size=n_matches * rows_per_match), 0, 1)  # near-perfect

    result = paired_brier_match_clustered_ci(y_true, y_prob_a, y_prob_b, match_id, n_resamples=500, seed=13)
    assert result["significant"] is True
    assert result["ci_low"] > 0


def test_paired_ci_identical_models_are_never_significant():
    # Same predictions for both "models" - the true difference is exactly
    # zero, so the interval must straddle zero, not spuriously exclude it.
    rng = np.random.default_rng(14)
    n_matches, rows_per_match = 100, 20
    match_id = np.repeat(np.arange(n_matches), rows_per_match)
    y_true = rng.integers(0, 2, size=n_matches * rows_per_match).astype(np.int8)
    y_prob = rng.uniform(0, 1, size=n_matches * rows_per_match)

    result = paired_brier_match_clustered_ci(y_true, y_prob, y_prob, match_id, n_resamples=300, seed=15)
    assert result["point_diff"] == 0.0
    assert result["ci_low"] <= 0.0 <= result["ci_high"]
    assert result["significant"] is False


def test_paired_ci_tighter_than_two_independent_intervals_under_shared_match_difficulty():
    # Construct match-level "difficulty" shared by BOTH models (a hard match
    # is hard for both) - this is exactly what the paired test is supposed
    # to cancel out, and what two independent per-model CIs (naively
    # compared) would double-count instead.
    rng = np.random.default_rng(16)
    n_matches, rows_per_match = 150, 40
    match_difficulty = rng.uniform(0, 1, size=n_matches)  # shared "true" outcome rate per match
    match_id = np.repeat(np.arange(n_matches), rows_per_match)
    y_true = np.concatenate(
        [rng.binomial(1, p, size=rows_per_match) for p in match_difficulty]
    ).astype(np.int8)
    base_pred = np.repeat(match_difficulty, rows_per_match)
    y_prob_a = base_pred  # baseline
    y_prob_b = np.clip(base_pred + 0.03, 0, 1)  # model: small, consistent, real improvement

    paired = paired_brier_match_clustered_ci(y_true, y_prob_a, y_prob_b, match_id, n_resamples=500, seed=17)
    independent_a = brier_match_clustered_ci(y_true, y_prob_a, match_id, n_resamples=500, seed=17)
    independent_b = brier_match_clustered_ci(y_true, y_prob_b, match_id, n_resamples=500, seed=17)

    paired_width = paired["ci_high"] - paired["ci_low"]
    unpaired_width = (independent_a["ci_high"] - independent_a["ci_low"]) + (
        independent_b["ci_high"] - independent_b["ci_low"]
    )
    assert paired_width < unpaired_width
