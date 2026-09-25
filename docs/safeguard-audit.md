# Safeguard audit

*2026-09-25. Every guard, gate, check, assertion and CI job in the repository,
asked three questions: what failure is it built to catch; has it ever been
observed catching that failure, or only ever observed passing; and can it be
made to fire deliberately.*

## Why this exists

Seven safeguards in this repository have now failed **in the state they exist
to certify**:

| | what it was supposed to catch | how it failed |
|---|---|---|
| `types.ts` staleness job | a generated file drifting | correctly built, never ran — gated on `push`, and 25 commits were unpushed |
| schema parity test | the two databases diverging | enumerated columns/indexes/constraints only; grants, RLS and policies diverged completely and it stayed green |
| vitest `include` pattern | web tests not running | the pattern did not match the directory the tests were in |
| an agent eval case | a wrong answer | asserted `must_call` — that the tool was called, not that the value was right |
| `20260924000003` migration | an unflagged Full Member | asserted a corpus property, so it could not run on an empty database |
| `/matches` verification | stale rows on the page | checked the database, not the built page; a warm `.next/cache` served seven stale rows |
| `verify-session.mjs` | work that never landed | reported the success path as a failure, three lines below printing "pushed" |

That is not a run of bad luck, it is a pattern: **a safeguard is written
against the failure it imagines and validated against the state it is already
in.** The state it exists to certify is, by construction, the one nobody has
produced.

So: for every guard, does a *demonstrated* failure path exist.

---

## The table

Ranked by consequence — what is wrong, and for how long, if the guard is absent
when its failure arrives. Not by count.

**Status:** `PROVEN` = made to fire deliberately, with the failure constructed.
`PROVEN (in anger)` = has caught a real failure in this repository's history.
`UNPROVEN` = only ever observed passing. `UNPROVABLE` = no reachable failure
path as written.

### Tier 1 — silent corruption of served data

| Guard | Failure it targets | Status | How proven |
|---|---|---|---|
| `sql_guard` five layers (`agent_tools/sql_guard.py`) | arbitrary SQL reaching the replica from a model-authored string | **PROVEN** | per-layer non-vacuity: a query only that layer rejects, shown rejected with all layers on and admitted with that one disabled |
| `assert_same_matches` (`models/resolve_outcomes.py`) | the two id spaces — resolving Supabase match 3 (CPL 2026) against corpus match 3 (a 2017 ODI), attaching a real outcome to a different game | **PROVEN — new** | `tests/models/test_identity_guard.py`; guard neutered → **7 of 9 fail**, and the 2 that pass are exactly the two "must not raise" cases |
| `mirror_match_rows` id-space guard | the 2026-09-23 incident: a backfill overwriting live-worker rows | **PROVEN (in anger)** + tested | caused real loss of `team_a`/`team_b`/`venue_id`; `test_refuses_to_mirror_over_a_live_worker_row` |
| anon grants / RLS (`check-anon-access.mjs`, parity `grants`/`rls`) | public write access, or 3.78M deliveries readable from the browser | **PROVEN (in anger)** + **PROVEN — new** | found 13 world-readable tables in 2026-09. Today: `GRANT INSERT ON teams TO anon` on the corpus → `grants` names `('teams','anon','INSERT')`; `DISABLE ROW LEVEL SECURITY` on `venues` → `rls` fails. Both reverted |
| `PARITY_TOLERANCE` (`ingest/replay_log.py`) | the deployed service and the local recomputation returning different probabilities — every logged row wrong, and nothing downstream able to see it | **UNPROVABLE → PROVEN — new** | was inline in a 120-line loop, reachable only by driving a full replay against a wrong service. Extracted to `parity_verdict`; 7 tests, and widening the constant to `1.0` fails exactly the 2 firing tests |
| `ModelVersionChanged` (`ingest/replay_log.py`) | a publish landing mid-replay, splitting the log across two models — `/accuracy` groups by `model_version`, so this is two models' reliability rendered as one | **PROVEN — new** | `tests/ingest/test_model_version_guard.py`; guard neutered → 2 of 4 fail |
| artifact digest (`models/artifact.py`) | serving a model whose bytes are not the registry's | **PROVEN** | `test_digest_mismatch_refuses_and_leaves_nothing_behind` |

### Tier 2 — wrong numbers published as right ones

| Guard | Failure it targets | Status | How proven |
|---|---|---|---|
| `assert_in_test_split` (`eval/splits.py`) | replaying a training-era match into the table `/accuracy` reads — measuring the model on data it was fit to | **PROVEN — new (2026-09-24)** | four pre-window dates and a `str` all refused; a real pre-2025 id refused by the manifest builder |
| refit gate (`eval/calibration_monitor.py`) | a calibrator promoted by a nightly cron because a counter crossed a line | **PROVEN — new (2026-09-24)** | `test_the_gate_holds_even_when_the_floor_is_satisfied` — the incidental-fire scenario exactly |
| `second_innings_predicate` single definition | five copies of the exclusion predicate drifting apart | **PROVEN** | `test_the_exclusion_predicate_has_one_definition` |
| `HistoricalBaseRateBaseline` refusing fitted matches | a baseline scored on the matches it was built from | **PROVEN** | `tests/eval/test_baselines.py` |
| `baselineVerdict` refusing "better" when the CI spans zero | a point estimate on the right side of zero reported as a result | **PROVEN** | `accuracy.test.ts` — and it is what makes today's headline read "not established" |
| ball-key partial unique index | duplicate predictions for one ball — how match 13143 reached 243 rows from 135 distinct payloads | **PROVEN (in anger)** + tested | the 496 keyless rows; `test_ball_key_uniqueness.py` |
| backup restore verification (`ops/backup.py`) | a dump that restores into something which merely looks populated | **PROVEN** | 9 raising tests; row counts plus per-table digests on both sides |

### Tier 3 — build, schema and process

| Guard | Failure it targets | Status | How proven |
|---|---|---|---|
| parity `columns` | a migration applied to one database only | **PROVEN — new** | added `audit_probe_column` to the corpus → named at index 170. Reverted |
| parity `indexes` / `constraints` / `policies` / `publication` | the same, other dimensions | **PROVEN — new** | one probe each on the corpus (`CREATE INDEX`, `ADD CONSTRAINT`, `CREATE POLICY`, `ALTER PUBLICATION ... ADD TABLE`). Each fired **only** its own parametrisation, which also proves the seven are independent. All reverted; 0 probe objects remain |
| `tokens.test.ts` contrast floors | a palette change quietly dropping text below AA | **PROVEN — new** | `--ink-soft` → `#B9B0A0`, `--bat` → `#6FB7C0` → three failures naming both tokens *and* the fill/text-variant cross-check. Reverted |
| `types.ts` staleness (CI) | a generated file drifting from the live schema | **PROVEN** | probe branch |
| agent prompt/tools staleness (CI) | the served route and the eval harness running different agents | **UNPROVEN** | never constructed |
| eval-results staleness and its marker (CI) | `results.json` describing a prompt that no longer exists; and a marker outliving its cause | **PROVEN** | `test_agent_eval_set` asserts the marker disappears once the fingerprints match |
| migration `DO` blocks | a data backfill that silently does nothing | **PARTLY PROVEN** | `full_member` proven in a scratch database (India and Ireland named, Kenya correctly ignored). The stale-`live` guard **can no longer be fired** — see F4 |
| `startup.run_checks` | a serving process with no pinned model | **PROVEN (in anger, today)** | refused to boot with `MODEL_VERSION must be set` when I started uvicorn without it |
| `verify-session.mjs` | work reported as done that never landed | **PROVEN — new (2026-09-24)** | all three paths: explicit baseline = HEAD still fails; post-push reports what landed; an older baseline lists the range |

---

## Findings

### F1. The pre-push hook has never run. Not once.

```
$ git config core.hooksPath
<UNSET>
$ ls .git/hooks/pre-push
absent
```

`.githooks/pre-push` was written on 2026-09-24 to catch the exact class of
failure that let 25 commits drift unpushed. It requires
`git config core.hooksPath .githooks`, run once per clone. **That was never
run.** Every push in this session went around it.

Its own header anticipates this — *"git will not run a committed hook unless it
is pointed at one"* — and then relies on a runbook line nobody executed. This
is the rule-18 shape one turn further out, and the third instance of the same
thing: `verify-session.mjs` exists precisely because the previous safeguard
needed a manual step, and it too runs only when someone remembers.

**Consequence:** every check the hook performs — `types.ts`, the generated
agent files, web typecheck, web tests, a clean tree — is currently performed
only by CI, which is to say only *after* the push. That is the window standing
rule 17 is about.

**Recommendation:** this cannot be fixed by writing a better hook. Either the
setup step becomes part of something already mandatory, or the hook is
abandoned in favour of checks that run unconditionally. A safeguard whose
activation is optional has an activation rate, and here it is zero.

**RESOLVED: `.githooks/` is deleted.**

The hook existed because CI only runs on `push`. But the failure it was built
for was not "CI ran late", it was **25 commits that were never pushed at all**
— and `verify-session.mjs`, which reads HEAD back from git rather than
asserting it, already covers that without needing to be installed.

The reason for deleting rather than keeping it as a nice-to-have is the
important part: **a check requiring manual per-clone activation implies
coverage it does not have.** Its presence in the tree, and its mention in two
session documents, read as "these gates run before every push". They did not
run once. An absent check is a known gap; a check that is present, documented
and inert is a false belief, and the false belief is worse — it is what stops
anyone adding the thing that would have worked.

### F2. CI's web build cannot tell a working page from a fully degraded one

`ci.yml` builds the web app with placeholder Supabase credentials, justified
like this:

> *"Env vars are placeholders: every page is force-dynamic, so nothing is
> prerendered and nothing connects."*

**That is false.** Only `/accuracy` is `force-dynamic`. `/`, `/matches` and
`/players` use `revalidate = 3600`, so they are prerendered at build time and
they do connect.

Constructed and measured, using CI's exact placeholder values:

```
BUILD EXIT=0
✓ Generating static pages (12/12)
matches.html rows: 0
```

**CI is green on a `/matches` that renders zero rows.** Every loader takes its
degradation path — the one deliberately built so that a paused free-tier
database yields a page rather than a 500 — and the build reports success.

This is the same shape as the stale-cache bug from step 1: *verified against
something adjacent to the claim*. The build proves the app compiles. It is
routinely read as proving the pages work.

**RESOLVED (part one): `web/scripts/check-build-output.mjs`.**

Asserts against the built HTML, needing no credentials of its own:

- `/matches` prerenders at least 50 rows (341 today)
- `/players` prerenders at least 20 (50 today)
- the landing figures are **not flagged stale**, and three non-zero figures are
  present

Thresholds are floors an order of magnitude below the real values, not counts.
A check that needs editing every time a match is added gets edited without
being read.

**Proven to fire**, which is the point of the whole audit. Building with CI's
exact placeholder values:

```
BUILD EXIT=0   (green, as CI sees it)
  FAIL  /matches rows: 0, expected at least 50. The page built, and it is empty.
  FAIL  /players rows: 0, expected at least 20. The page built, and it is empty.
  FAIL  the landing figures are flagged stale ("As of ..."), so at least one
        came from the committed fallback rather than the database
  ok    landing figures present and non-zero (largest 3,780,368)
CHECK EXIT=1
```

Note the fourth line passing. The landing figures degrade to **committed**
fallbacks, so "the figures are non-zero" is true on a completely dead build —
which is exactly why the staleness marker is the assertion that matters. Had
the check been written the obvious way it would have been vacuous.

It nearly was, in a second place: the first version stripped HTML tags and
matched `59,229,057,476,575,360` out of a bundle filename in Next's inlined
RSC payload, so the figure test would have passed on a blank page. Script
blocks are now stripped first. **An assertion that cannot fail is the exact
thing this file exists to catch, and one almost shipped inside it.**

**Open item (part two): read-only CI credentials.**

CI builds with placeholders, so the check cannot guard this job yet. It is
wired in now and *skipped loudly* — a `::warning::` on every run, never a pass
— and the build's env takes `secrets.NEXT_PUBLIC_SUPABASE_URL` and friends
when they exist, falling back to placeholders when they do not.

*What it would take:* three repository secrets —
`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` and
`SUPABASE_SECRET_KEY`. The first two are already public by construction (they
ship to the browser). The third is not, and wants a read-only role rather than
the service key. **No code change is needed** — adding the secrets switches
both the build and the check to real data automatically.

The stale comment has been corrected too, but the comment was never the bug.

### F3. Four of seven parity dimensions remain unproven

`columns`, `grants` and `rls` were made to fire today. `indexes`,
`constraints`, `policies` and `publication` never have been. They share a
mechanism with the three that were proven, which is evidence — but the grants
dimension also *looked* fine for months while thirteen tables were
world-readable, and the reason was that it was not being compared at all.
Shared mechanism is exactly what made that invisible.

### F4. One guard is now unprovable by construction

The stale-`live` backfill assertion in `20260924000003` cannot be re-fired: it
is applied, and migrations do not re-run. Its firing was never constructed
before it ran.

This is inherent to one-shot data migrations, and it is a standing rule.

---

**19. A migration's assertions get exactly one chance to be observed. After it
is applied they are documentation. Anything meant to hold CONTINUOUSLY belongs
in a test, not a migration.**

The migration asserts at apply time; the test asserts forever. `20260924000003`
carries two assertions and they fall on opposite sides of that line:

- `full_member` — the invariant is permanent (no Full Member present may be
  unflagged), so it also lives in `tests/db/test_full_member.py`, which runs on
  every suite and was proven to fire in a scratch database.
- the stale-`live` backfill — asserted once, at apply time, and **can never be
  fired again**. Nobody watched it. Its firing behaviour is now unknowable.

We hit the inverse of this boundary the same week, from the other direction:
the first version of the same migration asserted `count(*) = 11`, a property of
the *loaded corpus*, inside a file whose job is to build *schema*. It applied
cleanly to both real databases and broke every fresh-schema build.

So the boundary runs both ways, and it is the same boundary:

| | belongs in a migration | belongs in a test |
|---|---|---|
| asserts about | the schema, and this one transformation | the data, continuously |
| must hold on | an EMPTY database | a LOADED database |
| observed | once, at apply time | on every run |

Putting a continuous invariant in a migration loses it after one run. Putting a
data property in a migration breaks every clean build. Both errors were made
here within days of each other, in the same file.

---

The mitigation for the one-shot half is a scratch database — which is what
caught the empty-database error, and is how `full_member` was proven
afterwards. The stale-`live` assertion never was, and now cannot be.

### F5. The five guards left explicitly unproven, and why

Unprovable and unproven are different statuses, and the distinction decides
what to do about each.

| Guard | Why it has not been fired | Category |
|---|---|---|
Listing them was enough to show that four had no obstacle at all — only that
nobody had done them — so they were done rather than documented. One remains.

| Guard | Why it has not been fired | Category |
|---|---|---|
| agent prompt/tools staleness (`ci.yml`) | the failure IS reachable — edit the Python source and do not re-emit — but the gate lives inside a CI job, so confirming it costs a red build on `main`. Nothing about the check is wrong; the plumbing puts its failure path on the far side of a push | **needs infrastructure we do not have** |
| `20260924000003` stale-`live` assertion | applied; migrations do not re-run. See standing rule 19 | **no longer reachable, by construction** |

**Neither is in the category worth deleting.** That category is "no reachable
failure path *and* the guard does not earn its keep". The one guard that was
genuinely unreachable — `PARITY_TOLERANCE`, inline in a 120-line loop — was
not deleted but **extracted**, because the failure it catches is real and
serious and only its reachability was broken.

That is the decision rule the audit arrived at:

- **Extract** when the guard matters and the plumbing puts its failure out of
  reach. (`PARITY_TOLERANCE` → `parity_verdict`.)
- **Delete** when the guard implies coverage it does not have. (`.githooks/`.)
- **Move to a test** when the assertion must hold continuously rather than
  once. (Standing rule 19.)

The remaining CI staleness gate wants the first of those: move the logic into
a script that can be run and failed locally, and have CI call it — the same
shape as `check-build-output.mjs`, which is now the model for this.

---

## Two lessons from step 1, recorded here because they generalise

### Test the premise, not only the prediction

The proposed explanation for the baseline result was specific and mechanistic:
the model's edge comes from `elo_diff` and the venue features, which carry less
signal in international cricket because Elo spreads are narrower between Full
Members.

Checking only the prediction would have been inconclusive — the Full Member
margin *is* lower than the franchise margin (+0.0077 against +0.0107), which
looks like confirmation. Checking the **premise** settled it in one query: Elo
spreads between Full Members are **wider**, not narrower (mean gap 96.1 against
70.3), because India v Zimbabwe is a bigger mismatch than two IPL sides drafted
to be balanced.

**A plausible mechanism that produces the right-looking outcome for the wrong
reason is harder to catch than a vague guess**, because the outcome check
appears to confirm it. This one would have shipped as page copy. When a
hypothesis names a mechanism, the mechanism is both the cheaper thing to test
and the more decisive.

### The headline claim, and what Step 5 must be built against

> **Clearly better than a historical base rate. Not established against a
> logistic one.**

- vs historical base rate: **+0.0376 [+0.0218, +0.0539]** — better
- vs logistic: **+0.0083 [−0.0027, +0.0188]** — the interval includes zero

**The model did not change.** Same artifact, same sha256, and the refit did not
run in either report. The evidence base tripled — 100 matches to 340 — and a
marginal result did not survive it: the original cleared zero by **0.0014**.

Step 5's landing scorecard must be built against that sentence, not against
`0.1020`, which was both mislabelled "live" and is now stale (`0.1140` over
340). UI-PHASE-2.md §6 has been rewritten accordingly and names the framing it
must not use.

That sentence is the whole reason the accuracy page exists. A page that could
only print good news would not be evidence of anything.
