# Phase 3 close-out — the prediction log, and a page that admits what it measures

**Closed 2026-09-19.** All four §11 checkboxes ticked. This is the minimum
resume-ready milestone; everything after it is upside.

Two sessions: the log (`docs/phase3-session1-prediction-log.md`) and the page
plus the daily monitor. This document is what Phase 4 needs.

## What is live

| | |
|---|---|
| Accuracy page | `/accuracy` — reliability diagram, per-phase Brier, baselines, biggest misses |
| Prediction log | 12,081 backfilled predictions over 100 matches, all resolved; 25 live, none scored yet |
| Daily monitor | `.github/workflows/calibration.yml`, 03:00 UTC, writes `calibration_runs` |
| Baselines | Fitted on 1.33M train rows / 9,197 matches, published as a sha256-pinned artifact |

## The number that matters, and the one that flatters

The page separates two populations everywhere, and puts the small honest one
**first**:

```
LIVE      25 predictions, 1 match, none scored yet
          Outcomes come from the match archive, which does not yet contain
          these matches. Nothing can be scored until it does.

REPLAYED  100 matches, 12,081 predictions
          Brier 0.1020 [0.0745, 0.1311] over 100 matches
          4 of 10 bands off
```

The replayed sample is not "a re-display" of Phase 1's test numbers — it is
worse than that. Those 100 matches **are** the §9.1 test split, which Phase 1
used to *select* the shipped calibrator. There is no held-out data behind
them at all. The page says so in its heading rather than in a footnote:
"matches the model was tuned on… Read it as a demo."

Putting LIVE first was deliberate. Ordering by sample size would put the
flattering number at the top, which is the exact failure this page exists to
avoid.

## What it shows

Reliability, from `reliability_match_clustered`, with **both** sample counts:

```
predicted  balls  matches  observed  95% CI            
    0.037   2377       47     0.007  [0.000, 0.022]  off
    0.145   1284       55     0.115  [0.029, 0.237]
    0.549    682       49     0.764  [0.582, 0.903]  off
    0.652    679       47     0.816  [0.677, 0.928]  off
    0.969   2910       56     0.993  [0.981, 1.000]  off
```

The `matches` column is the point of §9.3: the first band has 2,377 balls and
**47 matches**. Balls inside one match are not independent evidence, so the
effective sample is 47. A page showing only `n` would imply forty times more
evidence than exists. Rows below 30 matches are greyed per §12.2.

Against both §9.2 baselines, scored on **the same 100 matches**:

| | baseline | model | improvement | 95% CI |
|---|---|---|---|---|
| logistic | 0.1169 | 0.1020 | +0.0149 | [0.0014, 0.0284] |
| historical base rate | 0.1406 | 0.1020 | +0.0386 | [0.0226, 0.0559] |

Both intervals exclude zero. This is a like-for-like comparison rather than
Phase 1's test-split figures placed beside a number from a different sample —
which required the baselines to become a published artifact, because they are
fitted objects and the monitor runs where the training corpus does not exist.

## §8.1's two halves, kept apart

The spec is explicit that steps 1–4 always run and steps 5–8 usually decline,
and that "a job that reports honestly and changes nothing is succeeding." The
monitor's own log says exactly that:

```
refit: SKIPPED - 100 matches in the window, floor is 500. Identity retained.
       This is the expected outcome, not a failure.
```

The floor is on **matches**, measured rather than asserted: the clustered CI
on 100 matches is ±0.03, while a calibration refit that helps moves Brier by
0.002–0.01. Resolving that needs a half-width near 0.005 — roughly 3,600
matches. 500 is where a refit could plausibly be *selected*, not where one
would be significant, and the job reports which.

When it does run, selection requires a **paired match-clustered margin whose
CI excludes zero**. `run_calibration_selection.py` selects by plain
`min(brier)`; that would promote a calibrator ahead by 0.0001, which on this
project's own Phase 1 evidence is the likely case.

## Found by doing, session 2

| Finding | Found by |
|---|---|
| **Two id spaces shared one column.** Supabase `match_id` 3 is a CPL 2026 match; LOCAL `match_id` 3 is a 2017 Pakistan-Australia ODI. `resolve_outcomes --match-id 3` would have scored live predictions against a 2017 result, silently | Querying both databases for the same id while planning live resolution |
| **The live population has zero resolvable predictions**, not a small number — the corpus ends 2026-08-24 and the live matches are September | Joining live matches to the corpus on (date, teams, venue) and getting `[]` three times |
| **Actions cannot reach the serving database.** The only repository secret is a Management-API token | Grepping every `secrets.*` reference before writing the workflow |
| **The five "biggest misses" were five balls of one over** — one miss displayed five times | Looking at the rendered page instead of the query |

The id collision is the one worth carrying. Nothing triggered it, because
resolution only ever ran over the manifest. It would have been triggered by
the *next* obvious step. Two defences now: the sequence starts above the
corpus so no new live match can collide, and resolution compares
competition, format and date across both databases before reading any label
— proven by pointing it at match 3 and watching it refuse.

## Standing rules

Rules 1–8 are in `docs/phase2-closeout.md`.

**9. A buffered log looks exactly like a hung or killed process.** This has
cost time twice — Phase 0's resumability test, and session 1's "the
background runs are being killed" conclusion, which was wrong: every run had
exited 0 and written a full summary while `tail` showed a stale prefix.
Before concluding a process is dead, check the state it should have changed —
row counts, files on disk — rather than its output. `python -u` and a
`count(*)` beat reading a redirect. The related "this query takes 151
seconds" was the same mistake wearing a different hat: three other runs were
hammering the same Postgres, and alone it takes 0.01 s.

## Open, carried into Phase 4

1. **Live predictions cannot be scored** until the Cricsheet corpus is
   refreshed past September 2026, and then only through a (date, teams,
   venue) crosswalk that does not exist yet. The mechanism is understood; the
   data is not there.
2. **The live curve is sparse** — snapshot reconstruction emits a delivery
   per scorecard change, not per ball.
3. **The refit branch has never executed against real data.** It is tested
   against synthetic windows above the floor, and the floor is 5× the data
   that exists.
4. **`calibration_runs` grows one row a day** and nothing prunes it. At ~40 kB
   a report that is ~15 MB a year against a 500 MB cap — fine for now, worth
   a retention policy before it is not.
5. **KNOWN TRAIN/SERVE SKEW** is still open, still awaiting the next retrain.
6. **4 of 10 deciles remain miscalibrated.** Now displayed publicly rather
   than only recorded, which was the point.

## For Phase 4

The simulation work compares against the direct classifier, and §11 wants
them "within 3 points across a test set". The test split has now been used
to select a calibrator *and* to populate a public page. Treat it as spent:
a simulation-vs-classifier comparison that wants to mean something needs
either the live log once it can be scored, or a fresh holdout carved before
anything else touches it.
