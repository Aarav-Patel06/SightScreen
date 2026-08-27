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
| `LOCAL_DATABASE_URL` | Docker-compose training Postgres connection string (port 5433, `sslmode=disable`) | `docker-compose.yml` | Local only |
| `CRICSHEET_DATA_DIR` | Cache dir for downloaded/extracted Cricsheet JSON | Any local path you choose | Local only |
| `SUPABASE_URL` | Supabase project REST URL | Supabase dashboard → Project Settings → API | Railway |
| `SUPABASE_SECRET_KEY` | Server-side key, bypasses RLS | Supabase dashboard → API keys | Railway, Vercel (server-only) |
| `SUPABASE_SESSION_POOLER_URL` | Supavisor session-mode pooler (port 5432) | Supabase dashboard → **Connect** → Session pooler | Railway (live worker, FastAPI, migrations) |
| `SUPABASE_TRANSACTION_POOLER_URL` | Supavisor transaction-mode pooler (port 6543) | Supabase dashboard → **Connect** → Transaction pooler | Vercel, anything serverless (Phase 2) |
| `AGENT_SQL_ROLE_DB_URL` | Read-only Postgres role for the agent's SQL tool, its own role and password | Created by a migration (Phase 6) | Railway (Phase 6) |
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

`SUPABASE_ACCESS_TOKEN` is a separate **GitHub Actions secret** (not an env var used by the
app itself) — a personal access token from Supabase dashboard → Account → Access Tokens,
used only by `ci.yml`'s `web/lib/types.ts` staleness check to call the Management API and
regenerate types for comparison. Never the same as `SUPABASE_SECRET_KEY`.

**Never use a direct connection string** (`db.<ref>.supabase.co`) anywhere in this project.
It resolves over IPv6 only and is unreachable from Railway or Docker's default bridge
network — it fails as a DNS error, not a connection error, which is confusing the first
time. Always copy pooler strings from the dashboard's **Connect** button rather than
assembling them by hand; the region prefix varies and the username is `postgres.<PROJECT_REF>`,
not bare `postgres`. See SPEC.md section 2.4. `config.py` fails fast at startup if either
mistake makes it into `.env`.

## Environment setup

After filling in `.env`/`.env.local`, sanity-check that every Postgres host in `api/.env`
actually resolves, before finding out the hard way from a connection error:

```powershell
Get-Content api\.env | ForEach-Object {
    if ($_ -match '^(LOCAL_DATABASE_URL|SUPABASE_SESSION_POOLER_URL|SUPABASE_TRANSACTION_POOLER_URL|AGENT_SQL_ROLE_DB_URL)=(.+)$') {
        $name = $Matches[1]
        $raw = $Matches[2]
        if ([string]::IsNullOrWhiteSpace($raw)) { return }
        $uriHost = ([Uri]$raw).Host
        $uriPort = ([Uri]$raw).Port
        try {
            $addrs = [System.Net.Dns]::GetHostAddresses($uriHost)
            Write-Host ("{0,-32} {1}:{2} -> OK ({3})" -f $name, $uriHost, $uriPort, ($addrs -join ', '))
        } catch {
            Write-Host ("{0,-32} {1}:{2} -> FAILED to resolve" -f $name, $uriHost, $uriPort) -ForegroundColor Red
        }
    }
}
```

This only reads the host and port out of each URL — it never touches or prints the
username/password portion. A `FAILED to resolve` result on a Supabase URL almost always
means a direct connection string (`db.<ref>.supabase.co`) slipped in instead of a pooler
string.

## Data sources

Historical training data is [Cricsheet](https://cricsheet.org/), openly licensed for
non-commercial use — see their site for current licence terms.

