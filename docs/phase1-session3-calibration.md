# Phase 1 session 3 — fixing calibration properly

Test touched exactly once, at the end, after the calibration method was
already chosen on a held-out chunk of validation. Full JSON report:
`api/data/eval_reports/calibration_selection_1789001840.json`. The
underlying LightGBM booster is **unchanged from session 2** — this session
is calibration only.

## Expected winner, stated before running

Phase-stratified Platt scaling — phase targets the diagnosed regime split,
and a 2-parameter parametric map should be more robust than isotonic on the
smaller per-stratum sample a phase+temporal split leaves. **This was
wrong.** Identity (no calibration) won. See "what actually happened" below.

## Decision 1 — the fit/select split, grounded in real data

Val (2024) splits almost exactly in half at the calendar midpoint:

| Chunk | Matches | Rows |
|---|---|---|
| Fit (≤ 2024-07-01) | 647 | 73,063 |
| Select (> 2024-07-01) | 649 | 75,644 |

649 matches is enough to discriminate 5 candidates on Brier and get usable
(if not razor-precise) match-clustered CIs per decile — no adjustment to the
calendar-midpoint split point was needed.

## Decision 2 — five candidates, select-chunk comparison

| Candidate | Select Brier | Deciles failing (of populated) |
|---|---|---|
| **Identity** | **0.1286** | **2 / 10** |
| Global isotonic | 0.1300 | 5 / 10 |
| Phase-stratified isotonic | 0.1301 | 5 / 10 |
| Global Platt | 0.1302 | 5 / 10 |
| Phase-stratified Platt | 0.1300 | 5 / 10 |

**Every active calibration method scored worse than doing nothing**, on
Brier *and* on decile-failure count — not a close call, and not just one
metric disagreeing with the other (Decision 2's stated tie-break condition
never came up). Spot-checking the select-chunk reliability tables explains
why: e.g. global isotonic's [0.9, 1.0) decile goes from well-calibrated
under identity (obs 0.950, contained) to miscalibrated under its own
correction (obs 0.946, predicted 0.978, not contained) — the isotonic map,
fit on the 647-match fit chunk, pulled predictions in a direction that
didn't hold up on a genuinely later, disjoint set of matches. This is the
same overfitting mechanism session 2's global isotonic showed on
train→test, just caught here on val's own internal fit→select split,
*before* it could touch test.

## Decision 3 — stratification tradeoff, not revisited further

Both phase-stratified candidates scored no better than their global
counterparts (isotonic: 0.1301 vs. 0.1300; Platt: 0.1300 vs. 0.1302) —
stratifying by phase didn't recover the benefit the diagnosis predicted, at
this data volume. `MIN_ROWS_PER_STRATUM=1000` never bound in practice (all
three phases cleared it comfortably in the 73,063-row fit chunk) — the
floor is implemented and tested, just not the thing that decided this
result. Finer stratification (`balls_remaining` bins) is not pursued this
session — with the *coarsest* available stratification already
underperforming no correction at all, a finer one (less data per stratum)
is expected to do worse, not better, per the tradeoff named in the plan.

## Decision 4 — final reliability (test, winner = identity)

| Decile | n | n_matches | Predicted | Observed | 95% CI (match-clustered) | Contains predicted? |
|---|---|---|---|---|---|---|
| [0.0, 0.1) | 58,662 | 1,098 | 0.035 | 0.033 | [0.023, 0.045] | ✅ |
| [0.1, 0.2) | 24,972 | 1,193 | 0.148 | 0.173 | [0.143, 0.205] | ✅ |
| [0.2, 0.3) | 20,545 | 1,242 | 0.250 | 0.296 | [0.263, 0.332] | ❌ |
| [0.3, 0.4) | 17,047 | 1,202 | 0.348 | 0.388 | [0.352, 0.426] | ❌ |
| [0.4, 0.5) | 14,787 | 1,188 | 0.449 | 0.487 | [0.447, 0.525] | ✅ |
| [0.5, 0.6) | 14,614 | 1,163 | 0.550 | 0.594 | [0.553, 0.632] | ❌ |
| [0.6, 0.7) | 15,277 | 1,155 | 0.651 | 0.687 | [0.651, 0.721] | ❌ |
| [0.7, 0.8) | 16,460 | 1,156 | 0.750 | 0.776 | [0.742, 0.810] | ✅ |
| [0.8, 0.9) | 21,665 | 1,151 | 0.852 | 0.847 | [0.811, 0.879] | ✅ |
| [0.9, 1.0) | 54,304 | 1,204 | 0.967 | 0.963 | [0.949, 0.975] | ✅ |

**4 of 10 deciles fail** the match-clustered check, even for the winning
(identity) candidate — every decile has 1,000+ effective matches, so this
isn't a thin-sample artifact. Honest verdict: Phase 1 ships with real,
measured, disclosed residual miscalibration in the middle-probability range,
not a fully calibrated model. No candidate tried this session improves on
it; a future session would need either substantially more validation data
or a genuinely different calibration approach (see `docs/phase1-closeout.md`'s
"things a future session would get wrong").

## Decision 5 — monotonicity

**Not applicable this session** — the check only runs for a phase-stratified
winner, and identity won. If a future session revisits phase-stratified
calibration (more data, a different stratification), re-run
`eval.run_calibration_selection.check_boundary_monotonicity` before shipping
it — it was built and is ready, just unused this time.

## Decision 6 — distribution shift over time (test, quarter-bucketed)

| Quarter | Matches | Brier | Deciles failing |
|---|---|---|---|
| 2025-Q1 | 241 | 0.1254 | 4/10 |
| 2025-Q2 | 390 | 0.1272 | 0/10 |
| 2025-Q3 | 350 | 0.1185 | 0/10 |
| 2025-Q4 | 269 | 0.1082 | 2/10 |
| 2026-Q1 | 240 | 0.1059 | 0/10 |
| 2026-Q2 | 420 | 0.1295 | 5/10 |
| 2026-Q3 | 269 | 0.1417 | 0/10 |

**No clean monotonic trend of worsening calibration with distance from the
2024 fit window** — Brier and decile-failure counts fluctuate quarter to
quarter (Brier ranges 0.1059–0.1417; failures range 0–5 of 10) rather than
climbing steadily. Since identity won, there's no fitted calibration map to
literally "go stale" here — but the underlying model's own raw calibration
clearly isn't stationary over time either. **Read as real evidence for
keeping §8.1/§8.2's ongoing monitoring jobs** (the variability is real and
worth watching), but not as a clean, single "recalibrate every N days"
number the way a monotonic decay curve would have given.

## Paired bootstrap vs. both baselines (test, final model = booster + identity)

| Baseline | Baseline Brier | Model Brier | Paired Δ | 95% CI | Beats baseline? |
|---|---|---|---|---|---|
| Logistic (§9.2 baseline 1) | 0.13423 | 0.12320 | 0.01104 | [0.00756, 0.01446] | **Yes** |
| Historical base rate (§9.2 baseline 2) | 0.14978 | 0.12320 | 0.02659 | [0.02190, 0.03139] | **Yes** |

## What actually happened vs. the pre-registered expectation

Phase-stratified Platt was expected to win on the reasoning that phase
targets the diagnosed regime split and a parametric map generalizes better
than a nonparametric one on limited per-stratum data. **Identity won
instead, and every real calibration method underperformed it.** The
miscalibration the diagnosis correctly identified as a *mechanism*
(same raw score, different regimes) turned out to be smaller, in this
corpus, than what any of the four correction methods could reliably capture
without themselves overfitting the available validation data — a more
fundamental finding than "pick a better calibration method," and one only
visible because this session built a genuine fit/select holdout instead of
fitting and eyeballing on the same data (which is exactly what let session
2's isotonic ship un-caught). Recorded here rather than rationalized away:
the premise that a fixable, regime-dependent bias definitely existed and
just needed the right method was itself not fully supported once measured
properly.

## Artifact and versioning

`api/data/models/win_prob_2nd/winprob2-20260910.pkl` — booster (session 2,
unchanged) + `IdentityCalibrator` + feature names + seed. One row written to
the **local** `model_versions` table (`winprob2-20260910`, `is_active=true`,
`test_brier=0.1232`, `test_log_loss=0.3804`) via `models/registry.py`.
**Not written to Supabase** — SPEC.md §2.1 states training never touches
Supabase, and `model_versions` is a Supabase-hosted table in that split;
actually deploying this version (artifact upload + a Supabase-side row) is
Phase 2 scope, not a training-time side effect.
