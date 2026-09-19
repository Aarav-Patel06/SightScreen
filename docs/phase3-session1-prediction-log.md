# Phase 3, Session 1 — the prediction log and outcome resolution

Builds the thing §8.5's accuracy page and §8.1's calibration monitor read.
Session 2 builds the page; this session builds the log, because a page over a
wrong log is worse than no page — it looks authoritative.

Scope: every prediction written to `predictions`, outcome resolution, and the
100-match replay that populates the log. **Not** the accuracy page, **not**
`calibration.yml`.

## What changed

| | |
|---|---|
| `predictions` | Three new columns — `innings`, `over_num`, `ball_in_over` — and a partial unique index over `(match_id, model_version, prediction_type, innings, over_num, ball_in_over)` |
| Both write paths | `ON CONFLICT DO NOTHING`, so a re-run costs nothing and a retry cannot duplicate |
| `eval/splits.py` | §9.1's four exclusion clauses exported as a shared fragment, plus a by-match label accessor |
| `serving/live_loop.py` | The worker predicts. It never had before |
| `models/resolve_outcomes.py` | New. `prediction_outcomes`' first writer since Phase 0 created the table |
| `ingest/replay_log.py` | New. Manifest, resumable run, reconciliation |

## What the log contains

```
manifest: 100 matches, 12081 expected rows
logged:   100 matches, 12081 rows
  PASS  every manifest match is logged
  PASS  row count matches the manifest per match
  PASS  no duplicate ball keys
  PASS  exactly one model_version across the log
  PASS  no predictions without a matches row
outcomes: 12081 resolved, 0 unresolved
  PASS  every logged prediction has an outcome
```

Re-running the logger writes **0 rows**; re-running resolution resolves **0**.

Selection is deterministic — `ORDER BY md5(match_id::text || 'phase3-session1')`
over the 2,179 test-split matches passing §9.1 — and the chosen ids are
committed in `api/data/phase3_manifest.json`, so the run reproduces even if
the selection query later changes. The spread is 2025-01-12 to 2026-08-21,
56 matches from 2025 and 44 from 2026.

Measured growth, replacing the planning estimate:

| | |
|---|---|
| `predictions` | 12,469 rows, **477 B/row** |
| `prediction_outcomes` | 12,081 rows, **169 B/row** |
| Per match, all in | **~79 kB** |
| Supabase now | **33 MB of the 500 MB cap** |
| Headroom at this rate | **~5,900 more matches** |

The plan projected 591 B per prediction row from a 374-row sample; the real
figure is 477 B. The estimate was inflated by fixed index overhead that a
tiny table cannot amortise — worth remembering the next time a per-row cost
is extrapolated from a few hundred rows.

## The seven things running it found

Not one of these came from reading the code.

### 1. The live provider never said who was batting

A win probability needs the as-of features, which need `batting_team_id` and
`bowling_team_id`. `currentMatches` supplies no toss — `cricketdata.py`
hardcoded `toss_winner=None` — and the reconstructed `Delivery` carries no
team identity. So the worker could not have scored a ball even with a model
in hand.

The provider does say it, in one place the parser was throwing away: the
innings label, `"Guyana Amazon Warriors Inning 1"`. Captured into
`InningsSnapshot.batting_team` and resolved through the existing alias path.
When it does not parse, the worker logs the reason once per match and writes
nothing — guessing would swap the two Elo ratings behind `elo_diff` and
produce a confident number about the wrong team, which nothing downstream
could detect.

### 2. The ball key collided with itself on the live path

`reconstruct` derives `ball_in_over` from the **legal** ball count, so a wide
and the legal ball after it both came out as ball 3 of the over. With
`predictions` keyed on `(innings, over_num, ball_in_over)` and written ON
CONFLICT DO NOTHING, the delivery after every extra was **silently
discarded** — a live curve quietly missing a ball, arriving through the
mechanism added to prevent exactly that.

`supabase/SCHEMA.md` had already written down why that numbering is wrong:

> the legal-ball "x.y" notation would collide with the `UNIQUE(match_id,
> innings, over_num, ball_in_over)` constraint on every over with an illegal
> delivery

The corpus schema predicted the collision three phases in advance and the
live adapter was written without it. Fixed by numbering within the over at
the point of accumulation, where the stream is known; `reconstruct` stays a
pure delta between snapshots. Seven regression tests, five of which fail
against the unfixed code — checked, not assumed.

### 3. Bit-identical parity was the wrong gate

The plan said the deployed and local scoring paths must agree to the bit. The
first run disagreed on its third match: `0.1227525101067121` from the
container against `0.12275251010671213` locally. One unit in the last place —
LightGBM accumulating differently on Linux/amd64 than on a Windows host, which
no amount of shared code removes.

Replaced with a 1e-9 tolerance: seven orders of magnitude tighter than the
four decimals anything reports, and far looser than one ULP. A real
divergence — wrong model, stale as-of data, different features — moves a
probability by 1e-2, not 1e-9. The run now always prints the **maximum
observed difference**, so a pass never hides the number it passed on.

### 4. The run failed partway, twice

First an unhandled SSL read timeout: `post_ball` caught HTTP errors but not
socket ones, so one slow response ended a 12,000-row run. Then the network
dropped mid-run. Both times the resume cost nothing, because the ball key
makes re-running free — which is the whole reason it exists. `post_ball` now
retries with backoff, and still refuses to retry a 409.

### 5. 121 logged rows that were never a replay

Match 13143 held 121 predictions and **13 distinct payloads** — the 12-ball
deploy smoke test, run about ten times across sessions 4a, 4b and 5. Had the
accuracy page been built first and pointed at `predictions`, it would have
computed calibration partly over ten copies of the same twelve balls. The
pre-Phase-3 rows have no ball key and every Phase 3 reader filters on
`innings IS NOT NULL`.

## A correction: the runs were never being killed

Mid-session I concluded that the background replay processes were "being
killed, not crashing — no traceback, no summary", and restructured the work
into foreground chunks around that belief. It was wrong twice over.

The log file was buffered through a shell redirect, so `tail` kept showing
me a stale prefix that ended four matches in. A process check happened to
land between runs and found nothing, which I read as confirmation. In fact
every run completed with exit code 0 and wrote a full summary — the last one
reported `121 rows written, 92 matches already complete`.

The separate "load_balls takes 151 seconds" reading came from profiling
while three other replay processes were hammering the same local Postgres.
With nothing else running it is **0.01 s**, and the query plans at 5.5 ms.

Two lessons, the second more useful than the first: a log you are tailing is
not the state of the system, and the database is — every progress check
should have read `count(*)`, which was accurate throughout. And a
measurement taken while other work is running measures the contention, not
the code. Same shape as session 4b's quota correction: the observation was
real, the inference from it was not.

### 6. A freshness check that only a laptop could see was broken

The full test run failed at 02:08 UTC / 22:08 EDT with `age_days: -1` — a
check reporting that the reference data arrives tomorrow. `_oldest_sync`
reads a `TIMESTAMPTZ` and calls `.date()`, which psycopg renders in the
session's timezone; the caller defaulted to `date.today()`, the machine's
local date. West of UTC those disagree for part of every evening.

It survived three phases because **neither place it runs can see it**: CI and
Railway are both UTC. Only a developer laptop can, and only after 8pm. Fixed
on both sides — UTC default, and `.astimezone(timezone.utc)` so the answer
does not depend on a Postgres session setting. Nothing was wrongly refused,
since a negative age is below every threshold, but `/health` would have
printed a negative number.

### 7. CI caught tests that could only pass on my machine

The suite was green locally and red in CI. The new exclusion tests name real
match ids — a real tie, a real no-result, a real flagged match — deliberately,
because a fixture I wrote could only encode my assumption about those shapes.
CI's Postgres has the schema and no corpus, so all five failed there.

Skipping them in CI would have satisfied the build and left the rule checked
nowhere that runs on every push — a check that exists but does not run, which
is standing rule 8. So the rules are now covered twice: seeded rows in
`test_resolve_outcomes_synthetic.py`, which run everywhere, and the
corpus-backed versions, which skip with an explicit reason when the data is
absent. One proves the logic on every push; the other confronts real data.

## The live path, proven the same day

The session's write-up said the CricketData path "stays unproven until a real
match is on — a calendar problem, not an engineering one." Ninety minutes
later one was on.

At `2026-09-19T01:13Z` the deployed worker logged its first prediction from a
live CPL 2026 match, chasing 207:

```
over.ball   time      p       score/wkts  need  left
  0.4      01:13:07  0.1108  0/0          207   117
  1.6      01:17:44  0.1117  4/0          203   109
```

Internally coherent — score 0 then 4, balls bowled 3 then 11, target
constant, phase `powerplay`. Innings label parsed, batting side resolved,
as-of features computed, model scored, keyed row written, through the
deployed container with no laptop involved.

What remains open is narrower than "unproven", and worth stating precisely:
snapshot reconstruction emits a delivery only when the scorecard changes
between polls, so the live curve is **sparser** than a true ball-by-ball feed
would produce. That is a completeness limit, not a correctness one.

## Decisions

**Innings 2 only, and the page says so.** No pre-toss or post-toss
predictions are produced, and none are invented. §7.2's `pre_toss` action is
"predict from Elo, venue, squads" — a *different model*, untrained and
uncalibrated. Putting its Brier beside the chase model's in one chart labelled
"by match phase" would plot two models as though they were two phases of one,
which is a worse honesty failure than an absent bar. `match_phase` stays the
literal `'innings2'` (§5.4's vocabulary, and true); §8.5's per-phase breakdown
uses the **within-innings** phase — powerplay/middle/death — which is what the
calibrator already conditions on per §9.3.

**The label is read, never derived.** It comes from
`match_states.batting_team_won`, the one place it is computed. Deriving it
from `matches.winner` would be a second implementation, and the case it gets
wrong is quiet: `winner` is NULL for a tie *and* for a no-result, so a naive
comparison records both as a loss for the chasing side. Tested against real
tied, no-result and anomaly-flagged matches from the corpus rather than
synthetic fixtures.

**An unresolvable prediction gets no row.** Not a row with NULL metrics —
`prediction_outcomes.actual` is NOT NULL and a tied match has no actual. The
unresolved count is reported with its reason.

**The exclusion predicate has one definition.** Those four clauses had been
hand-copied into five places, including a three-clause variant in
`drive_replay.py` that dropped `batting_team_won IS NOT NULL` — the query that
chose which matches to replay could pick a tie whose predictions could never
be resolved. Now a shared fragment, following `venue_stats.py`'s precedent.

## The boundary did not move

§2.1's trigger did not fire. Nothing here writes `match_states` to Supabase —
the replay reads it locally and the enriched payload carries what the page
needs — so its four REAL columns stay REAL and
`test_no_serving_code_writes_match_states_or_deliveries` still passes.

The guard itself was the thing that needed work. It globs `serving/**` plus
one named file, so the two new Supabase writers were invisible to it. Both are
now scanned, `prediction_outcomes` is a reviewed write target, and a new test
asserts every module the scan *claims* to cover is actually read — because a
rename would otherwise empty the set and every assertion below it would pass
vacuously.

Proved it fires: removing one module from the required list failed exactly
that test, naming the removed module, with the other six green. It also caught
this session's own code within minutes — a `LivePredictor` docstring mentioned
`REBUILD_SQL` and the substring scanner flagged serving code for referencing a
corpus mutator. Reworded, not loosened.

## Open, carried forward

1. **The live curve is sparse.** Reconstruction emits a delivery per
   scorecard change, not per ball.
2. **Live `ball_in_over` is stream-relative** when the worker joins mid-over —
   unique within the match, which is what the key needs, but not necessarily
   equal to the number the corpus would assign the same ball.
3. **The 100 matches come from the test split, which Phase 1 already
   evaluated on.** §9.1's "touched once" forbids *tuning* on test and
   displaying it is not tuning — but the accuracy page's numbers are
   therefore **not an independent estimate**. Session 2 must say so on the
   page.
4. **`prediction_outcomes` is unreadable by `anon`** — RLS on, no policy, no
   grant. Session 2 needs a policy or server-side rendering.
5. **Pre-Phase-3 rows stay unkeyed and unresolved.** Inert, but they are in
   the table.
