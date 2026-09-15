# Phase 2, Session 4a — containerisation, verified locally

**Date:** 2026-09-15
**Scope:** everything deployable, proved against a real container on this machine.
Session 4b does `railway login`, deploys both services, re-runs these same
verifications against the deployed containers, and does Decision 4's real pause test.

## Why this split

`docker run` with `LOCAL_DATABASE_URL` set is a stronger test of Decision 2
than watching a Railway deploy succeed: it proves the refusal, not just the
happy path. Everything below is verified against the actual image.

## What did not exist

The session was scoped as "deployment". Three things SPEC §3 describes as
existing did not:

| SPEC §3 | Reality |
|---|---|
| `api/src/serving/app.py` | No FastAPI anywhere; not even in `pyproject.toml` |
| `api/src/serving/live_loop.py` | `cricketdata.py` had `poll()`/`next_interval()` but no loop, entry point, or CLI |
| `api/Dockerfile` | No Dockerfile, `.dockerignore`, or Railway config |

And a blocker neither of us had named: **nothing in `api/src` could run in a
container.** Eighteen modules load config with
`dotenv_values(REPO_ROOT / "api" / ".env")`, which ignores `os.environ`
entirely; `.env` is gitignored, so in a container every one of those paths
`sys.exit`s. `config.py` separately required `LOCAL_DATABASE_URL` on port 5433
at import time — in a serving container, the one variable that must not exist.

Fixed surgically rather than by sweeping all eighteen: `db/env.py` provides
`os.environ`-first reads for serving code, and `config.py`'s required fields
became role-conditional. The training modules still read `api/.env`; they only
ever run on a laptop, and rewriting them would be unrelated churn.

## Housekeeping A — the `real` sweep

| Column | Was | Now | Why |
|---|---|---|---|
| `model_versions.test_brier`, `.test_log_loss` | REAL | **NUMERIC** | Written by training, read by the Phase 3 accuracy page from Supabase |
| `prediction_outcomes.brier`, `.log_loss` | REAL | **NUMERIC** | Written by §8's scoring job, read by the accuracy page and drift job |
| `player_state` ×4 ability columns | REAL | **NUMERIC** | A Kalman update reads its own prior and writes the posterior — rounding compounds. Empty until Phase 5, so free now and a data migration later |
| `match_states` ×4 | REAL | REAL | **Verified not to cross** |
| `matches.target_overs` | REAL | REAL | Crosses, but provably benign |
| `elo_ratings.rating`, `unresolved_entities.best_score` | REAL | REAL | Local-only / human-read diagnostic |

Two of those deserve their reasoning rather than a verdict.

**`match_states` looks like the scariest case and is the one I did not
convert.** Those four columns are model *features*. But no serving path writes
`match_states` at all: the three writes a deployed process makes are
`matches` (the live adapter's `_ensure_match_row`), `predictions`
(`serving/app.py`), and `unresolved_entities` (resolution queuing). So the
values never make the round trip. Converting would mean
altering 3.78M local rows *and* handing `Decimal` instead of `float` to
`eval/splits.py`'s numpy arrays: a training dtype change wearing a deployment
fix's clothing. **If Session 5 starts writing live `match_states` to Supabase,
convert first.**

That reasoning is now *enforced*, not just recorded.
`tests/db/test_float_boundary.py` scans the serving-authored modules
(`serving/*`, `ingest/cricketdata.py`) and fails if any of them writes
`match_states` or `deliveries` - scoped to code that actually runs in a
container rather than the import closure, because `serving/app.py` imports
`features/match_state.py` for `MatchStateRow` and that module also holds
`REBUILD_SQL`, which only `rebuild()` ever executes. A third test closes the
subtler hole: serving code must not *call* `recompute_format`,
`rebuild_venue_summary` and friends either, since they all accept a `conn` and
config.py's boundary check only forbids `LOCAL_DATABASE_URL`. Verified
non-vacuous by injecting an `INSERT INTO match_states` into `serving/app.py`
and confirming the failure names the file.

**`matches.target_overs` is benign for a checkable reason.** Its values carry
at most three significant digits, so float4's text output is identical at
`extra_float_digits` 0 and 1. `tests/db/test_float_boundary.py` asks the
database rather than asserting it in a comment — **113 distinct values, all
identical at both settings** — and a second test fails if any future migration
adds a float column to a table nobody has classified. That is the actual
lesson of the original bug: pin the claim, don't restate it.

The casts go via `::text::numeric`, not `::numeric`, because `float4::numeric`
is hardcoded to six significant digits and ignores the setting entirely. The
tables were empty, so nothing would have been lost — but the correct cast is
the one that stays correct when someone re-runs it against real data.

## Housekeeping B — the Elo fix is open skew

Reframed from "fixed" to **`KNOWN TRAIN/SERVE SKEW — open until the next
retrain`** in three places: SPEC.md §15, `models/registry.py`'s `KNOWN_SKEW`
(where someone stands when promoting a version), and the published Supabase
`model_versions.notes` (so it is visible from the serving side, via the
`notes` column added in this session's migration).

The content: `winprob2-20260910` trained against the pre-fix `elo_as_of`;
serving uses the fixed ordering. 162 of 12,916 matches (1.25%), mean 9.2 Elo
points, max 24.2, on a feature Phase 1 measured as individually
non-significant. Not closed by a retrain triggered on this finding alone — the
effect is small and an unplanned retrain would confound the next scheduled
comparison. **The next retrain must close it deliberately and say so.**

## The seven decisions

**1 — two services, one image.** `SERVICE_ROLE` (`api` | `worker`) dispatched
by `serving/entrypoint.py`. One image because they share ~100% of their
dependencies; two services because the API is request-scoped and scalable
while the worker is a singleton whose per-match in-memory state two replicas
would corrupt. `SERVICE_ROLE` rather than per-service config files because
both services share root directory `api` and therefore cannot have different
ones — and a start command typed into a dashboard is invisible from the repo.

**2 — the boundary, enforced.** A `model_validator` in `config.py`, sitting
beside the §2.4 pooler allowlist it deliberately mirrors: default-deny, a
specific diagnostic for the mistake someone will actually make, a spec
citation. On Railway, `LOCAL_DATABASE_URL` present is fatal and
`SUPABASE_SESSION_POOLER_URL` + `MODEL_VERSION` are required; off Railway, the
old rules are unchanged. Twelve tests, and the container itself:

```
$ docker run --rm --env-file violating.env sightscreen:4a
configuration rejected - settings: Value error, LOCAL_DATABASE_URL is set in a
Railway environment. Serving never touches local Postgres (SPEC.md section 2.1)
...
exit code 1
```

**3 — artifacts.** GitHub Release on the public repo, so the container
downloads with no credentials. `artifact_path` becomes the release URL with
the digest attached as a `#sha256=` fragment (PEP 503's convention), so the
location and the expected bytes cannot be updated independently. A pinned
`MODEL_VERSION` that disagrees with Supabase's active row refuses to start.
Publishing is a separate CLI, `models.publish_model_version`, which verifies
the download *before* writing the row — and which stays out of training,
because `registry.py:11-17` and `phase1-closeout.md:138-147` both warn against
training scripts writing to Supabase.

**4 — Supabase pause.** `classify_connection_error` → `PAUSED | AUTH |
UNREACHABLE | OTHER`. The paused signature is `Tenant or user not found`,
which reads exactly like a wrong password and cost real debugging time once
already. Everything retries with capped, jittered backoff **except `AUTH`,
which exits immediately** — retrying a wrong password forever is a silent
outage where the service looks alive and nothing works. Unit-tested here; the
real pause test is 4b.

**5 — idle behaviour.** One `currentMatches` call covers every live match, so
idle cost is per-poll. At **600s: 144 calls/day, 7.2% of the 2,000/day
quota**, leaving two concurrent T20s (~1,200 calls) affordable. Pinned as a
test, because that interval is the only thing between "always on" and "out of
quota by lunchtime". Heartbeat on every idle poll so silence means broken.
Sleep is sliced so Railway's SIGTERM stops the worker rather than killing it.

**6 — secrets and the resolved host.** Railway variables only; `.dockerignore`
excludes `.env`. Startup and `/health` report what was resolved, not what was
configured:

```
db_host              aws-0-ap-south-1.pooler.supabase.com:5432
db_resolved_ip       3.111.105.85
db_address_family    AF_INET
server_version       17.6
reference_age_days   0
```

The address family is there because §2.4's failure mode is a *DNS* failure on
an IPv6-only direct host — printing it is what separates "wrong host" from
"wrong password" at 2am.

**7 — the smoke test.** `tests/deploy/test_smoke.py`, gated on
`SMOKE_BASE_URL`. A local driver reads innings-2 balls from the corpus (the
container cannot, §2.1), POSTs each to the service, and then asserts against
Supabase **on a separate connection**: one row per ball, the pinned
`model_version`, `p` strictly in (0,1), and the stored probability equal to
the one the caller was handed.

## What the container found that review did not

**A secret leak into deploy logs.** The first boundary refusal printed
pydantic's raw `ValidationError`, whose repr embeds `input_value` — the entire
collected environment, `SUPABASE_SECRET_KEY` and `LIVE_API_KEY` included.
Railway captures deploy output, so that log would have outlived the container.
`config.py` now re-raises with only the messages; direct `Settings(...)`
construction still raises `ValidationError`, which is what `test_config.py`
asserts on. Verified by diffing the container's output against the real secret
values: **no leak**.

**Session 3's freshness check was measuring the wrong clock.** The first clean
container start refused with `StaleReferenceData`. The data had been synced
**14 hours earlier** and was as current as it could be — but the newest
*match* in the corpus was 2026-08-24, 22 days back, and Decision 4 enforced on
`max(effective_date)`. I had justified that with "cricket is played almost
daily", which is true and irrelevant: the corpus is a periodically-refreshed
archive, so its newest match is routinely weeks old between Cricsheet pulls.
Freshness now measures `reference_sync_state.synced_at`, which answers the
question actually being asked. Corpus age is still reported, it just no longer
refuses. The content hash — the airtight half — is unchanged.

**A file-descriptor leak, found by a 404 test.** `ensure_artifact` held
`mkstemp`'s descriptor open across the HTTP request, so the cleanup `unlink`
hit "file in use" on Windows and masked the real error with a
`PermissionError`.

**An over-strict test of my own.** The import-graph check flagged
`features.asof_summary` as training code reaching the serving graph. It is
genuinely dual-purpose — `rebuild_*` is training, `DERIVED_TABLES` and
`content_hash` are what the serving freshness check needs. Classified
explicitly with the reason rather than deleted or worked around.

## Verification

```
249 passed, 3 skipped, 5 deselected     (was 183). The 3 skipped are the
                                        smoke tests, which need SMOKE_BASE_URL;
                                        with it set they pass - 252 total.
docker build                            clean
docker run + LOCAL_DATABASE_URL         refuses, exit 1, no secrets in output
docker run, SERVICE_ROLE=api            healthy in 11s
docker run, SERVICE_ROLE=worker         startup checks pass, dispatch correct
artifact from the real GitHub Release   downloaded, sha256 verified, loaded
smoke test vs the local container       3 passed, 12 predictions read back
                                        out of Supabase on a separate connection
```

`/health` from the running container:

```json
{ "service_role": "api",
  "db_host": "aws-0-ap-south-1.pooler.supabase.com:5432",
  "db_resolved_ip": "65.0.195.55",
  "db_address_family": "AF_INET",
  "server_version": "17.6",
  "reference_age_days": 0,
  "reference_newest": "2026-08-24",
  "model_version": "winprob2-20260910",
  "model_sha256": "8c4f012a46f10c633aa242c95de424a22eaf5e9d4073ca52c69dbeb5206ee183",
  "model_notes": "KNOWN TRAIN/SERVE SKEW (open until the next retrain) ...",
  "status": "ok" }
```

`reference_age_days: 0` beside `reference_newest: 2026-08-24` is the bug from
the findings above, fixed: current data, an archive three weeks old, correctly
accepted. And the skew note reaching `/health` is the point of putting it in
`model_versions.notes` - it is visible from the serving side, not only in
SPEC.md.

## Carried to 4b

- `railway login`, link, deploy both services, re-run every check above
  against the deployed containers.
- Nothing is blocked. `winprob2-20260910` is published to Supabase with the
  release URL and digest in `artifact_path`; the container pulls and verifies
  it.
- Decision 4's real pause test, last, because restoring a paused project takes
  minutes and the whole database is unavailable meanwhile.

## Known limitations

- **The reference-sync ritual is still manual**, and the worker now refuses to
  serve 14 days after it was last run. That converts session 3's open item
  from a local inconvenience into a production outage on a timer. Automating
  it belongs to the Phase 2 close-out.
- **Every worker restart re-marks an in-flight match INFERRED**
  (`cricketdata.py:594-602` fires whenever `previous is None`, and in-memory
  snapshot state is lost). Correct but lossy, and Railway redeploys on every
  push.
- **The worker does not yet write `deliveries` or `match_states` to Supabase**,
  which §7.1 step 4 wants and the UI will need. Predictions therefore carry
  `delivery_id = NULL` with the ball reference in the payload. Session 5.
- **The 18 training modules still cannot run in a container.** Deliberate —
  they are laptop-only — but it means `python -m features.elo rebuild` will
  never work on Railway, which is the correct outcome and worth stating.

## Things a future session would get wrong

Carried forward into the Phase 2 closeout, in the convention
`docs/phase0-closeout.md` and `docs/phase1-closeout.md` already use.

**Exercise the FAILURE path of anything that touches config, not just the
success path.** This session's boundary check worked on the first try; what
did not was what it *printed*. pydantic's `ValidationError` repr embeds
`input_value`, which for a `BaseSettings` failure is the whole collected
environment, and Railway captures deploy output - so the refusal that proved
the boundary also wrote `SUPABASE_SECRET_KEY` and `LIVE_API_KEY` into a log
that would have outlived the container. No amount of reading `config.py`
finds that. Only running the failing case does. The rule generalises past
config: whenever a component's job is to refuse, the refusal is a code path
with its own behaviour, and testing only that it *refuses* is half the test.

**A test scoped to the import graph is not a test scoped to what runs.**
`serving/app.py` imports `features/match_state.py` for a dataclass; that
module also contains `INSERT INTO match_states`. Both the import-graph check
and the first serving-write guard flagged it, and both were wrong to - the
statement is a module constant only `rebuild()` executes. Scope structural
tests to the code that actually runs, and say in the test why the wider scope
was rejected, or the next person will "fix" it by widening it again.

**Prove a guard catches the thing it names.** The serving-write guard was
verified by injecting an `INSERT INTO match_states` into `serving/app.py` and
confirming it failed with that filename. A guard nobody has seen fail is a
guard nobody knows works.

**A freshness check must measure the clock you mean.** Session 3's refused on
`max(effective_date)` - how recently a MATCH was played - while the question
was how recently the SYNC ran. Those differ by weeks for a periodically
refreshed archive, and the justification ("cricket is played almost daily")
was true and irrelevant. Before enforcing on a timestamp, say out loud which
process advances it.
