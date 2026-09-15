"""Supabase connections for the deployed services (Phase 2 session 4).

SPEC.md section 13 lists free-tier auto-pause as a known failure mode and
makes it an explicit precondition for this session: "Free projects pause
after ~7 days without activity. Presents as connection errors that look like
code bugs ... before Phase 2 deployment either upgrade to Pro or add a
keepalive ping, and make the worker log a distinguishable error on a paused
project."

That last clause is the whole point of this module. A paused Supavisor
project rejects connections with "Tenant or user not found", which reads
exactly like a wrong password - docs/phase1-closeout.md:170-174 records the
team already losing time to it once. Classifying it means the log says
"paused" instead of handing you a psycopg traceback and a red herring.

Retry policy, and the one deliberate asymmetry:

  PAUSED / UNREACHABLE / OTHER -> retry forever with capped backoff. A
  crash-looping container on Railway burns restarts, buries the cause under
  its own noise, and recovers no faster than waiting would.

  AUTH -> exit immediately. Retrying a wrong password forever is a silent
  outage: the service looks alive, the logs scroll, and nothing works. Dying
  loudly is the correct failure.
"""

from __future__ import annotations

import random
import socket
import time
from urllib.parse import urlparse

import psycopg

BACKOFF_BASE_SECONDS = 5.0
BACKOFF_FACTOR = 2.0
BACKOFF_CAP_SECONDS = 300.0
BACKOFF_JITTER = 0.25

PAUSED = "paused"
AUTH = "auth"
UNREACHABLE = "unreachable"
OTHER = "other"

# Supavisor's reply for a paused (or non-existent) project. Matched on the
# exact phrase because it is the ONLY thing distinguishing a paused project
# from a bad credential - both arrive as an OperationalError.
_PAUSED_MARKERS = (
    "tenant or user not found",
    "project is paused",
)
_AUTH_MARKERS = (
    "password authentication failed",
    "role does not exist",
    "permission denied for database",
    "no pg_hba.conf entry",
)
_UNREACHABLE_MARKERS = (
    "could not translate host name",
    "name or service not known",
    "connection refused",
    "network is unreachable",
    "timeout expired",
    "connection timeout",
    "no route to host",
    "server closed the connection unexpectedly",
)


class AuthenticationFailed(RuntimeError):
    """Credentials are wrong. Not retryable - see the module docstring."""


def classify_connection_error(exc: BaseException) -> str:
    """PAUSED | AUTH | UNREACHABLE | OTHER from an exception's text.

    Order matters: the paused markers are checked first because "Tenant or
    user not found" would otherwise be swept up as an auth failure, which is
    precisely the misdiagnosis this function exists to prevent.
    """
    message = str(exc).lower()
    for marker in _PAUSED_MARKERS:
        if marker in message:
            return PAUSED
    for marker in _AUTH_MARKERS:
        if marker in message:
            return AUTH
    for marker in _UNREACHABLE_MARKERS:
        if marker in message:
            return UNREACHABLE
    return OTHER


def describe(kind: str) -> str:
    return {
        PAUSED: (
            "Supabase project appears PAUSED (free tier auto-pauses after ~7 days idle, "
            "SPEC.md section 13). This is not a credentials problem despite what the "
            "error says. Resume it from the dashboard; retrying meanwhile"
        ),
        AUTH: "Supabase credentials rejected",
        UNREACHABLE: "Supabase unreachable (DNS or network)",
        OTHER: "Supabase connection failed",
    }[kind]


def backoff_delay(attempt: int, *, rng: random.Random | None = None) -> float:
    """Exponential with jitter, capped. `attempt` is 1-based."""
    rng = rng or random
    raw = min(BACKOFF_BASE_SECONDS * (BACKOFF_FACTOR ** (attempt - 1)), BACKOFF_CAP_SECONDS)
    return raw * (1.0 + rng.uniform(-BACKOFF_JITTER, BACKOFF_JITTER))


def resolved_endpoint(db_url: str) -> dict:
    """What host:port did we actually get, and what did it resolve to?

    SPEC.md section 2.4's failure mode is a DNS failure on an IPv6-only
    direct host, which "will present as a DNS failure, not a connection
    error, which is confusing the first time". Printing the address family
    is what separates "wrong host" from "wrong password" at 2am - so the
    resolved address is reported rather than the env var being trusted.
    """
    parsed = urlparse(db_url)
    host, port = parsed.hostname or "", parsed.port or 5432
    info: dict = {"host": host, "port": port, "resolved": None, "family": None}
    try:
        addrs = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        info["resolved"] = f"DNS FAILED: {exc}"
        return info
    family, _, _, _, sockaddr = addrs[0]
    info["resolved"] = sockaddr[0]
    info["family"] = "AF_INET6" if family == socket.AF_INET6 else "AF_INET"
    return info


def connect_with_backoff(
    db_url: str,
    *,
    max_attempts: int | None = None,
    sleep=time.sleep,
    log=print,
    autocommit: bool = False,
) -> psycopg.Connection:
    """Connect, retrying anything except an authentication failure.

    `max_attempts=None` means forever, which is what the worker wants.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return psycopg.connect(db_url, autocommit=autocommit)
        except Exception as exc:  # noqa: BLE001 - classified immediately below
            kind = classify_connection_error(exc)
            if kind == AUTH:
                log(f"FATAL {describe(kind)}: {exc}")
                raise AuthenticationFailed(str(exc)) from exc
            if max_attempts is not None and attempt >= max_attempts:
                raise
            delay = backoff_delay(attempt)
            log(f"{describe(kind)}; retrying in {delay:.0f}s (attempt {attempt})")
            sleep(delay)
