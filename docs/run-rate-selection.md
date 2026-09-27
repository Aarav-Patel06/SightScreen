# The run rate early in a chase: selection (2026-09-27)

SPEC.md §11, "Next model task". Code: `api/src/features/run_rate.py` (the
one function training and serving would both call),
`api/src/eval/run_rate_selection.py` (stages `build`, `select`, `diagnose`,
`final`). Raw reports (gitignored):
`api/data/eval_reports/run_rate_selection_1790491457.json`,
`api/data/eval_reports/run_rate_diagnose_1790537813.json`.

**Everything here is from the validation split.** The test split is cached
but has not been scored. Its single look waits for the owner to confirm
the proposal at the end.

## Set-up

- Feature set `state_venue_elo_no_partnership` throughout. Candidates differ
  only in `current_run_rate` and `rrr_minus_crr`.
- Every candidate is a full LightGBM retrain on the §9.1 train split
  (1,327,310 rows, 9,197 chases) with the same seed and hyperparameters.
  Each stops early on the **fit chunk** of validation (≤ 2024-07-01, 647
  chases) and is scored raw on the **select chunk** (> 2024-07-01, 649
  chases).
- Training time: **3-4 s per retrain** (113-170 boosting rounds). Building
  the feature bundles took 56 s, once, for all three splits.
- The b prior, fitted on the train split only: T20 7.84 an over, ODI 5.13.
- Selection rule, in the code before any result existed: a candidate is
  eligible unless its paired interval against the control shows a
  regression overall or in the final 3 overs. The lowest first-5-overs
  Brier wins. Candidates tied with it go by order: c, then a by N, then b′
  by k, then b by k.

## Candidate table (select chunk, raw scores)

| Candidate | Overall | Final 3 overs | First 5 overs | Balls 2-6 | First 5 vs control ×10⁻⁴ (+ = better) | Eligible |
|---|---|---|---|---|---|---|
| control (today's run rate) | 0.12946 | 0.05668 | 0.17171 | 0.18628 | - | - |
| c: both dropped | 0.13250 | 0.05700 | 0.17575 | 0.18695 | −40 [−81, +1] | no: overall −30 [−59, −1] |
| a: N = 6 | 0.12916 | 0.05699 | 0.17121 | 0.18728 | +5 [−11, +21] | yes |
| a: N = 12 | 0.12919 | 0.05730 | 0.17191 | 0.18654 | −2 [−22, +18] | yes |
| a: N = 18 | 0.12967 | 0.05635 | 0.17287 | 0.18621 | −12 [−39, +16] | yes |
| a: N = 30 | 0.13131 | 0.05756 | 0.17804 | 0.18763 | −63 [−106, −23] | no: overall −19 [−35, −2] |
| b′: k = 6 | 0.12928 | 0.05647 | 0.17182 | 0.18661 | −1 [−18, +16] | yes |
| b′: k = 12 | 0.12882 | 0.05680 | 0.17138 | 0.18676 | +3 [−15, +21] | yes |
| b′: k = 24 | 0.12978 | 0.05741 | 0.17254 | 0.18737 | −8 [−29, +12] | yes |
| b′: k = 48 | 0.13040 | 0.05656 | 0.17259 | 0.18716 | −9 [−33, +14] | yes |
| b: k = 6 | 0.12924 | 0.05676 | 0.17121 | 0.18748 | +5 [−13, +23] | yes |
| b: k = 12 | 0.13065 | 0.05725 | 0.17294 | 0.18972 | −12 [−35, +8] | yes |
| b: k = 24 | 0.12962 | 0.05715 | 0.17192 | 0.18836 | −2 [−28, +22] | yes |
| b: k = 48 | 0.13093 | 0.05715 | 0.17296 | 0.18924 | −13 [−44, +15] | yes |

**The rule's winner is `a_n6`**, by tie-break alone. All eleven eligible
candidates are tied with the best one, and none beats the control on the
first 5 overs. The rule was not amended after the fact.

## Switch diagnostic (select chunk)

This is the mean change in p across one ball, per chase. A calibrated
probability has an expected one-ball change of zero, so a mean clearly away
from zero at one particular ball is a feature switching, not cricket.

| Candidate | Ball 1 | At its switch | Balls 2-9 |
|---|---|---|---|
| control | **−5.0 pp** [−5.6, −4.4] | ball 1 | flat |
| a: N = 6 / 12 / 18 / 30 | −0.3 to −0.4 pp | **−3.1 / −2.3 / −1.4 / −0.8 pp** at ball N | flat |
| b′: k = 12 | −0.1 pp [−0.3, +0.2] | none | −0.3 to −0.75 pp per ball |
| b: k = 6-48 | −0.9 to −1.4 pp | none | a similar drift |

Summed over balls 1-9, every candidate falls about 3.5-5 pp; the control
falls 4.9 pp. Withholding the run rate moves the fall to a later ball, and
shrinking it spreads the fall out. Neither removes it. **The ball-1 drop is
the model settling into a chase**, not purely the feature switching.

**Ball 36:** every model rises 0.8-1.9 pp on the ball after the T20
powerplay ends, where `phase_code` switches. This is logged as a known
issue in SPEC §11 and is not fixed.

## Early-chase bias: pre-existing, T20, recent

This is mean (p − won) on the select chunk, in percentage points, with
match-clustered 95% CIs. Positive means optimistic for the chasing side.

| Model | Before ball 1 | After 1 ball | After 2-5 | After 12-29 |
|---|---|---|---|---|
| served `winprob2-20260910` | +6.8 [+3.5, +10.4] | +5.0 [+1.6, +8.4] | +4.8 [+1.5, +8.2] | +5.0 [+2.0, +8.0] |
| control, selection fit | +8.4 [+5.1, +11.9] | +4.9 [+1.5, +8.3] | +5.0 [+1.7, +8.5] | +5.2 [+2.2, +8.2] |
| control, served pipeline, identity | +8.3 [+5.0, +11.9] | +5.0 [+1.6, +8.4] | +5.0 [+1.8, +8.5] | +5.1 [+2.1, +8.2] |

- **The bias is pre-existing.** The served model shows it too. It wasn't
  introduced by the retrain or by removing the partnership features. The
  served artifact stopped early on all of 2024, so this chunk is in-sample
  for its round count, and it is still biased.
- **By format:** T20 (587 chases) is +7.0 / +5.3 / +5.2 / +5.3 for the
  served model, all significant. ODI (62 chases) is +5.1 / +2.0 / +1.1 /
  +2.7, with intervals about ±11 pp: too few chases to say anything.
- **By quarter** (the chunk covers only July-December 2024, so a by-year
  split isn't possible within it):
  - 2024-Q3 (339 chases): served +3.1 / −0.1 / +0.8 / +2.4, all within noise.
  - 2024-Q4 (310 chases): +10.9 / +10.4 / +9.1 / +7.8, all significant.

  The bias is concentrated in the latest quarter. Two quarters can't tell
  drift from a one-off cohort. The test split (2025 on) would, and it
  hasn't been looked at.

## Calibration on the control (served pipeline)

The five calibrators were fit on the fit chunk and scored on the select
chunk. The control was stopped early on all of validation, at 149 rounds.

| Calibrator | Overall | Final 3 | First 5 | Balls 2-6 | Bias before ball 1 / after 2-5 |
|---|---|---|---|---|---|
| **identity** | **0.12943** | **0.05649** | **0.17160** | **0.18613** | +8.3 / +5.0 |
| global isotonic | 0.13082 | 0.05686 | 0.17395 | 0.18876 | +10.3 / +7.3 |
| phase isotonic | 0.13116 | 0.05829 | 0.17327 | 0.18770 | +9.8 / +6.7 |
| global Platt | 0.13097 | 0.05694 | 0.17474 | 0.18952 | +10.6 / +7.2 |
| phase Platt | 0.13083 | 0.05741 | 0.17370 | 0.18831 | +10.0 / +6.8 |

Against identity, every other calibrator's first-5-overs Brier is
significantly worse (by 17 to 31 ×10⁻⁴), and all of them increase the early
optimism. The fit chunk was less optimistic than the select chunk, so a map
fitted on it corrects in the wrong direction. **Identity wins on every
segment, not just overall.**

For comparison, served minus control-with-identity on this chunk: overall
−8.8 ×10⁻⁴ [−20.3, +3.4], final 3 overs −4.5 [−14.6, +5.1], first 5 overs
+1.8 [−11.8, +16.1]. None of these is significant.

## An informal look at the test split, on the record

SPEC's statement that the pre-ball prediction is better calibrated (Brier
0.158 vs 0.170, mean p 0.500 against a 0.524 win rate) came from the logged
backfill cohort. Those are §9.1 **test-split** matches. It was one
descriptive comparison, it motivated the task and selected nothing, and it
is the only test-split figure this work has used. It is also the opposite
sign to validation, where the pre-ball prediction is the most optimistic
one. That difference is itself something the test look will measure.

## Proposed final comparison (NOT RUN; awaiting the owner's confirmation)

Everything is scored in **one pass** over the current test split: 2,287
chases, 2025-01-01 to 2026-09-17, as cached on 2026-09-27. This is a larger
set than the one `winprob2-20260910`'s 0.1232 was measured on, and every
figure will say which set it's from.

| ID | Configuration | Role |
|---|---|---|
| S | served `winprob2-20260910`, as published (14 features, identity) | reference. Its Brier on the current test set is reported beside the original 0.1232, labelled as two different sets. |
| P | `state_venue_elo_no_partnership`, today's run rate, early-stopped on all of validation, **identity** (the calibration rule's winner above) | the candidate for promotion |
| A | `a_n6` under the same pipeline as P | the pre-fixed rule's winner, judged by SPEC's run-rate rule |

**P vs S (promotion rule, fixed now).** This is the paired, match-clustered
95% CI of (Brier_S − Brier_P), 2,000 resamples, where negative means P is
worse. P is promoted, via §8.4 shadow deployment, only if all of these hold:
1. overall: the CI's lower bound is above −0.0020. That margin is about an
   eighth of the model's measured gain over the logistic baseline, 0.0149.
2. final 3 overs: the CI's lower bound is above −0.0020.
3. first 5 overs: the CI is not entirely below zero.
4. P beats the three-feature logistic baseline under §9.3's rule.

If any of these fails, S stays and the partnership mismatch stays open.
P vs S is reported as a **combined effect**: removing the partnership
features, closing KNOWN_SKEW, and a fresh retrain. It isn't attributed to
any one of them.

**A (SPEC's run-rate rule, unchanged).** A is promotable only if its overall
paired CI against S shows no regression **and** its first-5-overs CI
excludes zero in A's favour. A vs P isolates the run-rate change alone and
is reported whatever the outcome. Selection predicts that A fails. If it
passes anyway, that is reported, not explained away.

**Reported for S, P and A:** Brier overall, final 3 overs, first 5 overs and
balls 2-6; reliability by decile for the first 5 overs and for balls 2-6,
with match-clustered CIs; early-chase bias by band; and the switch
diagnostic.
