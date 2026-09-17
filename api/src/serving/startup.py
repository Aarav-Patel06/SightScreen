"""The checks both Railway services run before they will serve anything
(Phase 2 session 4).

Four gates, all fatal, none of them degrading. The common thread: every one
of these failures produces a service that looks healthy and is wrong, which
is worse than a service that is down.

  1. The section 2.1 boundary - enforced in config.py, triggered by importing
     it. A container that can see LOCAL_DATABASE_URL refuses to start.
  2. The section 2.4 pooler host - also config.py's, plus the resolved IP
     printed here rather than assumed.
  3. Reference freshness - features/as_of.py's assert_reference_fresh. If the
     as-of summaries on Supabase are stale, every prediction silently uses
     different feature values than training did.
  4. The model pin - models/artifact.py. A version mismatch against Supabase's
     active row refuses to serve.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from config import settings
from db.defaults import running_on_railway
from db.env import env_value
from features.as_of import StaleReferenceData, assert_reference_fresh
from models.artifact import resolve_pinned_artifact
from serving.db import ReconnectingConnection, resolved_endpoint

DEFAULT_CACHE_DIR = "/app/artifacts"

_started_at = time.time()


def cache_dir() -> Path:
    return Path(env_value("MODEL_CACHE_DIR", DEFAULT_CACHE_DIR))


def supabase_url() -> str:
    url = settings.supabase_session_pooler_url
    if not url:
        # Unreachable on Railway (config.py requires it there); this is the
        # local-development path, where it is optional at the field level.
        raise RuntimeError(
            "SUPABASE_SESSION_POOLER_URL is required to run a serving process "
            "(SPEC.md sections 2.1 and 2.4)."
        )
    return url


def banner(role: str, log=print) -> dict:
    """Report what this process actually resolved, not what it was told."""
    endpoint = resolved_endpoint(supabase_url())
    facts = {
        "service_role": role,
        "on_railway": running_on_railway(),
        "db_host": f"{endpoint['host']}:{endpoint['port']}",
        # Parsed, not assumed. config.py rejects a non-pooler host outright,
        # but a deployed service should still be able to SHOW that the value
        # it received parses the way SPEC.md section 2.4 requires - otherwise
        # "it validated at boot" is something you have to take on trust.
        "db_host_is_pooler": endpoint["host"].endswith(".pooler.supabase.com"),
        "db_port_is_5432": endpoint["port"] == 5432,
        "db_resolved_ip": endpoint["resolved"],
        "db_address_family": endpoint["family"],
        "model_version_pin": settings.model_version,
        "cache_dir": str(cache_dir()),
        # Names only, never values. A pasted secret with a trailing newline
        # presents as an auth failure and sends you looking at the password.
        "config_whitespace_stripped": list(settings.whitespace_stripped),
    }
    for key, value in facts.items():
        log(f"  {key:<26} {value}")
    if settings.whitespace_stripped:
        log(
            "  WARNING: surrounding whitespace was stripped from "
            f"{', '.join(settings.whitespace_stripped)} - fix the value at its source; "
            "a trailing newline in a connection string presents as an auth failure."
        )
    if not (facts["db_host_is_pooler"] and facts["db_port_is_5432"]):
        raise RuntimeError(
            f"SUPABASE_SESSION_POOLER_URL resolved to {facts['db_host']}, which is not "
            "a Supavisor session-mode endpoint (*.pooler.supabase.com:5432). "
            "See SPEC.md section 2.4."
        )
    return facts


def run_checks(role: str, *, log=print) -> dict:
    """Connect, verify reference freshness and the model pin, load the model.

    Returns the state a /health response and a prediction call both need.
    Raises on any failure; the caller is expected to die rather than serve.
    """
    log(f"sightscreen {role} starting")
    facts = banner(role, log=log)

    # A holder rather than a bare connection: a Supabase pause terminates the
    # live connection, and a service that cannot rebuild one needs a redeploy
    # to recover, which is not recovery. See serving/db.py.
    database = ReconnectingConnection(supabase_url(), log=log, autocommit=True)
    conn = database.get()

    with conn.cursor() as cur:
        cur.execute("SELECT current_setting('server_version'), current_database()")
        # NOT `database` - that name holds the ReconnectingConnection above, and
        # rebinding it here shadowed the holder with the string "postgres". The
        # dict returned below then carried a str where /health expected an
        # object, so every /health call raised AttributeError. Caught by the
        # deployed service, not by any test: nothing exercised /health between
        # the fix and the deploy.
        server_version, database_name = cur.fetchone()
    facts["server_version"] = server_version
    facts["database"] = database_name
    log(f"  {'server_version':<20} {server_version}")

    freshness = assert_reference_fresh(conn)
    facts["reference_age_days"] = freshness["age_days"]
    facts["reference_newest"] = str(freshness["newest_breakpoint"])
    log(f"  {'reference_age_days':<20} {freshness['age_days']}")

    pinned = settings.model_version
    if not pinned:
        raise RuntimeError("MODEL_VERSION must be set to run a serving process")
    resolved = resolve_pinned_artifact(conn, pinned, cache_dir())
    facts["model_version"] = resolved["model_version"]
    facts["model_sha256"] = resolved["sha256"]
    facts["model_notes"] = resolved["notes"]
    log(f"  {'model_version':<20} {resolved['model_version']} (sha256 {resolved['sha256'][:12]}...)")
    if resolved["notes"]:
        log(f"  model_notes          {resolved['notes'][:120]}")

    log(f"sightscreen {role} ready")
    return {"conn": conn, "database": database, "facts": facts, "model": resolved}


def uptime_seconds() -> float:
    return time.time() - _started_at


def service_role() -> str:
    """SERVICE_ROLE, falling back to Railway's own service name.

    The fallback means a service named `worker` in the Railway dashboard does
    the right thing even if someone forgets the variable - but SERVICE_ROLE
    stays authoritative, because relying on a dashboard name to pick a code
    path is invisible from the repo.
    """
    explicit = os.environ.get("SERVICE_ROLE")
    if explicit:
        return explicit.strip().lower()
    railway_name = (os.environ.get("RAILWAY_SERVICE_NAME") or "").strip().lower()
    return railway_name if railway_name in ("api", "worker") else "api"


# Re-checked periodically rather than once at boot. A container that has been
# up for three weeks would otherwise still be reporting the reference age it
# measured on the day it started - and the 14-day staleness limit would never
# fire, because nothing re-evaluates it. Observed directly: after 20.5 hours
# of uptime /health still said reference_age_days=1 when the real answer was
# 2. Same family of defect as a health check that reports a startup fact.
REFERENCE_RECHECK_SECONDS = 300.0

_reference_checked_at = 0.0
_reference_error: str | None = None


def refresh_reference_state(conn, facts: dict, *, now: float | None = None) -> str | None:
    """Re-evaluate reference freshness at most every REFERENCE_RECHECK_SECONDS.

    Updates `facts` in place and returns None when healthy, or the staleness
    message when not. Does not raise: this runs on a serving path where the
    caller decides between degrading and refusing.
    """
    global _reference_checked_at, _reference_error
    now = time.monotonic() if now is None else now
    if now - _reference_checked_at < REFERENCE_RECHECK_SECONDS:
        return _reference_error

    _reference_checked_at = now
    try:
        freshness = assert_reference_fresh(conn)
    except StaleReferenceData as exc:
        _reference_error = str(exc)
        facts["reference_stale"] = True
        return _reference_error
    _reference_error = None
    facts["reference_stale"] = False
    facts["reference_age_days"] = freshness["age_days"]
    facts["reference_newest"] = str(freshness["newest_breakpoint"])
    facts["reference_corpus_age_days"] = freshness["corpus_age_days"]
    return None
