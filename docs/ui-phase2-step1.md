# UI Phase 2, Step 1 — the data layer

Full Member priority, the Full Member replay, and the three defects from
UI-PHASE-2.md §2.3.

**Zero external spend.** Local Docker Postgres, a local uvicorn, and Supabase.
The three optional CricketData calls for ids 1–3 were considered and declined;
see Defect A. Nothing touched Railway or the Anthropic API.

---

## Two corrections to UI-PHASE-2.md §2.1

### It is eleven Full Members, not twelve

§2.1 says to flag the twelve ICC Full Members and "assert the flagged count is
exactly 12". That assertion would have failed the migration.

**Afghanistan has no row in `teams`.** `name ILIKE '%afghan%'` returns nothing
on either database, and all 347 teams present appear in at least one match, so
there is no orphan row either. Inserting a team with no matches to make the
count reach twelve would be inventing data to satisfy an assertion, so the
migration asserts 11 and the reason is written beside it.

The *test* asserts something stronger and drift-proof
(`tests/db/test_full_member.py`): every flagged team is one of the canonical
twelve, and every canonical name present in the table is flagged. A count would
be satisfied by eleven wrong teams, and would become a false failure the day
Afghanistan is ingested — the kind someone "fixes" by editing the number. The
set formulation starts requiring Afghanistan automatically instead.

### The name-matching trap does not exist in this corpus

§2.1 warns that "India A", "India U19" and "India Women" share the prefix.
Checked rather than assumed: a sweep of all 347 team names for
`(women|u-?19|u-?23|under|\sA$|\sA\s|emerging|development|academy|XI)` returns
six rows — Africa XI, Asia XI, ICC World XI, and three regex false positives
(Me**xi**co, Sydney Th**under**, Sylhet Th**under**). No A-team, age-group or
women's side exists here at all.

So exact-name matching is safe, and the only real consideration is the three
composite invitational sides, which are deliberately left FALSE: they are
selections, not members.

The warning was still worth acting on — it is the reason the match condition is
an exact `name IN (...)` rather than an `ILIKE`, which would have been a live
bug the moment the corpus gained a women's fixture.

---

## The three defects

### Defect A — ids 1, 2, 3 "Unknown v Unknown": excluded, not recovered

These three rows lost `team_a`, `team_b` and `venue_id` to the backfill
incident. The provider UUIDs survive in `external_ids`, so three CricketData
calls could recover the names.

**Declined.** Ids 1 and 2 have zero predictions and id 3 had 25, none with
outcomes, and none has a corpus counterpart — so they can never be scored and
can never show a strip. Three provider calls would buy three near-empty rows
with names on them.

Excluded in `web/lib/match-index.ts` by a property rather than an id list: *a
match whose two sides cannot be named is not listed.* An id list would silently
stop matching the day the ids changed.

### Defect B — the four rows stuck at `status='live'`

§2.3 prescribes "a match with a `winner` is complete". **That rule changes
nothing here.** The four stuck rows are ids 1, 2, 3 and 1000001, and all four
have `winner IS NULL`; the rows that *do* have winners (9337, 9339, 13143) were
already `complete`. Applying the prescribed rule would have been a no-op
reported as a fix.

The rule that fits: *a row still marked `live` whose `start_time` is more than
24 hours in the past cannot still be live.* All four started between 16 and 20
September. Applied as a one-off `UPDATE` in
`20260924000003_teams_full_member.sql`.

**Deliberately not a recurring sweep.** The recurring mechanism is Session 3's
`LivePredictor.record_status` plus the `run_once` change that observes status on
empty polls — before those, `live_loop` read match-end from an in-memory
snapshot and never wrote it back, which is why these four exist. A standing
age-based sweep would quietly paper over that worker failing again.

The guard is an **upper** bound (`> 4` raises), not an equality, because the
migration runs on both databases and the correct count differs: 4 on the
serving database, 0 on the corpus. An upper bound is also the direction
standing rule 15 actually protects — the danger in an `UPDATE` is touching more
rows than intended.

### Defect C — "two rows show a result but say Not replayed"

**There were three, and neither of §2.3's two hypotheses was right.**

Matches 9337, 9339 and 13143 held **496** `source='backfill'` prediction rows
with `innings IS NULL` and **zero** `prediction_outcomes`. The payloads were
complete and well-formed; only the three ball-key columns were NULL, and
`delivery_id` is NULL on every prediction row in the table.

- *The replay did not skip them.* They were never in
  `api/data/phase3_manifest.json`, which holds exactly 100 matches and
  `expected_rows: 12081` — precisely the good-row count.
- *The index was not reading the wrong signal.* `innings IS NOT NULL` in
  `match-index.ts` is the same filter `models/resolve_outcomes.py` and
  `eval/calibration_monitor.py` use. The rows were genuinely unusable by every
  consumer.

They were written by ad-hoc `drive_replay` runs **before** migration
`20260918000003` added the ball key — that migration's header names these exact
three matches. Because `innings IS NULL` sits outside the partial unique index
`predictions_ball_key_uniq`, the `ON CONFLICT` target was unsatisfiable and
every retried POST wrote another row: 13143 ended up with 243 rows carrying only
135 distinct payloads.

They showed a result because Session 3's mirror copied `winner` onto all 107
match rows regardless of prediction state. So each row's two halves came from
different eras, and `/matches` was telling the truth with the wrong words: not
"not replayed", but *replayed before the ball key existed, and unscoreable*.

**Fixed rather than relabelled.** The 496 rows were deleted under a guarded
transaction (standing rule 15: precondition `= 496`, a check that none carried
outcomes, and a postcondition of 0 — `DELETE 496` exactly), then all three
matches were replayed through the endpoint.

---

## A finding this turned up: the deployed service is stale

122 of the keyless rows were written **on 2026-09-24**, after `drive_replay` was
fixed in `032b5ca`. The client was not at fault — its request body carries
`innings`, `over_num` and `ball_in_over`, and the indices into `_BALLS_QUERY`
check out.

The local service is not at fault either. Its OpenAPI schema lists all three
fields on `WinProbRequest`, and replaying 9337 and 9339 through it produced
125/125 and 125/125 **fully keyed** rows.

That leaves the deployment. A FastAPI request model silently ignores unknown
fields, so a Railway deployment predating `20260918000003` (2026-09-18) would
accept the ball key, drop it, and write NULL — which is exactly the observed
signature. **Not verified here**, because this step was scoped to zero Railway
contact, and not fixed here. It is a deploy-time check: confirm
`/openapi.json` on the deployed service lists `over_num` before driving any
replay through it.

---

## The replay

`api/data/full_member_manifest.json` — 248 matches, all Full Member v Full
Member T20Is and ODIs in the test split. Not a sample: the whole eligible set,
because a random 100 would leave the hero stale for the same reason the fixture
did.

| | candidates | replayable | already present | replayed | rows |
|---|---|---|---|---|---|
| T20 | 161 | 151 | 5 | 146 | 15,976 |
| ODI | 99 | 97 | 6 | 91 | 22,744 |
| **Total** | **260** | **248** | **11** | **237** | **38,720** |

The 12 non-replayable are no-results and reconciliation anomalies, excluded by
`second_innings_predicate`. Run result: **38,487 rows written, 13 matches
already complete, 0 drift, 0 failures**; `verify` passes every check —
per-match row counts, no duplicate ball keys, one model version, no orphans,
40,822 outcomes resolved and 0 unresolved.

### The test-split constraint, now asserted

§2.1 requires the window be enforced rather than observed. It was not: the
boundary reached the replay path as the literal `"2025-01-01"` in
`build_manifest`, a hand-copy of `_VAL_END + 1` from a module whose own
docstring says boundary dates are private so nobody does exactly that. And
`run()` never re-checked — it trusted the manifest.

`eval/splits.py` now exports `TEST_SPLIT_START` (derived, not written out) and
`assert_in_test_split`. `run()` calls it **twice per match**: against the
manifest's claim, before the already-logged skip, so an out-of-window match is
reported even on a run that would write nothing; and again against
`match_states.match_date` after loading, because a manifest is a file and a
file can be edited.

It raises rather than filters. A filter would silently drop such a match and
leave a short run nobody questions.

Also measured, so the doc's `start_time` and the code's `match_date` need no
reconciliation: across all 278,940 second-innings states since 2024-12-01,
`match_date` and `start_time::date` disagree **0 times**.

### Two deviations from the plan, both measured

The approved plan said `--deployed 5` against a local uvicorn. Measured rates:
the endpoint path runs at **3.3 s/ball**, the batch path at **0.04 s/ball** —
40,822 balls through the endpoint would have been ~37 hours. So the bulk run
used `--deployed 0`, and parity is proven by the residue run instead, which
drove all three of its matches through the endpoint against the same service
and artifact: **max diff 0.000e+00 over 372 balls**, bit-identical rather than
merely inside the 1e-9 tolerance.

Separately, both replays ran concurrently for a few minutes, which serialised
them through one uvicorn worker and dropped the rate to ~19 s/ball. The Full
Member run was stopped and restarted; match 6951's partial 33 rows were topped
up correctly by the ball-key `ON CONFLICT` (126 scored, 93 new).

---

## `/accuracy`: before and after

Run 9 → run 10, model `winprob2-20260910` unchanged.

| | before (run 9) | after (run 10) |
|---|---|---|
| predictions | 12,081 | **51,173** |
| matches | 100 | **340** |
| Brier | 0.101987 | **0.113978** |
| clustered CI | [0.074521, 0.131124] | **[0.096243, 0.133450]** |
| log loss | 0.321144 | 0.355285 |
| powerplay | 0.131969 (n 3,855) | 0.156867 (n 14,877) |
| middle | 0.099998 (n 6,142) | 0.108024 (n 28,817) |
| death | 0.052390 (n 2,084) | 0.051605 (n 7,479) |
| vs historical base rate | +0.038648 [0.022573, 0.055872] ✅ | +0.037550 [0.021828, 0.053919] ✅ |
| vs logistic | +0.014897 [0.001413, 0.028421] ✅ | **+0.008277 [−0.002744, 0.018824] ❌** |
| deciles failed | 4 of 10 | 4 of 10 |
| refit | not run (100 < 500), identity | not run (340 < 500), identity |
| live | n=0, 2 matches / 40 unresolved | unchanged |

### The headline: the model no longer beats the logistic baseline significantly

This is the finding of the step. Before, the model beat a plain logistic
baseline by 0.0149 with a match-clustered CI that **excluded zero**. After, the
margin is 0.0083 and the CI is **[−0.0027, 0.0188]**, which includes zero —
`model_is_better` flipped to `false`. The historical-base-rate comparison still
holds comfortably.

**The model did not change.** `winprob2-20260910` is the same artifact, same
sha256, and the refit did not run in either report. What changed is the
population, which went from a 100-match random sample to 340 matches dominated
by Full Member internationals. The 100-match figure was the flattering one.

### Why the Brier rose, decomposed

Brier by format and cohort, computed directly from `predictions` joined to
`prediction_outcomes`:

| format | cohort | matches | n | Brier |
|---|---|---|---|---|
| T20 | original 100 | 92 | 9,956 | 0.099236 |
| T20 | newly added | 149 | 16,348 | 0.107963 |
| ODI | original 100 | 8 | 2,125 | 0.114880 |
| ODI | newly added | 91 | 22,744 | 0.124670 |

Two effects, both real, and they compound:

1. **Mix.** ODIs score worse than T20s in *both* cohorts. The original
   population was 82% T20 by prediction count; the new one is roughly half ODI.
   Reweighting alone raises the aggregate even with per-format skill unchanged.
2. **International cricket is genuinely harder.** Within each format the new
   matches are worse by almost the same amount — T20 +0.0088, ODI +0.0098. That
   is not a mix artefact. Evenly-matched Full Member sides produce closer games,
   and a closer game is less predictable; a franchise T20 with a mismatch
   resolves early, England v India does not.

The powerplay carries most of the damage (0.1320 → 0.1569), which is consistent:
an ODI powerplay is 60 balls of a 300-ball chase, so the outcome is further away
and the prior is doing more of the work.

### The refit floor is now much closer

`MIN_WINDOW_MATCHES = 500`. The window went from 100 to 340, so the margin fell
from 400 matches to **160**. The refit still did not run and identity was
retained — the model did not change in a UI phase, which was the requirement.
But the next addition of this size would trip it, and a refit is a
model-selection event. It should be a deliberate act, not a side effect of
adding matches for a landing page.

---

## Resulting state

| | before | after |
|---|---|---|
| `matches` rows | 107 | 344 |
| rows listed on `/matches` | 107 | **341** (3 unnameable excluded) |
| rows saying "Not replayed" | 5 | **0** |
| `status = 'live'` | 4 | **0** |
| predictions | 12,617 | 51,213 |
| with `innings IS NULL` | 496 | **0** |
| `prediction_outcomes` | 12,081 | 51,173 |
| Full Member v Full Member | 11 | **248** |
| Supabase size | 41 MB | 59 MB (of 500 MB) |

The landing hero fixture regenerated from real data:
**England v India, ODI, 2026-07-19, India tour of England** — replacing
Bangladesh v New Zealand from February, which is what the hero would have shown
before the replay.

---

## A verification trap worth recording: the warm Next.js data cache

The database said zero matches would render "Not replayed". The **built page
said seven** — 6949, 6950, 6951, 6952 and the three repaired residue matches.

Neither the sweep nor the data was at fault. Replaying
`loadPredictionsByMatch`'s exact queries returned 51,213 rows across all 342
matches, and every payload in those seven matches passes `parsePrediction`'s
field checks (verified column by column, not assumed).

It was Next.js's Data Cache. `/matches` sets `revalidate = 3600`, supabase-js
goes through `fetch`, and `.next/cache` survived from a build predating the
replay. The sweep's page URLs were unchanged, so they were cache **hits** and
returned the pre-replay result set; the matches query had gained a new
`.not("team_a", "is", null)` filter, so its URL changed, so it **missed** and
came back fresh.

That mixture is the dangerous part. A wholly stale page is obvious. This one
rendered the correct 341 rows — the new count, the new exclusions — with seven
rows silently showing the old world, and looked entirely plausible.

`rm -rf .next/cache && npm run build` gives 341 rows, **0 "Not replayed", 0
"Unknown"**.

**The rule:** after a bulk data change, verify against a build with a cleared
data cache, and verify against the built artifact rather than the database.
Querying Postgres and concluding the page is correct is checking a different
thing than the one being shipped — the database was right and the page was
wrong, at the same moment.

---

## A defect this step introduced, and the test that caught it

The first version of `20260924000003` asserted `count(*) FROM teams WHERE
full_member = 11`. It applied cleanly to both real databases and was wrong.

`tests/features/test_asof_summary.py` builds a scratch database by running
every migration into it. On an empty `teams` the count is 0, so the assertion
raised and **the entire migration chain failed** — not just that test, but any
fresh clone, any CI run that provisions a database, and the restore path.

The mistake is worth naming precisely: **a migration builds schema, and schema
migrations run against databases with no data in them.** The assertion was
checking a property of the corpus in a file whose job is to be applicable
anywhere. It passed everywhere I looked because I only looked at the two
databases that already had 347 teams in them.

Rewritten to assert the invariant the `UPDATE` is actually responsible for —
*no Full Member that is present may be left unflagged* — which is vacuously
true on an empty table and the real check on a loaded one. Proved it still
fires by inserting `India`, `Ireland` and `Kenya` unflagged into a scratch
database: it named India and Ireland and correctly ignored Kenya.

The count of eleven now lives where it can mean something: `COMMENT ON COLUMN`,
and `tests/db/test_full_member.py`, which runs against real data and asserts
the flagged **set** in both directions.

Note the amended file differs from what was applied to the two real databases,
which ran the count version. Migrations are tracked by version and will not
re-run there, and the two are logically equivalent on a populated database —
both assert the `UPDATE` succeeded. Verified after the amendment: 11 flagged on
both, identical name sets.

---

# Follow-up, same day: four questions put to the results

## Afghanistan is an upstream gap, and cannot be fixed here

Eleven Full Members with Afghanistan absent is suspicious on its face — they
play plenty of T20Is and ODIs. Three explanations were checked and all three
are ruled out:

- **Spelling variant?** No. `teams` has no row matching `%afg%`, and the only
  `%ghan%` match across all 347 rows is **Ghana**.
- **Alias?** No. `team_aliases` has no `%afg%` row across its 354.
- **Entity resolution split them?** No — there is nothing to split.

It is a publishing policy, stated in the first paragraph of the Cricsheet
archive's own `README.txt`:

> "A further 374 matches have been withheld due to either featuring the
> Afghanistan men's team or being played in the Afghanistan Premier League, due
> to the Cricsheet policy to no longer feature matches involving Afghanistan
> men or played in Afghanistan Premier League"
> — https://cricsheet.org/withheld-matches

Confirmed against the raw archive rather than taken on trust: across the 22,734
match files, `"Afghanistan"` appears as a team in **zero**, while the same
search finds Zimbabwe in 646 and Ireland in 520.

So there are no Afghanistan matches to replay and eleven is correct. Recorded
in `tests/db/test_full_member.py`, whose `ABSENT_FROM_CORPUS` tripwire now
fails with an actionable message if the policy is ever reversed and 374
matches arrive at once.

## The baseline finding: the segment story does not survive testing

The proposed reading was that the model's edge over the logistic comes from
`elo_diff` and the two venue features, all three carrying less signal in
international cricket — narrower Elo spreads between Full Members, better-known
grounds — and that Phase 1's ablation finding was arriving where it mattered.

It was tested rather than accepted, and it fails twice.

**The prediction fails.** Paired, match-clustered, same model and baselines:

| segment | matches | n | margin vs logistic | CI | |
|---|---|---|---|---|---|
| Full Member v FM | 248 | 40,822 | +0.007664 | [−0.005682, +0.020812] | not significant |
| franchise/associate | 92 | 10,351 | +0.010695 | [−0.003800, +0.026108] | **not significant** |

The segments differ by 0.003 against CI widths near 0.027, and **neither is
significant on its own**. "Clear in franchise cricket, not established in
internationals" requires the first half to be true, and it is not.

**The premise fails, on its load-bearing half.** Elo spreads between Full
Members are *wider*, not narrower:

| | matches | mean gap | median | p90 |
|---|---|---|---|---|
| Full Member v FM | 250 | **96.1** | 81.0 | 202.5 |
| non-Full-Member | 1,953 | **70.3** | 58.4 | 146.6 |

Which is the right way round on reflection: India v Zimbabwe is a far bigger
mismatch than two IPL franchises, which are drafted to be balanced. The venue
half of the read does hold — international grounds carry ~120 prior chases on
average against ~79 — but more history should make that feature better
estimated, not weaker, so it does not rescue the mechanism.

**What the data does support**, like-for-like against run 9:

| cohort | matches | margin | CI | |
|---|---|---|---|---|
| the original 100 manifest matches | 100 | +0.014897 | [+0.001413, +0.028421] | significant |
| the 240 added in step 1 | 240 | +0.006231 | [−0.006369, +0.018983] | not significant |

The original cohort reproduces run 9 to six decimal places, so the segmentation
is sound. Its CI lower bound was **+0.0014** — it cleared zero by about a tenth
of a percent. That result was marginal and did not survive tripling the sample.
It is a fragility story, not a segment story, and the distinction matters
because only one of them is about cricket.

### What `/accuracy` does now

Reports the pooled verdict — `baselineVerdict` already refuses to say "better"
when the interval includes zero — plus the sample-size history, plus the
segment table **labelled as a null result**. The segment block exists to
foreclose the obvious question rather than to answer it, and its note says so
in as many words.

`compareSegments` is deliberately **not** a significance test. The correct test
is a paired difference-of-differences with its own clustered interval, which
the monitor does not compute; claiming one would be the same overclaim in a new
place. It reports two directly observable facts — whether the intervals overlap
and whether either excludes zero — and the copy says "no detectable difference"
rather than "no difference" for exactly that reason.

## The refit is now gated, not merely floored

The floor was the wrong *kind* of protection. `n_matches >= 500` is a threshold
on a number that grows whenever anyone loads data, and step 1 moved it from 100
to 340 while populating a landing page. The next backfill of that size would
have promoted a calibrator from a nightly cron with nobody deciding anything —
§8.4's shadow-deployment discipline defeated by a counter crossing a line.

`refit_decision` now requires `--allow-refit`, checked **before** the floor.
`.github/workflows/calibration.yml` does not pass it. The report records
`would_be_eligible`, because a gate that declines identically at 10 matches and
at 10,000 hides the fact that the evidence threshold has been reached, and then
the decision never gets made at all.

Five existing tests gained `allow_refit=True` — they were written to exercise
the floor, which now sits behind the gate. Named here because this phase's
rhetoric leans on unmodified tests, and these were modified on purpose. Four
new tests cover the gate, including one that proves it holds **when the floor
is satisfied** — the scenario that would have fired incidentally.

---

# Two errors I caught myself, and why they are the same error

**A migration asserting a corpus property.** Schema migrations must hold for an
empty database. `count(*) = 11` was a fact about 347 loaded teams, written into
a file whose job is to run anywhere, and it broke every fresh-schema build
until the suite caught it.

**A build read from the database instead of the page.** The database said zero
rows would say "Not replayed"; the built page said seven, because a warm
`.next/cache` served stale sweep results beside a fresh matches query.

These are the same mistake twice: **verifying against a convenient proxy rather
than against the artifact**. The applied databases were a proxy for "all
databases". The Postgres query was a proxy for "what the page renders". Both
proxies were true; neither was the claim being made. That is standing rule 12,
and this is its third or fourth instance in this repository — which is itself
the argument for treating it as a rule rather than as a run of bad luck.

# On the brief's four errors

Twelve versus eleven, the phantom name-matching trap, two versus three "Not
replayed", and a stuck-`live` fix that would have been a no-op.

**The last one is the worst shape and deserves naming.** §2.3 prescribed "a
match with a `winner` is complete". Run as written, it would have executed
without error, reported success, and changed nothing — because all four stuck
rows have `winner IS NULL`, while the three rows that *do* carry winners were
already `complete`. A fix that fails is loud. A fix that runs, reports success
and changes nothing is silent, and it closes the ticket.

Only checking `winner IS NULL` **before** writing the fix caught it. The
general form is the one the rest of this document keeps arriving at: the
precondition is the part worth verifying, because the action will happily
succeed against a world where it was never needed.
