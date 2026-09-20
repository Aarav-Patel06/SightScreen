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

## Found by running it — every phase

Carried forward from `docs/phase2-closeout.md` and extended. Across four
phases, **not one defect that mattered came from review, and not one came
from a test written before the code.** The pattern is stable enough to plan
around: write the thing, point it at the real dependency, and look at what it
actually does.

| Phase / session | Defect | Found by |
|---|---|---|
| 0 | `sslmode` breaking CI and local dev, twice | Running CI |
| 0 | A connection left in a transaction hanging the suite | Running the suite |
| 1 s1 | 992 innings-2 rows satisfy §9.1's documented predicate but have a NULL `required_run_rate` | Querying the real corpus, not reading the spec |
| 2 s1 | Postgres `ROUND(numeric)` rounds half away from zero, Python's half to even; a 30-ball chase hit exactly 22.5 | Incremental-vs-bulk parity on the real corpus |
| 2 s2 | 0.00% partnership error at every interval — an artifact of uniform ball-gap timing, not a result | Running the measurement and disbelieving the answer |
| 2 s3 | `extra_float_digits` differs between local Postgres and Supabase's pooler; `float4::numeric` caps at 6 significant digits | The sync's own hash verification, on its first run |
| 2 s4a | pydantic's `ValidationError` repr embeds the entire environment, secrets included | Running the failure path in a container |
| 2 s4b | 11 defects, including a `/health` that reported green throughout an outage | Three real Supabase pauses |
| 2 s5 | 13 tables world-readable to `anon`; `anon` held write privileges on all five policied tables | The first time anything authenticated as `anon` |
| 2 s5 | `web/lib/types.ts` four migrations stale, behind a CI check that had been `skipped` for ten runs | Reading the Actions history instead of assuming CI was green |
| 2 s5 | Realtime drops `postgres_changes` messages silently | The anon gate failing twice, then being measured rather than re-run |
| 3 s1 | **The ball key collided with itself.** A wide and the next delivery shared `(over, ball_in_over)`, so the live path silently dropped the ball after every extra | A live match arriving mid-session and producing fewer predictions than deliveries |
| 3 s1 | `POST /predict/win-prob` retried by the transport: 125 posts, 128 rows | Counting what landed instead of trusting the loop's tally |
| 3 s1 | Match 13143's 121 logged rows are 13 distinct payloads — the smoke test run ten times | Looking at the log before computing anything from it |
| 3 s1 | Reference freshness compared a UTC timestamp to a LOCAL date | Running the suite at 22:08 EDT |
| 3 s1 | Corpus-dependent tests could only pass on my machine | CI going red while the suite was green locally |
| 3 s2 | **Two id spaces shared one column.** Supabase `match_id` 3 is a CPL 2026 match; LOCAL `match_id` 3 is a 2017 Pakistan-Australia ODI | Querying both databases for the same id while planning live resolution |
| 3 s2 | The live population has **zero** resolvable predictions, not a small number | Joining live matches to the corpus on (date, teams, venue) and getting `[]` three times |
| 3 s2 | Actions could not reach the serving database at all | Grepping every `secrets.*` reference before writing the workflow |
| 3 s2 | The five "biggest misses" were five balls of one over — one miss shown five times | Looking at the rendered page instead of the query |
| 3 s2 | The Actions step summary would have crashed a *successful* run after it wrote its row | Running it with the env var set instead of assuming |
| 3 s2 | The CI watcher reported failure on a green build — 60 requests/hour unauthenticated, polled every 20s, no error handling | Re-running it and checking the rate limit rather than the build |
| 6 s1 | **Every player attribute is empty — `batting_hand`, `bowling_style`, `dob` are 0 of 18,468** — and Cricsheet cannot supply them, which blocks §10.1's flagship query and a §6.4 feature | Reading the only `INSERT INTO players` in the repo, then counting |
| 6 s1 | **An exploration agent asserted those columns were "populated" and were "exactly what makes §10.4's example citation possible".** They are empty | Checking the claim against the loader source and a `count(*)`, instead of taking the report at its word |

The agent row is not a cheap shot at tooling — it is the same failure mode as
every other row in this table, arriving through a new door. A subagent report
reads like evidence because it is specific, cites files and line numbers, and
is mostly right. This one was right about the DDL and wrong about the data,
which is precisely the gap a schema listing cannot close: the column exists,
so it looks available. The rule that caught it is the one already written
down — go and look at what the system actually does. **Treat a subagent's
factual claims as leads to verify, not as observations already made.**

The CI-watcher row is worth its place too. A monitoring tool that reports failure on
success is the same defect as `/health` returning `ok` through an outage,
pointed the other way — and it was structurally guaranteed, not unlucky: the
budget runs out in about fifteen minutes and the `api` job takes eleven plus
queue time, so it broke on precisely the runs worth watching. Deleted rather
than fixed; a single query reports the actual run state and cannot lie about
its own exit code.

### Session 2's four, in detail

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

**10. When a verification needs access you do not have, say what you can
establish, say what you cannot, and name who can close it.** Do not let the
reasoning stand in for the observation. Confirming the monitor's first real
run, I could read the run status, the row it wrote, and the deployed page
rendering that row — and I could enumerate every line the code can print and
show the credential is never interpolated into any of them. I could not read
what GitHub actually stored: `/actions/jobs/{id}/logs` returns `403 Must
have admin rights`. "The code cannot emit this" and "GitHub did not store
this" are different claims, and only the repo owner could check the second.
Handing it back took one sentence and produced a better answer than my
inference would have: the archive scan showed GitHub's masking firing on
`SUPABASE_SESSION_POOLER_URL`, which confirmed the secret reached the job
and was masked on egress — something no amount of reading the source could
establish.

A worked example of the same rule catching a real discrepancy: that scan
reported the log saying `wrote calibration_runs row 3` while I had reported
row 4. Both were right. There had been **two** dispatches — 04:50 on
`7ae01fd` and 22:56 on `208f200` — and the earlier one wrote row 3 with the
baseline section reporting itself unavailable, because the artifact was not
registered until the later commit. Checking the workflow's full run history
rather than the single run I had been handed is what resolved it.

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
