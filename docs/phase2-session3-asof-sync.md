# Phase 2, Session 3 — as-of summaries on Supabase

**Date:** 2026-09-15
**Closes:** SPEC.md §15's 2026-09-10 decision. Railway deployment (Session 4) is no longer blocked.

## What was blocking deployment

Three of the model's fourteen features are as-of aggregates over the full
corpus, and every one of them read a table that §2.1 keeps local:

| Feature | Read | Lives |
|---|---|---|
| `venue_chase_win_rate` | `match_states` + `matches` | local only |
| `venue_avg_first_innings` | `deliveries` + `matches` | local only |
| `elo_diff` | `elo_ratings` | local only (and empty on Supabase) |

A worker on Railway had nothing to compute from. The fix is two derived
tables at the same `(entity, date, cumulative value)` grain, built locally and
synced — and the whole risk is that the new implementation silently disagrees
with the one the model was trained against.

## Decision 1 — the grain, and the real row counts

A time series, not a snapshot: **one row per (entity, date), carrying the
cumulative state including that date**. A lookup takes the latest row strictly
before `as_of_date`, which accumulates exactly the matches with
`start_time < as_of_date`, because any match in between would itself be a
breakpoint.

| Table | §15's estimate | Actual |
|---|---|---|
| `venue_asof_summary` | ~13,000 | **10,508** |
| `elo_asof_summary` | (was to be a raw `elo_ratings` sync, 25,662) | **25,290** |

The date key is `(start_time AT TIME ZONE 'UTC')::date`, not the session's
`::date`. Two corpus properties make that lossless, and both are now asserted
as tests rather than assumed: **no match starts off midnight UTC** (0 of
13,143), and **`deliveries.match_date` equals the key for every row** (0
mismatches). Comparing `date < date` also removes a timezone hazard the
previous `timestamptz < date` comparison carried — Postgres resolves that by
casting the date to midnight *in the session timezone*, so a Supabase project
on a non-UTC timezone would have shifted every serving lookup by hours.

`chase_wins` is stored as `NUMERIC` at scale 1 rather than as an integer
count. `avg(numeric)` is `numeric_div(sum, count)`, and `numeric_div`'s result
scale depends on its operands' dscale, so storing `3` instead of `3.0` can
change the last digits. This is the `::numeric` lesson from session 1 applied
before it bit rather than after.

## Decision 2 — the N=10 floor is applied at read time

The summary stores raw counters and has no opinion about the floor. Three
reasons: it is a hyperparameter and changing it should not require a rebuild;
it was already written in two places (training passed it explicitly, serving
relied on the default) and baking it into the table would have made a third;
and 24–33% of matches hit this path, so the two must not be able to disagree.
There is now exactly one `10` in the codebase, `venue_stats.MIN_VENUE_MATCHES`,
and every default points at it.

`elo_as_of` has no floor — it has a sentinel, `STARTING_RATING = 1500.0`. That
also stays at read time and deliberately does **not** acquire a `None` path:
the model was trained on `1500.0` for cold-start teams and `NaN` for cold-start
venues, and those are different facts.

## Decision 3 — one helper, two backends

```
            features/as_of.py :: compute_as_of_features(conn, ...)
                      |   the single entry point; normalises as_of_date -> date
                      |   `conn` is the ONLY thing that differs between paths
        +-------------+--------------+
        v                            v
  features/elo.py            features/venue_stats.py
    elo_as_of                  venue_chase_win_rate_as_of
      -> elo_asof_summary        venue_avg_first_innings_as_of
      -> 1500.0 if no row          -> venue_asof_summary
                                   -> None below MIN_VENUE_MATCHES
  callers:
    models/win_prob_2nd.py::_match_level_features   conn = LOCAL_DATABASE_URL
    ingest/replay.py (re-exports the name)          conn = LOCAL_DATABASE_URL
    Phase 4 Railway worker                          conn = SUPABASE_SESSION_POOLER_URL

  oracle, gate + rebuild only, never on a serving path:
    venue_chase_win_rate_as_of_direct / venue_avg_first_innings_as_of_direct
    elo_as_of_direct
```

`compute_as_of_features` moved out of `ingest/replay.py` into
`features/as_of.py` so training does not import from `ingest`. Before this,
training called the four primitives itself and serving called the wrapper —
two entry points, which is how they came to differ by the *type* of
`as_of_date` each passed without anything failing.

## Decision 4 — staleness

| Trigger | Catches | Limit |
|---|---|---|
| Content hash | Partial/failed sync, truncation, manual edits, corruption | None — detectable from Supabase alone |
| Corpus-freshness bound | "Rebuilt locally, sync never ran" | A proxy, not a proof |

Being precise about the second one, because it has a real limit: a worker that
can only read Supabase **cannot prove the sync ran**. Stale data and its stale
hash are mutually consistent. The freshness bound infers staleness from the
fact that cricket is played almost daily — newest breakpoint older than 14
days refuses, 7 days warns. It takes the *older* of the two tables' newest
dates, so one falling behind is not masked by the other.

`assert_reference_fresh` raises `StaleReferenceData`, which is fatal rather
than degrading: serving a win probability from reference data that doesn't
match what the model was trained on produces a number that looks fine and is
wrong. Seven tests cover it, including a dropped row, an edited value, a
missing state row and an out-of-date breakpoint.

The sync itself verifies: it pushes, **recomputes the hash on the Supabase
side**, and rolls back without recording anything if it disagrees. That check
fired on its first run — see the findings below.

## Decision 5 — sizing, and §2.1

| Table | Rows | Size |
|---|---|---|
| `elo_asof_summary` | 25,290 | 2,912 kB |
| `venue_asof_summary` | 10,508 | 1,928 kB |
| `reference_sync_state` | 2 | 32 kB |
| **Total** | | **4.76 MB** |

Supabase is at 25 MB of 500 MB. **§2.1 is tightened by this session, not
loosened.** Nothing from the corpus moved: `deliveries` (3.78M),
`match_states` (3.78M), `matches` (13,143) and `elo_ratings` (25,662) all stay
local. `elo_ratings` in particular — the original plan was to sync it, and it
isn't, because its `match_id` is `NOT NULL REFERENCES matches(match_id)` and
Supabase's `matches` is the live worker's own write target with a `SERIAL`
`match_id`. Syncing 13,143 corpus matches would have interleaved corpus and
live ids in one space with nothing distinguishing them. What crosses is two
tables whose entire content is `(entity, date, value)` — serving data by
purpose, which is §2.1's actual test.

## THE GATE

```
layer 1 venue: 1,820,384 pairs,    0 mismatches
layer 1 elo:   1,940,352 triples,  0 mismatches
layer 2 venue:    10,508 lookups,  0 divergences
layer 2 elo:      25,899 lookups,  0 divergences
layer 3: both summary tables byte-identical in both databases
11 passed in 374.55s
```

Layer 1 is the full **cross product** — every venue × every one of the 5,216
corpus dates, every (team, format) × every corpus date — not merely the pairs
that occur. That matters: restricting it to real pairs would never test a
lookup landing *between* two breakpoints, which is the one thing the
"latest row strictly before" rule has to get right.

Layer 2 runs the actual Python helpers over every lookup training performs,
asserting `==` rather than `pytest.approx`. Stated plainly, since it is the
one place the gate is not literally what it claims: layer 2's oracle is the
set-based SQL form of the direct helper, materialised once, not 12,088
separate Python calls — those are full corpus scans and would run for hours,
and a gate that takes hours gets deselected, which is how gates rot. It is
closed two ways: the oracle is built from the same module-level SQL fragments
`venue_stats.py`'s `*_direct` functions execute, and a deterministic 200-pair
sample runs those Python functions against it.

Layer 3 diffs both tables row by row across the two databases. Given identical
contents and identical SQL — the helper is the same function object, handed a
different connection — the returned values are identical by construction.

## What the gate found

Three real defects, none of which review would have caught.

**1. `elo_as_of` was non-deterministic.** Every `start_time` is midnight, so a
team playing twice on one date writes two `elo_ratings` rows with an identical
`as_of`, and `ORDER BY as_of DESC LIMIT 1` chose between them by heap order.

- 372 tied groups; for **137** of them the shipped helper returned the
  *earlier* match's rating — the pre-second-match value.
- 140 of 25,899 training lookups resolved to the wrong side.
- **162 of 12,916 matches (1.3%) had their `elo_diff` change**, mean 9.2 Elo
  points, max 24.2.

Fixed with `, match_id DESC` (`recompute_format` walks matches
`ORDER BY start_time, match_id`, so the highest `match_id` on a date is that
date's last match). Phase 1's registered `winprob2-20260910` was trained with
the ambiguous ordering for those 162 matches. **Not retrained on this finding
alone** — the effect is small against a feature whose scale is hundreds of
points — but the training run was not bit-reproducible before this fix and now
is, so whether to re-register belongs with the next retrain. Collapsing to a
date grain is the only reason anyone looked.

**2. Serving returned rounded Elo ratings from Supabase.** `real`'s TEXT
output depends on `extra_float_digits`, psycopg decodes in text mode, and
Supabase's pooler hands out sessions with `0` where local Postgres uses the
12+ default of `1`. The identical stored float4 came back to Python as
`1496.445` locally and **`1496.44`** from Supabase. The stored bits were
identical — the server-side hash matched — so only layer 3's comparison of the
*decoded Python values* caught it. Fixed by storing `NUMERIC`.

Two further traps surfaced while fixing it, both verified rather than assumed:

- `float4::numeric` is hardcoded to `FLT_DIG` (6) significant digits and
  ignores `extra_float_digits` entirely — it turns `1601.9048` into `1601.9`.
  The rebuild casts via `::text::numeric` under a pinned setting instead.
- `date`'s output depends on `DateStyle`, so content hashes render each column
  explicitly rather than using `row::text`.

**3. The sync's own verification fired on its first run**, rolling back
`elo_asof_summary` because the recomputed Supabase hash disagreed with the
local one — which is exactly what it exists to do, and is how finding 2 was
found.

## Files

| File | Change |
|---|---|
| `supabase/migrations/20260915000001_asof_summaries.sql` | New. Both summaries + `reference_sync_state` |
| `api/src/features/asof_summary.py` | New. Rebuild job, `DerivedTable`, content hashing |
| `api/src/features/as_of.py` | New. `compute_as_of_features`, `assert_reference_fresh`, `StaleReferenceData` |
| `api/src/features/venue_stats.py` | Summary-backed helpers; direct versions retained as `*_direct`; shared predicate fragments; `MIN_VENUE_MATCHES` |
| `api/src/features/elo.py` | `elo_as_of` reads the summary; `elo_as_of_direct` added with the tie-break |
| `api/src/models/win_prob_2nd.py` | `_match_level_features` delegates to the single helper |
| `api/src/ingest/replay.py` | Re-exports `compute_as_of_features` |
| `api/src/ingest/sync_reference_tables.py` | `sync_derived_table`: replace, verify the remote hash, then record |
| `tests/db/test_asof_parity.py` | New. The three-layer gate |
| `tests/features/test_asof_summary.py` | New. Date grain, Elo tie-break, staleness refusal |
| `tests/features/test_elo.py`, `test_venue_stats.py` | Poison pills now rebuild the summary *with the pill in it* |
| `tests/conftest.py` | New tables added to `TABLES_TO_RESET` |
| `supabase/SCHEMA.md`, `SPEC.md` §15 | The ritual, the decision, the two findings |

## The ritual

```powershell
python supabase/apply_migrations.py
python -m features.match_state rebuild
python -m features.elo rebuild
python -m features.asof_summary rebuild      # ~27s
python -m ingest.sync_reference_tables       # verifies the hash on Supabase
pytest tests/db/test_asof_parity.py -v       # ~6 min, full corpus, both DBs
```

Order matters: `asof_summary` reads `match_states` and `elo_ratings`, and the
sync refuses to run for a derived table with no local `reference_sync_state`
row.

## Open

- **Not automated.** The rebuild-and-sync is a manual ritual. Wiring it into a
  scheduled job belongs with the Phase 2 close-out; until then the worker's
  14-day freshness bound is the backstop.
- **Whether to re-register Phase 1's model** after the Elo tie-break fix.
  Measured impact is above; the recommendation is to fold it into the next
  retrain rather than trigger one.
- **The gate is local-only.** It needs the full corpus and credentials to both
  databases, so it skips in CI, like `tests/db/test_schema_parity.py`.
- **One migration-history wrinkle, recorded so nobody is surprised.**
  `20260915000001` originally declared `rating REAL` and was applied to both
  databases before finding 2 surfaced. Rather than add a second migration for
  a same-session correction, the file was amended to `NUMERIC` and both
  databases were brought to it with an explicit `ALTER COLUMN`. A fresh clone
  applying the file lands on the same schema, and
  `tests/db/test_schema_parity.py` confirms the two live databases already
  have. The alternative — a follow-up `ALTER` migration — would have been
  more faithful history for one unreleased day's work.
