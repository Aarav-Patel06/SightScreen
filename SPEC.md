# Cricket Prediction Platform — Build Specification

**Version:** 1.0
**Owner:** Aarav
**Status:** Source of truth. Update this document when decisions change; do not let the code and this file diverge.

---

## 0. Read this first (for the coding agent)

This document is the authoritative reference for the project. When implementing:

- **Follow the phase order in §11.** Do not build later phases before earlier ones pass their acceptance criteria.
- **Never train or evaluate with a random train/test split.** All splits are temporal. See §9.
- **Never update model weights in response to a single wrong prediction.** See §8 for the correct self-correction design.
- **All numbers shown to a user must carry an uncertainty or sample-size qualifier.** No bare point estimates.
- When a section says "must," it is a hard requirement. When it says "should," deviation is allowed with a comment explaining why.

---

## 1. What we are building

A cricket analytics platform that predicts match outcomes and individual player performance, updates live during a match, and improves measurably over time.

Four user-facing surfaces:

| Surface | Purpose |
|---|---|
| **Live match** | Score, win probability, player predictions, matchups, impact leaderboard |
| **Upcoming** | Pre-match predictions, predicted XI, venue profile, matchups to watch |
| **Player** | AI-summarised form, strengths grid, next-innings prediction, scouting report |
| **Agent** | Natural-language Q&A over the full ball-by-ball database and all models |

Plus one non-obvious surface that is a core differentiator:

| **Model accuracy page** | Public track record — calibration, recent misses, whether it is improving |

### 1.1 What makes this different from Cricbuzz / ESPNCricinfo

Those apps show a win probability bar and nothing else. Three things separate this project:

1. **Win probability is the unit of account.** Every ball's probability swing is attributed to players, producing win-probability-added (WPA) — a live player-of-the-match race that reflects actual impact, not just runs scored.
2. **Player-level predictive distributions**, not raw career stats. Every player number is a forecast with an uncertainty band.
3. **Published accountability.** The system logs every prediction, scores itself against outcomes, and shows users its own track record.

### 1.2 Non-goals

Explicitly out of scope. Do not build these:

- Betting odds, stake recommendations, or anything framed as gambling advice
- Beating the broadcast feed on latency (impossible on a student budget — see §4.3)
- Live video, highlights, or commentary reproduction
- Fantasy team optimisation as a primary feature (may emerge later from player predictions)
- Test match win probability in v1 (draws make it a three-outcome problem; defer)

---

## 2. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| **Backend language** | Python 3.11+ | Ingestion, features, training, inference |
| **Backend API** | FastAPI | Prediction service + agent tool endpoints |
| **Database** | Supabase (managed Postgres) | Ball-by-ball store, prediction log, agent SQL target |
| **Local training DB** | Postgres 16 in Docker | See §2.1 — training data stays local |
| Queries | `supabase-py` for CRUD, raw SQL via `psycopg` for analytics | Skip an ORM; the analytics queries are all hand-written SQL |
| Dataframes | Polars | Faster than pandas on ball-level tables |
| Models | LightGBM | Win probability, ball outcome, score projection |
| Calibration | scikit-learn `IsotonicRegression` | |
| Bayesian player model | NumPy, hand-rolled Kalman update | Do not pull in PyMC/Stan; overkill and slow |
| **Frontend** | Next.js 15 (App Router) + React + TypeScript | |
| **BFF / agent orchestration** | Next.js API routes (Node.js, TypeScript) | Anthropic SDK streaming, session handling |
| Charts | Recharts | Win probability curve, form curves |
| Realtime | Supabase Realtime (`postgres_changes`) | Replaces Redis pub/sub and SSE entirely |
| **LLM** | Anthropic API (Claude) | Agent, scouting reports, match narratives |
| **Version control / CI** | GitHub + GitHub Actions | Also runs all scheduled jobs — see §2.3 |
| **Frontend deploy** | Vercel | Canonical pairing with Next.js |
| Backend deploy | Railway (or Fly.io) | Required for the live worker — see §2.2 |
| Monitoring | `/health` endpoint + stdout logs + GitHub Actions run history | Don't over-engineer |

### 2.1 Two databases, on purpose

Supabase's free tier caps the database at 500 MB. Rough sizing: `deliveries` at ~1M rows with indexes lands near 200 MB, and `match_states` is wider still at ~300 MB. Add the `predictions` table, which grows every ball of every match you track, and you blow through the free tier before you have a working model.

The split that solves this without feeling like a hack:

| Database | Contents | Why |
|---|---|---|
| **Local Postgres** (Docker) | Full historical corpus, `match_states`, training pipelines, backtests | Unlimited, free, and much faster for the repeated full-table scans that model training does |
| **Supabase** | Live and recent matches, `predictions`, `player_state`, `model_versions`, and read-only views for the agent | Hosted, has Realtime, reachable from Vercel |

Trained model artifacts are committed to GitHub Releases (or Supabase Storage) and pulled by the serving container. Training never touches Supabase; serving never touches local Postgres.

~~If you later want the agent to query the full historical corpus, either upgrade Supabase to Pro ($25/mo) or restrict v1 to T20 only, which roughly halves the row count and fits comfortably.~~

**Superseded, Phase 6 session 1 — and the sizing sentence above was wrong twice.** T20 is 2,417,673 of 3,780,368 deliveries, which is **64%**, not roughly half; and at the corpus's measured density T20-only comes to **457 MB against 465 MB of Supabase free space**. That is not "comfortably", it is 8 MB of headroom before `predictions` grows at 79 kB/match — and the only way to shrink it further is to drop the `batter_id`/`bowler_id` indexes, which turns every matchup question into a sequential scan of 2.4M rows that the 5 s statement timeout then kills. It is not a cheaper option, it is a broken one.

So the corpus gets a **third home**: its own Postgres service, holding `deliveries`, `matches`, `players`, `teams` and `venues` and **not** `match_states`. Built and measured at **577 MB**.

The deciding argument is blast radius, not the $300/yr. Filling the *serving* database to ~79% to host an analytical replica puts the live product at risk: at the 500 MB cap, predictions stop being written and both the match page and the accuracy page break. A separate replica isolates that completely — if it fills, the agent degrades and serving is untouched.

**The replica is not a third member of the schema-parity regime.** It is a derived, rebuildable projection — the same category as the as-of summaries, just larger. Its schema is created by `api/src/agent_tools/replica.py`'s own bootstrap, *not* by `supabase/migrations/`, so `apply_migrations.py` keeps two targets and `test_schema_parity.py` keeps comparing exactly two databases. Excluding `match_states` also keeps its four `REAL` columns exempt from the NUMERIC conversion, since nothing serving-side would write them.

`AGENT_SQL_ROLE_DB_URL` correspondingly gets the *inverse* of the Supavisor host rule the two Supabase fields carry: a Supabase host in that variable is now rejected outright, because pointing the agent's read-only role at the serving database is precisely the blast radius this split exists to avoid.

### 2.2 What Vercel cannot do

Vercel runs serverless functions. It cannot host a process that polls an API every 15 seconds for four hours. Hobby-tier function timeout is 10 seconds, and Vercel Cron on hobby is limited to once-daily schedules. There is no configuration that makes this work.

So:

| Component | Host |
|---|---|
| Next.js frontend + API routes + agent orchestration | **Vercel** |
| FastAPI prediction service | **Railway** (or Vercel Python functions if you keep it strictly stateless) |
| Live polling worker | **Railway** — one small always-on container, ~$5/mo |

One always-on container is the unavoidable cost of a live product. Don't fight it; note it in your README as a deliberate architectural decision, because "I knew serverless was wrong for this workload" is a good interview answer.

### 2.3 GitHub Actions replaces the scheduler

Every scheduled job in §8 is daily or slower, which is exactly what Actions cron is good at. This removes APScheduler and any need for a persistent scheduler process.

| Workflow | Schedule | Job |
|---|---|---|
| `calibration.yml` | Daily 03:00 UTC | Report reliability; refit only if a candidate beats identity (§8.1) |
| `drift.yml` | Weekly Monday | Segment bias detection (§8.2) |
| `retrain.yml` | Biweekly | Retrain + shadow evaluation (§8.4) |
| `ci.yml` | On push | Tests, lint, type check |

Free tier gives 2,000 Actions minutes/month, which is ample. Run history is your job log for free, and a failed calibration run emails you automatically.

### 2.4 Connection pooling — read this before writing any connection code

Supabase exposes **three** connection paths, and the naive choice fails outright rather than degrading.

Direct connections (`db.<ref>.supabase.co:5432`) resolve over **IPv6 only** unless you buy the IPv4 add-on (~$4/mo). Railway has no outbound IPv6, and neither does Docker's default bridge network. So the direct connection string is unusable for this project. It will present as a DNS failure, not a connection error, which is confusing the first time.

Use the pooler for everything:

| Caller | Path | Port |
|---|---|---|
| Vercel functions, anything serverless | Supavisor **transaction** mode | 6543 |
| Railway live worker, Railway FastAPI | Supavisor **session** mode | 5432 |
| Migrations, admin scripts, local tooling | Supavisor **session** mode | 5432 |

Both use the same host pattern, and note the username changes — this is the detail everyone misses:

```
postgresql://postgres.<PROJECT_REF>:<PASSWORD>@aws-0-<REGION>.pooler.supabase.com:5432/postgres
```

The user is `postgres.<ref>`, not `postgres`. Copy the exact string from the dashboard's **Connect** button rather than assembling it by hand; the region prefix varies.

Mode differences that matter:

- **Transaction mode (6543)** hands a backend connection to a client only for the duration of a query. Prepared statements are unavailable — disable them in your client. Session state does not reliably reset between clients, so never rely on `SET`, temp tables, or session-scoped settings here.
- **Session mode (5432)** holds a backend connection for the life of the client connection, behaving like a direct connection. Use it anywhere you need session state, prepared statements, or transactions spanning multiple statements — which includes all migrations and the agent's `SET LOCAL statement_timeout`.

Pool size is shared across both modes. On the free tier that ceiling is low, so do not open connections speculatively.

---

## 3. Repository structure

Monorepo, single GitHub repository. Vercel deploys only `web/`; Railway deploys only `api/`.

```
cricket-engine/
├── README.md
├── SPEC.md                        <- this document
├── docker-compose.yml             <- local postgres for training
├── .github/
│   └── workflows/
│       ├── ci.yml
│       ├── calibration.yml        <- daily
│       ├── drift.yml              <- weekly
│       └── retrain.yml            <- biweekly
├── supabase/
│   └── migrations/                <- Supabase CLI migrations (SQL files)
├── api/                           <- Python backend, deploys to Railway
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── src/
│       ├── ingest/
│       │   ├── cricsheet.py       <- historical bulk loader
│       │   ├── live_client.py     <- live API adapter (interface + impls)
│       │   ├── replay.py          <- replays a historical match as if live
│       │   └── entity_resolution.py
│       ├── features/
│       │   ├── match_state.py     <- builds the per-ball state row
│       │   ├── dls.py             <- DLS resource table lookup
│       │   └── elo.py             <- team strength ratings
│       ├── models/
│       │   ├── win_prob_2nd.py    <- second innings classifier
│       │   ├── score_proj_1st.py  <- first innings score projection
│       │   ├── ball_outcome.py    <- multinomial delivery model
│       │   ├── simulate.py        <- Monte Carlo innings simulation
│       │   ├── player_ability.py  <- Bayesian latent ability + Kalman update
│       │   └── registry.py        <- model versioning, load/promote
│       ├── selfcorrect/
│       │   ├── calibration.py     <- daily isotonic refit (Actions)
│       │   ├── drift.py           <- weekly segment bias detection (Actions)
│       │   └── retrain.py         <- biweekly retrain + shadow eval (Actions)
│       ├── serving/
│       │   ├── app.py             <- FastAPI app
│       │   ├── live_loop.py       <- the polling worker (separate process)
│       │   ├── wpa.py             <- win probability added attribution
│       │   └── narrative.py       <- LLM-written summaries
│       ├── agent_tools/
│       │   ├── routes.py          <- tools exposed as FastAPI endpoints
│       │   └── sql_guard.py       <- SQL validation and sandboxing
│       ├── db/
│       │   ├── local.py           <- training DB connection
│       │   └── supabase.py        <- serving DB connection
│       └── eval/
│           ├── splits.py          <- temporal split logic (single source of truth)
│           ├── metrics.py         <- brier, log loss, reliability
│           └── report.py          <- generates the evaluation writeup
├── web/                           <- Next.js, deploys to Vercel
│   ├── package.json
│   ├── app/
│   │   ├── match/[id]/page.tsx    <- live match page
│   │   ├── upcoming/page.tsx
│   │   ├── player/[id]/page.tsx
│   │   ├── accuracy/page.tsx      <- public model track record
│   │   ├── ask/page.tsx           <- agent chat
│   │   └── api/
│   │       ├── agent/route.ts     <- Anthropic SDK, streams, calls Python tools
│   │       └── revalidate/route.ts
│   ├── components/
│   ├── lib/
│   │   ├── supabase.ts            <- client + realtime subscriptions
│   │   └── types.ts               <- shared types, generated from Supabase schema
│   └── tsconfig.json
├── notebooks/                     <- exploration only, never imported by api/src
└── tests/
```

Generate `web/lib/types.ts` with `supabase gen types typescript` and commit it. Regenerate on every migration — this gives you end-to-end type safety from Postgres column to React prop, which is one of the genuine wins of this stack.

---

## 4. Data sources

### 4.1 Historical (training) — Cricsheet

- URL: `https://cricsheet.org/downloads/`
- Format: JSON per match, or bulk zips per competition
- Coverage: all internationals plus IPL, BBL, PSL, CPL, The Hundred, county, and more
- Licence: openly licensed for non-commercial use — check the current terms and cite it in your README
- Cost: free

**Download these to start:** all T20 (internationals + leagues) and all ODI. Skip Tests for v1.

Expected volume: IPL alone is roughly 1,100 matches × ~240 balls ≈ 250k deliveries. Full T20 + ODI corpus lands in the low millions of rows. Postgres handles this comfortably on a laptop.

### 4.2 Live — polling API

Pick one. All are freemium.

| Provider | Entry cost | Notes |
|---|---|---|
| **Cricket Data (formerly CricAPI)** — cricketdata.org | Free tier; paid from ~$5.99/mo | **Chosen, but has no usable ball-by-ball** — `match_bbb` returns only penalty/extras deliveries and has no wicket field. Used via snapshot reconstruction. See §15. |
| **Sportmonks** — sportmonks.com/cricket-api | 14-day free trial; 3,000 calls/hour on all tiers | Cleaner schema, better docs, costs more |
| **Roanuz** — cricketapi.com | Paid | Good IPL coverage |

**Requirement:** wrap whichever you pick behind a `LiveClient` interface with methods `list_live_matches()`, `get_match_state(match_id)`, `get_deliveries_since(match_id, last_ball)`. You will switch providers at some point; make it a one-file change.

**Do not scrape Cricbuzz or ESPNCricinfo.** Their endpoints are undocumented, break without warning, and scraping violates their terms. A demo that dies mid-interview is worse than a $6/month line item.

### 4.3 Latency reality — must be surfaced in the UI

- Your poll interval: 10–20 seconds
- Provider lag behind actual play: 5–30 seconds
- TV broadcast is itself 20–40 seconds behind the ground

Net: your prediction appears **30–60 seconds after the ball is bowled.**

**Provider lag is unmeasurable on CricketData.** No endpoint carries a per-ball timestamp, so the ball-bowled → provider-publish leg cannot be observed at all. Only poll → write is measurable.

The UI must therefore not display a false-precision figure like "42s behind live." Show the measured half and be honest about the unknown half: "updates every 15s · provider lag not published." Revisit only if a provider exposing ball timestamps is adopted. This is honesty, and it also positions the product clearly as analytics rather than a betting tool.

### 4.4 Entity resolution — the hidden time sink

Cricsheet and your live API will not agree on names. `"V Kohli"` vs `"Virat Kohli"` vs `"Kohli, V"`. Same for venues and teams.

Build `entity_resolution.py` in Phase 0, not later. Approach:
1. Canonical `players` table with an internal `player_id`
2. `player_aliases` table mapping `(source, source_name, source_id) -> player_id`
3. Fuzzy match on first ingest (RapidFuzz, token-set ratio, threshold ~85)
4. Anything below threshold goes to a `unresolved_entities` table for manual review
5. Never silently create a duplicate player

Budget two full days for this. Everyone underestimates it.

---

## 5. Database schema

Core tables. Write these as Supabase CLI migrations (plain SQL files under `supabase/migrations/`), applied to both the local training database and Supabase so the two schemas never diverge.

### 5.1 Reference tables

```sql
CREATE TABLE venues (
  venue_id        SERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  city            TEXT,
  country         TEXT,
  UNIQUE (name, city)
);

CREATE TABLE teams (
  team_id         SERIAL PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  short_name      TEXT
);

CREATE TABLE players (
  player_id       SERIAL PRIMARY KEY,
  canonical_name  TEXT NOT NULL,
  batting_hand    TEXT,          -- 'RHB' | 'LHB' | NULL
  bowling_style   TEXT,          -- 'RF' | 'LFM' | 'OB' | 'SLA' | 'LB' | ...
  dob             DATE
);

CREATE TABLE player_aliases (
  alias_id        SERIAL PRIMARY KEY,
  player_id       INT REFERENCES players(player_id),
  source          TEXT NOT NULL,   -- 'cricsheet' | 'cricketdata' | ...
  source_name     TEXT NOT NULL,
  source_id       TEXT,
  UNIQUE (source, source_name)
);
```

### 5.2 Match and delivery tables

```sql
CREATE TABLE matches (
  match_id        SERIAL PRIMARY KEY,
  external_ids    JSONB NOT NULL DEFAULT '{}',   -- {'cricsheet': '...', 'cricketdata': '...'}
  competition     TEXT NOT NULL,
  format          TEXT NOT NULL,                 -- 'T20' | 'ODI'
  venue_id        INT REFERENCES venues(venue_id),
  start_time      TIMESTAMPTZ NOT NULL,
  team_a          INT REFERENCES teams(team_id),
  team_b          INT REFERENCES teams(team_id),
  toss_winner     INT REFERENCES teams(team_id),
  toss_decision   TEXT,                          -- 'bat' | 'field'
  winner          INT REFERENCES teams(team_id), -- NULL if no result
  result_method   TEXT,                          -- 'normal' | 'dls' | 'tie' | 'no_result'
  status          TEXT NOT NULL                  -- 'scheduled' | 'live' | 'complete'
);
CREATE INDEX ON matches (start_time);
CREATE INDEX ON matches (status);

CREATE TABLE deliveries (
  delivery_id     BIGSERIAL PRIMARY KEY,
  match_id        INT NOT NULL REFERENCES matches(match_id),
  innings         SMALLINT NOT NULL,             -- 1 | 2
  over_num        SMALLINT NOT NULL,             -- 0-indexed
  ball_in_over    SMALLINT NOT NULL,
  legal_ball_num  SMALLINT NOT NULL,             -- cumulative legal balls in innings
  batter_id       INT REFERENCES players(player_id),
  non_striker_id  INT REFERENCES players(player_id),
  bowler_id       INT REFERENCES players(player_id),
  runs_batter     SMALLINT NOT NULL DEFAULT 0,
  runs_extras     SMALLINT NOT NULL DEFAULT 0,
  extra_type      TEXT,                          -- 'wide'|'noball'|'bye'|'legbye'|NULL
  wicket_type     TEXT,                          -- 'bowled'|'caught'|'lbw'|...|NULL
  player_out_id   INT REFERENCES players(player_id),
  UNIQUE (match_id, innings, over_num, ball_in_over)
);
CREATE INDEX ON deliveries (match_id, innings, legal_ball_num);
CREATE INDEX ON deliveries (batter_id);
CREATE INDEX ON deliveries (bowler_id);
```

### 5.3 Derived state table

This is what the models consume. One row per delivery. Rebuild from `deliveries` — never hand-edit.

```sql
CREATE TABLE match_states (
  delivery_id           BIGINT PRIMARY KEY REFERENCES deliveries(delivery_id),
  match_id              INT NOT NULL,
  innings               SMALLINT NOT NULL,
  -- state BEFORE this delivery is bowled
  score                 SMALLINT NOT NULL,
  wickets               SMALLINT NOT NULL,
  balls_bowled          SMALLINT NOT NULL,
  balls_remaining       SMALLINT NOT NULL,
  target                SMALLINT,                -- NULL in innings 1
  runs_required         SMALLINT,                -- NULL in innings 1
  current_run_rate      REAL,
  required_run_rate     REAL,
  rrr_minus_crr         REAL,
  partnership_runs      SMALLINT,
  partnership_balls     SMALLINT,
  balls_since_wicket    SMALLINT,
  phase                 TEXT NOT NULL,           -- 'powerplay'|'middle'|'death'
  batter_runs_so_far    SMALLINT,
  batter_balls_faced    SMALLINT,
  dls_resources_pct     REAL,
  -- outcome label (filled after match completes)
  batting_team_won      BOOLEAN
);
CREATE INDEX ON match_states (match_id, innings);
```

### 5.4 Model and prediction tables

```sql
CREATE TABLE model_versions (
  model_version   TEXT PRIMARY KEY,              -- 'winprob2-2026-09-14'
  model_type      TEXT NOT NULL,
  trained_at      TIMESTAMPTZ NOT NULL,
  train_end_date  DATE NOT NULL,
  test_brier      REAL,
  test_log_loss   REAL,
  is_active       BOOLEAN NOT NULL DEFAULT FALSE,
  is_shadow       BOOLEAN NOT NULL DEFAULT FALSE,
  artifact_path   TEXT NOT NULL
);

CREATE TABLE predictions (
  prediction_id   BIGSERIAL PRIMARY KEY,
  match_id        INT NOT NULL REFERENCES matches(match_id),
  delivery_id     BIGINT REFERENCES deliveries(delivery_id),  -- NULL for pre-match
  model_version   TEXT NOT NULL REFERENCES model_versions(model_version),
  prediction_type TEXT NOT NULL,      -- 'win_prob'|'score_proj'|'batter_runs'|'bowler_econ'
  subject_id      INT,                -- player_id for player predictions
  payload         JSONB NOT NULL,     -- {'p': 0.73} or {'mean': 51, 'p50plus': 0.38}
  match_phase     TEXT NOT NULL,      -- 'pre_toss'|'post_toss'|'innings1'|'break'|'innings2'
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON predictions (match_id, created_at);
CREATE INDEX ON predictions (model_version, prediction_type);

CREATE TABLE prediction_outcomes (
  prediction_id   BIGINT PRIMARY KEY REFERENCES predictions(prediction_id),
  actual          JSONB NOT NULL,
  brier           REAL,
  log_loss        REAL,
  resolved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**The `predictions` table is the most important non-obvious piece of infrastructure in this project.** It is what makes §8 possible and what powers the accuracy page. You cannot backfill it. Build it in Phase 3 at the latest.

### 5.5 Player state table

```sql
CREATE TABLE player_state (
  player_id           INT NOT NULL REFERENCES players(player_id),
  format              TEXT NOT NULL,
  as_of               DATE NOT NULL,
  bat_ability_mean    REAL,       -- latent skill, standardised
  bat_ability_sd      REAL,
  bowl_ability_mean   REAL,
  bowl_ability_sd     REAL,
  innings_observed    INT NOT NULL,
  PRIMARY KEY (player_id, format, as_of)
);
```

Keep the full history of `as_of` rows — that history *is* the form curve shown on the player page.

---

## 6. Models

### 6.1 Team strength (Elo)

Simple and cheap. Compute before anything else; it feeds every other model.

- Standard Elo, K=20, starting rating 1500, separate ratings per format
- Update after every completed match
- Store as a time series so you can look up a team's rating *as of* any past match (critical to avoid leakage)

### 6.2 Second-innings win probability (the workhorse)

**Type:** LightGBM binary classifier + isotonic calibration
**Target:** `batting_team_won`
**Granularity:** one prediction per delivery

**Features (must be computable from state before the delivery):**

| Feature | Source |
|---|---|
| `balls_remaining` | match_states |
| `wickets_in_hand` | 10 − wickets |
| `runs_required` | match_states |
| `required_run_rate` | match_states |
| `current_run_rate` | match_states |
| `rrr_minus_crr` | match_states |
| `target` | match_states |
| `partnership_runs`, `partnership_balls` | match_states |
| `balls_since_wicket` | match_states |
| `phase` | categorical |
| `striker_ability`, `non_striker_ability` | player_state as of match date |
| `remaining_batting_ability` | sum of ability of yet-to-bat players |
| `current_bowler_ability` | player_state |
| `elo_diff` | Elo as of match date |
| `venue_chase_win_rate` | rolling, computed from prior matches only |
| `venue_avg_first_innings` | rolling, prior matches only |
| `dls_resources_pct` | DLS table lookup |

**Critical:** every rolling/aggregate feature must be computed using only data available *before* the match in question. Write a single helper `as_of(date)` and route all such lookups through it.

**Calibration:** fit `IsotonicRegression` on the validation split, store the fitted object with the model artifact. Serving must apply it.

### 6.3 First-innings score projection

**Type:** LightGBM regressor predicting final innings total, plus quantile regressors at 10th/90th percentile for the band
**Features:** same state features minus the chase-specific ones, plus `batting_ability_remaining`
**Output:** `{mean, p10, p90}`

Convert to a win probability by comparing the projected distribution against the venue's historical chase-success curve. This is approximate and should be labelled lower-confidence in the UI.

### 6.4 Ball outcome model (powers simulation and player predictions)

**Type:** LightGBM multiclass
**Classes:** `{0, 1, 2, 3, 4, 6, wicket, wide, noball}`
**Features:** striker ability, bowler ability, phase, `batter_balls_faced` (settling effect), match state, venue, batting hand vs bowling style

This one model produces:
- Monte Carlo win probability (simulate the rest of the innings 5,000 times)
- Projected final score
- Batter's expected final runs and P(50+)
- Bowler's expected economy for the next over and P(wicket)

Simulation must run in under 200ms for 5,000 sims. Vectorise with NumPy; do not loop ball by ball in Python.

### 6.5 Player latent ability (Bayesian, updates every innings)

Each player carries `(mean, sd)` per format for batting and bowling ability.

**Update rule (Kalman-style), run after every completed innings:**

```
# 1. Process noise: skill drifts between innings
sd_prior² = sd_prev² + q * days_since_last_innings

# 2. Observation: this innings' performance, standardised against
#    expected performance given conditions (opposition, venue, phase)
obs, obs_sd = standardise_innings(innings_stats, conditions)

# 3. Bayesian update
k        = sd_prior² / (sd_prior² + obs_sd²)
mean_new = mean_prior + k * (obs - mean_prior)
sd_new   = sqrt((1 - k) * sd_prior²)
```

Two properties this gives you for free, both of which matter:
- A debutant's estimate moves a lot; an established player's barely moves. Correct.
- The `sd` widens during a layoff and narrows with play, so the UI can show a genuine confidence band.

Tune `q` (drift rate) by maximising held-out predictive likelihood. Start at a value that lets ability drift meaningfully over a season but not over one innings.

**Note:** this is *not* the same as §8's self-correction. This is normal Bayesian belief updating about a player, and it is correct to do per-innings.

### 6.6 Win probability added (WPA)

For each delivery: `wpa = P(win | after ball) − P(win | before ball)`.

Attribution rules:
- Runs off the bat → credit the striker, debit the bowler
- Wicket → debit the dismissed batter, credit the bowler (and fielder if catch/run-out; split 70/30 bowler/fielder for catches, 0/100 for run-outs)
- Extras → debit the bowler only
- Dot balls → small credit to bowler, small debit to striker

Sum per player per match for the live impact leaderboard. Sum per player per season for a "most valuable player" table that will look nothing like the runs-scored table — which is the point.

---

## 7. Live pipeline

### 7.1 The loop

Runs as a separate worker process per live match.

```
every 15 seconds:
  1. state = live_client.get_match_state(match_id)
  2. if state.ball_count == last_seen: sleep; continue          # most polls are no-ops
  3. new_deliveries = live_client.get_deliveries_since(match_id, last_seen)
  4. insert into deliveries; rebuild affected match_states rows
  5. predictions = predict_all(current_state)
        - win probability (calibrated)
        - projected score (innings 1) or none (innings 2)
        - striker expected final runs, P(50+), P(out this over)
        - bowler expected economy next over, P(wicket)
        - WPA delta for the ball just bowled
  6. write every prediction to the predictions table
  7. write to Supabase; Realtime broadcasts the insert to subscribed clients
  8. last_seen = state.ball_count
```

**Rate limit budget:** at 15s intervals, one match is 240 calls/hour. Sportmonks' 3,000/hour ceiling allows ~12 concurrent matches. Cricket Data's free tier is much tighter — check your quota and back off to 30s intervals if needed.

### 7.2 Phase transitions

The worker must emit explicit events, because the UI narrative depends on them:

| Event | Trigger | Action |
|---|---|---|
| `pre_toss` | Match created | Predict from Elo, venue, squads |
| `toss` | Toss result arrives | Re-predict with bat/field known |
| `innings1_start` | First ball | Switch to score projection model |
| `innings_break` | Innings 1 ends | **Target locked.** Switch to win prob model. Biggest accuracy jump. |
| `innings2_ball` | Every delivery | Full prediction set |
| `match_end` | Result known | Resolve all predictions, score them, trigger player ability updates |

### 7.3 Replay mode (build this in Phase 2, not later)

`replay.py` takes a completed match and feeds it through the identical pipeline at configurable speed.

Why this matters more than it sounds:
- You can develop and demo without waiting for a live match
- It gives you deterministic integration tests
- It is how you generate the win-probability curve for any historical match on demand

The live client and the replay client must implement the same interface, so the rest of the system cannot tell them apart.

### 7.4 Delivery to the browser

**Supabase Realtime**, which removes the need for Redis, SSE endpoints, and a relay layer entirely.

The Railway worker writes predictions to Supabase. The browser subscribes directly to inserts on that table:

```ts
const channel = supabase
  .channel(`match:${matchId}`)
  .on('postgres_changes', {
    event: 'INSERT',
    schema: 'public',
    table: 'predictions',
    filter: `match_id=eq.${matchId}`,
  }, (payload) => applyPrediction(payload.new))
  .subscribe()
```

The write to Postgres *is* the broadcast. No second code path can drift out of sync with the first, which is the real reason to prefer this over a pub/sub layer.

Two requirements:
- Enable Realtime on the `predictions` table in the Supabase dashboard, and add it to the `supabase_realtime` publication in a migration so it isn't a click someone forgets.
- Add row-level security allowing anonymous `SELECT` on `predictions` and `matches`. Without RLS policies, Realtime silently delivers nothing to anonymous clients — a failure mode that looks exactly like a broken subscription.

On page load, fetch current state via a normal Next.js server component query, then subscribe for deltas. Don't try to reconstruct history from the realtime stream.

---

## 8. Self-correction system

**The wrong design, which you must not build:** model predicts 70%, team loses, model adjusts weights. In cricket a 30% outcome happens three times in ten. Punishing the model for individual surprises teaches it to hedge everything toward 50%.

Mistakes carry information **in aggregate**, not individually. Four layers:

### 8.1 Calibration monitor — daily

**Phase 1 finding that governs this job:** on roughly 650 matches of held-out data, every calibration method tested (global isotonic, phase-stratified isotonic, global Platt, phase-stratified Platt) scored *worse* than no calibration at all. An unconditional refit would actively degrade the model. Recalibration is a candidate that must earn its place, never a scheduled certainty.

```
1. Pull all resolved predictions from the trailing 12 months
2. Bucket by predicted probability (deciles)
3. For each bucket: predicted mean vs observed rate, with a
   MATCH-CLUSTERED CI (see §9.3 — raw pp deviation is not a valid test)
4. Report the reliability table regardless of what happens next
5. Split the window temporally: fit candidate maps on the earlier part,
   evaluate on the later part
6. Candidates must include IDENTITY. Fit isotonic and Platt as challengers.
7. Promote a new calibrator ONLY if it beats identity on the held-out
   later part, by a paired match-clustered margin whose CI excludes zero
8. Otherwise keep identity and log that it won again
9. Alert if any decile's clustered CI excludes its predicted mean
```

Steps 1–4 are the monitoring, which always runs and always has value. Steps 5–8 are the refit, which usually should decline to act. Do not conflate them: a job that reports honestly and changes nothing is succeeding.

The reliability table from step 4 is what users see on the accuracy page, and it should show real miscalibration where real miscalibration exists. Phase 1 closed with 4 of 10 test deciles failing the clustered check — that is disclosed debt, and hiding it behind a cosmetic transform would defeat the purpose of the page.

### 8.2 Segment drift detection — weekly

Slice residuals by: venue, competition, format, match phase, chasing vs defending, team.

For each segment with n ≥ 100 predictions:
- Compute mean residual (actual − predicted)
- Binomial test against zero
- Flag any segment significant at p < 0.01

A flagged segment means one of two things: a missing feature, or a genuine regime change (new pitch, rule change, tournament conditions). Route flags to a review queue; do not auto-correct. Human judgment decides whether to add a feature or add a bias term.

### 8.3 Player ability updates — per innings

Covered in §6.5. This runs automatically at `match_end`.

### 8.4 Scheduled retrain with shadow deployment — biweekly

```
1. Retrain on all data up to (today − 30 days)
2. Evaluate on the most recent 30 days, which the new model has never seen
3. Compare Brier and log loss against the current active model on the same window
4. If new model is better by a margin exceeding its standard error:
      mark is_shadow = TRUE, run alongside active for 7 days
5. After shadow period, if still better on live traffic: promote
6. Otherwise: discard, log why
```

**Never promote a model because it is newer.** Every promotion must have a recorded reason with numbers.

### 8.5 The accuracy page (user-facing)

Renders directly from `predictions` joined to `prediction_outcomes`:

- Reliability diagram: predicted probability vs observed frequency, with n per bucket
- Brier score over time, broken out by match phase
- "Biggest misses" — the five most confident wrong predictions in the last month, with a link to the match
- Comparison against the two baselines from §9.2, so users can see the model actually adds value

No cricket app does this. Ship it.

---

## 9. Evaluation protocol

### 9.1 Splits — non-negotiable

```
Train:      matches with start_time <= 2023-12-31
Validation: 2024-01-01 to 2024-12-31   (calibration fitting, hyperparameters)
Test:       2025-01-01 onwards          (touched once, at the end)
```

All splits are **by match, by date**. A random ball-level split leaks the match outcome into training through other deliveries in the same match and will produce a meaningless ~95% accuracy. `eval/splits.py` is the only place split logic may live; every training script imports from it.

### 9.2 Required baselines

You must report against both:

1. **Three-feature logistic regression** on `required_run_rate`, `wickets_in_hand`, `balls_remaining`
2. **Historical base rate** — the observed win rate of all past chases at a similar state (bin by runs required / balls remaining / wickets)

If LightGBM does not clearly beat both, that is a finding worth reporting honestly. Do not hide it.

### 9.3 Metrics and targets

**Targets are relative to the measured baseline, not absolute.** The original absolute figures in this spec were too pessimistic and have been corrected against real measurements from Phase 1 Session 1.

Measured baselines on the test split (three-feature logistic, innings 2):

| Bucket | Logistic baseline |
|---|---|
| Overall | 0.1342 |
| Final 3 overs | 0.0925 |

Why the innings-wide average is lower than it intuitively should be: a chase contributes up to 120 rows, and the late ones are near-determined states where Brier is 0.02–0.05. Averaging across the innings pulls the mean well below the ~0.25 of an opening-ball coin flip. This is a property of the row distribution, not of model quality — never read a low innings-wide Brier as evidence of a good model.

| Metric | Target |
|---|---|
| Brier, second innings, overall | ≤ 0.125, and must beat the logistic baseline by more than the standard error |
| Brier, final 3 overs | ≤ 0.085 |
| Brier, start of chase | ~0.25 (correct — it is near a coin flip) |
| Brier, first innings | Establish a baseline first; do not carry over a guessed figure |
| Calibration | Every decile's observed win rate must be consistent with its predicted mean, judged by a match-clustered 95% CI on the observed rate — not by a raw percentage-point deviation |

**Do not judge calibration by raw pp deviation per decile.** `n` counts balls, and balls within a match are correlated, so 200 balls may be 5 effective observations. A 3pp gap at that sample size is often noise. Compute each decile's observed rate with a match-clustered interval and ask whether it contains the predicted mean.

**Calibration must be conditional on match phase, not global.** A single monotone map over [0,1] assumes miscalibration depends only on predicted probability. It does not: a prediction of 0.7 arises both early in a chase (genuinely uncertain, often overconfident) and late (usually reliable). A global map merges those populations into one bin and mis-corrects both. Fit separately by phase bucket, or use a method that takes `balls_remaining` as an input.

**Identity is a legitimate candidate.** LightGBM trained on log loss over millions of rows is often already well calibrated, and isotonic can add variance without reducing bias. If no calibration method beats identity on held-out data, ship identity and record why.

**Selecting a calibration method requires two held-out chunks.** Fitting and selecting on the same validation data is circular. Split validation temporally: fit candidate maps on the earlier portion, select among them on the later portion, then touch test once.

**Standard error must be computed by bootstrapping over matches, not over balls.** Balls within a match are highly correlated, so treating them as independent understates the standard error by roughly an order of magnitude and will make a noise-level improvement look significant. Effective sample size is closer to the match count than the row count.

Measured baseline CIs (match-clustered, 2000 resamples): overall `[0.1264, 0.1419]`, final 3 overs `[0.0806, 0.1041]`.

**Model comparison uses a PAIRED bootstrap, not overlapping CIs.** Do not ask whether the model's point estimate falls outside the baseline's interval — that is the unpaired test and it is far too conservative here. Both models score the same matches, so resample matches once and compute the *difference* in Brier on each resample. Match-level difficulty cancels, and the paired interval is typically several times tighter than either model's individual CI.

The criterion: the 95% CI of (baseline Brier − model Brier), paired and match-clustered, lies entirely above zero. This same paired test governs shadow-model promotion in §8.4 — never promote on a comparison of independent intervals.

Any model that fails to beat the baseline by a match-clustered significant margin is not an improvement, regardless of how close to target its absolute number looks.

### 9.4 Player predictions — set expectations correctly

Individual innings scores are roughly geometrically distributed. **R² on individual innings runs will be near zero and that is not a bug.** Do not build or advertise a point predictor for "how many runs will X score."

Evaluate distributions instead:
- Calibration of P(50+): of all innings where you said 30%, did ~30% reach 50?
- CRPS (continuous ranked probability score) against the actual score
- Season-aggregate correlation: predicted vs actual season runs, which *should* be strong

State this limitation explicitly in the UI and in your writeup. Knowing what you cannot predict is a stronger signal than claiming you can.

---

## 10. Agent layer

### 10.1 Purpose

Natural-language Q&A over the full ball-by-ball database and all model outputs. This is the highest-value interview piece because it is genuinely hard to fake — it needs a real fact base underneath.

Example queries it must handle:
- "How does Rashid Khan bowl to left-handers in the powerplay?"
- "Who has the highest WPA in this IPL season?"
- "Is Jaiswal in form right now?"
- "What's the win probability in the India match and why did it move?"

### 10.1a Language split

The agent spans both halves of the stack, deliberately:

| Concern | Where | Why |
|---|---|---|
| Anthropic SDK loop, streaming to client | `web/app/api/agent/route.ts` (TypeScript) | Streaming a token feed to React is far cleaner from a Next.js route handler than proxying it through Python |
| Tool *execution* | FastAPI endpoints in `api/src/agent_tools/` (Python) | Tools need the models, the feature code, and `sqlglot` |
| SQL validation | Python only | Security-critical code must not be duplicated in two languages |

The TypeScript route receives a tool-use block from Claude, POSTs to the corresponding FastAPI endpoint, and feeds the result back into the loop. Authenticate that internal call with a shared secret header — the FastAPI service must never accept tool calls from the open internet.

Do not reimplement the SQL guard in `node-sql-parser` to keep everything in TypeScript. Two validators means two chances to disagree, and the one that disagrees is the vulnerability.

### 10.2 Tools

| Tool | Signature | Notes |
|---|---|---|
| `query_ball_data` | `(sql: str) -> rows` | Read-only. See §10.3 for guards. |
| `get_live_prediction` | `(match_id: int) -> dict` | Current full prediction set |
| `get_player_form` | `(player_id: int, format: str) -> dict` | Ability curve + next-innings distribution |
| `get_matchup` | `(batter_id, bowler_id, filters) -> dict` | Head-to-head with sample size |
| `resolve_entity` | `(name: str, kind: str) -> id` | Name → internal id. Agent must call this first. |

### 10.3 SQL safety — mandatory

Text-to-SQL is a well-known injection surface. Five layers, all required:

1. **Separate Supabase Postgres role** with `SELECT` only, granted on a small set of read-only views — not the base tables. No `INSERT`/`UPDATE`/`DELETE`/DDL grants at all. Create it in a migration, connect the agent tool with its own connection string, and never let it reuse the service-role key. This is the layer that actually protects you; the rest are defence in depth.
2. **Parse, don't regex.** Use `sqlglot` to parse the query into an AST. Reject anything that isn't a single `SELECT`. Regex blocklists are trivially bypassed.
3. **Statement timeout:** `SET LOCAL statement_timeout = '5s'` on the connection.
4. **Forced `LIMIT`:** rewrite the AST to append `LIMIT 1000` if absent.
5. **Reject multiple statements** — no semicolon-separated batches.

Log every generated query. You will want them for debugging and they make a good appendix in your writeup.

**As built (Phase 6 session 1).** Two of the five layers are enforced by the database and three by Python, and `agent_tools/sql_guard.py`'s `LAYERS` registry records which is which — so nobody can "cover" layer 1 with a Python assertion that proves nothing about the grants.

| Layer | Enforced by | Proven by |
|---|---|---|
| `readonly_role` | database | `replica.py --verify`, against a live replica |
| `single_statement` | python | `tests/agent/test_sql_guard_adversarial.py` |
| `single_select` | python | same |
| `statement_timeout` | database | `replica.py --verify` |
| `forced_limit` | python | `tests/agent/test_sql_guard_adversarial.py` |

**Every layer has a non-vacuity proof**, because "five layers" is a claim and a five-layer defence where layer 2 never fires alone is a four-layer defence with a comment. For each layer there is a query that *only* that layer rejects, demonstrated twice: rejected with everything on, and admitted with that one layer disabled. The `skip` parameter exists for exactly this, and `test_the_production_entry_point_never_skips_a_layer` asserts the served path never passes it.

Four deviations from the list above, all forced by what the parser actually does:

- **Layer 1 says "Supabase Postgres role … create it in a migration".** It is a *replica* role, created by the replica's own bootstrap — see §2.1. Writes against the join views are refused with SQLSTATE 55000 (not auto-updatable) *before* PostgreSQL consults the grants, so `--verify` reads the privileges straight out of `has_table_privilege` rather than inferring them from an error code.
- **Layer 2 must walk the whole tree, not check the root.** PostgreSQL executes data-modifying CTEs, and `WITH x AS (INSERT …) SELECT * FROM x` parses with `Select` at the root.
- **Anything sqlglot cannot model is refused.** `SET ROLE`, `RESET ROLE`, `ALTER ROLE` and `EXPLAIN ANALYZE` all fall back to an `exp.Command` node that round-trips its raw text unchanged.
- **Comments must be stripped, not merely parsed — and this is the one an implementer would inherit from the text above.** `SELECT 1 -- ; DROP TABLE deliveries` parses to a single clean `Select` and regenerates as `SELECT 1 /* ; DROP TABLE deliveries */`. sqlglot treats a comment as trivia to be *preserved*, not content to be evaluated, so it hands the payload through; the naive `"DROP" in sql.upper()` blocklist that layer 2 exists to replace would have refused it. Layer 2 is still right — the fix is `comments=False`, not regex — but "parse, don't regex" is not sufficient on its own, because **a parser partitions its input into what it models and what it carries, and the carried part is invisible to every check written against the model**. See standing rule 11 in `docs/phase6-session1.md`.

The caller gets one uninformative payload whatever the cause — `{"error": "query rejected", "ref": "<uuid>"}` — with no table name, column name or layer identity, because a message that varies by cause is an oracle telling an attacker which layer they tripped. The detail goes to `agent_query_log` on Supabase, joined by `ref`.

### 10.4 Citation requirement

**Load-bearing as of Phase 6 session 1, not a nicety.** Gap 3's resolution puts the entire honesty burden here: `get_player_form` refuses to estimate ability while `query_ball_data` will happily return the rows an average could be computed from. That split only works if the agent reliably declines to dress the average as an ability estimate — which is a behaviour, so it must be **in the eval set, not just the system prompt**. A prompt instruction with no eval behind it is an intention, not a control. See `docs/phase6-session1.md` §5.

The agent must state sample sizes. "Rashid concedes 5.8 to left-handers in the powerplay **(based on 214 deliveries since 2023)**." A 3-ball sample and a 300-ball sample must be visibly different to the user. Enforce this in the system prompt and check it in your evals.

### 10.5 Scouting reports and narratives

Separate, simpler LLM calls — not the agent loop.

- **Scouting report:** feed the player's model outputs (ability curve, strengths grid, recent innings) into a prompt that returns two paragraphs. Cache per player per day.
- **Match narrative:** every over, feed the last over's deliveries and the WP change into a prompt returning two sentences. Cache per over.

Both must be generated from model outputs, never from raw stats the LLM interprets itself — otherwise it will hallucinate numbers.

---

## 11. Build phases

Each phase ends with a demoable artifact and explicit acceptance criteria. Do not start a phase before the previous one passes.

### Phase 0 — Data foundation (week 1)

- [ ] GitHub repo, monorepo structure per §3, `ci.yml` running tests
- [ ] Local Postgres via docker-compose; Supabase project created
- [ ] Supabase CLI migrations for all tables in §5, applied to both databases
- [ ] `supabase gen types typescript` output committed to `web/lib/types.ts`
- [ ] Cricsheet bulk loader for all T20 + ODI
- [ ] Entity resolution with alias table and unresolved queue
- [ ] `match_states` builder
- [ ] Elo ratings computed as a time series

**Acceptance:**
- `SELECT COUNT(*) FROM deliveries` returns > 1,000,000
- Every player carrying a Cricsheet registry ID resolves by exact ID match. Fewer than 50 such players in the unresolved queue — an exact registry match is authoritative and no downstream heuristic may override it.
- Players lacking a registry ID may queue freely; report the count but do not treat it as a failure. Fuzzy matching is the fallback path for a minority of the corpus, not the main road.
- Zero wrong merges across the golden test set, including deliberate near-collisions.
- A spot-check of five random matches against Cricsheet source JSON matches exactly, verified by a test.

**Never tune resolver thresholds to make this criterion pass.** If the queue is large, the resolution *order* is wrong, not the thresholds. Diagnose before adjusting.

### Phase 1 — Win probability model, offline (week 2)

- [ ] `eval/splits.py` with temporal splits
- [ ] Both baselines from §9.2
- [ ] LightGBM second-innings classifier
- [ ] Isotonic calibration
- [ ] Reliability diagram + metrics report

**Acceptance (revised — the original figures here were guesses and were superseded by §9.3):** Test Brier ≤ 0.125 overall, ≤ 0.085 in the final 3 overs. Beats both §9.2 baselines by a paired match-clustered margin whose 95% CI excludes zero. Calibration judged per §9.3's clustered-CI criterion, never raw pp deviation.

**Closed 2026-09-13:** test Brier 0.1232 overall / 0.0652 final-3-overs; paired deltas vs logistic +0.0110 and vs base rate +0.0266, both CIs above zero. Identity beat all four calibration candidates on a held-out selection split and shipped uncalibrated; 4 of 10 test deciles still fail the clustered check — disclosed debt carried into Phase 2.

### Phase 2 — Live loop and minimal UI (week 3)

- [x] `LiveClient` interface + one real provider implementation
- [x] `replay.py` implementing the same interface
- [x] Live worker with the §7.1 loop, deployed to Railway as an always-on process
- [x] FastAPI prediction service on Railway
- [x] Realtime enabled on `predictions`, with RLS policies for anonymous select
- [x] Next.js match page on Vercel: score, WP bar, WP curve, latency indicator
- [x] Supabase Realtime subscription driving live updates

**Acceptance:** A replayed historical match renders a full, smooth WP curve end to end. A real live match (or a replay if none is on) updates in the deployed Vercel app within 60s of the ball — not just locally.

**Closed 2026-09-18.** Match 9337 (CPL, chase won off the last over) replayed from the local corpus through the deployed Railway service into `https://sight-screen-rose.vercel.app/match/9337`. **125 of 125 balls rendered, in order, no gaps**, curve 0.256 → 0.926. Latency from the Supabase INSERT to the pixel on the deployed page, paired per ball by a headless browser reading the rendered DOM rather than by impression: **median 702 ms, p95 918 ms, max 1085 ms, zero over 60s** — the criterion met with roughly 55x of headroom at the worst ball. Adding the client-to-Railway leg (~1.6 s per POST, measured across the run) puts end-to-end at about 2.3 s. Two honest limits on what this proves: the provider-fed worker path is **not** exercised (no CricketData call is involved, and the cadence comes from a laptop, so the live path stays unproven until a real match is on — a calendar problem, not an engineering one), and §12.1's items 3, 4, 5 and 7 are deliberately absent, being blocked on per-ball player identity (§15) or on Phase 6's agent. See `docs/phase2-closeout.md`.

### Phase 3 — Prediction log and accuracy page (week 4)

- [x] Every prediction written to `predictions`
- [x] Outcome resolution job at `match_end`
- [x] `calibration.yml` GitHub Action running the daily monitor
- [x] Public accuracy page at `/accuracy`

**Acceptance:** After replaying 100 historical matches, the accuracy page renders a populated reliability diagram computed entirely from logged predictions.

**Closed 2026-09-19.** `/accuracy` renders a reliability diagram computed entirely from logged predictions: 12,081 backfilled predictions over 100 matches, Brier **0.1020 [0.0745, 0.1311]** match-clustered, with **4 of 10 deciles off** — the disclosed debt §8.1 step 4 asks to be shown rather than smoothed. Every decile carries both its ball count and its **match** count, because the first band is 2,377 balls and 47 matches and only the second number is evidence. Both §9.2 baselines are beaten on the *same* matches with intervals excluding zero (logistic +0.0149 [0.0014, 0.0284]; base rate +0.0386 [0.0226, 0.0559]), which required publishing the fitted baselines as a pinned artifact since the monitor runs where the training corpus does not exist. The daily monitor reports and declines to refit — 100 matches against a floor of 500 — and says so in those words. **The page separates two populations and puts the small one first:** the 100 replayed matches ARE the §9.1 test split Phase 1 selected the calibrator on, so they are in-sample and labelled a demo; the 25 live predictions have no outcome yet because the corpus ends 2026-08-24. See `docs/phase3-closeout.md`.

**Session 1 of 2, 2026-09-19 — the log exists and reconciles.** 100 matches from the §9.1 test split, chosen deterministically and recorded in `api/data/phase3_manifest.json`. **12,081 predictions, every one resolved, zero unresolved**, reconciled by `python -m ingest.replay_log verify`: every manifest match logged, per-match row counts equal to the manifest, no duplicate ball keys, exactly one `model_version`, no predictions without a `matches` row. Re-running the logger writes 0 rows and re-running resolution resolves 0 — idempotent by a natural ball key, not by luck. Measured growth: **477 B per prediction row, 169 B per outcome row, ~79 kB per match all in**; Supabase sits at 33 MB of its 500 MB cap, leaving room for roughly 5,900 more matches at this rate. The accuracy page and `calibration.yml` are session 2, so the acceptance criterion above is not yet met - only the log it will read.

> **This is the minimum resume-ready milestone.** If co-op applications are due and you are here, you have a real project. Everything after this is upside.

### Phase 4 — Ball outcome model, simulation, first innings (weeks 5–6)

- [ ] Multinomial ball outcome model
- [ ] Vectorised Monte Carlo simulation
- [ ] First-innings score projection with bands
- [ ] Comparison writeup: direct classifier vs simulation

**Acceptance:** 5,000 sims in under 200ms. Simulation-based WP within 3 points of the direct classifier across a test set (they should broadly agree; large divergence means a bug).

### Phase 5 — Player predictions and WPA (weeks 7–8)

**Scope note (2026-09-14):** live BBB is unavailable on the current provider, so this phase is built against the historical corpus, not the live feed. Season/career WPA, player ability curves, next-innings predictions, scouting reports, and post-match impact breakdowns all work from Cricsheet and are unaffected. Only the *live in-match* WPA ticker and live batter/bowler prediction cards are deferred. They need no new logic if a BBB provider is ever adopted — only an adapter swap. See §15.

**Precondition (2026-09-21), run before the ability model is built:** `python -m ingest.check_player_identity --strict`. §6.5 keys its ability posterior on `player_id`, and the loader guarantees one `player_id` per Cricsheet **registry id** — not one per person. Where Cricsheet issues two ids for one human, that human gets two half-histories and two wrong posteriors, and the split is invisible in the output because the smaller sample WIDENS `sd`, which §6.5 ships to the UI as a confidence band. Measured 2026-09-21: 1 near-duplicate group across 18,468 players, 0 with both sides active, so the corpus is clean today — but Cricsheet refreshes, so re-run it rather than trusting that number. See §15.

- [ ] Bayesian player ability model with per-innings updates
- [ ] Batter and bowler live predictions
- [ ] WPA attribution
- [ ] Live impact leaderboard on the match page

**Acceptance:** P(50+) is calibrated within 5 points across deciles. WPA sums to approximately the total win probability change across the match (sanity check — if it doesn't, attribution is buggy).

### Phase 6 — Agent (weeks 9–10)

- [x] Tools as FastAPI endpoints, shared-secret authenticated *(session 1)*
- [x] SQL guard with all five layers, plus the read-only ~~Supabase~~ **replica** role *(session 1)*
- [ ] Agent loop in `web/app/api/agent/route.ts` with citation enforcement
- [ ] Streaming chat UI at `/ask`

**Session 1 closed 2026-09-20.** Corpus replica built and loaded (3,780,368 deliveries, 577 MB); five-layer guard with a non-vacuity proof per layer; 99 tests green. Layers 1 and 3 verified against a live PostgreSQL 17.6 with the real corpus — `agent_ro` reads five views, is denied all five base tables on privilege (42501), holds no write privilege anywhere per `has_table_privilege`, and a `pg_sleep(10)` is cancelled at 5 s while `pg_sleep(1)` with no timeout completes. Four tools built; `get_player_form` returns a structured unavailability naming Phase 5, **not** a rolling average. See `docs/phase6-session1.md`.

**Not closed by session 1:** the replica is verified on local Postgres, not on the hosted service, and `agent_query_log`'s migration has not been pushed. Both need the account owner; `replica.py --verify` is the command that closes the first.

**Acceptance:** A 20-question eval set covering stats lookups, form questions, live match questions, and adversarial SQL attempts. Zero successful injections. Every numeric answer carries a sample size. *(Session 2 — and §10.4's citation rule is the actual safeguard behind `get_player_form`'s refusal, not a nicety; see `docs/phase6-session1.md` §5, Gap 3.)*

### Phase 7 — Upcoming and player pages (weeks 11–12)

- [ ] Upcoming match page: pre-match prediction, predicted XI, venue profile, matchups to watch, weather
- [ ] Player page: form curve, strengths grid, next-innings distribution, scouting report, compare mode
- [ ] Telegram or Discord bot hitting the same API

**Acceptance:** Every page renders for any player with ≥ 20 innings, and degrades gracefully with a clear message below that.

---

## 12. Frontend specification

### 12.1 Live match page

Top to bottom:
1. Header: teams, venue, situation line ("RR need 48 off 24, 3 wickets left"), latency indicator
2. Score card with WP bar; below it the prediction history strip (pre-toss → post-toss → innings break → now) and the last-over delta
3. Two metric cards: current batter prediction, current bowler prediction
4. Matchup callout: current batter vs current bowler, historical + predicted, with sample size
5. WPA leaderboard, sorted by absolute impact
6. WP curve for the match so far, with markers on the highest-|WPA| balls
7. Action buttons: "why did it move?", "what if...", "model track record"

### 12.2 Design rules

- **Every number gets an uncertainty or sample size.** No bare point estimates anywhere.
- **Uncertainty must be *specific to the number shown*** (clarified 2026-09-18). An interval derived from an aggregate metric is not uncertainty — it is decoration that looks principled, and it violates this rule while appearing to satisfy it. Where no per-prediction interval exists, say so and link to the real calibration evidence instead of manufacturing a band.
- **Grey out small samples** rather than displaying a misleading precise number.
- **Confidence labelling by phase.** Pre-toss predictions must be visibly marked low-confidence. A bot that knows when it doesn't know is more trustworthy than one that prints 62% in the same font at ball one and ball 119.
- Mobile-first. Most cricket viewing is second-screen on a phone.

---

## 13. Risks and known failure modes

| Risk | Mitigation |
|---|---|
| **Temporal leakage** | Single `splits.py`; all rolling features go through `as_of()`. If test accuracy looks amazing, assume leakage first. |
| **Entity resolution rabbit hole** | Timebox to two days; unresolved queue rather than perfection. |
| **No live matches during dev** | Replay mode from Phase 2. Never block on the cricket calendar. |
| **API quota exhaustion** | No-op polls when ball count is unchanged; back off to 30s; cache aggressively. |
| **Over-updating on single outcomes** | §8. Aggregate-only correction, reviewed flags, shadow deployment. |
| **Scope creep** | Phases are ordered by value density. Phase 3 is a complete project. |
| **SQL injection via agent** | Five layers in §10.3, role-level permissions being the one that matters. |
| **LLM hallucinating stats** | Narratives and reports generated *from model outputs only*, never from the LLM's own knowledge. |
| **Supabase free-tier auto-pause** | Free projects pause after ~7 days without activity. Presents as connection errors that look like code bugs. Harmless in Phase 0-1; before Phase 2 deployment either upgrade to Pro or add a keepalive ping, and make the worker log a distinguishable error on a paused project. |
| **Perceived as a betting tool** | Explicit latency disclosure, no odds, no stake language, accuracy page framing it as analytics. |
| **Supabase 500 MB free tier** | Training corpus stays in local Postgres (§2.1). Supabase holds serving data only. T20-only if still tight. |
| **Trying to run the poller on Vercel** | It cannot work (§2.2). One Railway container, budgeted from day one. |
| **Supabase connection exhaustion** | Transaction pooler (6543) from Vercel, session pooler (5432) from Railway. Never direct — it is IPv6-only (§2.4). |
| **Realtime silently delivering nothing** | Almost always missing RLS policies, not a broken subscription. Check policies first. |
| **Schema drift between Python and TypeScript** | Regenerate `types.ts` on every migration; make CI fail if it's stale. |

---

## 14. Resume and interview framing

The technically defensible claims this project earns you, in rough order of strength:

1. Built a live win-probability model over 3.78M deliveries, achieving test Brier 0.1232 against a 0.1342 three-feature logistic baseline — a paired, match-clustered improvement whose 95% CI excludes zero. Evaluated five calibration methods against an identity control on a held-out selection split; identity won, and shipped uncalibrated with the reliability shortfall disclosed rather than cosmetically corrected.
2. Designed a Bayesian latent-ability model with per-innings Kalman updates, producing uncertainty-aware player form estimates
3. Built a text-to-SQL agent over the ball-by-ball database with AST-validated query sandboxing and enforced sample-size citation
4. Shipped a public model-accuracy page — reliability diagrams and honest miss reporting — as a first-class product feature

The fourth is the one most likely to be remembered, because almost nobody does it.

For the "communicate technical decisions to non-technical stakeholders" requirement specifically: write a one-page model card in plain language — what it predicts, what it explicitly cannot predict (see §9.4), how confident it is at each phase, and how it corrects itself. Link it from the accuracy page. Bring it to interviews.

### 14.1 Stack coverage

Every target keyword is earned by real work here, not name-dropped:

| Keyword | Where it's genuinely used |
|---|---|
| Python | Ingestion, feature engineering, LightGBM training, Monte Carlo simulation |
| FastAPI | Prediction service and authenticated agent tool endpoints |
| SQL | Schema design, analytics queries, and the agent's text-to-SQL target |
| Supabase | Serving database, Realtime subscriptions, RLS policies, generated types |
| Next.js | App Router, server components for initial load, route handlers for the agent |
| React | Live match page, charts, streaming chat |
| Node.js / TypeScript | Agent orchestration, BFF layer, end-to-end typed data flow |
| Vercel | Frontend and API route deployment |
| GitHub | Monorepo, Actions for CI and all three scheduled ML jobs |

The strongest single line you can write from this stack: *end-to-end type safety from Postgres schema through generated TypeScript into React props, with a Python ML service behind an authenticated boundary.* That describes real architectural judgment rather than a tool list.

---

## 15. Open decisions

Log decisions here as you make them, with dates and reasoning.

| Date | Decision | Reasoning |
|---|---|---|
| 2026-09-14 | Live data provider: **Cricket Data**, consumed via snapshot reconstruction rather than its ball-by-ball feed. | Spike across three providers asked one question: does a single response carry per-delivery striker_id, bowler_id, runs and wicket? **CricketData: no** — `match_bbb` is entitled on our key but returns only penalty/extras deliveries (11/20/18/20 balls for matches of ~190/213/574/229) and has no wicket field in its schema at all. **Big Balls Sports Data: no** — their public OpenAPI spec has six cricket endpoints and zero ball/delivery schema; "ball-by-ball scorecards" means per-player aggregates. **Sportmonks: yes** (documented `balls` include with `batsman_id`, `bowler_id`, `score.runs`, `score.is_wicket`) but €29–125/mo, rejected on cost. So we keep the $5.99 CricketData key and reconstruct a delivery stream by diffing consecutive scorecard snapshots, with an explicit confidence flag per inferred ball. |
| | Include ODI in v1 or T20 only (drives the Supabase sizing question in §2.1) | |
| | FastAPI on Railway vs Vercel Python functions | |
| | Whether to upgrade Supabase to Pro so the agent can query full history | |
| 2026-09-08 | Defer player-ability features (striker_ability, non_striker_ability, remaining_batting_ability, current_bowler_ability) from §6.2 to Phase 5 | player_state doesn't exist until §6.5 in Phase 5. Building a rushed proxy now would contaminate the comparison later; deferring gives a clean ablation measuring exactly what player features are worth. Phase 1's model uses state + venue + Elo only. |
| 2026-09-10 | RESOLVED and **IMPLEMENTED 2026-09-15** (Phase 2 Session 3; Railway deployment is no longer blocked on this). Shipped as two derived tables rather than a raw `elo_ratings` sync: `venue_asof_summary` (10,508 rows) and `elo_asof_summary` (25,290 rows), 4.76 MB total, both present in **both** databases and read by the same helper in both. `elo_ratings` stays local — its `match_id` references `matches`, and syncing `matches` would put corpus rows in the same `match_id` space the live worker writes into. Original reasoning below; see `docs/phase2-session3-asof-sync.md` for what the parity gate found. | Option (a) is not viable — local Postgres is on a laptop and unreachable from Railway. Option (b) is also not a §2.1 exception: §2.1 splits by purpose, not provenance, and these are consumed at serving time. Both tables are aggregates, not corpus: elo_ratings ~26k rows, venue summary ~13k, a few MB total. The rule that stands is that deliveries and match_states never go to Supabase. Sync runs after any Elo or venue rebuild, and the live path must use the identical as_of helper so serving and training lookups cannot diverge. |
| 2026-09-14 | **Only the *live in-match* parts of Phase 5 are blocked on a ball-by-ball-capable provider. Most of Phase 5 is not.** | Snapshot reconstruction recovers score, wickets and ball counts exactly, but carries no per-delivery striker or bowler identity — CricketData's feed simply does not contain it (see the provider decision above). §6.5's per-innings ability updates, §6.6's WPA attribution and §12.1's impact leaderboard all require knowing who faced and who bowled each ball. But that identity exists for 3.78M historical deliveries in local Postgres, free. Live BBB and historical BBB are different things, and almost everything valuable sits on the side we already own. Unaffected: season and career WPA leaderboards, §6.5 player ability curves (Cricsheet publishes within ~a day of a match ending), next-innings predictions, scouting reports, post-match ball-by-ball impact breakdowns, the agent's text-to-SQL, player pages, upcoming-match predictions, the accuracy page. Blocked: only the live in-match WPA ticker and live batter/bowler prediction cards on §12.1. Build Phase 5 against the corpus; if a BBB provider is ever adopted the WPA code is unchanged and only the adapter swaps, so the €29/mo decision defers indefinitely rather than gating anything. Phases 2–4 are unaffected either way: the Phase 1 model uses no player features. |
| 2026-09-15 | **KNOWN TRAIN/SERVE SKEW - open until the next retrain.** `elo_as_of` was non-deterministic; the fix is shipped, but the registered model was trained before it. | Every `matches.start_time` in this corpus is exactly midnight, so a team playing twice on one date writes two `elo_ratings` rows with an identical `as_of`, and the shipped `ORDER BY as_of DESC LIMIT 1` chose between them by heap order. Measured: 372 tied groups, of which 137 returned the *earlier* match's rating. Fixed by tie-breaking on `match_id DESC`, so an as-of lookup now returns the end-of-day rating. **The skew is that `winprob2-20260910` was trained against the buggy ordering while serving uses the fixed one.** Bounded: 162 of 12,916 matches (1.25%) have a different `elo_diff`, mean 9.2 Elo points, max 24.2, on a feature Phase 1 measured as individually non-significant. Deliberately NOT closed by a retrain triggered on this finding alone - the effect is small against a feature whose scale is hundreds of points, and an unplanned retrain would confound the next scheduled comparison. **It closes when the next retrain runs, and that retrain must state explicitly that it closed it.** Carried in `models/registry.py`'s KNOWN_SKEW and in the published Supabase `model_versions.notes`, so it is visible from the serving side and not only here. |
| 2026-09-15 | Serving values must not depend on Postgres session settings. `elo_asof_summary.rating` is `NUMERIC`, not `REAL`, and its rebuild pins `extra_float_digits`. | Caught by the parity gate's first run, not by review. `real`'s TEXT output — which is what psycopg decodes — depends on `extra_float_digits`, and Supabase's pooler hands out sessions with `0` where local Postgres uses the 12+ default of `1`. The identical stored float4 came back to Python as `1496.445` locally and `1496.44` from Supabase: a silent training/serving divergence in the exact feature this session exists to keep aligned. Two further traps found while fixing it: `float4::numeric` is hardcoded to 6 significant digits and ignores the setting entirely (it turns 1601.9048 into 1601.9), so the rebuild casts via `::text::numeric`; and `date`'s output depends on `DateStyle`, so content hashes render columns explicitly rather than using `row::text`. |
| 2026-09-15 | Float columns that cross the local/Supabase boundary are `NUMERIC`: `model_versions.test_brier`/`.test_log_loss`, `prediction_outcomes.brier`/`.log_loss`, `player_state`'s four ability columns. `match_states`, `matches.target_overs`, `elo_ratings` and `unresolved_entities` deliberately keep `REAL`. | The sweep session 3's finding demanded. A column is at risk when a value is written on one side and read on the other, or read back through a pooler whose `extra_float_digits` differs. `player_state` is the worst case and the cheapest fix - a Kalman update reads its own prior and writes the posterior, so rounding compounds rather than staying cosmetic, and the table is empty until Phase 5. `match_states` looks scarier (those are model *features*) but is **verified not to cross**: the live worker's only Supabase write is `INSERT INTO matches`, so nothing serving-side inserts into it; converting would mean altering 3.78M local rows and handing `Decimal` to `eval/splits.py`'s numpy arrays, which is a training dtype change wearing a deployment fix's clothing. If Session 5 starts writing live `match_states` to Supabase, convert first. `matches.target_overs` does cross but is benign because its values carry at most three significant digits - pinned by `tests/db/test_float_boundary.py` against all 113 distinct values rather than asserted in a comment, which is the actual lesson of the original bug. |
| 2026-09-15 | Reference freshness is measured on `reference_sync_state.synced_at`, not on the newest match date in the summaries. | Session 3's Decision 4 enforced on `max(effective_date)` and justified it with "cricket is played almost daily". That conflated two clocks. The corpus is a periodically-refreshed archive, so its newest match is routinely weeks old even when the sync ran minutes ago. Caught by the first real container start: data synced 14 hours earlier was refused as "22 days old" and deployment could not proceed. `synced_at` measures the question actually being asked - is this database's copy current - and does not false-positive between Cricsheet refreshes. The corpus age is still reported, it just no longer refuses. The content hash, which is the airtight half, is unchanged. |
| 2026-09-18 | The **phase-confidence chip is the standing approach** for conveying win-probability uncertainty, until something produces genuine per-prediction intervals. | §12.2's clarification of the same date explains why: the only band available today would be derived from the aggregate Brier (0.1232), which is identical at ball 1 and ball 119 and therefore not uncertainty at all. The chip labels confidence by phase (pre-toss and powerplay marked low) and the WP bar links to `/about/model`, which reads the real numbers out of `model_versions` — test Brier, the uncalibrated-by-selection result, and the 4-of-10 failing deciles. A per-prediction interval needs either a calibration curve with enough resolved predictions to bin (Phase 3's accuracy page is the first thing that could produce one) or a model that emits a distribution rather than a point. Revisit when either exists; until then, saying "we do not have this" is the honest display. |
| 2026-09-18 | **Phase 6 precondition: set `prepare_threshold=0` (or `None`) on any psycopg connection using the transaction pooler (6543).** | §2.4 records that transaction mode has no prepared statements, but nothing in the codebase acts on it — grep for `prepare_threshold` finds only a comment in `supabase/apply_migrations.py`. psycopg's default is `prepare_threshold=5`, meaning it silently promotes a statement to a server-side prepared statement on its **sixth** execution. Under Supavisor transaction mode that connection may be handed to a different backend, so the prepared statement is not there and the query fails. **This is a bug that survives every smoke test and appears only under real traffic** — five identical queries pass, the sixth does not, so it correlates with load rather than with code. Nothing uses 6543 today (Session 5's match page talks REST and websockets via supabase-js), so this is recorded rather than fixed speculatively. It becomes load-bearing the moment Phase 6's agent SQL tool opens a Postgres connection from a Vercel function. `config.py` now enforces the port per caller so the two URLs cannot be swapped, but port enforcement does not set the driver flag. |
| 2026-09-18 | **`POST /predict/win-prob` is not idempotent, and the transport retries it. Phase 3 must dedupe the prediction log or make the write idempotent before computing calibration.** | Measured driving match 9339 through the deployed service: the driver posted exactly 125 balls (matching the corpus), and Supabase received **128 rows — 125 distinct payloads and 3 exact duplicates**, each pair holding consecutive `prediction_id`s (157/158, 187/188, 251/252). Consecutive ids with byte-identical payloads mean one request was processed twice in immediate succession: a slow response, a retry by urllib or Railway's edge, and a server that had already committed. The driver's own count agreed — it reported 124 written and 1 failed, the "failure" being a client-side timeout on a request that had in fact succeeded. Harmless for the live curve, which plots in `prediction_id` order and renders a repeated point invisibly. **Not harmless for §11's Phase 3 acceptance**, where the accuracy page groups predictions into calibration bins: a duplicated prediction is counted twice against one outcome, which biases the reliability diagram by exactly the ~2.4% duplication rate seen here. Note the obvious dedupe key is subtler than it looks - extras legitimately repeat `balls_bowled` with a *different* payload (this match has 125 deliveries across 111 legal balls), so the key must be the payload content, not the ball number. Deliberately not fixed here: a unique index on `predictions` changes what the live worker can write, and the right key belongs to whoever builds the accuracy page. |
| 2026-09-18 | **A gate must be proven to fire, and the job it lives in must be proven green.** Extension of the standing rules; the third instance of one shape. | The `web/lib/types.ts` staleness check in `ci.yml` did its job: it went red on 6919ee1 and 6a8e032 (sessions 2 and 3), correctly catching the file drifting behind four migrations. Nobody read it. Then cc38d0a (session 4a) introduced a *different* failure earlier in the same job, and for **ten consecutive runs** the staleness step was reported as `skipped` while the drift grew to three missing tables and a missing column - one of which session 5 then needed. CI has been red on **every Phase 2 push**, 2026-09-15 through 2026-09-18; the last green run was 2026-08-25. The masking failure was itself environmental: `tests/serving/test_boundary.py` constructs `Settings(_env_file=None, ...)` and its docstring says that stops `api/.env` supplying values behind the test's back - true, but pydantic-settings reads `os.environ` as well, and `ci.yml` exports `LOCAL_DATABASE_URL` into the job, so seven Railway-simulating tests saw a local database URL they never passed. Passed on every laptop; failed everywhere it mattered. Fixed three ways: a `settings_env_isolated` fixture that clears every variable `Settings` reads, with a test asserting the list still covers every field; the staleness check extracted into its **own job**, so a slow expensive check can never again mask a fast cheap one; and the gate proven to fire by deliberately staling the file and watching CI go red at that step. The first two instances of this shape were the parity gate not covering policies or grants, and the `match_states` guard inferring execution truth from module-level imports. **Fourth instance, 2026-09-21, and the purest one yet:** `web/vitest.config.ts` included `app/**/*.test.tsx` but not `app/**/*.test.ts`. Route handlers are plain TypeScript, so `app/api/agent/route.test.ts` - eighteen assertions covering the /ask password gate and both spend caps - passed when named directly on the command line and was **silently collected by nothing** under `npm test`. Same shape as the types.ts staleness check sitting in a job that had been red for three sessions: something that would have caught a real problem, not running, with green output either way. The tell is identical too - the suite reports a number, and nobody knows what number it should be. Found by running the new file directly and getting `No test files found` from the same pattern that had just reported 84 passing. |
| 2026-09-18 | **Supabase Realtime drops `postgres_changes` messages occasionally and silently. The match page therefore re-reads the whole match every 30s and merges, unconditionally.** Cause unidentified; open. | Observed three times: twice as `check:anon` failing its round trip with nothing in 15s and passing on an immediate re-run, and once in a measurement round that lost 3 of 5 probes (+0s, +1s and +6s after SUBSCRIBED) while delivering +3s and +12s. **A correction, recorded because the reasoning matters more than the conclusion:** from the first two observations I said this looked like a cold-start pattern rather than randomness, both being the first channel after an idle period. The measurement contradicted it - three rounds with 4-minute idle gaps delivered 15 of 15 at a mean of 180-270 ms, and a 30-minute gap delivered 10 of 10. Across eight measured rounds and 40 probes, the only losses are the three in the first round ever measured - so the loss is real, rare, and not a function of how long the channel has been idle. Two data points supported an inference the third refuted, which is the same shape as session 4b's quota claim: correctly scoped to what had been observed, stated as though it covered the phenomenon. The mitigation is deliberately **not** keyed to a trigger, and this is the point worth inheriting: an unexplained silent failure justifies an unconditional refetch **better** than a pinned trigger would, because you cannot engineer around a cause you have not found. A trigger-keyed fix would have been a bet on the inference that turned out to be wrong. It is a full refetch rather than `WHERE prediction_id > high_water_mark`: a row dropped mid-stream sits below the mark forever, so a tail-only refetch leaves a permanent hole in the curve and then never looks at it again. Cost is one indexed query per 30s per open tab against a table the browser already reads. When the resync finds a row the stream should have carried, the page **says so** rather than healing quietly - per §12.2, an invisible failure made visible. Measured against the deployed app during a 125-ball replay: the stream delivered all 125 and the resync never fired, so the net is genuinely a net. |
| 2026-09-19 | **The prediction log's idempotency key is `(match_id, model_version, prediction_type, innings, over_num, ball_in_over)`, not `delivery_id`.** Migration 20260918000003; the write is `ON CONFLICT DO NOTHING` on both the HTTP and worker paths. | Closes the non-idempotent-POST finding of 2026-09-18 (125 posts, 128 rows). Section 5.4 already provides `delivery_id` for ball identity and it cannot be used: it carries a FOREIGN KEY into `deliveries`, which is empty on Supabase by section 2.1's design and must stay that way, so any non-NULL value violates the constraint. Dropping the FK on one side only would make the two databases structurally different and need an exemption in the schema-parity gate - weakening a gate to work around a schema problem. `(innings, over_num, ball_in_over)` instead, for three reasons: it mirrors `deliveries`' own `UNIQUE (match_id, innings, over_num, ball_in_over)`, so it is the identity the corpus already uses; it is provider-independent, because `ingest/live_client.py`'s `Delivery` carries exactly these fields, so a live ball and a replayed ball produce the same key without either knowing a local `delivery_id`; and `balls_bowled` alone provably cannot work, since an extra repeats it with a different payload (match 9339 is 125 deliveries across 111 legal balls). The index is PARTIAL on `innings IS NOT NULL`, because the 374 rows written before this migration cannot be given a key retroactively - their payload carries only the ambiguous `balls_bowled`. Every Phase 3 reader filters on `innings IS NOT NULL`. Those rows are: match 9337 (125, the session 5 acceptance replay), 9339 (128, including the 3 duplicates that motivated this), and 13143 - **121 rows but only 13 distinct payloads, which is the 12-ball deploy smoke test run about ten times**, and must never reach a calibration bin. |
| 2026-09-19 | **The live provider cannot say which side is batting, so the deployed worker declines to predict on it rather than guessing. The worker's scoring path is real and tested; the CricketData path is blocked on a provider fact.** | Found by wiring the worker to predict (Phase 3 session 1) and discovering the input was not there. A win probability needs the as-of features, which need `batting_team_id` and `bowling_team_id`. `currentMatches` supplies no toss (`cricketdata.py` hardcodes `toss_winner=None`), and the reconstructed `Delivery` carries no team identity - snapshot reconstruction cannot infer one. The only signal the provider gives is the innings label in its `score` array, `"Guyana Amazon Warriors Inning 1"`, which the parser had been discarding; it is now captured into `InningsSnapshot.batting_team` and resolved through the existing alias path. **When that label does not parse, the worker logs the reason once per match and writes nothing.** Guessing is the thing being refused: picking the wrong side swaps the two Elo ratings behind `elo_diff` and produces a confident number about the wrong team, with nothing downstream able to detect it. The scoring path itself is verified end to end against `ReplayClient`, which implements the same `LiveClient` interface and does know the teams - five tests, including that the pin is re-confirmed before every write and that an unknown batting side produces exactly zero rows. **CORRECTED the same day, by a live match turning up.** The paragraph above ended "what stays unproven is CricketData's own label surviving contact with a real match" - and then one did. At 2026-09-19T01:13Z the deployed worker logged its first prediction from a genuinely live CPL 2026 match (`match_id` 3, chasing 207): innings label parsed, batting side resolved through the alias table, as-of features computed, model scored, keyed row written. Two rows over the following minutes were internally coherent - score 0 then 4, balls bowled 3 then 11, runs required 207 then 203, target constant, phase `powerplay`, p 0.1108 then 0.1117. So the live path IS proven, and the honest framing of what remains is narrower: snapshot reconstruction emits a delivery only when the scorecard changes between polls, so the live curve is sparser than a true ball-by-ball feed would give. That is a completeness limit, not a correctness one. |
| 2026-09-19 | **Reference freshness compared a UTC timestamp against a LOCAL date, and could only ever be seen from a laptop in the evening.** `assert_reference_fresh` now takes UTC on both sides. | Surfaced by this session's full test run at 02:08 UTC / 22:08 EDT: `age_days: -1`, a freshness check reporting that the data arrives tomorrow. Two clock sources had been mixed. `_oldest_sync` reads a `TIMESTAMPTZ` and calls `.date()`, which psycopg renders in the SESSION's timezone; the caller defaulted to `date.today()`, which is the machine's LOCAL date. West of UTC the two disagree for part of every evening. **It survived three phases because neither place it runs can see it**: CI and Railway are both UTC, so local and UTC agree there, and the only machine where they differ is a developer's - during the hours when nobody was running the suite. Fixed on both sides: the default is `datetime.now(timezone.utc).date()`, and `_oldest_sync` now does `.astimezone(timezone.utc).date()` so the answer does not depend on a Postgres session setting either. That second half is the same class of defect as session 3's `extra_float_digits` finding - a value that changes with a session variable rather than with the data - and it is worth checking for directly whenever a timestamp or float crosses the local/Supabase boundary. Practically the impact was bounded (a negative age is below every threshold, so nothing was wrongly refused) but `/health` would have reported a negative number and the staleness arithmetic was off by a day. |
| 2026-09-19 | **Two id spaces were sharing `matches.match_id`, and resolving a live prediction would have read a different match's result.** Sequence moved above the corpus; resolution now checks identity before reading any label. | Supabase `match_id` 3 is a Caribbean Premier League 2026 match; LOCAL `match_id` 3 is a 2017 Pakistan-Australia ODI, complete, with a winner. The replay mirror copies corpus rows keeping their local ids, while the live worker's `_ensure_match_row` inserts without one, so Supabase's SERIAL assigned from 1 upwards. `models/resolve_outcomes.py` takes match ids from Supabase and reads labels from the corpus, so `--match-id 3` would have attached a 2017 result to predictions about a 2026 match - every row well formed, nothing downstream able to tell. Nothing had triggered it because resolution only ever ran over the replay manifest; the next obvious step, scoring live predictions, is exactly what would have. Two defences: migration 20260919000001 sets the sequence to 1,000,000 so no new live match can collide, and `assert_same_matches` compares competition, format and start date across both databases before any label is read - verified by pointing it at match 3 and watching it refuse with both matches named. **The general shape: a shared integer is not a crosswalk.** Linking a live match to its eventual Cricsheet record needs (date, teams, venue), which is still unbuilt. |
| 2026-09-19 | **The accuracy page separates backfilled from live predictions everywhere, and shows live first.** `predictions.source` records which, written by the producer rather than inferred. | The 100 replayed matches are the §9.1 TEST split - the same data Phase 1 used to SELECT the shipped calibrator - so their numbers are in-sample with respect to model selection and there is no held-out data behind them. Calling that "retrospective" would understate it. The live population is the only one that will ever be an honest estimate, and it currently holds 25 predictions with **zero** scored: outcomes come from `match_states.batting_team_won` in the local corpus, which ends 2026-08-24, while the live matches are 2026-09-16/17/18. Verified, not assumed - joining them to the corpus on (date, team pair, venue) returns nothing for all three, and the corpus holds 0 matches on or after 2026-09-01. So the live panel shows a count and the reason rather than a number, greyed per §12.2's small-sample rule, and sits ABOVE the replayed one: ordering by sample size would put the flattering figure first, which is the failure the page exists to prevent. Derived classification was rejected - comparing `created_at` to the match start would misclassify a replay of a match played today, and retrofitting the distinction later is worse than a small honest number now. |
| 2026-09-20 | **The corpus carries no player attributes at all, and cannot from Cricsheet. `players.batting_hand`, `bowling_style` and `dob` are populated for 0 of 18,468 rows.** This blocks §10.1's flagship agent query AND §6.4's ball-outcome model. Phase 4 must know before it starts. | Found while planning Phase 6 session 1. The columns have existed since `20260826180001` and are synced to Supabase by `sync_reference_tables.py:50`, which made them look live; the only write to `players` anywhere is `INSERT INTO players (canonical_name)` (`cricsheet.py:214`), and a direct count confirms 0/18,468 on all three. **This is not fixable by re-parsing.** Cricsheet's `register/people.csv` - the authoritative registry the loader already seeds from - carries exactly `identifier, name, unique_name` plus cross-provider key columns (`key_cricinfo`, `key_cricketarchive`, `key_opta`, ...). There is no handedness, no bowling style and no date of birth anywhere in the Cricsheet distribution, and the per-match JSON carries only names and registry ids. **Two things this blocks.** §10.1's first example query, "How does Rashid Khan bowl to left-handers in the powerplay?", is unanswerable - the agent must decline it, and the tool descriptions must state the column is absent rather than letting the model write a query that silently returns nothing. §6.4 specifies batting hand versus bowling style as a ball-outcome feature; that feature cannot be built today, so Phase 4 either drops it from the design or acquires the data first. **The route, and what actually blocks it:** `people.csv`'s `key_cricinfo` and `key_cricketarchive` columns are a ready-made crosswalk, so hydration is a *join* against a source that publishes attributes, keyed on an identifier already held - not a parser fix and not a new dataset. **The blocker is licensing, not availability.** §4.2 already rules out scraping ESPNCricinfo and Cricbuzz - undocumented endpoints that break without warning, and scraping violates their terms - so `key_cricinfo` identifies the right row in a source we have decided not to take data from. Hydration therefore needs a provider whose terms permit attribute retrieval, and **that assessment is the prerequisite**: establish which source is usable before anyone budgets time for the ingest job, because the join itself is trivial and the permission is not. The population rate of those key columns is also unmeasured. Recorded as its own decision rather than folded into a session's tool scope, because it changes what two later phases can build. |
| 2026-09-21 | **"One `player_id` per registry id" is not "one `player_id` per person", and §6.5 aggregates ability per `player_id`.** A Phase 5 precondition: run `python -m ingest.check_player_identity --strict` before the ability model is built. | The loader creates one player per Cricsheet registry id, which is the only guarantee it can actually offer - and `players` carries no registry column at all, so this is easy to misread: the id lives in `player_aliases.source_id`. Cricsheet sometimes issues **two registry ids for one human**, and the loader then faithfully creates two players. Found 2026-09-21 while investigating an unrelated encoding question: `A Davidson Soler` (`524943f1`) and `A Davidson-Soler` (`71d6fd2c`). **Inert today and dangerous in Phase 5.** One side holds 269 deliveries and the other holds 0, so nothing aggregates wrongly now. But §6.5's Kalman update reads its own prior per player and writes back the posterior keyed on `player_id`, so a split human gets **two half-histories and two wrong posteriors** - and the failure is invisible in the output, because fewer innings per side WIDENS `sd`, which §6.5 ships to the UI as "a genuine confidence band". The artifact of the split renders as a legitimate uncertainty estimate. Nothing downstream can distinguish them. **Measured, across all 18,468 players**, folding accents, smart quotes, dashes, punctuation, case and whitespace: **1** near-duplicate group, and **0** groups with more than one side holding deliveries - which is the population that could corrupt an estimate. So Phase 5 is safe as of this corpus. **The result is not durable**, which is why this is a committed script and not a note: Cricsheet refreshes, and a clean scan today says nothing about a scan after the next download. `--strict` exits 1 only on groups where more than one side is active. **It reports and never merges.** Merging two registry ids asserts that two humans are one - a claim about the world, not about data, which is why `_merge_team` exists for teams and is driven from a hand-curated list. Venues and teams were checked by the same fold in the same investigation and have **0** split groups, so `venue_chase_win_rate` is not affected and the shipped model carries no defect from this. |
