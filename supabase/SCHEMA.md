# Schema summary

Generated after Phase 0 session 3 (schema migrations), kept current through session 6 (`match_states`/Elo, which closes Phase 0).

Full decision record (why each denormalization/index/RLS choice was made) lives in the Session 3/4/6 sections of the Phase 0 plan. This file is the "what," not the "why."

## The two-database split (SPEC.md §2.1)

Both databases run the **exact same 15-table schema** (verified by `tests/db/test_schema_parity.py`, not by eyeballing `\dt` output), but hold very different amounts of data:

| Database | What it actually holds |
|---|---|
| **Local training Postgres** (docker-compose, port 5433) | The **full** historical corpus — every T20/ODI men's match Cricsheet has, all `deliveries`, all `match_states`, full `elo_ratings` history |
| **Supabase** (hosted) | Live/recent matches only. `deliveries`/`match_states`/`elo_ratings` exist with identical structure but stay near-empty until Phase 2's live worker starts writing to them. `predictions`, `player_state`, `model_versions` are genuinely Supabase-native — they only ever live there |

Schema identity is enforced by applying the same `supabase/migrations/*.sql` files to both via `python supabase/apply_migrations.py`. Realtime/RLS objects (`anon`/`authenticated` roles, `supabase_realtime` publication) are self-bootstrapped in the last migration so the files never need to differ per target (see `20260826180008_realtime_and_rls.sql`).

## Tables

### Reference data (`20260826180001_reference_tables.sql`)

| Table | Purpose |
|---|---|
| `venues` | Canonical venue list. `UNIQUE(name, city)`. |
| `teams` | Canonical team list. `UNIQUE(name)`. |
| `players` | Canonical player list with batting/bowling style, dob. |
| `player_aliases` | `(source, source_name, source_id) -> player_id`, for cross-source name reconciliation (§4.4). A partial unique index on `(source, source_id)` (session 4 followup) makes registry-ID lookup a true exact-match short-circuit — the same registry ID can legitimately appear with different name spellings across match files. Pre-seeded from Cricsheet's own `register/people.csv` (18,468 people) before any match is parsed, so registry IDs are available on the very first encounter, not discovered incrementally. |
| `team_aliases` | Same pattern as `player_aliases`, for team names. |
| `venue_aliases` | Same pattern as `player_aliases`, for venue names. |

### Matches and deliveries (`20260826180002_matches_deliveries.sql`)

| Table | Purpose |
|---|---|
| `matches` | One row per match. `external_ids` JSONB carries the Cricsheet filename; a unique index on `external_ids->>'cricsheet'` makes the loader idempotent. `has_reconciliation_anomaly` (session 5): a mismatch between summed delivery runs and the declared chase target is flagged, not rejected — rejecting loses data that needs a re-parse to recover, a flag is a `WHERE` clause. `target_runs`/`target_overs` (session 6): the chasing innings' own recorded target, persisted from Cricsheet's JSON at load time — authoritative even when DLS-revised, never derived as `innings_1_total + 1` except defensively for the small number of normal/tied matches missing the field outright (see `match_state.py`'s module docstring for the exact counts found in the real corpus). |
| `deliveries` | One row per ball. Beyond §5.2: `batting_team_id`/`bowling_team_id` (denormalized, avoids a join on the largest table for every WPA/training/agent query), `match_date` (denormalized, avoids a join for every temporal filter), `is_super_over` (Cricsheet models a super over as extra innings 3+; flagged so it never silently mixes into normal-chase training data), `wicket_count` (flags the rare double-dismissal ball; the named `wicket_type`/`player_out_id` columns still only carry the primary dismissal). `ball_in_over` is the 1-indexed position within the over counting *every* delivery including wides/no-balls — see the column comment; the legal-ball "x.y" notation would collide with the `UNIQUE(match_id, innings, over_num, ball_in_over)` constraint on every over with an illegal delivery. |

### Derived state (`20260826180003_match_states.sql`)

| Table | Purpose |
|---|---|
| `match_states` | One row per delivery, state *before* that ball — built via SQL window functions in one `INSERT ... SELECT` (`python -m features.match_state rebuild`, ~113s on 3.78M rows), never Python row-by-row, never hand-edited. `match_date` denormalized here too (this table has no other temporal column). `batting_team_won` is nullable; ties/no-results get `NULL` automatically (three-valued SQL logic on `matches.winner`). `has_reconciliation_anomaly` and `is_dls_decided` (session 6) are also denormalized from `matches`, for the same hot-filter reasoning as `match_date`. No rows exist here for super-over deliveries at all — the builder's base CTE filters them out entirely. **The required Phase 1 training filter, to be implemented in `eval/splits.py` (§9.1's only sanctioned home for split logic), is exactly:** `WHERE batting_team_won IS NOT NULL AND NOT has_reconciliation_anomaly` — `is_dls_decided` is available for a sensitivity re-run but is *not* excluded by default, since the result is real. |

### Models and predictions (`20260826180004_model_and_predictions.sql`)

| Table | Purpose |
|---|---|
| `model_versions` | Registry of trained model artifacts, active/shadow flags. |
| `predictions` | Every prediction ever made, logged. The load-bearing table for §8's self-correction system and the accuracy page. Empty until Phase 2/3. |
| `prediction_outcomes` | Resolved actuals + Brier/log-loss per prediction. Empty until Phase 3. |

### Player ability (`20260826180005_player_state.sql`)

| Table | Purpose |
|---|---|
| `player_state` | Bayesian latent ability, one row per `(player_id, format, as_of)` — the full history *is* the form curve. PK doubles as the as-of lookup index. Empty until Phase 5. |

### Entity resolution queue (`20260826180006_unresolved_entities.sql`)

| Table | Purpose |
|---|---|
| `unresolved_entities` | Generic below-threshold queue for players/teams/venues (§4.4 step 4). Reworked in session 4 (`20260827000001_entity_resolution_followups.sql`) from single `best_candidate_id`/`best_score` columns to a `candidates` JSONB array (top-3, with scores), `first_seen_match_id`, and a `reason` code — a reviewer needs more than one number to decide. Real corpus result: 0 players queued (registry-ID resolution is terminal, session 5's root-cause fix), 44 venues, 0 teams (2 resolved via the review CLI as genuinely distinct franchises). |

### Team strength (`20260826180007_elo_ratings.sql`)

| Table | Purpose |
|---|---|
| `elo_ratings` | Elo as a time series (§6.1) — one row per team per format per completed match, never a single current-rating column. `elo_as_of(conn, team_id, format, date)` (`api/src/features/elo.py`) is the only sanctioned read path for any later phase's `elo_diff` feature; it returns the 1500 default before a team's first match, never `NULL`. Team renames (Delhi Daredevils→Capitals, Kings XI Punjab→Punjab Kings, RCB Bangalore→Bengaluru, +3 more display-name variants) need no Elo-specific handling — they're represented as `team_aliases` rows pointing at one continuous `team_id`, so Elo sees unbroken history automatically. `python -m features.elo rebuild`, ~44s on the real corpus. |

### Realtime + RLS (`20260826180008_realtime_and_rls.sql`)

No new tables — enables RLS with anonymous/authenticated `SELECT` policies on `predictions`, `matches`, `players`, `venues`, `teams` (the tables a browser reads directly), and adds `predictions` to the `supabase_realtime` publication. No write policies for `anon`/`authenticated` anywhere; all writes go through the service-role key, which bypasses RLS by design.

## Verifying this stays true

```powershell
python supabase/apply_migrations.py                # apply migrations to both DBs
pytest tests/db/test_schema_parity.py -v            # confirm they're still identical
python -m features.match_state rebuild              # rebuild match_states (idempotent)
python -m features.elo rebuild                      # rebuild elo_ratings (idempotent)
pytest tests/features/test_match_state.py -v        # off-by-one, phase boundaries, parity
pytest tests/features/test_elo.py -v                 # idempotency, as-of lookup, rename continuity
```
