# Phase 1 session 1 — temporal splits and baselines

This is the number every future model gets compared to. Both baselines are fit on
`train` only and evaluated once against `test` (touched exactly once, here);
`val` is reported alongside for transparency, not for model selection — there's
nothing to tune on it at this baseline level of complexity.

Corpus, after `eval/splits.py`'s full predicate (`innings=2`, `batting_team_won IS
NOT NULL`, `NOT has_reconciliation_anomaly`, `required_run_rate IS NOT NULL`):
train 1,327,310 rows / 9,197 matches, val 148,707 / 1,296, test 258,333 / 2,179.

## Results (test split)

| Bucket | n | Logistic Brier | Logistic log loss | Base-rate Brier | Base-rate log loss |
|---|---|---|---|---|---|
| Overall | 258,333 | **0.1342** | 0.4153 | **0.1498** | 0.4593 |
| Powerplay | 84,320 | 0.1682 | 0.5007 | 0.2078 | 0.5925 |
| Middle | 130,878 | 0.1245 | 0.3837 | 0.1341 | 0.4339 |
| Death | 43,135 | 0.0975 | 0.3440 | 0.0840 | 0.2758 |
| Final 3 overs (`balls_remaining<=18`) | 20,800 | 0.0925 | 0.3600 | 0.0811 | 0.2589 |

Val-split numbers (same models, no refitting) are consistent in shape: overall
0.1411 / 0.1525, powerplay 0.1817 / 0.2082, middle 0.1291 / 0.1380, death 0.0968 /
0.0858, final 3 overs 0.0861 / 0.0797 (logistic / base-rate respectively). Full
JSON: `api/data/eval_reports/baselines_1787828424.json`.

**Note on the "start of chase" / "final 3 overs" cutoffs (section 9.3):** the
powerplay phase bucket stands in for "start of chase" — `splits.py`'s dataset
doesn't carry each match's `scheduled_balls` (only `match_state.py`'s internal
build CTE has it), and a single global `balls_remaining` threshold for "first
over" would be wrong for ODI vs. T20 without it. `final_3_overs` is exact
(`balls_remaining<=18`).

## Match-clustered confidence intervals (logistic baseline, test split)

Balls within a match are highly correlated, so a naive ball-level bootstrap
(or a naive standard error computed as if 258,333 rows were 258,333 independent
observations) understates the uncertainty by roughly an order of magnitude —
effective sample size is closer to the 2,179-match count. `eval/metrics.py`'s
`brier_match_clustered_ci` resamples matches with replacement (2,000 resamples),
letting every resampled match contribute all of its own rows:

| Bucket | Point | 95% CI | n matches |
|---|---|---|---|
| Overall | 0.1342 | [0.1264, 0.1419] | 2,179 |
| Final 3 overs | 0.0925 | [0.0806, 0.1041] | 1,466 |

**This is the bar SPEC.md section 9.3 sets for the next session's model:**
Brier ≤ 0.125 overall *and* beating 0.1342 by more than this interval's standard
error (≈0.0039); Brier ≤ 0.085 in the final 3 overs. Both targets are relative to
these measured numbers, not the spec's original absolute figures (0.16–0.18),
which were set before any baseline existed and would have let a *regression*
report as a success.

## The estimate, and what actually happened

Before running anything, the committed estimate for the logistic baseline was:

| Bucket | Estimated Brier | Actual Brier |
|---|---|---|
| Overall | 0.19–0.21 | **0.1342** |
| Start of chase (powerplay) | 0.24–0.25 | **0.1682** |
| Middle | 0.21–0.23 | **0.1245** |
| Death | 0.16–0.19 | **0.0975** |
| Final 3 overs | 0.11–0.15 | **0.0925** |

Every bucket came in better than predicted. Per the anti-rationalization
instruction this was committed against, that gap gets investigated, not waved
off. Conclusion after review: **the estimate was too pessimistic, not evidence
of a leak** — but the reasoning below was corrected once (see "what was wrong
with the first explanation").

### Why the estimate was wrong

The real reason is the **row distribution, not feature design**. A chase
contributes up to ~120 rows (T20) or ~300 (ODI) to the *overall* Brier average,
and the late ones are near-determined states — Brier 0.02–0.05 individually,
visible in this session's own death/final-3-overs numbers above. Averaging from
~0.25 at the first ball down to ~0.03 at the last ball of a typical chase lands
around 0.13–0.15 for *any* reasonable model, including ones with far less
predictive power than this one. **A low innings-wide Brier is never, by itself,
evidence of a good model** — it's disproportionately a property of how many
near-certain-outcome rows the row-level average includes, which is exactly why
section 9.3's targets are phase/bucket-specific rather than one number, and
exactly why the next model has to beat this baseline's *bucketed* numbers by a
significant margin, not just its overall one.

*(An earlier draft of this doc attributed the miss primarily to
`required_run_rate` already being a computed ratio rather than a raw feature,
with the row-distribution effect as secondary. That ordering was wrong — the
ratio-feature effect is real but minor; the row distribution is the dominant
cause, per review.)*

### What the shuffled-split canary does and does not show

`tests/eval/test_splits.py`'s shuffled-ball-level-split test found a small gap
(shuffled 0.1337 vs. honest 0.1342). **This gap is not evidence against a leak
in this baseline, and should not be read as reassurance.** A 3-feature linear
model has essentially no capacity to exploit match-level near-duplicate rows
crossing a train/test boundary — it can't "remember" a specific match, only fit
a single smooth global function of three numbers. A small gap here is exactly
what a low-capacity model would produce whether or not the underlying split
construction was safe; the test is uninformative for this baseline, not
supportive of it. It becomes a real instrument starting next session, when
LightGBM (tree-based, high capacity, able to carve out feature-space regions
tight enough to functionally memorize a specific match) is the thing being
checked — that's the point at which a shuffled-split canary showing a large gap
would actually mean something, and its absence here should not be cited as
having "passed" a leak check for this model class.

### What does support the conclusion

- **Val (0.1411) and test (0.1342) agree**, on two temporally disjoint, non-
  adjacent periods (2024 vs. 2025+). A leak tied to a specific period (e.g. a
  feature that only sees the future within one of these windows) would be more
  likely to show up as disagreement between the two than as agreement.
- **`match_states`' own off-by-one was hand-verified against source in Phase 0
  session 6** (`tests/features/test_match_state.py`), independently of this
  session — `required_run_rate`/`wickets`/`balls_remaining` are trusted inputs
  here, not re-derived from raw deliveries.
- **The result is directionally sensible, not just numerically convenient.**
  Death-overs Brier is far better than powerplay's for both baselines, exactly
  the expected shape (state late in a chase is far more informative than state
  at the start) — and the historical base-rate baseline (a nonparametric
  lookup) beats the logistic baseline specifically in the death/final-3-overs
  buckets while losing to it everywhere else, matching each method's expected
  strength (a lookup is sharper where the outcome is close to a deterministic
  function of the bin; a smooth linear model generalizes better in noisier,
  less-determined bins) rather than an unexplained anomaly in one baseline
  only.

These two arguments carry the conclusion on their own; the row-distribution
explanation accounts for *why* the estimate missed, and the canary is set aside
as uninformative for this model class rather than counted as support.

## Leakage test suite

`tests/eval/test_splits.py` (7 tests, all against the real corpus) and
`tests/eval/test_baselines.py` (7 tests, synthetic data) — see the module
docstrings for what each test does and does not catch. The full "what these
tests would not catch" discussion lives in the session's plan file, not
duplicated here. `tests/eval/test_metrics.py` adds 3 tests for the
match-clustered bootstrap (point estimate matches the plain Brier score, the
clustered SE is materially wider than a naive ball-level bootstrap on
correlated data, and a single-match dataset reproduces an exact score on every
resample).
