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
import re
import socket
import time
from urllib.parse import urlparse

import psycopg

BACKOFF_BASE_SECONDS = 5.0
BACKOFF_FACTOR = 2.0
BACKOFF_CAP_SECONDS = 300.0
BACKOFF_JITTER = 0.25

# ReconnectingConnection's OWN backoff, separate from any caller's retry
# loop. The two layers add rather than multiply: the outer loop decides how
# often to try at all, this decides the minimum gap between actual connection
# attempts. Worst case for the worker is DEGRADED_INTERVAL_SECONDS (60s) per
# cycle with at most one connection attempt inside it; for the API it is one
# attempt per cooldown window no matter how much traffic arrives.
RECONNECT_COOLDOWN_BASE_SECONDS = 10.0
RECONNECT_COOLDOWN_CAP_SECONDS = 120.0

# A burst of health checks costs one round trip, not one each.
PROBE_CACHE_SECONDS = 5.0

PAUSED = "paused"
AUTH = "auth"
UNREACHABLE = "unreachable"
TIMEOUT = "timeout"
OTHER = "other"

# Supavisor's reply for a paused (or non-existent) project. This is the ONLY
# thing distinguishing a paused project from a bad credential - both arrive as
# an OperationalError, and both read like a credentials problem.
#
# Matched with a PATTERN, not a fixed phrase, because the fixed phrase was
# wrong. docs/phase1-closeout.md recorded the signature as "Tenant or user not
# found"; the text Supavisor actually returns is
#
#   FATAL:  (ENOTFOUND) tenant/user postgres.<project_ref> not found
#
# - a slash rather than "or", with the username interpolated in between. The
# literal never matched, so a genuinely paused project classified as OTHER and
# logged "Supabase connection failed" instead of naming the pause. Caught by
# pausing the real project; no amount of reading the code would have found it,
# because the code faithfully implemented a misremembered string.
# A pause produces TWO different signatures, and the second was only visible
# because a service was already connected when the project was paused:
#
#   new connection attempted while paused ->
#       FATAL:  (ENOTFOUND) tenant/user postgres.<ref> not found
#   existing connection killed at pause time ->
#       AdminShutdown: terminating connection due to administrator command
#
# The second arrives on the NEXT query rather than on connect, so it surfaces
# wherever the connection is used rather than where it was made.
_PAUSED_PATTERNS = (
    re.compile(r"tenant\s*(?:or|/)\s*user.*?not found", re.IGNORECASE | re.DOTALL),
    re.compile(r"ENOTFOUND", re.IGNORECASE),
    re.compile(r"project is paused", re.IGNORECASE),
    re.compile(r"terminating connection due to administrator command", re.IGNORECASE),
    re.compile(r"AdminShutdown", re.IGNORECASE),
)
_AUTH_MARKERS = (
    "password authentication failed",
    "role does not exist",
    "permission denied for database",
    "no pg_hba.conf entry",
)
# Split from UNREACHABLE deliberately. A DNS or refused-connection failure
# usually means the endpoint is wrong - a configuration problem that will not
# heal on its own. A timeout usually means the endpoint is right and something
# is slow or saturated. Both retry, but they send you to different places, and
# collapsing them into one label loses that.
_TIMEOUT_MARKERS = (
    "timeout expired",
    "connection timeout",
    "timed out",
)
_UNREACHABLE_MARKERS = (
    "could not translate host name",
    "name or service not known",
    "connection refused",
    "network is unreachable",
    "no route to host",
    "server closed the connection unexpectedly",
)


class AuthenticationFailed(RuntimeError):
    """Credentials are wrong. Not retryable - see the module docstring."""


class ConnectionCoolingDown(RuntimeError):
    """A reconnect was requested inside the cooldown window and skipped.

    Distinct from a connection FAILURE: nothing was attempted. Callers
    should treat it as "still down" without counting it as new evidence."""


def classify_connection_error(exc: BaseException) -> str:
    """PAUSED | AUTH | UNREACHABLE | OTHER from an exception's text.

    Order matters: the paused markers are checked first because "Tenant or
    user not found" would otherwise be swept up as an auth failure, which is
    precisely the misdiagnosis this function exists to prevent.
    """
    raw = str(exc)
    message = raw.lower()
    for pattern in _PAUSED_PATTERNS:
        if pattern.search(raw):
            return PAUSED
    for marker in _AUTH_MARKERS:
        if marker in message:
            return AUTH
    for marker in _TIMEOUT_MARKERS:
        if marker in message:
            return TIMEOUT
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
        UNREACHABLE: "Supabase unreachable (DNS or network - check the endpoint)",
        TIMEOUT: "Supabase timed out (endpoint reachable but slow or saturated)",
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


class ReconnectingConnection:
    """A Supabase connection that re-establishes itself after an outage.

    Both services previously connected once, at startup, and held that
    connection for their lifetime. That works right up until the connection
    dies, at which point nothing brings it back - the API returned 500s
    forever and the worker logged a failed poll every 600s with a corpse of a
    connection. Recovery needed a redeploy, which is not recovery.

    Observed directly by pausing the project: the live connections were
    terminated with AdminShutdown and neither service ever reconnected.

    `get()` returns a usable connection, reconnecting with backoff if the
    current one has died. `probe()` answers "is the database actually there
    right now", which is what a health check has to ask rather than reporting
    a fact cached at boot.
    """

    def __init__(self, db_url: str, *, log=print, autocommit: bool = True) -> None:
        self._url = db_url
        self._log = log
        self._autocommit = autocommit
        self._conn: psycopg.Connection | None = None
        self._failed_attempts = 0
        self._next_attempt_at = 0.0
        self._probe_cached: tuple[bool, str | None, str | None] | None = None
        self._probe_cached_at = 0.0
        # The classification of the last REAL failure. Kept so a caller
        # refused by the cooldown can still report why the database is
        # down, rather than classifying the cooldown message itself and
        # reporting a useless 'connection failed'.
        self.last_kind: str | None = None

    def _cooldown(self) -> float:
        """Its own backoff, independent of any caller's retry loop.

        Without this, every request arriving during an outage would open a
        fresh connection attempt against a project that cannot serve it. A
        health checker polling every few seconds plus request traffic would
        hammer a paused project and could trip Supabase-side limits - and
        SPEC.md section 2.4 warns the free-tier pool ceiling is low and shared,
        so speculative connections are expensive even when they succeed.
        """
        if self._failed_attempts == 0:
            return 0.0
        raw = RECONNECT_COOLDOWN_BASE_SECONDS * (2 ** (self._failed_attempts - 1))
        return min(raw, RECONNECT_COOLDOWN_CAP_SECONDS)

    def get(self, *, max_attempts: int | None = None) -> psycopg.Connection:
        if self._conn is not None and not self._conn.closed:
            return self._conn

        now = time.monotonic()
        if now < self._next_attempt_at:
            raise ConnectionCoolingDown(
                f"not retrying Supabase for another {self._next_attempt_at - now:.0f}s "
                f"(after {self._failed_attempts} failed attempts)"
            )

        if self._conn is not None:
            self._log("Supabase connection was closed; reconnecting")
        try:
            self._conn = connect_with_backoff(
                self._url, max_attempts=max_attempts, log=self._log, autocommit=self._autocommit
            )
        except Exception as exc:
            self.last_kind = classify_connection_error(exc)
            self._failed_attempts += 1
            cooldown = self._cooldown()
            self._next_attempt_at = time.monotonic() + cooldown
            self._log(f"reconnect failed; next attempt no sooner than {cooldown:.0f}s from now")
            raise
        self._failed_attempts = 0
        self._next_attempt_at = 0.0
        self._probe_cached = None
        self.last_kind = None
        return self._conn

    def discard(self, exc: BaseException | None = None) -> str | None:
        """Drop the current connection so the next get() rebuilds it.

        Returns the classification of `exc`, so the caller can log what
        happened in the same breath as recovering from it.
        """
        kind = classify_connection_error(exc) if exc is not None else None
        if kind is not None:
            self.last_kind = kind
        self._probe_cached = None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - already broken; closing is best-effort
                pass
            self._conn = None
        return kind

    def probe(self, *, now: float | None = None) -> tuple[bool, str | None, str | None]:
        """(reachable, classification, detail) using a real round trip.

        Two deliberate properties, both because of SPEC.md section 2.4's
        warning that the free-tier pool is small and shared:

        It REUSES the existing connection and never opens one. A health
        endpoint that opens a connection per call can exhaust the very pool it
        is reporting on, which is its own outage - and Railway's own health
        checker polls frequently, before anything else is pointed at it.

        It CACHES the result for PROBE_CACHE_SECONDS, so a burst of health
        checks costs one round trip rather than one each.

        It does NOT reconnect: a health check reports the current state, it
        does not repair it and then claim success.
        """
        now = time.monotonic() if now is None else now
        if self._probe_cached is not None and now - self._probe_cached_at < PROBE_CACHE_SECONDS:
            return self._probe_cached

        conn = self._conn
        if conn is None or conn.closed:
            result = (False, None, "no open connection")
        else:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                result = (True, None, None)
            except Exception as exc:  # noqa: BLE001 - classified for the caller
                result = (False, classify_connection_error(exc), str(exc).strip()[:200])
        self._probe_cached = result
        self._probe_cached_at = now
        return result
