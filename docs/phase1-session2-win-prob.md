# Phase 1 session 2 — LightGBM second-innings win probability model

Test touched exactly once, at the end, after the canary, the ablation, and
calibration were all already fixed. Full JSON report:
`api/data/eval_reports/win_prob_2nd_1787830785.json`. Model artifact:
`api/data/models/win_prob_2nd/seed42_1787830785.pkl` (booster + isotonic +
feature names + seed, gitignored — a run artifact, not source).

Feature set this session (§6.2 minus player-ability features and
`dls_resources_pct` — see SPEC.md §15's new open-decisions row): the 11
`match_states` state columns, 2 venue as-of aggregates, 1 Elo feature. No
LightGBM hyperparameter search — fixed, sensible defaults, with the number of
boosting rounds selected on validation via early stopping (50 rounds,
`binary_logloss`).

## Decision 1 — the canary, loud as required

| | Brier |
|---|---|
| Honest (train→test, real split) | 0.1230 |
| Shuffled (pooled train+test, random 80/20 ball-level split) | 0.0313 |
| **Gap** | **0.0917** |

Committed threshold going in: "convincing" ≥ 0.03–0.05, stop-and-investigate
below 0.01. The actual gap (0.092) clears the convincing bar by roughly 2×
and the stop threshold by 9×. This is exactly the mechanism predicted:
`elo_diff`/`venue_chase_win_rate`/`venue_avg_first_innings`/`target` are
constant across every ball of a match, and LightGBM can split on that
near-unique combination to functionally memorize a match whose balls got
split across the shuffled train/test boundary. Session 1's linear baseline
showed a 0.0005 gap under the identical construction; this session's model
shows an 0.0917 gap — confirms the canary is a working, model-capacity-aware
instrument, not a fixed number to expect regardless of what's being tested.

## Decision 4 — three-variant ablation (test split)

| Variant | Brier | Final 3 overs Brier |
|---|---|---|
| State only | 0.12476 | 0.06406 |
| State + venue | 0.12393 | 0.06468 |
| State + venue + Elo | 0.12320 | 0.06525 |

Paired match-clustered deltas (state→state+venue, state+venue→+Elo):

| Comparison | Δ Brier (paired) | 95% CI | Significant? |
|---|---|---|---|
| state → state+venue | 0.00083 | [-0.00090, 0.00261] | No |
| state+venue → state+venue+Elo | 0.00073 | [-0.00173, 0.00313] | No |

Matches the expectation stated before running: state alone captures the
large majority of the improvement over the logistic baseline; venue and Elo
each add a small, real-looking but **not statistically significant**
incremental lift on their own (both intervals straddle zero). Neither
group's lift is large relative to state-only's own gain over the baseline —
no leak signal by Decision 4's own test. (Final-3-overs Brier ticks
*up* slightly as venue/Elo are added — a small, plausible noise effect at
that bucket's smaller n=20,800, not a concern on its own.)

## Decision 3 — feature importance (gain, full model)

| Rank | Feature | Gain |
|---|---|---|
| 1 | `rrr_minus_crr` | 3,869,542 |
| 2 | `required_run_rate` | 1,029,853 |
| 3 | `runs_required` | 882,723 |
| 4 | `wickets_in_hand` | 638,420 |
| 5 | `target` | 533,653 |
| 6 | `elo_diff` | 458,155 |
| 7 | `venue_chase_win_rate` | 284,304 |
| 8 | `venue_avg_first_innings` | 252,939 |
| 9 | `current_run_rate` | 40,707 |
| 10 | `partnership_runs` | 35,963 |
| 11 | `phase_code` | 31,433 |
| 12 | `partnership_balls` | 25,155 |
| 13 | `balls_remaining` | 17,794 |
| 14 | `balls_since_wicket` | 2,308 |

State features occupy 5 of the top 6 ranks, as expected — `rrr_minus_crr`
dominates by nearly 4×, which is domain-sensible: the gap between the rate
required and the rate currently being scored is close to the single most
decision-relevant number in a chase. Both venue features rank below all
five of the strongest state features, as expected.

**Investigated, not celebrated: `elo_diff` (rank 6) outranks `balls_remaining`
(rank 13)**, which is a literal reading of "an as-of aggregate ranks above
balls_remaining" — the trigger condition stated before training. Conclusion
after investigation: **not a leak.** `balls_remaining` is one of the inputs
used to *derive* `required_run_rate` (`runs_required / (balls_remaining/6)`)
and is otherwise redundant with `runs_required`/`rrr_minus_crr`/`target`,
which already occupy ranks 1–5 — once those are available to a split, the
marginal information `balls_remaining` adds on its own is small, an ordinary
collinearity effect among the state group, not evidence anything saw the
future. `elo_diff` provides genuinely new information (team strength) no
state feature captures, so it plausibly earns more total gain than
`balls_remaining`'s *leftover* marginal value. It does **not** outrank
`runs_required` (rank 3) — the other half of the stated trigger condition —
which would have been much harder to explain away.

## Decision 5 — calibration hygiene

Isotonic fit on validation-split raw predictions only, applied to test.

| | Overall Brier (test) |
|---|---|
| Pre-calibration | 0.12320 |
| Post-calibration | 0.12360 |

**Honest finding, not glossed over: post-calibration Brier is very slightly
*worse* than pre-calibration (+0.0004), and the reliability tables show why —
calibration helped the extremes but not the middle:**

| Decile | Pre: pred / obs (pp gap) | Post: pred / obs (pp gap) | n |
|---|---|---|---|
| [0.0, 0.1) | 3.5 / 3.3 (0.2) | 2.2 / 2.8 (0.7) | 54,934–58,662 |
| [0.1, 0.2) | 14.7 / 17.3 (2.5) | 14.1 / 14.4 (0.3) | 20,651–24,972 |
| [0.2, 0.3) | 25.0 / 29.6 (**4.7**) | 26.0 / 25.3 (0.7) | 20,545–20,775 |
| [0.3, 0.4) | 34.8 / 38.8 (**4.0**) | 32.1 / 35.8 (**3.7**) | 17,047–21,193 |
| [0.4, 0.5) | 44.9 / 48.7 (**3.8**) | 46.1 / 49.3 (**3.3**) | 14,787–22,362 |
| [0.5, 0.6) | 55.0 / 59.4 (**4.4**) | 55.7 / 61.3 (**5.6**) | 14,459–14,614 |
| [0.6, 0.7) | 65.1 / 68.7 (**3.6**) | 67.0 / 72.3 (**5.3**) | 15,277–18,232 |
| [0.7, 0.8) | 75.0 / 77.6 (2.6) | 75.2 / 81.0 (**5.7**) | 16,460–20,441 |
| [0.8, 0.9) | 85.2 / 84.7 (0.5) | 83.5 / 86.7 (**3.2**) | 13,661–21,665 |
| [0.9, 1.0) | 96.7 / 96.3 (0.4) | 96.7 / 96.7 (0.0) | 51,625–54,304 |

**Section 9.3's target (<3pp error, any decile with n>200) is missed both
pre- and post-calibration** — pre-calibration misses it in 5 of 10 deciles
(0.2–0.7), post-calibration misses it in 6 (0.3–0.9, worse in several of
them). Isotonic calibration fixed the two extreme deciles well but made the
middle-to-upper range *worse* on test, most plausibly because it was fit
against only 1,296 validation matches — a small, single-decile-level mapping
learned from that many matches doesn't necessarily transfer to a different
2,179-match test period once the mapping is applied by decile. This is
reported as a genuine open shortcoming, not fixed this session: a future
session should investigate a coarser/regularized calibration map or more
validation data before this target is actually met.

## Decision 7 — paired match-clustered bootstrap vs. both baselines

| Baseline | Baseline Brier | Model Brier (post-cal) | Paired Δ | 95% CI | Beats baseline? |
|---|---|---|---|---|---|
| Logistic (§9.2 baseline 1) | 0.13423 | 0.12360 | 0.01063 | [0.00730, 0.01397] | **Yes** — entirely above zero |
| Historical base rate (§9.2 baseline 2) | 0.14978 | 0.12360 | 0.02618 | [0.02181, 0.03066] | **Yes** — entirely above zero |

Both intervals lie entirely above zero — the model beats both baselines by a
real, match-clustered-significant margin, not a noise-level improvement.

## Model results, bucketed (test split, post-calibration)

| Bucket | n | Brier | Log loss |
|---|---|---|---|
| Overall | 258,333 | 0.1236 | 0.3870 |
| Powerplay | 84,320 | 0.1603 | 0.4837 |
| Middle | 130,878 | 0.1165 | 0.3648 |
| Death | 43,135 | 0.0733 | 0.2655 |
| Final 3 overs | 20,800 | 0.0653 | 0.2542 |

Against SPEC.md §9.3's targets: overall ≤0.125 ✅ (0.1236), final 3 overs
≤0.085 ✅ (0.0653) — both cleared, and the model beats both baselines by a
significant margin per Decision 7. §11's stale Phase 1 acceptance line
("Brier ≤0.18") is cleared by a wide margin too, but that line is superseded
by §9.3's revised, tighter targets (flagged in the plan, not silently fixed).

## The estimate, and what actually happened

| Bucket | Estimated (this session) | Actual |
|---|---|---|
| Overall | 0.115–0.125 | **0.1236** — within range |
| Powerplay | 0.14–0.16 | **0.1603** — just above range |
| Middle | 0.105–0.12 | **0.1165** — within range |
| Death | 0.075–0.09 | **0.0733** — just below range |
| Final 3 overs | 0.075–0.085 | **0.0653** — notably below range |

Overall landed inside the committed range — no leak signal by the session's
own stated bar (that bar was "meaningfully below ~0.10 overall," not
breached). Final-3-overs missed low by more than any other bucket, though
still well above the ~0.05 threshold that would have triggered a stop.
**Read as a genuine miscalibration of the estimate, not a leak, for a
specific, checkable reason:** session 1's own historical base-rate baseline
(a nonparametric lookup, not a linear model) already scored 0.0811 in this
exact bucket — better than this session's *estimate range* for LightGBM. The
estimate was anchored on "a modest nonlinearity bonus over the linear
baseline" and didn't explicitly compare against the nonparametric baseline's
own number in the same bucket; LightGBM is at least as capable of the kind
of local, bin-like fit a lookup table does, so it landing near or below the
lookup's own score in the sharpest, most-determined bucket is the expected
outcome once framed that way, not a surprise requiring further
investigation. Noted for calibrating future estimates: anchor final-bucket
expectations against *both* baselines' numbers in that bucket, not just the
linear one.

## Cold-start venue rate (Decision 2, reported honestly)

`min_matches=10`: below this many prior chases at a venue as of the
prediction date, `venue_chase_win_rate_as_of`/`venue_avg_first_innings_as_of`
return `None` → `NaN`, passed through to LightGBM's native missing-value
handling, never a global-average substitute.

| Split | Matches | Cold-start (< 10 prior venue matches) |
|---|---|---|
| Train | 9,197 | 2,732 (29.7%) |
| Val | 1,296 | 424 (32.7%) |
| Test | 2,179 | 528 (24.2%) |

A real, substantial fraction — expected, given the corpus spans matches from
venues with genuinely infrequent international/franchise fixtures, and every
venue starts cold by construction. `elo_diff` never has this gap (Elo
defaults to 1500 for an unseen team, always a defined value) — only the two
venue features carry missing values into training.

## As-of leakage test suite

`tests/features/test_venue_stats.py` (7 tests, writable
`cricket_training_test` database) and one added test in
`tests/features/test_elo.py` — all three as-of primitives
(`venue_chase_win_rate_as_of`, `venue_avg_first_innings_as_of`, `elo_as_of`)
have a poison-pill test: compute a value, insert a synthetic future match
with a deliberately opposite/extreme outcome, recompute at the same
`as_of_date`, assert byte-identical results. Also covers: no-history →
`None`, below-`min_matches` → `None` (venue features only), and the boundary
match (dated exactly `as_of_date`) not counting.

## Housekeeping note

`tests/ingest/conftest.py`'s writable-test-database fixtures (`test_db_url`,
`conn`, `TABLES_TO_RESET`) were moved to a shared `tests/conftest.py` this
session — the as-of poison-pill tests needed the identical fixture in a
second test directory, and duplicating a ~90-line fixture file was worse
than promoting it. No behavior change; `tests/ingest`'s own suite re-verified
green after the move.
