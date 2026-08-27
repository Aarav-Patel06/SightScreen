# Phase 0 close-out

Data foundation, closed. 7 sessions. This is the reference for what got decided and why — read the "things a future session would get wrong" section before touching anything in `api/src/ingest/`, `api/src/features/`, or `eval/splits.py`.

## Final numbers

| Metric | Value |
|---|---|
| Deliveries | 3,780,368 (target: >1,000,000) |
| Matches | 13,143 (10,574 T20, 2,569 ODI — men's only) |
| `match_states` rows | 3,779,439 (see "why not 3,780,368" below) |
| `elo_ratings` rows | 25,662 (20,728 T20, 4,934 ODI) |
| Players | 18,468 (seeded from Cricsheet's `register/people.csv`) |
| Teams / Venues | 350 / 349 |
| Unresolved queue | 0 players, 0 teams, 44 venues (pending, reviewed by you) |
| Local training DB size | ~1.2 GB |
| Supabase (reference tables only) | ~5.5 MB of ~500 MB cap |
| `match_states` rebuild time | 112.7s, isolated |
| Elo rebuild time | 44.1s, isolated |
| Full corpus load time | ~23 min (two passes) |

## Decision log by session

**Session 1 — env/secrets scaffolding.** Fail-fast config on both sides (`pydantic-settings`, a hand-rolled TS module) instead of scattered `os.environ`/`process.env` reads. `.gitignore` verified before any secret could enter history.

**Session 2 — local Postgres + Supabase wiring.** `docker-compose.yml` pinned to `postgres:17.6` — the *exact* version Supabase reported via `select version()`, not just the major version, so training and serving can never drift apart in a way that looks like a data bug. Discovered mid-session that Supabase's direct connection host (`db.<ref>.supabase.co`) is IPv6-only and unreachable from Docker's bridge network or Railway — everything goes through Supavisor pooler hosts instead, split into session-mode (5432, migrations/long-running) and transaction-mode (6543, serverless). `config.py` validates this at startup (rejects a `db.` host, rejects a bare `postgres` username on a pooler URL).

**Session 3 — schema migrations.** Two-database split enforced by applying identical migrations to both (`supabase/apply_migrations.py`), verified by an actual test (`tests/db/test_schema_parity.py`), not `\dt` eyeballing. Self-bootstrapping migrations (`anon`/`authenticated` roles, `supabase_realtime` publication created only where missing) so the same file works on bare local Postgres and hosted Supabase with zero per-target branching. `ball_in_over` redefined from the conventional "x.y" over notation (which collides on every wide/no-ball) to a plain sequential position within the over. Four tables added beyond §5 (`elo_ratings`, `unresolved_entities`, `team_aliases`, `venue_aliases`) since the spec named these concepts without giving DDL.

**Session 4 — entity resolution.** Registry-ID-first design, RapidFuzz fallback, tri-state outcome (auto-resolve / auto-create / queue) biased hard toward "queue" over "guess" — thresholds set well above §4.4's literal "~85" on your explicit instruction that a wrong merge is worse than a missed one. 20 golden tests written before the implementation.

**Session 5 — bulk load.** Root cause of an entire queue's worth of false ambiguity, found by diagnosing before changing anything: a *present* registry ID that simply hadn't been seen before was treated the same as *no* registry ID, so it fell through to fuzzy/collision logic that a real registry ID should have short-circuited entirely. Fixed by making a present-but-unmatched `source_id` terminal. Then closed the actual order-dependence by pre-seeding the whole registry (`people.csv`, 18,468 people) before any match is parsed. Result: 290,268 player resolutions, 100% by registry ID, 0% fuzzy.

**Session 6 — `match_states` + Elo, closing Phase 0.** SQL window functions, one `INSERT...SELECT`, no Python row-by-row. Off-by-one caught with a hand-verified fixture, not a plausibility check. `matches.target_runs`/`target_overs` persisted at load time — Cricsheet's own recorded target is authoritative, DLS-revised or not (a real target of 70 off 6 overs is nowhere near `innings_1_total + 1`). Elo needs zero rename-specific code: a rename is a `team_aliases` row pointing at one continuous `team_id`, confirmed empirically (Delhi Capitals' Elo history reaches back to 2008, not 2019).

**Session 7 — closing out.** Generated types, CI staleness check, reference-table sync, final backup, this document.

## Things a future session would get wrong without knowing them

**`ball_in_over` is not the "x.y" over notation.** It's the 1-indexed position of a delivery within its over counting *every* delivery, legal and illegal. A wide followed by its re-bowled replacement are both real rows and both need a value — under the conventional notation they'd share one, violating `UNIQUE(match_id, innings, over_num, ball_in_over)`. If you ever reparse Cricsheet JSON by hand instead of reusing `cricsheet.py`'s loader, get this wrong and every over with an extra in it fails to load.

**Why `match_states` has 3,779,439 rows, not 3,780,368.** The difference is exactly 929 — every super-over delivery. Cricsheet models a super over as extra innings (3, 4, ...) in the same match; `deliveries.is_super_over` flags them, and `match_state.py`'s base CTE filters `WHERE NOT is_super_over` before anything else runs. No `match_states` row has ever existed for a super-over ball, and none should. If a future rebuild ever produces a different number here, check that filter first, not the window functions.

**Phase boundaries scale with the innings' own length, not a hardcoded overs table.** T20's 1–6/7–15/16–20 and ODI's 1–10/11–40/41–50 are the *unreduced* boundaries — internally they're fractions (0.30/0.45 for T20, 0.20/0.60 for ODI) applied to `scheduled_balls`, which comes from `target_overs` for a chase or the format's nominal length otherwise. A rain-reduced 14-over T20 chase gets powerplay ending at over `round(14×0.30)=4`, not over 6. If you ever see a reduced-overs match with a powerplay that looks wrong, check `matches.target_overs` before assuming the phase logic is broken.

**The BPL/LPL lineage question was answered by declining to answer it, on purpose.** 3 renames are seeded (Delhi Daredevils→Capitals, Kings XI Punjab→Punjab Kings, RCB Bangalore→Bengaluru) plus 3 low-stakes display-name variants. A fuzzy-name × non-overlapping-date-range audit of the real 350-team corpus surfaced ~30-40 more candidate pairs, heavily concentrated in BPL and LPL, where the franchise model is city-slot re-licensing to new ownership groups — not a corporate rebrand. Some of those pairs are almost certainly the same lineage; some are almost certainly not; nobody on this project has the administrative history to tell which, and fuzzy string similarity alone can't either (it already produced a false positive on a case you personally confirmed were different teams: MI Cape Town vs Cape Town Blitz, and independently Gujarat Titans vs Gujarat Lions). **Do not auto-link these based on a future fuzzy pass.** If you want to resolve any of them, it has to come from actual research into who owned what, case by case — the same discipline session 4's entity resolution applies to identity, applied here to lineage. Until then, each restarts Elo at 1500, which is an honest cold start, not corrupted data.

**Registry-ID-present-but-unseen is terminal — nothing gets to second-guess it.** If you ever touch `entity_resolution.py`'s `_resolve` function, do not let any code path run fuzzy scoring, surname-collision checks, or temporal-plausibility checks after a `source_id` is confirmed present with no existing alias. That is definitionally a new, real, distinct person per Cricsheet's own registry — treating it with the same suspicion as a nameless string is the exact bug that produced 2,079 false-positive queue entries in a 2,000-match sample before it was found and fixed.

**`has_reconciliation_anomaly` means flagged, never rejected.** A mismatch between summed delivery runs and the declared chase target is real but not always corruption — a "no result" match can have a revised target with no `outcome.method` key at all, and a slow-over-rate penalty run isn't reflected in any delivery event. These matches load with the flag set. **`eval/splits.py` (Phase 1, doesn't exist yet) must exclude them from training** — the exact required predicate, also documented in `match_state.py`'s docstring and `supabase/SCHEMA.md`, is:
```sql
WHERE batting_team_won IS NOT NULL AND NOT has_reconciliation_anomaly
```
`is_dls_decided` is available on the same table for a sensitivity re-run but must **not** be excluded by default — the result is real, DLS or not.

**The full corpus never leaves local Postgres, and that's not an oversight.** At 3.78M delivery rows, pushing them to Supabase would be ~740MB against a 500MB free-tier cap — over budget before counting anything else. Only the small reference tables (`venues`/`teams`/`players`/the three alias tables`) sync to Supabase, via `sync_reference_tables.py`, run once after the load, not incrementally. `deliveries`/`matches`/`match_states`/`elo_ratings` exist on Supabase with identical schema but stay near-empty until Phase 2's live worker starts writing *live/recent* matches specifically. If a future session wants to query the full historical corpus from a Vercel/Railway-deployed service, it isn't there — that's local-only, on purpose, per SPEC.md §2.1.

**Fuzzy matching essentially never fires in this corpus, and that's correct, not untested-and-lucky.** 290,268 player resolutions, 100% by registry ID. 197 team/venue resolutions out of ~330,000 went through fuzzy — almost all venues (punctuation/city-suffix variants), a handful of teams. This near-zero rate is the *design working*, not a coverage gap in disguise — but it does mean the fuzzy path's only real validation is the 21-case golden test set (`tests/ingest/test_entity_resolution.py`), not this load. Phase 2's live-API name reconciliation (a second source, no shared registry) is where it gets its first real workout against data this loader never exercised it against. Don't mistake "it didn't fire" for "it's proven."

**Rebuilds are idempotent commands, not scripts you patch.** `python -m features.match_state rebuild` and `python -m features.elo rebuild` both truncate/delete and fully recompute from `deliveries`/`matches` every time — there is no incremental-update code path for either, on purpose (§2's "never hand-edited, never incrementally patched"). If Phase 1 wants a new `match_states` column, it goes in the `INSERT...SELECT`, and you rerun the whole thing (112.7s — cheap enough that there's no reason to build incremental logic).

**Shared test connections must use `autocommit=True`.** Two real hangs/false-failures this phase came from a module-scoped `conn` fixture reused across many read-only tests without autocommit: the first left a transaction open whose lock then blocked a later test's `TRUNCATE` indefinitely (a genuine multi-minute hang, found via `pg_stat_activity`, not assumed); the second was a fingerprint query that included a `BIGSERIAL` primary key, which gets fresh values every rebuild even when nothing meaningful changed. If you write a new test file with a shared connection fixture, use `autocommit=True` and exclude surrogate keys from any "is it identical" comparison.
