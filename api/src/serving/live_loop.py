"""The always-on live polling worker (SPEC.md section 7.1, Phase 2 session 4).

Section 7.1's loop assumes a match is already in progress. A deployed worker
spends most of its life in the state the spec does not describe: nothing is
live, and it must wait without burning the day's quota or spinning.

Quota arithmetic, against CricketData's 2,000 hits/day. One `currentMatches`
call covers every live match at once, so idle cost is per-poll, not per-match:

    interval   calls/day   % of quota
      60s        1440         72%      unusable
     300s         288       14.4%
     600s         144        7.2%      <- IDLE_INTERVAL_SECONDS
     900s          96        4.8%

A tracked T20 at 15s costs roughly 600 calls, so two concurrent T20s are
~1,200. Idle therefore has to stay near 150/day for the matches themselves to
remain affordable. Once a match IS tracked the cadence is handed back to
LiveBudget.interval() - 15s in play, 45s between overs, degrading to 60s then
120s as quota runs down - which Phase 2 session 2 already built and tested.

Two behaviours that only matter because this runs unattended:

  A heartbeat is logged on every idle poll, so silence means broken rather
  than quiet. A worker that logs nothing for six hours is indistinguishable
  from a dead one.

  Sleeping happens in short slices. Railway sends SIGTERM on every redeploy,
  and a worker mid-`sleep(600)` would be killed rather than stopped. Slicing
  means shutdown is honoured within a second.
"""

from __future__ import annotations

import signal
import time
from datetime import datetime, timezone

from db.env import env_value
from ingest.cricketdata import CricketDataClient, HttpTransport
from serving.db import (
    OTHER,
    ConnectionCoolingDown,
    PAUSED,
    TIMEOUT,
    UNREACHABLE,
    classify_connection_error,
    describe,
)

# config and serving.startup are imported inside run() rather than here.
# config validates the whole environment at import time, so a module-level
# import would make merely IMPORTING this module require a fully configured
# environment - which couples `pytest tests/serving` to deployment config for
# no benefit. The loop's pure logic (run_once, sleep_in_slices) is what the
# tests exercise, and it needs neither.

IDLE_INTERVAL_SECONDS = 600.0
# Faster than idle while degraded, so recovery is noticed promptly, but
# still slow enough that an hour of downtime costs ~60 provider calls.
DEGRADED_INTERVAL_SECONDS = 60.0
SLEEP_SLICE_SECONDS = 1.0
IDLE_CALLS_PER_DAY = int(86_400 / IDLE_INTERVAL_SECONDS)


class Shutdown:
    """SIGTERM/SIGINT flag. Railway sends SIGTERM on every redeploy."""

    def __init__(self) -> None:
        self.requested = False

    def install(self) -> "Shutdown":
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._handle)
        return self

    def _handle(self, signum, _frame) -> None:
        print(f"received {signal.Signals(signum).name}; finishing this poll then exiting")
        self.requested = True


def sleep_in_slices(seconds: float, shutdown: Shutdown, *, sleep=time.sleep) -> None:
    """Sleep, but notice a shutdown request within SLEEP_SLICE_SECONDS."""
    remaining = seconds
    while remaining > 0 and not shutdown.requested:
        slice_len = min(SLEEP_SLICE_SECONDS, remaining)
        sleep(slice_len)
        remaining -= slice_len


def _log(message: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}", flush=True)


def run_once(client: CricketDataClient, tracked: dict, *, log=_log) -> float:
    """One iteration. Returns the number of seconds to wait before the next.

    Returns the interval rather than sleeping itself so the loop stays
    testable without a clock.
    """
    live = client.list_live_matches()
    if not live:
        log(
            f"heartbeat: no live matches; next check in {IDLE_INTERVAL_SECONDS:.0f}s "
            f"({IDLE_CALLS_PER_DAY} calls/day idle)"
        )
        tracked.clear()
        return IDLE_INTERVAL_SECONDS

    interval = IDLE_INTERVAL_SECONDS
    for summary in live:
        match_id = summary.match_id
        deliveries = client.poll(match_id)
        tracked[match_id] = tracked.get(match_id, 0) + len(deliveries)
        log(
            f"match {match_id}: +{len(deliveries)} deliveries "
            f"({tracked[match_id]} this session)"
        )
        interval = min(interval, client.next_interval(match_id))
    return interval


def run(*, max_iterations: int | None = None, log=_log) -> dict:
    """The worker. Runs until SIGTERM unless max_iterations is set (tests)."""
    from config import settings
    from serving import startup

    state = startup.run_checks("worker", log=log)
    conn = state["conn"]
    database = state["database"]

    api_key = env_value("LIVE_API_KEY")
    if not api_key:
        raise RuntimeError("LIVE_API_KEY must be set to run the live worker")
    provider = settings.live_api_provider or "cricketdata"
    if provider != "cricketdata":
        raise RuntimeError(f"unsupported LIVE_API_PROVIDER {provider!r}")

    client = CricketDataClient(conn, HttpTransport(api_key))
    shutdown = Shutdown().install()
    tracked: dict = {}
    iterations = 0
    # True once a cycle has failed on the database, so the next cycle asks
    # Postgres before paying the provider. Cleared on reconnection.
    degraded = False

    log(f"worker polling; idle cadence {IDLE_INTERVAL_SECONDS:.0f}s ({IDLE_CALLS_PER_DAY}/day)")
    while not shutdown.requested:
        if max_iterations is not None and iterations >= max_iterations:
            break
        iterations += 1
        # Check the DATABASE before calling the provider, while degraded.
        #
        # run_once -> list_live_matches -> _fetch_snapshots makes the
        # CricketData HTTP request BEFORE it touches Postgres, so every
        # degraded cycle paid for a poll whose result could not be persisted.
        # Measured over a 3.3-hour outage: 189 cycles, and the provider's own
        # counter showed hitsToday=942 of 2000 against ~144 for a normal idle
        # day. At a 60s degraded interval a full-day outage would burn ~1,440
        # calls, 72% of the quota, on discarded work. The database is the thing
        # actually blocking and probing it is free, so ask it first.
        if degraded:
            reachable, _, _ = database.probe(allow_reconnect=True)
            if not reachable:
                log(
                    f"still degraded - {describe(database.last_kind or OTHER)}; "
                    f"skipping the provider poll to preserve quota; "
                    f"next check in {DEGRADED_INTERVAL_SECONDS:.0f}s"
                )
                sleep_in_slices(DEGRADED_INTERVAL_SECONDS, shutdown)
                continue
            conn = database.get()
            client._conn = conn
            degraded = False
            log("reconnected to Supabase; resuming normal polling")

        try:
            interval = run_once(client, tracked, log=log)
        except Exception as exc:  # noqa: BLE001 - a poll failure must not kill the worker
            # Name the cause rather than dumping the exception. A paused
            # Supabase kills the live connection with AdminShutdown, which
            # surfaced here as an unexplained "poll failed" during the real
            # pause test - the classification exists precisely so this line
            # says "paused" instead.
            # Prefer the REMEMBERED cause over a fresh classification.
            #
            # Measured over the third pause: cycle 1 correctly said
            # "PAUSED ... (AdminShutdown)" and every cycle after it said
            # "Supabase connection failed (OperationalError)". Once discard()
            # drops the connection, the next cycle fails on a CLOSED
            # connection, whose text matches no PAUSED pattern - so the
            # operator sees the true cause once and a generic message
            # thereafter. Arrive at the logs ten minutes in and the pause is
            # invisible. Same failure shape as /health losing its
            # classification, in the one place that fix was not applied.
            fresh = classify_connection_error(exc)
            kind = fresh if fresh != OTHER else (database.last_kind or OTHER)
            log(
                f"poll failed - {describe(kind)} ({type(exc).__name__}); "
                f"retrying in {DEGRADED_INTERVAL_SECONDS:.0f}s"
            )
            if kind in (PAUSED, UNREACHABLE, TIMEOUT, OTHER):
                # Rebuild the connection on the next pass. Without this the
                # worker holds a dead connection forever and only a redeploy
                # brings it back, which is not recovery.
                database.discard(exc)
                degraded = True
                try:
                    conn = database.get(max_attempts=1)
                    client._conn = conn
                    degraded = False
                    log("reconnected to Supabase")
                except ConnectionCoolingDown:
                    pass  # nothing attempted; the cooldown will expire
                except Exception:  # noqa: BLE001 - still down; try again next pass
                    pass
            interval = DEGRADED_INTERVAL_SECONDS
        if max_iterations is None or iterations < max_iterations:
            sleep_in_slices(interval, shutdown)

    log(f"worker stopped after {iterations} iterations")
    conn.close()
    return {"iterations": iterations, "tracked": tracked}


if __name__ == "__main__":
    run()
