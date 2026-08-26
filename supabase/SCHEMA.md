# Schema summary

Generated after Phase 0 session 3 (schema migrations). Review this before Session 4 loads any data — changing the schema after a million-row bulk load is expensive.

Full decision record (why each denormalization/index/RLS choice was made) lives in the Session 3 section of the Phase 0 plan. This file is the "what," not the "why."

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
| `player_aliases` | `(source, source_name, source_id) -> player_id`, for cross-source name reconciliation (§4.4). |
| `team_aliases` | Same pattern as `player_aliases`, for team names. |
| `venue_aliases` | Same pattern as `player_aliases`, for venue names. |

### Matches and deliveries (`20260826180002_matches_deliveries.sql`)

| Table | Purpose |
|---|---|
| `matches` | One row per match. `external_ids` JSONB carries the Cricsheet filename; a unique index on `external_ids->>'cricsheet'` makes the loader idempotent. |
| `deliveries` | One row per ball. Beyond §5.2: `batting_team_id`/`bowling_team_id` (denormalized, avoids a join on the largest table for every WPA/training/agent query), `match_date` (denormalized, avoids a join for every temporal filter), `is_super_over` (Cricsheet models a super over as extra innings 3+; flagged so it never silently mixes into normal-chase training data), `wicket_count` (flags the rare double-dismissal ball; the named `wicket_type`/`player_out_id` columns still only carry the primary dismissal). `ball_in_over` is the 1-indexed position within the over counting *every* delivery including wides/no-balls — see the column comment; the legal-ball "x.y" notation would collide with the `UNIQUE(match_id, innings, over_num, ball_in_over)` constraint on every over with an illegal delivery. |

### Derived state (`20260826180003_match_states.sql`)

| Table | Purpose |
|---|---|
| `match_states` | One row per delivery, state *before* that ball. Rebuilt from `deliveries` by a single idempotent command — never hand-edited. `match_date` denormalized here too (this table has no other temporal column). `batting_team_won` is nullable; ties/no-results get `NULL`, and the training-time exclusion of those rows lives in `eval/splits.py` (Phase 1), not a schema constraint — the rows are still needed for replay/WP-curve/agent queries. No rows exist here for super-over deliveries at all. |

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
| `unresolved_entities` | Generic below-threshold queue for players/teams/venues (§4.4 step 4). Acceptance target: fewer than 50 rows after Session 5's bulk load. |

### Team strength (`20260826180007_elo_ratings.sql`)

| Table | Purpose |
|---|---|
| `elo_ratings` | Elo as a time series (§6.1) — one row per team per format per completed match, never a single current-rating column. `elo_as_of(team_id, format, date)` is the only sanctioned read path for any later phase's `elo_diff` feature. |

### Realtime + RLS (`20260826180008_realtime_and_rls.sql`)

No new tables — enables RLS with anonymous/authenticated `SELECT` policies on `predictions`, `matches`, `players`, `venues`, `teams` (the tables a browser reads directly), and adds `predictions` to the `supabase_realtime` publication. No write policies for `anon`/`authenticated` anywhere; all writes go through the service-role key, which bypasses RLS by design.

## Verifying this stays true

```powershell
python supabase/apply_migrations.py       # apply migrations to both DBs
pytest tests/db/test_schema_parity.py -v  # confirm they're still identical
```
