# Phase 2 close-out — live loop, deployment, and a match page that admits what it doesn't know

**Closed 2026-09-18.** All seven §11 checkboxes ticked; acceptance met and
measured rather than asserted.

Five sessions: the `LiveClient` seam and replay (1), the CricketData adapter
(2), as-of summaries on Supabase (3), containerisation verified locally (4a),
Railway deployment through three real pauses (4b), and the Vercel match page
(5). Per-session detail lives in `docs/phase2-session*.md`; this document is
what a future session needs and what Phase 3 should inherit.

## What is live

| | |
|---|---|
| Prediction service | `https://api-production-5fa3.up.railway.app` — FastAPI, `/health` and `POST /predict/win-prob` |
| Live worker | Railway, §7.1 poll loop, same image, dispatched by `SERVICE_ROLE` |
| Match page | `https://sight-screen-rose.vercel.app/match/<id>` — Next.js 15, Root Directory `web` |
| Serving database | Supabase `xrvgmjmvgmtepgryyulk`, session pooler on 5432 |
| Model | `winprob2-20260910`, pinned, artifact from a GitHub Release with a `#sha256=` fragment |
| CI | three independent jobs — `api`, `types`, `web` — green as of `2aaf7b3` |

## The acceptance, measured

§11 asks for a replayed match rendering a full smooth curve in the **deployed**
Vercel app, updating within 60s. Match 9337 (CPL, chase won off the last over),
125 innings-2 balls driven from the local corpus through the deployed service:

```
125 posted, 0 failed, 125 rows on Supabase, 125 distinct payloads
125 of 125 balls rendered on the deployed page, in order, no gaps
curve 0.256 -> 0.926

Supabase INSERT -> pixel on the deployed page, per ball:
  median 702 ms    p95 918 ms    max 1085 ms    over 60s: 0
```

Measured by a headless browser reading the rendered DOM ten times a second
and pairing each increment against the row's `created_at`, because "it
looked fast" is not a measurement. Adding the client-to-Railway leg (~1.6 s
per POST across the run) puts end-to-end at roughly 2.3 s — the criterion met
with about 55x headroom at the worst ball.

**What this does not prove**, stated because a green demo invites
over-reading: no CricketData call is involved anywhere in it. The
provider-fed worker path is unexercised and stays that way until a real
match is on, which is a cricket-calendar problem rather than an engineering
one. The cadence came from a laptop, so the system is not self-driving in
this test.

## The methodology, and it is the most transferable thing here

**Every defect worth finding in this project was found by running it against
the real dependency.** Not one came from review, and not one came from a
test written before the code. Carried forward from session 4b, now extended
across all five sessions of Phase 2:

| Phase / session | Defect | Found by |
|---|---|---|
| 0 | `sslmode` breaking CI and local dev, twice | Running CI |
| 0 | A connection left in a transaction hanging the suite | Running the suite |
| 1 s1 | 992 innings-2 rows satisfy §9.1's documented predicate but have a NULL `required_run_rate` | Querying the real corpus, not reading the spec |
| 2 s1 | Postgres `ROUND(numeric)` rounds half away from zero, Python's `round()` half to even; a reduced 30-ball chase hit exactly 22.5 | Incremental-vs-bulk parity on the real corpus |
| 2 s2 | 0.00% partnership error at every interval — an artifact of uniform ball-gap timing, not a result | Running the measurement and disbelieving the answer |
| 2 s3 | `extra_float_digits` differs between local Postgres and Supabase's pooler; `float4::numeric` silently caps at 6 significant digits | The sync's own hash verification, on its first run |
| 2 s4a | pydantic's `ValidationError` repr embeds the entire environment, secrets included | Running the failure path in a container |
| 2 s4b | 11 defects, including a `/health` that reported green throughout an outage | Three real Supabase pauses |
| 2 s5 | **13 tables world-readable to `anon` on Supabase**, including `elo_asof_summary` (25,290 rows) and `player_aliases` (18,468) | The first time anything authenticated as `anon` |
| 2 s5 | `anon` held INSERT/UPDATE/DELETE on all five policied tables, blocked only by the absence of a policy | The same script's negative control |
| 2 s5 | `POST /predict/win-prob` is not idempotent and the transport retries it — 128 rows from 125 posts | Counting what landed instead of trusting the loop's own tally |
| 2 s5 | `web/lib/types.ts` four migrations stale; the check that catches it had been `skipped` for ten runs | Reading the Actions history instead of assuming CI was green |
| 2 s5 | Realtime drops `postgres_changes` messages silently | The anon gate failing twice, and being measured rather than just re-run |

The common shape: **the code was correct with respect to a belief about the
world, and the belief was wrong.** A remembered error string, an assumed
rounding mode, a default that differs between two Postgres instances, a
timing model chosen for convenience, an exception repr nobody had read, a
green CI badge nobody had opened. Re-reading the code finds none of them,
because the code faithfully implements the mistake.

## Proving a gate fires — the template for every gate from here

This is the first time in the project a check has been proven to fire rather
than merely to exist, and it should be the pattern.

`ci.yml` has diffed `web/lib/types.ts` against the Supabase Management API
since Phase 0 session 7. Reading the run history:

```
6919ee1, 6a8e032    api fails at [Check web/lib/types.ts is not stale]   <- fired, correctly
cc38d0a .. f81047e  api fails at [Run tests], staleness check SKIPPED    <- ten consecutive runs
```

The gate was never broken. It fired on the two pushes where the file first
drifted, nobody read it, and then a test failure in session 4a moved the red
earlier in the same job and the step stopped executing at all. CI was red on
**every Phase 2 push**; the last green run was 2026-08-25.

Three fixes, in increasing order of how much they matter:

1. The masking failure itself. `tests/serving/test_boundary.py` builds
   `Settings(_env_file=None, ...)` and its docstring says that is what stops
   `api/.env` supplying values behind the test's back. True, and incomplete:
   pydantic-settings reads `os.environ` too, and `ci.yml` writes
   `LOCAL_DATABASE_URL` to `GITHUB_ENV`, so seven Railway-simulating tests
   saw a local database URL they never passed. Green on every laptop, red
   everywhere that mattered. Now a `settings_env_isolated` fixture clears
   every variable `Settings` reads — with a test asserting that list still
   covers every field, because a fixture is only as good as its list.
2. The staleness check is its **own job**. A cheap check that an expensive
   one can mask is a check you eventually stop having.
3. **The gate was proven to fire.** Branch `ci/prove-staleness-gate` deleted
   `reference_sync_state` from `types.ts` — real drift, and `tsc` stays
   clean, so only the gate could see it. The run came back exactly as
   predicted:

   ```
   run 1650709 -> failure
     api      success
     types    failure  FAILED AT [Check web/lib/types.ts is not stale]
     web      success
   ```

   Right job, right step, other two untouched. Branch deleted afterwards.

Predicting the failure *and its location* before pushing is what makes this
proof rather than observation. Do it for every gate.

## Why the Realtime resync is unconditional

Supabase Realtime drops `postgres_changes` messages occasionally and
silently. Observed three times: twice as `check:anon` timing out at 15s and
passing on an immediate re-run, once in a measurement round that lost 3 of 5
probes (+0s, +1s and +6s after `SUBSCRIBED`) while delivering +3s and +12s.

From the first two I inferred a cold-start pattern — both were the first
channel after an idle period. **The measurement refuted it.** Three rounds
with 4-minute idle gaps delivered 15 of 15 at 180-270 ms, and a 30-minute
gap delivered 10 of 10. Across eight measured rounds and 40 probes the only
losses are the three in the first round ever measured. So the loss is real,
rare, and not a function of how long the channel has been idle; the cause
remains unidentified.

The mitigation is deliberately not keyed to a trigger, and this is the part
to inherit: **an unexplained silent failure justifies an unconditional
refetch better than a pinned trigger would, because you cannot engineer
around a cause you have not found.** Had the inference been trusted, the fix
would have been scoped to the first few seconds after a cold subscribe — a
bet on a hypothesis the third run contradicted, and one that would have
failed open exactly where it was least visible.

So the page re-reads the whole match every 30s and merges. Two details carry
weight:

- **A full refetch, not `WHERE prediction_id > high_water_mark`.** A row
  dropped mid-stream sits below the mark forever after, so a tail-only
  refetch leaves a permanent hole in the curve and then never looks at it
  again. A full refetch costs one indexed query per 30s per open tab,
  against a table the browser already reads.
- **When it recovers something, the page says so** — "1 update the stream
  dropped, recovered by resync". Per §12.2, a silent failure made visible.
  During the 125-ball deployed replay the stream delivered everything and
  the counter stayed at zero, so the net is genuinely a net rather than the
  mechanism in disguise.

## Standing rules

Rules 1-7 are stated in full in `docs/phase2-session4b-railway.md`. In brief:
(1) a literal match against a remembered vendor error string is untestable by
construction; (2) health computed at startup is worse than no health
endpoint; (3) a value that can change must be read when asked or carry a
visible staleness bound; (4) an empty environment variable is semantically
absent but structurally present; (5) exercise the failure path of anything
touching config, including what it *prints*; (6) two services sharing an
image are deployed together or you say why not; (7) when reasoning about
cost, state which code path you measured.

**8. A check that exists is not a check that runs, and its job status must be
verified rather than assumed. When you add a gate, prove it fires and prove
the job it lives in is green.** Third instance of one shape: the parity test
not covering policies or grants, the `match_states` guard inferring
execution truth from module-level imports, and now a gate skipped for ten
runs behind an unrelated failure. Proving it fires means predicting the
failure and its location, then observing exactly that.

## Two corrections, both self-caught

Worth recording as a pair, because the shape is identical and the second was
caught faster than the first.

- **Session 4b, quota.** "Neither service burned provider quota during the
  outage" was true of the startup-backoff path, which is what had run, and
  wrong about the in-loop degraded path, which had not. Measured from the
  provider's own counter: 47% of a day's quota. Became rule 7.
- **Session 5, Realtime.** "Two occurrences on the first channel after idle
  looks like a cold-start pattern, not randomness" was an inference from two
  data points, contradicted by the third. Recorded in §15 next to the
  finding itself.

Both claims were correctly scoped to what had been observed and stated as
though they covered the phenomenon. The generalisation is the error, not the
observation — and in both cases the fix was to go and measure.

## Open, carried into Phase 3

1. **`POST /predict/win-prob` is not idempotent** and the transport retries
   it. 128 rows from 125 posts on the match 9339 run (three byte-identical
   pairs with consecutive ids); the 9337 run produced none, so it is
   intermittent. Harmless on a curve plotted in `prediction_id` order,
   **not** harmless for calibration bins, which would double-count. The
   dedupe key is subtler than it looks: extras legitimately repeat
   `balls_bowled` with a different payload.
2. **KNOWN TRAIN/SERVE SKEW** — `winprob2-20260910` was trained against the
   pre-fix `elo_as_of`. It closes at the next retrain, and that retrain must
   say so explicitly. Visible to readers of the UI through
   `model_versions.notes` and `/about/model`.
3. **Calibration debt** — shipped uncalibrated, 4 of 10 test deciles failing.
   Disclosed on `/about/model` rather than waiting for Phase 3's accuracy
   page. The phase-confidence chip is the standing substitute for a
   per-prediction interval until something can produce a real one.
4. **`prepare_threshold=0` on any transaction-pooler (6543) connection**,
   before Phase 6's agent SQL tool. It passes five identical queries and
   fails the sixth, so it correlates with load rather than with code.
5. **Realtime drops, cause unidentified.** Mitigated, not explained.
6. **The live provider path is unproven end to end** — blocked on a real
   match being on.
7. **§12.1 items 3, 4, 5 and 7** need per-ball player identity, which §15
   records as blocked on a ball-by-ball-capable provider; item 7 is Phase 6's
   agent.

## For Phase 3 specifically

The accuracy page reads `predictions`, `prediction_outcomes` and
`model_versions` from Supabase and computes calibration per `model_version`.
Every one of those is a boundary this project has already been bitten at.
Before trusting any calibration figure, compute it twice — once from Supabase
and once locally — and diff. Session 3's parity gate is the template, and it
exists because the `::numeric` bug taught the lesson first. Deduplicate
before binning (open item 1), and prove the gate you write actually fires
(rule 8).
