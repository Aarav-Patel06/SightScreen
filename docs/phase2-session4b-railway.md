# Phase 2, Session 4b — Railway deployment, and three real pauses

**Dates:** 2026-09-16 to 2026-09-18
**Commits:** `866dbf5` → `7364196` → `c7e9287` → `40783e6` → `a85bf92`
**Scope:** deploy both services, then verify against the deployed containers
rather than against tests.

Session 4a built everything and proved it locally. This session deployed it
and found eleven defects doing so — none of which the local verification
could have caught, because every one of them lived in the gap between "the
code is correct" and "the code is correct about reality."

## Final numbers

| | |
|---|---|
| Services deployed | 2 (`api`, `worker`), one image, `SERVICE_ROLE` dispatch |
| api URL | `https://api-production-5fa3.up.railway.app` |
| Image size | 701 MB (unchanged; see the note on multi-stage below) |
| Tests | **274 passed, 3 skipped, 5 deselected** (was 183 at the start of session 4) |
| Smoke test | 3 passed against the deployed api, 12 predictions read back from Supabase on a separate connection |
| Real Supabase pauses | 3 (12 min, 12 min, 3.3 h) |
| Defects found by deploying | 11 |
| Defects found by review or tests first | 0 |
| Secrets leaked into Railway-retained logs | 0, verified 3 times |
| Worker restarts across a 3.3-hour outage | 0 |

## What is deployed

Two Railway services in project `SightScreen` / `production`, both with root
directory `api`, both running the same image, dispatched by `SERVICE_ROLE`
through `serving/entrypoint.py`. The project was named `giving-comfort` when
Railway generated it and has since been renamed; the six shared variables had
never actually been set, which cost one round trip to establish.

`/health` on the deployed api reports what the process resolved, not what it
was configured with:

```json
{ "status": "ok",
  "db_host": "aws-0-ap-south-1.pooler.supabase.com:5432",
  "db_host_is_pooler": true, "db_port_is_5432": true,
  "db_resolved_ip": "3.111.105.85", "db_address_family": "AF_INET",
  "db_reachable": true, "db_status": "ok",
  "server_version": "17.6",
  "reference_age_days": 3, "reference_corpus_age_days": 24,
  "facts_measured_age_seconds": 2.1,
  "model_version": "winprob2-20260910",
  "model_sha256": "8c4f012a46f10c633aa242c95de424a22eaf5e9d4073ca52c69dbeb5206ee183",
  "model_notes": "KNOWN TRAIN/SERVE SKEW (open until the next retrain) ..." }
```

`reference_age_days: 3` beside `reference_corpus_age_days: 24` is worth
reading twice: the data was synced three days ago and the newest *match* in
it is 24 days old. Those are different facts, and conflating them is what
session 3's freshness check did.

## `railway redeploy` is not `railway up`

**`redeploy` replays a previous deployment. `up` deploys what you have.**

This cost real time and deserves the prominence. After the boundary test left
`api` crash-looping (correctly — it had `LOCAL_DATABASE_URL` set), the
deployment exhausted `restartPolicyMaxRetries: 10` and Railway stopped it.
Removing the variable did not restart it, so `railway redeploy` looked like
the obvious move. It brought the service back on an **older image**.

The only reason that was noticeable: `/health` on the current build emits
`db_host_is_pooler`, `db_port_is_5432` and `config_whitespace_stripped`, and
the resurrected build did not. The fields came back `None` and the
discrepancy was visible in one request.

**So a verbose `/health` is an accident that paid off, and it is kept
deliberately.** An endpoint that reports only `{"status": "ok"}` cannot tell
you that the wrong image is running. One that enumerates what the process
resolved, loaded and measured turns "did my deploy land?" into a diff. Every
field added to it since has earned itself twice: once as a diagnostic, once
as a build fingerprint.

Corollary, now a standing rule: **both services share an image, so deploy
both.** The worker crashed during the second pause because `startup.py` — a
module *both* services import — was fixed and only `api` was shipped. The two
services ran divergent code for hours with no signal anywhere.

## The three pauses, as a unit

This is the methodological finding of the phase, and it matters more than any
individual defect.

**Pause #1 existed because SPEC §13 required it.** It found that the paused
signature recorded in `docs/phase1-closeout.md` — `"Tenant or user not
found"` — was not what Supavisor returns. The real text is
`FATAL: (ENOTFOUND) tenant/user postgres.<ref> not found`: a slash, with the
username interpolated. The literal never matched, so a genuinely paused
project classified as `OTHER` and logged "Supabase connection failed",
defeating the entire requirement. **The code was correct with respect to a
wrong fact, and there is no review that finds that.**

**Pauses #2 and #3 existed because of what #1 proved about testing.** Once
you have watched a passing test assert a string someone picked while reality
said something else, "it is pinned by a test" stops being evidence. The four
fixes from #1 — the corrected pattern, the `AdminShutdown` signature, the
`/health` probe, `ReconnectingConnection` — were all pinned by tests and all
unproven. Pause #2 was the only way to know.

It found three more: `/health` still lied in a different way (losing its
classification once the connection closed), recovery was demand-triggered
rather than autonomous, and a shadowing bug in the reconnection fix itself
meant every `/health` call raised `AttributeError` — deployed and unnoticed,
because nothing exercised `/health` between writing the fix and shipping it.

**Pause #3 existed because #2's fixes were, again, only pinned by tests** —
and because the worker's in-loop reconnection path had been pre-empted by the
crash rather than exercised. It proved that path:

```
05:07:27  poll failed - Supabase connection failed (OperationalError); retrying in 60s
05:07:29  reconnected to Supabase
05:08:32  heartbeat: no live matches; next check in 600s (144 calls/day idle)
```

Zero container starts across a 3.3-hour outage — the same process throughout,
straight back to the normal cadence. And it found two more defects on top.

**What each pause proved that the previous could not:**

| | Proved | Could not have been proved before |
|---|---|---|
| #1 (12 min) | The recorded signature was wrong | Nothing else reads Supavisor's actual reply |
| #2 (12 min) | #1's four fixes; api recovery precondition | #1's fixes did not exist yet |
| #3 (3.3 h) | In-loop reconnection; `/health` self-heal; sustained-outage steady state | #2's worker crashed before reaching the loop; #2 was too short for the cooldown to reach its cap |

The length mattered independently. Twelve minutes could not show the cooldown
reaching and holding its 120s cap (189 cycles, 96 attempts, one attempt per
two cycles at the cap), and could not have surfaced the quota burn at all.

## The defect log

Each with the thing that found it. Note the right-hand column.

| # | Defect | Found by |
|---|---|---|
| 1 | Paused signature literal was wrong (`tenant/user`, not `tenant or user`) | Pause #1 |
| 2 | A pause has **two** signatures; `AdminShutdown` on an existing connection was unhandled | Pause #1 |
| 3 | `/health` returned `ok` throughout an outage — every value cached at startup | Pause #1 |
| 4 | Neither service reconnected; recovery required a redeploy | Pause #1 |
| 5 | `database` shadowed by `server_version, database = cur.fetchone()` — every `/health` raised `AttributeError` | Deployed api logs |
| 6 | Blank env var passed "Field required"; an unresolved `${{shared.X}}` resolves to `""` | First real deploy |
| 7 | Reference freshness measured the corpus clock, not the sync clock — refused data synced 14 hours earlier as "22 days old" | First clean container start |
| 8 | Model pin checked only at startup; a promotion mid-life writes misattributed rows | Startup-cache sweep |
| 9 | Recovery demand-triggered — `/health` reported degraded indefinitely with the database available | Pause #2 |
| 10 | Classification degraded to `OTHER` after the first degraded cycle | Pause #3 |
| 11 | Degraded loop burned provider quota on discarded polls | Pause #3 |

Defects 3, 7, 8 and 9 are one family — **a value computed once and reported
as current** — which is why the standing rule is phrased as a category rather
than four fixes.

Defects 10 and 11 are one mistake in two places: a pattern fixed where it was
observed and left everywhere else. The classification loss was fixed in
`/health` and not the worker loop; the quota reasoning covered startup backoff
and not the degraded loop.

## Across Phases 0-2: the defects worth finding were found by running it

**REQUIRED IN THE PHASE 2 CLOSE-OUT.** This is not a session-4b observation;
it is the pattern of all three phases, and Phase 3 should inherit it as a
methodology rather than rediscover it.

Eleven defects this session, **all** found by deploying, **none** found by
review or by a test written first. That is not an accident of this session's
subject matter. Every defect across the project that mattered was found the
same way:

| Phase / session | Defect | Found by |
|---|---|---|
| 0 | `sslmode` breaking CI and local dev, twice | Running CI |
| 0 | A connection left in a transaction hanging the suite | Running the suite |
| 1 s1 | 992 innings-2 rows pass §9.1's documented predicate but have a NULL `required_run_rate` | Querying the real corpus, not reading the spec |
| 2 s1 | Postgres `ROUND(numeric)` rounds half away from zero; Python's `round()` rounds half to even. A reduced 30-ball chase hit exactly 22.5 | Incremental-vs-bulk parity on the real corpus |
| 2 s2 | 0.00% partnership error at every interval, an artifact of uniform ball-gap timing rather than a result | Running the measurement and disbelieving the answer |
| 2 s3 | `extra_float_digits` differs between local Postgres and Supabase's pooler; `float4::numeric` is capped at 6 significant digits | The sync's own hash verification, on its first run |
| 2 s4a | pydantic's `ValidationError` repr embeds the entire environment, secrets included | Running the failure path in a container |
| 2 s4b | 11 defects (table above) | Three real Supabase pauses |

The common shape: **the code was correct with respect to a belief about the
world, and the belief was wrong.** A remembered error string, an assumed
rounding mode, a default that differs between two Postgres instances, a
timing model chosen for convenience, an exception's repr nobody had read. No
amount of re-reading the code finds any of them, because the code faithfully
implements the mistake.

What does find them is comparatively cheap: run the thing against the real
dependency and look at what it actually does. Three pauses cost perhaps two
hours of wall time and found eleven defects, four of which would have
produced silently wrong numbers in production.

**For Phase 3:** the accuracy page reads `predictions`, `prediction_outcomes`
and `model_versions` from Supabase and computes calibration per
`model_version`. Every one of those is a boundary this project has already
been bitten at. Before trusting a calibration figure, write the thing that
produces it and compare it against the same computation run locally - the
session-3 parity gate is the template, and it exists because the
`::numeric` bug taught the lesson first.

## A correction, not a finding

After pause #2 I reported that neither service burned provider quota during
an outage. **That was true for the path that had run, and wrong about the
path that hadn't.**

The worker was blocked in *startup* backoff for the whole of pause #2 and
never reached its polling loop, so the claim was accurate about what I had
observed. The in-loop degraded path behaves differently:
`list_live_matches()` → `_fetch_snapshots()` issues the CricketData HTTP
request **before** it touches Postgres, so every degraded cycle paid for a
poll whose result could not be persisted.

Measured during pause #3 from the provider's own counter:
`hitsToday=942` of `2000` — **47% of the day's quota**, against ~144 for a
normal idle day. At the 60s degraded interval a full-day outage would consume
~1,440 calls, 72% of the quota, entirely on discarded work.

Fixed by probing Postgres first while degraded — free, and the thing actually
blocking — and skipping the provider until the connection returns.

**The re-examination is the lesson, not the number.** The original claim was
not careless; it was correctly scoped to what had executed and stated as
though it covered the feature. Which is now rule 7.

## The model pin, and why a staleness bound was not enough

Worth its own section because it is the only defect with consequences outside
this session.

SPEC §8.4 promotes models while containers are alive. SPEC §5.4's
`predictions.model_version` is what Phase 3's accuracy page groups
calibration by. A container that keeps serving after a promotion writes rows
tagged with a version that did not produce them — and the audit trail looks
*clean*, because every row carries a plausible version. Phase 1's entire
evaluation apparatus would attribute those predictions to the wrong model and
never know.

A staleness bound on `/health` is insufficient: noticing a promotion 300
seconds late still means 300 seconds of misattributed rows. So the check sits
on the **prediction path**, in `ActiveVersionGuard`, with a 15s cache.

The trade, stated rather than assumed: read-per-prediction is safest and
costs a round trip per call against a pooler whose free-tier ceiling §2.4
warns is low — and the live worker will eventually predict every 15 seconds
per match. 15s bounds exposure to a handful of rows, few enough to find and
fix. Measured: 100 predictions over 10 seconds of traffic → 1 version query.
300s was rejected explicitly; minutes of silent misattribution is the thing
being prevented.

**On mismatch the request is refused with 409, never relabelled.** An
in-flight prediction cannot be honestly tagged either way: the probability
came from the old artifact, so the new version would be a lie about which
model produced it, and the old version *is* the misattribution. The mismatch
is sticky — once seen, every subsequent call refuses without re-querying,
because a container holding the wrong artifact in memory does not get to
recover by waiting. It has to be restarted onto the right pin.

Verified during pause #3 that a **failed** version read does not look like a
promotion: three `/predict` attempts, three 503s with the PAUSED message,
zero 409s.

## Decision log by session

**4a (2026-09-15)** — Housekeeping: 8 REAL→NUMERIC across `model_versions`,
`prediction_outcomes`, `player_state`; `match_states` deliberately not
converted and the claim enforced by a test; the Elo fix reframed as
`KNOWN TRAIN/SERVE SKEW`. Decisions 1–7 implemented and verified against a
local container, including `docker run` proving the §2.1 boundary refuses.

**4b (2026-09-16 to 18)** — Deployed both services; three pauses; eleven
defects; `TIMEOUT` split from `UNREACHABLE` so all four connection causes
classify distinctly and only `AUTH` exits; `ReconnectingConnection` with its
own cooldown (50 synthetic requests → 1 attempt; 40 real requests over 34s →
2 attempts); `probe()` that reuses the pooled connection, never opens one
(40 `/health` calls during an outage → 0 connection attempts), and caches for
5s (25 calls → 1 round trip).

## Things a future session would get wrong

The seven standing rules. These carry into the Phase 2 close-out.

1. **A literal match against a remembered vendor error message is untestable
   by construction.** The code was correct with respect to a wrong fact, and
   review cannot find that. Where classification depends on a vendor's error
   text, the text must come from having triggered it, and the test must pin
   the captured string with a note saying when and how it was captured.

2. **Health computed at startup is worse than no health endpoint**, because it
   reports green during the exact failure it exists to catch. Any health check
   must touch the dependency it claims to be reporting on.

3. **The category behind rule 2: a value that can change must be read when
   asked, or carry an explicit and visible staleness bound.** "Computed once
   and reported as current" was four separate defects before it was
   recognised as one habit. `/health` now reports
   `facts_measured_age_seconds` so a reader can see how old a measurement is
   instead of assuming it is current.

4. **An empty environment variable is semantically absent but structurally
   present.** Validators that only check presence will pass it, and the
   failure then surfaces somewhere unrelated with a misleading message. An
   unresolved `${{shared.NAME}}` reference resolves to `""`, and two of six
   sailed through "Field required" before the service failed on a third
   variable entirely.

5. **Exercise the FAILURE path of anything touching config, not just the
   success path.** The boundary check worked first try; what did not was what
   it *printed* — pydantic's `ValidationError` repr embeds `input_value`,
   which for a `BaseSettings` failure is the whole collected environment.
   Railway retains deploy logs, so the refusal that proved the boundary would
   also have written live credentials into a log outliving the container.

6. **Two services sharing an image must be deployed together, or you must say
   explicitly why not.** A partial deploy leaves them on divergent code with
   no signal. Fixing `startup.py` and shipping only `api` is what crashed the
   worker mid-pause.

7. **When reasoning about cost or resource use, state which code path you
   measured.** A claim true of one path is routinely false of another. "Zero
   quota burned" was true of startup backoff and wrong about the degraded
   loop, and the difference was 47% of a day's provider quota.

Two environment notes that are not rules but cost time anyway: Docker Desktop
stops on its own and every local DB test then fails with `ConnectionTimeout`
(relaunch from `%LOCALAPPDATA%\Programs\DockerDesktop`); and a psycopg
fixture without `connect_timeout` blocked a smoke test for **20.5 hours**
against an unreachable database before anyone noticed. All smoke fixtures are
now bounded at 20s.

## On image size, decided and not revisited

701 MB, two copies. A multi-stage build was considered and rejected: the
weight is `lightgbm`, `scikit-learn`, `scipy` and `numpy`, all of which are
*runtime* dependencies — the container unpickles a LightGBM booster and a
scikit-learn-based calibrator. Multi-stage would strip pip and build tooling,
perhaps 100–150 MB, not 400. Getting materially smaller means changing the
artifact format to drop scikit-learn, which is a **Phase 3 calibrator
conversation, not packaging.** Railway builds it without complaint.

## Open, carried to Session 5 and the Phase 2 close-out

- **The rebuild-and-sync ritual is still manual**, and the worker now refuses
  to serve 14 days after it was last run. Session 3's open item has become a
  production outage on a timer. Automating it belongs to the Phase 2
  close-out.
- **READ THIS BEFORE WRITING ANY SERVING-SIDE `match_states` INSERT.** The
  worker does not write `deliveries` or `match_states` to Supabase, which
  §7.1 step 4 specifies and which the UI needs to render a win-probability
  curve. That is Session 5's problem, and it is the exact moment
  `match_states`' four REAL columns begin crossing the local/Supabase
  boundary. They were left unconverted in 4a **only** because nothing
  serving-side wrote them; `current_run_rate`, `required_run_rate`,
  `rrr_minus_crr` and `dls_resources_pct` are model *features*, and the
  failure mode is silent rounding in model inputs that no metric would
  reveal. **Convert them to NUMERIC first** (the pattern is
  `20260915000002_numeric_metrics.sql`; note the `::text::numeric` cast, since
  `float4::numeric` is capped at 6 significant digits) and expect
  `eval/splits.py` to start receiving `Decimal` where it expects `float`.
  `tests/db/test_float_boundary.py` will fail the moment the write is added —
  that failure is the reminder, not a nuisance.
- **Every worker restart re-marks an in-flight match INFERRED**
  (`cricketdata.py:594-602`), and Railway redeploys on every push.
- **KNOWN TRAIN/SERVE SKEW** awaits the next retrain, which must close it
  deliberately and say so.
- **A live T20 latency measurement** is still pending from session 2; the
  harness is built and hard-capped at 200 hits.
