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

Session 3 changed the idle half. The worker now PICKS its matches (Worker,
Subscriptions): idle, it calls only `cricScore`, once an hour (24/day), and
polls `currentMatches` only while a picked match is due. One call per
iteration however many matches are tracked (it used to be 1+N). Innings 1 is
polled every 300s, tightening to 60s near its end and through the break; a
T20 is budgeted at 450 calls, an ODI at 1,050, with 200 held in reserve
(ingest/cricketdata.py, CALLS_TO_FINISH).

Two behaviours that only matter because this runs unattended:

  A heartbeat is logged on every idle poll, so silence means broken rather
  than quiet. A worker that logs nothing for six hours is indistinguishable
  from a dead one.

  Sleeping happens in short slices. Railway sends SIGTERM on every redeploy,
  and a worker mid-`sleep(600)` would be killed rather than stopped. Slicing
  means shutdown is honoured within a second.
"""

from __future__ import annotations

import json
import signal
import time
from datetime import datetime, timedelta, timezone

from db.env import env_value
from features.as_of import compute_as_of_features
from features.match_state import IncrementalMatchStateBuilder
from ingest.cricketdata import (
    INTERVAL_SPARSE,
    RESERVE_CALLS,
    BudgetExhausted,
    CricketDataClient,
    HttpTransport,
    UnsupportedMatch,
    projected_calls,
)
from ingest.match_pick import Candidate, Team, rank_candidates
from ingest.replay import predict_win_prob
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

# Session 3: the worker picks its matches. Idle, it spends only the hourly
# fixture check (24 calls/day, 1.2% of the quota) instead of polling
# currentMatches every IDLE_INTERVAL_SECONDS (144/day); the heartbeat line is
# still logged every IDLE_INTERVAL_SECONDS and costs nothing.
FIXTURE_CHECK_SECONDS = 3600.0
FIXTURE_CALLS_PER_DAY = int(86_400 / FIXTURE_CHECK_SECONDS)
# A picked match is polled from this long before its scheduled start.
PRE_START_LEAD = timedelta(minutes=10)
MAX_SUBSCRIPTIONS = 2


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


class LivePredictor:
    """Scores innings-2 balls as the worker sees them, and logs each one
    (SPEC.md section 7.2's `innings2_ball` action, Phase 3 session 1).

    Until this existed the worker predicted nothing at all: `run_once`
    polled, counted deliveries and logged a line. Every prediction in the
    database had arrived over HTTP from a laptop. "Every prediction written
    to `predictions`" (section 11) was therefore true only of the path a
    human drove.

    Composed rather than reimplemented. `IncrementalMatchStateBuilder` turns
    a `Delivery` stream into the same rows the offline bulk builder produces
    (and is parity-tested against it), and `predict_win_prob` is the one
    scoring path the service also calls.

    (The previous sentence deliberately does not name the bulk builder's SQL
    constant: tests/db/test_float_boundary.py substring-scans serving code
    for it, and caught this docstring on its first run. A scanner that
    cannot tell prose from a call is the right trade for one that cannot be
    fooled by an alias.) This class is the wiring plus the honesty
    checks, and deliberately nothing else.

    **It declines more often than it predicts, and that is correct.** It
    scores a ball only when the innings is 2, the target is known, and the
    provider has said which side is batting. CricketData supplies no toss
    and no per-ball team, so on that provider the last condition depends on
    an innings label that may not parse - see cricketdata.py's
    `current_teams`. A declined match is logged once, with the reason.
    Guessing the batting side would swap the two Elo ratings behind
    elo_diff and produce a confident number about the wrong team.

    Verified against `ReplayClient`, which implements the same interface and
    does know the teams. The CricketData path stays unproven until a real
    match is on - a calendar problem, not an engineering one.
    """

    def __init__(self, conn, model: dict, version_guard, log=_log) -> None:
        self._conn = conn
        self._model = model
        self._version_guard = version_guard
        self._log = log
        self._builders: dict[int, object] = {}
        self._as_of: dict[int, dict] = {}
        self._batting: dict[int, int] = {}
        self._declined: set[int] = set()
        self._finished: set[int] = set()
        # Chases decided by the balls themselves - see _chase_decided.
        self.decided: set[int] = set()
        self.logged = 0

    def has_baseline(self, match_id: int) -> bool:
        """True once this match's builder exists (or it has finished), so
        run_once knows a first sighting with no new balls still needs
        observing - see the catch-up in observe."""
        return match_id in self._builders or match_id in self._finished

    def _decline(self, match_id: int, reason: str) -> None:
        """Say why, once per match. Once, because this runs every poll and a
        line per ball would bury the reason it is worth reading."""
        if match_id not in self._declined:
            self._declined.add(match_id)
            self._log(f"match {match_id}: not predicting - {reason}")

    def observe(self, client, state, deliveries: list) -> int:
        """Feed one poll's deliveries through the builder, scoring innings-2
        balls. Returns how many predictions were written."""
        match_id = state.match_id
        builder = self._builders.get(match_id)
        fresh_builder = builder is None
        if fresh_builder:
            builder = IncrementalMatchStateBuilder(match_id, state.format)
            self._builders[match_id] = builder
        if state.target_runs is not None and state.target_overs is not None:
            # Re-supplying is intentional: a DLS revision mid-chase changes
            # the target, and the builder applies it to subsequent balls only.
            builder.set_target(state.target_runs, state.target_overs)

        if fresh_builder:
            # A late join or a restart: everything bowled before this poll
            # (the adapter's catch-up span) goes through the builder UNSCORED.
            # Its per-ball states are invented - the span puts all its runs
            # on its last ball - but the totals after it are exact, so every
            # ball from here on is scored from the true score and wickets.
            # Without this the chase started from 0/0 (session 3). The gap is
            # visible as the first live row's balls_bowled > 0.
            seen = client.get_deliveries_since(match_id, 0)
            for delivery in seen[: len(seen) - len(deliveries)]:
                row = builder.process(delivery)
                if row.innings == 2 and _chase_decided(row, delivery):
                    self.decided.add(match_id)

        written = 0
        for delivery in deliveries:
            row = builder.process(delivery)
            if row.innings != 2:
                continue
            if _chase_decided(row, delivery):
                self.decided.add(match_id)
            if row.target is None:
                self._decline(match_id, "no target known for the chase")
                continue

            as_of = self._as_of.get(match_id)
            if as_of is None:
                batting, bowling = client.current_teams(match_id)
                if batting is None or bowling is None:
                    self._decline(
                        match_id,
                        "the provider has not said which side is batting "
                        "(no toss, no per-ball team, unparsed innings label)",
                    )
                    continue
                if state.match_date is None:
                    self._decline(match_id, "the provider supplied no match date (the as-of key)")
                    continue
                # Once per match: the as-of inputs are constant across a
                # chase, so this is one computation, not one per ball.
                as_of = compute_as_of_features(
                    self._conn,
                    state.venue_id,
                    batting,
                    bowling,
                    state.format,
                    state.match_date,
                )
                self._as_of[match_id] = as_of
                self._batting[match_id] = batting

            # Re-confirm the pin before writing, exactly as the HTTP path
            # does. A promotion mid-match would otherwise tag these rows
            # with a version that did not produce them.
            self._version_guard.confirm(self._conn)

            probability = predict_win_prob(self._model["artifact"], row, as_of)
            if probability is None:
                continue
            written += self._insert(match_id, row, delivery, probability, self._batting[match_id])

        self.logged += written
        return written

    def _insert(self, match_id: int, row, delivery, probability: float, batting_team_id: int) -> int:
        payload = {
            "p": probability,
            "innings": row.innings,
            "balls_bowled": row.balls_bowled,
            "balls_remaining": row.balls_remaining,
            "runs_required": row.runs_required,
            "score": row.score,
            "wickets": row.wickets,
            "target": row.target,
            "phase": row.phase,
        }
        # source='live': predicted before the result existed, which is the
        # only population whose accuracy means anything. Everything else
        # defaults to 'backfill' (migration 20260919000001).
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO predictions
                    (match_id, delivery_id, model_version, prediction_type, payload,
                     match_phase, created_at, innings, over_num, ball_in_over, source,
                     batting_team_id)
                VALUES (%s, NULL, %s, 'win_prob', %s, 'innings2', now(), %s, %s, %s, 'live', %s)
                ON CONFLICT (match_id, model_version, prediction_type, innings, over_num, ball_in_over)
                    WHERE innings IS NOT NULL
                    DO NOTHING
                RETURNING prediction_id
                """,
                (
                    match_id,
                    self._model["model_version"],
                    json.dumps(payload),
                    delivery.innings,
                    delivery.over_num,
                    delivery.ball_in_over,
                    batting_team_id,
                ),
            )
            return 1 if cur.fetchone() else 0

    def record_status(self, match_id: int, status: str) -> bool:
        """Persist the match lifecycle column. Returns True if it changed.

        `matches.status` was write-once until this existed. `_ensure_match_row`
        early-returns an existing row and then `ON CONFLICT DO NOTHING`, and
        this loop read match-end from an in-memory snapshot without writing it
        back - so a match inserted as 'live' stayed 'live' forever, and four
        rows on Supabase still said 'live' months after they ended. Any surface
        keying off the column got a permanently frozen answer that looked like
        the product working.

        NOT an outcome, and this does not weaken `finish`'s boundary below.
        Status is where the match is in its own lifecycle, which the provider
        snapshot knows; the outcome is who won, which only the corpus knows.

        `IS DISTINCT FROM` makes the overwhelmingly common unchanged case a
        no-op that touches no row and returns False, so the caller can log
        transitions only. That matters because this runs on every poll of
        every live match.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE matches SET status = %s "
                "WHERE match_id = %s AND status IS DISTINCT FROM %s",
                (status, match_id, status),
            )
            return cur.rowcount > 0

    def finish(self, match_id: int, *, log=None) -> None:
        """section 7.2's `match_end`. Stops tracking - and deliberately does
        NOT resolve outcomes.

        Resolution needs the true label, and the only place that is computed
        is `match_states.batting_team_won`, in the LOCAL corpus, which
        section 2.1 forbids a deployed process from touching. The provider's
        own `winner` is not a substitute: CricketData does not supply one,
        and taking it would create a second source of truth for the label
        that could disagree with the corpus and never be noticed.

        So `match_end` here means "this match is done, release its state";
        scoring the predictions is `models/resolve_outcomes.py`, run where
        the corpus is.
        """
        if match_id in self._finished:
            # A completed match can be listed more than once (ReplayClient
            # keeps it; a decided chase can precede the provider's
            # matchEnded), so this can be reached on several polls. Standing
            # rule 14: a line that repeats becomes furniture, and the next real
            # one is read past.
            return
        self._finished.add(match_id)

        (log or self._log)(
            f"match {match_id}: complete; outcomes resolve separately "
            f"(models.resolve_outcomes, where the corpus is)"
        )
        self._builders.pop(match_id, None)
        self._as_of.pop(match_id, None)
        self._batting.pop(match_id, None)
        self._declined.discard(match_id)


class Subscriptions:
    """The matches the worker has picked, and whether it can afford them
    (session 3).

    Each fixture check ranks the provider's fixture list (ingest.match_pick)
    and rebuilds the set greedily: every match already being tracked is kept
    first, whatever turns up - a started match is never dropped for a bigger
    one - then candidates are added biggest first while the provider's own
    remaining count, less RESERVE_CALLS, covers every subscribed match to
    its end. At most MAX_SUBSCRIPTIONS: the day's biggest match and, if the
    quota covers both, a second.

    The cost is summed as if the matches never overlapped. Overlapping ones
    share each currentMatches call, so this over-counts, which is the safe
    direction.
    """

    def __init__(self, resolve, log=_log) -> None:
        self._resolve = resolve
        self._log = log
        self._subs: dict[str, Candidate] = {}
        self._last_picked: tuple[str, ...] | None = None

    @property
    def provider_ids(self) -> set[str]:
        return set(self._subs)

    def check(self, client, now: datetime) -> dict | None:
        """One fixture check (one provider call). Returns the decision for
        pipeline_runs when the subscribed set changed, else None."""
        rows = client.list_fixtures()
        ranked = rank_candidates(rows, self._resolve, now)
        budget = client.budget

        kept: dict[str, Candidate] = {}
        cost = 0
        for provider_id, candidate in self._subs.items():
            snapshot = client.snapshot_of(provider_id)
            if snapshot is not None and snapshot.started and not snapshot.ended:
                kept[provider_id] = candidate
                cost += projected_calls(candidate.format, snapshot)

        picked, refused = [], []
        for candidate in ranked:
            if candidate.provider_id in kept:
                continue
            need = projected_calls(candidate.format)
            if len(kept) >= MAX_SUBSCRIPTIONS:
                reason = f"already subscribed to {MAX_SUBSCRIPTIONS}"
            elif budget.observed_at is None:
                reason = "quota unknown: no provider response yet"
            elif not budget.affords(cost + need):
                reason = (
                    f"needs ~{need} calls on top of {cost} committed; {budget.remaining} left "
                    f"today, {RESERVE_CALLS} held in reserve"
                )
            else:
                kept[candidate.provider_id] = candidate
                cost += need
                picked.append(candidate)
                continue
            refused.append((candidate, reason))

        self._subs = kept
        client.subscribed = set(kept)
        key = tuple(sorted(kept))
        if key == self._last_picked:
            return None
        self._last_picked = key
        record = {
            "picked": [_describe(c, projected_calls=projected_calls(c.format)) for c in kept.values()],
            "refused": [_describe(c, reason=reason) for c, reason in refused[:10]],
            "hits_today": budget.hits_today if budget.observed_at else None,
            "hits_limit": budget.hits_limit,
            "reserve": RESERVE_CALLS,
        }
        self._log(
            "pick: "
            + (", ".join(f"{' v '.join(c.teams)} (tier {c.tier}, {c.format})" for c in kept.values()) or "nothing")
            + f"; {len(refused)} refused; quota {record['hits_today']}/{budget.hits_limit}"
        )
        return record

    def polling_due(self, now: datetime) -> bool:
        return any(now >= c.start - PRE_START_LEAD for c in self._subs.values())

    def seconds_until_due(self, now: datetime) -> float | None:
        if not self._subs:
            return None
        return max(0.0, min((c.start - PRE_START_LEAD - now).total_seconds() for c in self._subs.values()))

    def prune(self, client) -> None:
        """Release matches that have ended."""
        for provider_id in list(self._subs):
            snapshot = client.snapshot_of(provider_id)
            if snapshot is not None and snapshot.ended:
                del self._subs[provider_id]
        client.subscribed = set(self._subs)


def _describe(candidate: Candidate, **extra) -> dict:
    return {
        "provider_id": candidate.provider_id,
        "teams": list(candidate.teams),
        "tier": candidate.tier,
        "format": candidate.format,
        "start": candidate.start.isoformat(),
        **extra,
    }


def _chase_decided(row, delivery) -> bool:
    """Is the chase over once this ball is counted?

    `row` is the state BEFORE the ball (the builder's discipline), so the
    ball's own runs, wicket and legality are added here. A chase ends when
    the target is reached, the side is all out, or the balls run out -
    whichever comes first, and whatever the provider has said yet.
    """
    runs = delivery.runs_batter + delivery.runs_extras
    wickets = delivery.wicket_count if delivery.wicket_type is not None else 0
    legal = delivery.extra_type not in ("wide", "noball")
    if row.target is not None and row.score + runs >= row.target:
        return True
    if row.wickets + wickets >= 10:
        return True
    return row.balls_remaining - (1 if legal else 0) <= 0


def run_once(
    client: CricketDataClient,
    tracked: dict,
    *,
    log=_log,
    predictor: LivePredictor | None = None,
    idle_interval: float = IDLE_INTERVAL_SECONDS,
) -> float:
    """One iteration. Returns the number of seconds to wait before the next.

    Returns the interval rather than sleeping itself so the loop stays
    testable without a clock.

    `predictor` is optional so the polling logic stays testable without a
    model, a database or an artifact - the pre-Phase-3 tests construct no
    predictor and exercise exactly the path they always did.
    """
    live = client.list_live_matches()
    if not live:
        log(
            f"heartbeat: no live matches; next check in {idle_interval:.0f}s "
            f"({int(86_400 / idle_interval)} calls/day at this cadence)"
        )
        tracked.clear()
        return idle_interval

    interval = IDLE_INTERVAL_SECONDS
    for summary in live:
        match_id = summary.match_id
        deliveries = client.poll(match_id)
        tracked[match_id] = tracked.get(match_id, 0) + len(deliveries)
        scored = ""

        # Status is read on EVERY poll, not only when balls arrive. It used to
        # sit inside the `and deliveries` branch below, which meant a match
        # that ended on a poll returning nothing new never transitioned and
        # never finished - and the last poll of a match is exactly the one
        # most likely to be empty. `get_match_state` reads the snapshot
        # `list_live_matches` already fetched plus one local row, so this
        # costs no provider quota.
        state = client.get_match_state(match_id) if predictor is not None else None

        # A first sighting is observed even with no new balls, so the
        # catch-up reaches the builder: a worker restarted after the winning
        # ball, before the provider's matchEnded, still decides the chase.
        if predictor is not None and (deliveries or not predictor.has_baseline(match_id)):
            written = predictor.observe(client, state, deliveries)
            if deliveries:
                scored = f", {written} prediction(s) logged"

        # Complete when the provider says so OR when the chase is decided
        # (target reached, all out, balls exhausted). Observed first so a
        # ball that decides the chase is counted on the poll that brings it.
        status = None
        if state is not None:
            status = "complete" if match_id in predictor.decided else state.status
        if status is not None and predictor.record_status(match_id, status):
            log(f"match {match_id}: status -> {status}")

        # section 7.2's match_end: resolve once the result is known. Outside
        # the deliveries branch for the same reason as the status read.
        if status == "complete":
            predictor.finish(match_id, log=log)

        log(
            f"match {match_id}: +{len(deliveries)} deliveries "
            f"({tracked[match_id]} this session){scored}"
        )
        interval = min(interval, client.next_interval(match_id))
    return interval


class Worker:
    """One iteration of the always-on worker once it picks its own matches
    (session 3): the hourly fixture check, then run_once only while a picked
    match is due. Idle, currentMatches is not called at all.

    `step` takes the time and returns how long to wait, so the harness can
    drive the real thing on a fake clock (ingest.snapshot_replay)."""

    def __init__(self, client, subs: Subscriptions, predictor, *, log=_log, record_pick=lambda _r: None) -> None:
        self.client = client
        self.subs = subs
        self.predictor = predictor
        self.tracked: dict = {}
        self.next_check: datetime | None = None
        self._log = log
        self._record_pick = record_pick

    def step(self, now: datetime) -> float:
        if self.next_check is None or now >= self.next_check:
            self.next_check = now + timedelta(seconds=FIXTURE_CHECK_SECONDS)
            record = self.subs.check(self.client, now)
            if record is not None:
                self._record_pick(record)

        if self.subs.polling_due(now):
            interval = run_once(
                self.client, self.tracked, log=self._log, predictor=self.predictor,
                idle_interval=INTERVAL_SPARSE,
            )
            self.subs.prune(self.client)
        else:
            self._log(
                f"heartbeat: no picked match due; fixture check hourly "
                f"({FIXTURE_CALLS_PER_DAY} calls/day idle)"
            )
            self.tracked.clear()
            interval = IDLE_INTERVAL_SECONDS
            due = self.subs.seconds_until_due(now)
            if due is not None:
                interval = min(interval, due)
        # Never sleep past the next fixture check.
        interval = min(interval, (self.next_check - now).total_seconds())
        return max(interval, SLEEP_SLICE_SECONDS)


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
    # run_checks already resolved and verified the pinned artifact and built
    # the version guard; the worker simply had nothing to do with them until
    # Phase 3 session 1.
    predictor = LivePredictor(conn, state["model"], state["version_guard"], log=log)
    worker = Worker(
        client, Subscriptions(_pick_resolver(client), log=log), predictor,
        log=log, record_pick=lambda record: _record_pick(client._conn, record, log),
    )
    shutdown = Shutdown().install()
    iterations = 0
    # True once a cycle has failed on the database, so the next cycle asks
    # Postgres before paying the provider. Cleared on reconnection.
    degraded = False

    log(
        f"worker picking; fixture check every {FIXTURE_CHECK_SECONDS:.0f}s "
        f"({FIXTURE_CALLS_PER_DAY}/day idle), heartbeat every {IDLE_INTERVAL_SECONDS:.0f}s"
    )
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
            predictor._conn = conn
            degraded = False
            log("reconnected to Supabase; resuming normal polling")

        try:
            interval = worker.step(datetime.now(timezone.utc))
        except BudgetExhausted as exc:
            # A quota hold, not a database failure: the connection is fine
            # and must not be dropped. No call was made.
            log(f"quota: {exc}; next check in {IDLE_INTERVAL_SECONDS:.0f}s")
            interval = IDLE_INTERVAL_SECONDS
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
                    predictor._conn = conn
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
    return {"iterations": iterations, "tracked": worker.tracked}


def _pick_resolver(client: CricketDataClient):
    """Team lookup for the fixture ranking, through the adapter's own
    resolution (so the men's-senior guard and allow_create=False apply)."""

    def resolve(name: str) -> Team | None:
        try:
            team_id = client._resolve_team(name)
        except UnsupportedMatch:
            return None
        with client._conn.cursor() as cur:
            cur.execute("SELECT full_member FROM teams WHERE team_id = %s", (team_id,))
            row = cur.fetchone()
        return Team(team_id=team_id, full_member=bool(row and row[0]))

    return resolve


def _record_pick(conn, record: dict, log=_log) -> None:
    """One pipeline_runs row per change of pick: `worker_pick`, carrying what
    was picked, what was refused and why, and the provider's quota figure.
    Best effort, like startup.record_start."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pipeline_runs (pipeline, started_at, finished_at, status, counts) "
                "VALUES ('worker_pick', now(), now(), 'success', %s)",
                (json.dumps(record),),
            )
    except Exception as exc:  # noqa: BLE001 - see docstring
        log(f"could not record the pick in pipeline_runs: {type(exc).__name__}")


if __name__ == "__main__":
    run()
