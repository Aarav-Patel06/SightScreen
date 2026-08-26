# SightScreen
A Cricket Prediction Platform

See [SPEC.md](SPEC.md) for the full build specification.

## Setup

1. Copy `api/.env.example` to `api/.env` and `web/.env.local.example` to `web/.env.local`,
   then fill in real values (never commit these — both patterns are gitignored).
2. Python service: `pip install -e "./api[dev]"`
3. Web app: `cd web && npm install`

## Environment variables

Two Supabase API keys matter here, and mixing them up is the most common way to
accidentally leak data:

- **`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`** is public by design. It's safe in the browser
  bundle — row-level security (RLS) policies on the Supabase tables are what actually
  protect the data, not secrecy of this key.
- **`SUPABASE_SECRET_KEY`** bypasses RLS entirely. It belongs only in the Python service
  and in server-side Next.js route handlers — **never** under a `NEXT_PUBLIC_` prefix or
  anywhere reachable from the browser.

| Variable | Purpose | Where to get it | Deploy target |
|---|---|---|---|
| `LOCAL_DATABASE_URL` | Docker-compose training Postgres connection string | `docker-compose.yml` (you define it) | Local only |
| `CRICSHEET_DATA_DIR` | Cache dir for downloaded/extracted Cricsheet JSON | Any local path you choose | Local only |
| `SUPABASE_URL` | Supabase project REST URL | Supabase dashboard → Project Settings → API | Railway |
| `SUPABASE_SECRET_KEY` | Server-side key, bypasses RLS | Supabase dashboard → API keys | Railway, Vercel (server-only) |
| `SUPABASE_DB_URL` | Direct Postgres connection (port 5432) for the long-running live worker | Supabase dashboard → Database settings | Railway (Phase 2) |
| `AGENT_SQL_ROLE_DB_URL` | Read-only Postgres role for the agent's SQL tool | Created by a migration, own connection string | Railway (Phase 6) |
| `AGENT_TOOL_SHARED_SECRET` | Authenticates web → FastAPI tool calls | Generate any random secret | Railway, Vercel (Phase 6) |
| `ANTHROPIC_API_KEY` | Scouting reports, match narratives, agent chat | [console.anthropic.com](https://console.anthropic.com) | Railway, Vercel |
| `LIVE_API_PROVIDER` | Which `LiveClient` implementation to load | One of `cricketdata`/`sportmonks`/`roanuz` | Railway (Phase 2) |
| `LIVE_API_KEY` | Live provider credential | Chosen provider's dashboard | Railway (Phase 2) |
| `PORT` | FastAPI bind port | Usually auto-injected | Railway |
| `NEXT_PUBLIC_SUPABASE_URL` | Public Supabase URL | Supabase dashboard → Project Settings → API | Vercel |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Public key, safe in the browser | Supabase dashboard → API keys | Vercel |
| `API_BASE_URL` | FastAPI service URL, for agent tool calls | Your Railway service URL | Vercel (Phase 6) |

`SUPABASE_URL` and `SUPABASE_SECRET_KEY` are also needed as **GitHub Actions secrets**
(`Settings → Secrets and variables → Actions`) for `calibration.yml`/`drift.yml`, which read
and write Supabase's `predictions`/`model_versions` tables on a schedule.

## Data sources

Historical training data is [Cricsheet](https://cricsheet.org/), openly licensed for
non-commercial use — see their site for current licence terms.

