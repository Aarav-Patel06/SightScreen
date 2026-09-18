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
| `SERVICE_ROLE` | Which process the container starts: `api` or `worker` | Set per Railway service; defaults to `api` | Railway |
| `MODEL_VERSION` | The model this container is pinned to, e.g. `winprob2-20260910` | Must match Supabase's active `model_versions` row | Railway |
| `MODEL_CACHE_DIR` | Where the downloaded artifact is cached | Defaults to `/app/artifacts` in the image | Railway (optional) |
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

## Deployment

Two Railway services, one Docker image, one repository. Vercel deploys `web/`;
Railway deploys `api/`. The always-on worker is a deliberate cost — a process
that polls every 15 seconds for four hours cannot run on serverless, and no
configuration makes it work (SPEC.md section 2.2).

| Service | `SERVICE_ROLE` | What it does |
|---|---|---|
| `api` | `api` | FastAPI prediction service — `GET /health`, `POST /predict/win-prob` |
| `worker` | `worker` | Always-on live poller (SPEC.md section 7.1), idling at 600s |

Both run `python -m serving.entrypoint`, which dispatches on `SERVICE_ROLE`.
One image because the two share ~100% of their dependencies; two services
because their lifecycles differ — the API is request-scoped and scalable, the
worker is a singleton whose per-match in-memory state two replicas would
corrupt.

Set every variable in the Railway dashboard. Nothing is committed; `.env` is
gitignored and excluded from the build context. **`LOCAL_DATABASE_URL` must
NOT be set on Railway** — a container that can see it refuses to start, by
design (SPEC.md section 2.1), and there is a test proving it does.

Publishing a model is a separate, deliberate step, not a side effect of
training. A serving container pulls its artifact from a GitHub Release and
verifies the bytes before loading them:

```powershell
# 1. create a GitHub Release tagged with the model version and upload the .pkl
# 2. register it on Supabase (verifies the download and its sha256 first)
cd api/src
python -m models.publish_model_version winprob2-20260910 `
  --url https://github.com/Aarav-Patel06/SightScreen/releases/download/winprob2-20260910/winprob2-20260910.pkl
```

The digest is appended to the stored URL as a `#sha256=` fragment, so the
location and the expected bytes cannot drift apart. If `MODEL_VERSION`
disagrees with Supabase's active row, the container refuses to start rather
than serving predictions that would be attributed to the wrong model.

To redeploy:

```powershell
npx @railway/cli login
npx @railway/cli link            # project "giving-comfort", environment "production"
npx @railway/cli up --service api
npx @railway/cli up --service worker
curl https://<your-api-service>.up.railway.app/health
```

`/health` reports what the process actually resolved rather than what it was
told — the pooler host, the resolved IP and address family, the loaded model's
sha256, and how many days ago the reference tables were synced. The address
family matters: a direct Supabase host resolves IPv6-only and is unreachable
from Railway, and it fails as a DNS error rather than a connection error
(SPEC.md section 2.4).

Two operational notes worth knowing before they bite:

- The worker refuses to serve if the reference tables were last synced more
  than 14 days ago. That sync is still a manual ritual (see
  `supabase/SCHEMA.md`), so a fortnight of not running it takes the worker
  down deliberately rather than letting it serve stale features.
- Supabase's free tier pauses a project after ~7 days idle. The worker logs
  `Supabase project appears PAUSED` and retries with backoff instead of
  crash-looping. A genuine credentials failure is the one case that exits
  immediately — retrying a wrong password forever is a silent outage.


## Data sources

Historical training data is [Cricsheet](https://cricsheet.org/), openly licensed for
non-commercial use — see their site for current licence terms.

