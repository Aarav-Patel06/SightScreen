"""Snapshot replay: a whole match fed to the REAL worker as CricketData
responses, on a fake clock (session 3).

Nothing live has run end to end through the worker yet, and waiting for a
match to find out whether the pick, the innings break or the chase work is
a calendar problem posing as a test. This module turns a Cricsheet match
into the provider responses the worker would have seen - `cricScore` for the
fixture check, `currentMatches` for polling, the provider's `hitsToday`
rising by one per call - and drives `serving.live_loop.Worker.step` with
them, exactly as `run()` does, only with the clock advanced by the interval
the worker asked for instead of slept.

  Timeline      the match as the provider would report it at any moment:
                a fixture until the scheduled start, then live, then ended.
                A legal ball every SECONDS_PER_BALL, a changeover between
                overs, an innings break between innings.
  ClockTransport serves a Timeline at the fake clock's time; counts calls.
  run_replay    one match, fixture to end, through the real worker.
  check         what must hold afterwards, raised as HarnessFailure: the
                match complete, the chase scored from its first ball (or
                from the join, marked), the target and chasing side right,
                the state exact at every over boundary, the calls within
                the budget's projection.

Response shapes are copied from the recorded bodies in
tests/fixtures/cricketdata/ (currentMatches, cricScore), not from memory.
tests/serving/test_snapshot_replay.py runs it, including against sabotaged
workers, to prove the check catches a broken step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ingest.cricketdata import CricketDataClient, LiveBudget, projected_calls
from serving.live_loop import LivePredictor, Subscriptions, Worker, _pick_resolver

SECONDS_PER_BALL = 30.0
SECONDS_CHANGEOVER = 60.0
# Deliberately not whole minutes: a real break never is, and a round one lines
# up with a poller's round cadence, hiding a worker that sleeps through it.
INNINGS_BREAK = {"T20": timedelta(minutes=15, seconds=41), "ODI": timedelta(minutes=31, seconds=37)}
# How long an ended match stays in currentMatches.
ENDED_LISTED_FOR = timedelta(hours=6)
# Most legal balls the chase may move between two polls: see check().
MAX_SPAN = 2


class HarnessFailure(AssertionError):
    """What must hold after a replay did not."""


@dataclass(frozen=True)
class _Ball:
    at: datetime
    innings: int  # 1 or 2
    legal_before: int
    runs_before: int
    wickets_before: int
    legal: bool
    runs: int
    wickets: int


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


class Timeline:
    """A Cricsheet match as CricketData would report it over time."""

    def __init__(self, cricsheet: dict, start: datetime, provider_id: str = "replay-0001") -> None:
        info = cricsheet["info"]
        self.format = info["match_type"]  # Cricsheet: "ODI" or "T20"
        self.teams: tuple[str, str] = tuple(info["teams"])
        self.batting = tuple(inn["team"] for inn in cricsheet["innings"][:2])
        self.start = start
        self.provider_id = provider_id
        self.series = (info.get("event") or {}).get("name") or f"{self.teams[0]} v {self.teams[1]}"
        number = (info.get("event") or {}).get("match_number") or 1
        kind = "ODI" if self.format == "ODI" else "T20I"
        self.name = f"{self.teams[0]} vs {self.teams[1]}, {_ordinal(number)} {kind}, {self.series}"
        self.venue = ", ".join(v for v in (info.get("venue"), info.get("city")) if v)
        self.outcome = info.get("outcome") or {}

        self.balls: list[_Ball] = []
        clock = start
        for index, innings in enumerate(cricsheet["innings"][:2]):
            if index == 1:
                clock = self.balls[-1].at + INNINGS_BREAK[self.format]
                self.chase_starts = clock
            legal = runs = wickets = 0
            for over_index, over in enumerate(innings["overs"]):
                if over_index:
                    clock += timedelta(seconds=SECONDS_CHANGEOVER)
                for delivery in over["deliveries"]:
                    clock += timedelta(seconds=SECONDS_PER_BALL)
                    extras = delivery.get("extras") or {}
                    is_legal = "wides" not in extras and "noballs" not in extras
                    taken = len(delivery.get("wickets") or [])
                    total = delivery["runs"]["total"]
                    self.balls.append(_Ball(clock, index + 1, legal, runs, wickets, is_legal, total, taken))
                    legal += 1 if is_legal else 0
                    runs += total
                    wickets += taken
        self.ends = self.balls[-1].at + timedelta(seconds=SECONDS_PER_BALL)
        first = [b for b in self.balls if b.innings == 1]
        self.target = first[-1].runs_before + first[-1].runs + 1

    # -- truth, for check() --

    def chase(self) -> list[_Ball]:
        return [b for b in self.balls if b.innings == 2]

    # -- what the provider says at time `now` --

    def _totals(self, now: datetime) -> list[tuple[int, int, int]]:
        totals: dict[int, list[int]] = {}
        for ball in self.balls:
            if ball.at > now:
                break
            totals[ball.innings] = [
                ball.runs_before + ball.runs,
                ball.wickets_before + ball.wickets,
                ball.legal_before + (1 if ball.legal else 0),
            ]
        if 2 not in totals and self.chase_starts <= now:
            totals[2] = [0, 0, 0]  # the chase has an entry before its first ball
        return [tuple(totals[i]) for i in sorted(totals)]

    def _status(self, now: datetime) -> str:
        if now < self.start:
            return f"Match starts at {self.start:%b %d, %H:%M} GMT"
        if now >= self.ends:
            by = self.outcome.get("by") or {}
            if "runs" in by:
                return f"{self.outcome['winner']} won by {by['runs']} runs"
            if "wickets" in by:
                return f"{self.outcome['winner']} won by {by['wickets']} wkts"
            return "Match over"
        return "Live"

    def current_matches(self, now: datetime) -> dict:
        if now < self.start or now >= self.ends + ENDED_LISTED_FOR:
            return {"status": "success", "data": []}
        score = [
            {"r": r, "w": w, "o": legal // 6 + (legal % 6) / 10, "inning": f"{self.batting[i]} Inning 1"}
            for i, (r, w, legal) in enumerate(self._totals(now))
        ]
        row = {
            "id": self.provider_id,
            "name": self.name,
            "matchType": self.format.lower(),
            "status": self._status(now),
            "venue": self.venue,
            "date": self.start.date().isoformat(),
            "dateTimeGMT": self.start.strftime("%Y-%m-%dT%H:%M:%S"),
            "teams": list(self.teams),
            "teamInfo": [{"name": t} for t in self.teams],
            "score": score,
            "series_id": "replay-series",
            "fantasyEnabled": False,
            "bbbEnabled": False,
            "hasSquad": True,
            "matchStarted": True,
            "matchEnded": now >= self.ends,
        }
        return {"status": "success", "data": [row]}

    def cric_score(self, now: datetime) -> dict:
        ms = "fixture" if now < self.start else ("result" if now >= self.ends else "live")
        totals = self._totals(now)
        by_team = {self.batting[i]: f"{r}/{w} ({legal // 6}.{legal % 6})" for i, (r, w, legal) in enumerate(totals)}
        row = {
            "id": self.provider_id,
            "dateTimeGMT": self.start.strftime("%Y-%m-%dT%H:%M:%S"),
            "matchType": self.format.lower(),
            "ms": ms,
            "series": self.series,
            "status": self._status(now),
            "t1": f"{self.teams[0]} [{self.teams[0][:3].upper()}]",
            "t2": f"{self.teams[1]} [{self.teams[1][:3].upper()}]",
            "t1s": by_team.get(self.teams[0], ""),
            "t2s": by_team.get(self.teams[1], ""),
        }
        return {"status": "success", "data": [row]}


@dataclass
class Clock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


@dataclass
class ClockTransport:
    """Serves the timeline at the clock's time, with the provider's own
    counter rising by one per call. `mutate(endpoint, body)` lets a test
    corrupt a response on its way to the worker."""

    timeline: Timeline
    clock: Clock
    hits_start: int = 0
    mutate: object = None
    calls: int = 0
    by_endpoint: dict = field(default_factory=dict)
    # The TRUE chase legal-ball count at each currentMatches call - what the
    # provider had, not what the worker made of it.
    chase_seen: list = field(default_factory=list)

    def get(self, endpoint: str, params: dict[str, str]) -> dict:
        self.calls += 1
        self.by_endpoint[endpoint] = self.by_endpoint.get(endpoint, 0) + 1
        if endpoint == "currentMatches":
            totals = self.timeline._totals(self.clock())
            if len(totals) > 1:
                self.chase_seen.append(totals[1][2])
            body = self.timeline.current_matches(self.clock())
        elif endpoint == "cricScore":
            body = self.timeline.cric_score(self.clock())
        else:
            raise AssertionError(f"the worker called {endpoint!r}, which a replay does not serve")
        body["info"] = {"hitsToday": self.hits_start + self.calls, "hitsUsed": 1, "hitsLimit": 2000}
        if self.mutate is not None:
            body = self.mutate(endpoint, body)
        return body


@dataclass
class ReplayResult:
    timeline: Timeline
    transport: ClockTransport
    steps: int
    picks: list
    started_at: datetime


def load_cricsheet(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_replay(
    conn,
    timeline: Timeline,
    model: dict,
    version_guard,
    *,
    begin: datetime | None = None,
    hits_start: int = 0,
    mutate=None,
    log=lambda _m: None,
) -> ReplayResult:
    """The match through the real worker, from `begin` (default: three
    hours before the start, so the pick happens from the fixture list) to
    an hour after the end. `begin` inside the chase is a late join."""
    begin = begin or timeline.start - timedelta(hours=3)
    clock = Clock(begin)
    transport = ClockTransport(timeline, clock, hits_start=hits_start, mutate=mutate)
    client = CricketDataClient(conn, transport, LiveBudget(clock=clock))
    predictor = LivePredictor(conn, model, version_guard, log=log)
    picks: list = []
    worker = Worker(
        client, Subscriptions(_pick_resolver(client), log=log), predictor, log=log, record_pick=picks.append
    )
    steps = 0
    stop = timeline.ends + timedelta(hours=1)
    while clock.now <= stop:
        interval = worker.step(clock.now)
        steps += 1
        clock.now += timedelta(seconds=interval)
    return ReplayResult(timeline, transport, steps, picks, begin)


def check(conn, result: ReplayResult, *, joined_late: bool = False) -> dict:
    """Raise HarnessFailure listing everything that does not hold; return a
    summary when all of it does."""
    timeline = result.timeline
    problems: list[str] = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT match_id, status FROM matches WHERE external_ids->>'cricketdata' = %s",
            (timeline.provider_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HarnessFailure("the worker never created the match row - the pick or the subscription failed")
        match_id, status = row
        cur.execute("SELECT team_id FROM teams WHERE name = %s", (timeline.batting[1],))
        chaser = cur.fetchone()[0]
        cur.execute(
            "SELECT (payload->>'balls_bowled')::int, (payload->>'score')::int, (payload->>'wickets')::int, "
            "(payload->>'target')::int, batting_team_id FROM predictions "
            "WHERE match_id = %s AND source = 'live' AND innings = 2 ORDER BY prediction_id",
            (match_id,),
        )
        rows = cur.fetchall()

    if status != "complete":
        problems.append(f"match status is {status!r}, not 'complete'")
    chase = timeline.chase()
    last_legal = max(b.legal_before for b in chase if b.legal)
    if not rows:
        problems.append("no chase predictions were written")
    else:
        balls = [r[0] for r in rows]
        first_expected = 0
        if joined_late:
            joined = timeline._totals(result.started_at)
            first_expected = joined[1][2] if len(joined) > 1 else 0
        if min(balls) != first_expected:
            problems.append(
                f"the chase's first prediction is at ball {min(balls)}, expected {first_expected} "
                f"({'the join' if joined_late else 'subscribed before the chase: no gap'})"
            )
        if max(balls) != last_legal:
            problems.append(f"the last prediction is at ball {max(balls)}, the chase's last legal ball is {last_legal}")
        if {r[3] for r in rows} != {timeline.target}:
            problems.append(f"targets {sorted({r[3] for r in rows})}, expected {timeline.target}")
        if {r[4] for r in rows} != {chaser}:
            problems.append(f"batting_team_id {sorted({r[4] for r in rows})}, expected the chaser {chaser}")
        # Exact at every over boundary: the ball before it was caught alone
        # (in-play polls are faster than balls), so the state is the truth,
        # not a span's convention.
        truth: dict[int, set[tuple[int, int]]] = {}
        for ball in chase:
            truth.setdefault(ball.legal_before, set()).add((ball.runs_before, ball.wickets_before))
        wrong = [
            (b, s, w) for b, s, w, _t, _bt in rows
            if b % 6 == 0 and b >= first_expected and (s, w) not in truth.get(b, set())
        ]
        if wrong:
            problems.append(f"{len(wrong)} over-boundary state(s) differ from the match, e.g. {wrong[:3]}")
    # Polled fast enough to predict each ball as it happens. A span of more
    # than MAX_SPAN legal balls between two polls means balls scored after
    # the fact with invented states (the span puts its runs on its last
    # ball). Two is the accepted cost of the 45s changeover cadence. Counted
    # from ball 0, so a first chase poll that is already several balls in
    # is a span; a late join counts from its first poll, the catch-up.
    seen = result.transport.chase_seen if joined_late else [0, *result.transport.chase_seen]
    spans = [b - a for a, b in zip(seen, seen[1:])]
    if spans and max(spans) > MAX_SPAN:
        problems.append(f"the chase moved {max(spans)} legal balls between two polls (at most {MAX_SPAN})")
    budget = projected_calls(timeline.format)
    if result.transport.calls > budget:
        problems.append(f"{result.transport.calls} provider calls, over the {budget} the budget projects")
    if problems:
        raise HarnessFailure("; ".join(problems))
    return {
        "match_id": match_id,
        "predictions": len(rows),
        "calls": result.transport.calls,
        "calls_by_endpoint": dict(result.transport.by_endpoint),
        "budget": budget,
        "steps": result.steps,
    }
