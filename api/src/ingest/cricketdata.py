"""CricketData live adapter via snapshot reconstruction (SPEC.md sections
4.2, 7.1, Phase 2 session 2).

CricketData has no usable ball-by-ball feed. Its `match_bbb` endpoint is
entitled on our key but returns only penalty/extras deliveries (11-20 balls
for matches of 190-574) and carries no wicket field at all - see the
provider spike recorded in SPEC.md section 15. So this adapter does not
read a delivery stream; it *reconstructs* one by diffing consecutive
scorecard snapshots.

What that recovers exactly, because it comes straight from the provider's
own aggregates: score, wickets, balls bowled, and therefore
balls_remaining, wickets_in_hand, runs_required, required/current run
rate, rrr_minus_crr, target and phase.

What it recovers approximately: partnership_runs, partnership_balls and
balls_since_wicket, which depend on *which* ball in a multi-ball poll gap
took the wicket. Derived (not dropped) from wicket-count deltas, with the
documented convention that an ambiguous wicket is attributed to the last
ball of the span, and every such ball flagged INFERRED.

What it cannot recover at all: per-delivery striker and bowler identity.
That costs the Phase 1 model nothing (it uses no player features) but
blocks Phase 5's WPA and player predictions - logged in SPEC.md section 15
rather than discovered later.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol

from ingest.entity_resolution import resolve_team, resolve_venue
from ingest.live_client import (
    Delivery,
    MatchState,
    MatchSummary,
    ReconstructionConfidence,
)

BASE_URL = "https://api.cricapi.com/v1"
SOURCE = "cricketdata"

# SPEC.md section 7.1 polls every 15s; we tighten to that mid-over and back
# off between overs, where the next ball is 40s+ away regardless.
INTERVAL_IN_PLAY = 15.0
INTERVAL_BETWEEN_OVERS = 45.0
INTERVAL_QUOTA_STRAINED = 60.0
INTERVAL_QUOTA_CRITICAL = 120.0

# Fractions of the daily budget at which polling degrades rather than stops.
QUOTA_STRAINED_AT = 0.90
QUOTA_CRITICAL_AT = 0.98

NOMINAL_BALLS = {"T20": 120, "ODI": 300}
FORMAT_BY_MATCH_TYPE = {"t20": "T20", "odi": "ODI"}

# "...Match reduced to 21 overs per side due to rain, DLS target 106"
_REDUCED_OVERS_RE = re.compile(r"reduced to (\d+(?:\.\d+)?) overs", re.IGNORECASE)
_DLS_TARGET_RE = re.compile(r"(?:DLS|D/L)\s+target\s+(\d+)", re.IGNORECASE)


class CricketDataError(Exception):
    """Any provider-boundary failure."""


class UnsupportedMatch(CricketDataError):
    """The worker must refuse to track this match (unresolvable team, a
    format we have no model for, ...). Refusing is the designed
    degradation, not an error to swallow."""


class BudgetExhausted(CricketDataError):
    """Refused before spending a call we cannot afford."""


# --- Transport ---------------------------------------------------------------


class Transport(Protocol):
    """Injectable so every test runs offline against recorded fixtures.
    SPEC-mandated (Decision 6): no test may hit the network or burn quota."""

    def get(self, endpoint: str, params: dict[str, str]) -> dict: ...


class HttpTransport:
    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def get(self, endpoint: str, params: dict[str, str]) -> dict:
        query = urllib.parse.urlencode({"apikey": self._api_key, **params})
        with urllib.request.urlopen(f"{BASE_URL}/{endpoint}?{query}", timeout=self._timeout) as response:
            return json.loads(response.read().decode("utf-8"))


class FixtureTransport:
    """Serves recorded responses by endpoint name, so the adapter's own
    parsing and reconstruction are exercised against bodies the real
    provider actually produced."""

    def __init__(self, fixture_dir: Path, mapping: dict[str, str] | None = None) -> None:
        self._dir = fixture_dir
        self._mapping = mapping or {
            "currentMatches": "current_matches",
            "cricScore": "cric_score",
            "matches": "matches_page0",
            "match_bbb": "match_bbb_extras_only",
            "match_scorecard": "match_scorecard_missing",
        }

    def get(self, endpoint: str, params: dict[str, str]) -> dict:
        name = self._mapping.get(endpoint)
        if name is None:
            raise CricketDataError(f"no fixture mapped for endpoint {endpoint!r}")
        return json.loads((self._dir / f"{name}.json").read_text(encoding="utf-8"))


# --- Budget ------------------------------------------------------------------


@dataclass
class LiveBudget:
    """Decision 3: enforced, not hoped.

    Authoritative from the provider itself - every successful response
    carries `info.hitsToday`/`hitsLimit`, so the count survives a worker
    restart rather than drifting away from a local tally.
    """

    hits_today: int = 0
    hits_limit: int = 2000

    def observe(self, body: dict) -> None:
        info = body.get("info") or {}
        if isinstance(info.get("hitsToday"), int):
            self.hits_today = info["hitsToday"]
        if isinstance(info.get("hitsLimit"), int):
            self.hits_limit = info["hitsLimit"]

    @property
    def remaining(self) -> int:
        return max(0, self.hits_limit - self.hits_today)

    @property
    def consumed_fraction(self) -> float:
        return 1.0 if self.hits_limit <= 0 else self.hits_today / self.hits_limit

    def estimated_cost(self, format_: str, balls_bowled: int, interval_seconds: float) -> int:
        """Polls needed to finish this match: remaining balls at ~30s of
        real time each, divided by the poll interval."""
        remaining_balls = max(0, NOMINAL_BALLS.get(format_, 120) * 2 - balls_bowled)
        seconds_remaining = remaining_balls * 30.0
        return int(seconds_remaining / max(interval_seconds, 1.0))

    def require_affordable(self, format_: str, balls_bowled: int, interval_seconds: float) -> None:
        cost = self.estimated_cost(format_, balls_bowled, interval_seconds)
        if cost > self.remaining:
            raise BudgetExhausted(
                f"refusing to start: needs ~{cost} calls to finish, {self.remaining} left today "
                f"({self.hits_today}/{self.hits_limit} used)"
            )

    def interval(self, between_overs: bool) -> float:
        """Degrade rather than stop when the quota runs low (your explicit
        instruction: 60s polling beats stopping)."""
        if self.consumed_fraction >= QUOTA_CRITICAL_AT:
            return INTERVAL_QUOTA_CRITICAL
        if self.consumed_fraction >= QUOTA_STRAINED_AT:
            return INTERVAL_QUOTA_STRAINED
        return INTERVAL_BETWEEN_OVERS if between_overs else INTERVAL_IN_PLAY


# --- Snapshot parsing --------------------------------------------------------


def overs_to_balls(overs: float) -> int:
    """18.1 overs is 18 overs and 1 ball, not 18.1 of anything."""
    whole = int(overs)
    return whole * 6 + round((overs - whole) * 10)


@dataclass(frozen=True)
class InningsSnapshot:
    runs: int
    wickets: int
    balls: int
    # Who is batting in this innings, as the provider names them (Phase 3
    # session 1). CricketData labels each score entry "<Team> Inning <n>",
    # and until now the parser threw that away - which left the live worker
    # unable to say which side was chasing, and therefore unable to compute
    # the as-of features a win probability needs. `Delivery` carries no team
    # identity and `currentMatches` carries no toss, so this string is the
    # ONLY place the provider says it. None when the label does not parse.
    batting_team: str | None = None


@dataclass(frozen=True)
class MatchSnapshot:
    """One poll's view of a match, parsed from `currentMatches`."""

    provider_id: str
    name: str
    format: str | None  # None for a format we have no model for
    status: str
    venue: str
    teams: tuple[str, ...]
    innings: tuple[InningsSnapshot, ...]
    started: bool
    ended: bool
    reduced_overs: float | None
    dls_target: int | None
    observed_at: datetime

    @property
    def ball_count(self) -> int:
        return sum(i.balls for i in self.innings)

    @property
    def current_innings(self) -> int | None:
        return len(self.innings) if self.innings else None


# "Guyana Amazon Warriors Inning 1" -> "Guyana Amazon Warriors". Anchored at
# the end so a team whose own name contains the word survives intact.
_INNING_LABEL_RE = re.compile(r"^(?P<team>.+?)\s+inning\s*\d+\s*$", re.I)


def _batting_team(label: str | None) -> str | None:
    """The batting side from a provider innings label, or None.

    None rather than a guess: a caller that cannot name the batting team
    must decline to predict, and a wrong team id would silently swap the
    two Elo ratings that feed elo_diff.
    """
    if not label:
        return None
    match = _INNING_LABEL_RE.match(label.strip())
    return match.group("team").strip() if match else None


def parse_match(raw: dict, observed_at: datetime | None = None) -> MatchSnapshot:
    status = raw.get("status") or ""
    reduced = _REDUCED_OVERS_RE.search(status)
    dls = _DLS_TARGET_RE.search(status)
    innings = tuple(
        InningsSnapshot(
            runs=int(s.get("r") or 0),
            wickets=int(s.get("w") or 0),
            balls=overs_to_balls(float(s.get("o") or 0)),
            batting_team=_batting_team(s.get("inning")),
        )
        for s in (raw.get("score") or [])
    )
    return MatchSnapshot(
        provider_id=raw["id"],
        name=raw.get("name") or "",
        # Unknown matchType is real: some rows in the live `matches` feed
        # omit it entirely (hit during the spike).
        format=FORMAT_BY_MATCH_TYPE.get((raw.get("matchType") or "").lower()),
        status=status,
        venue=raw.get("venue") or "",
        teams=tuple(raw.get("teams") or ()),
        innings=innings,
        started=bool(raw.get("matchStarted")),
        ended=bool(raw.get("matchEnded")),
        reduced_overs=float(reduced.group(1)) if reduced else None,
        dls_target=int(dls.group(1)) if dls else None,
        observed_at=observed_at or datetime.now(timezone.utc),
    )


def scheduled_balls(snapshot: MatchSnapshot) -> tuple[int, bool]:
    """(balls, reduced_unknown). A reduced match only states its new length
    inside the free-text status, so when parsing fails we fall back to the
    nominal length and say so rather than silently serving a wrong
    balls_remaining."""
    if snapshot.reduced_overs is not None:
        return overs_to_balls(snapshot.reduced_overs), False
    nominal = NOMINAL_BALLS.get(snapshot.format or "", 120)
    looks_reduced = "reduced" in snapshot.status.lower() or snapshot.dls_target is not None
    return nominal, looks_reduced


# --- Decision 4: the validation gate -----------------------------------------


class TransitionVerdict(str, Enum):
    ACCEPT = "accept"
    NO_CHANGE = "no_change"
    REJECT = "reject"
    CORRECTION = "correction"


def validate_transition(prev: MatchSnapshot | None, nxt: MatchSnapshot) -> tuple[TransitionVerdict, str]:
    """The boundary nothing gets past. The provider will duplicate, reorder,
    correct and drop; none of that may reach match_states."""
    if prev is None:
        return TransitionVerdict.ACCEPT, "first observation"
    if prev.provider_id != nxt.provider_id:
        return TransitionVerdict.REJECT, "snapshot is for a different match"
    if len(nxt.innings) < len(prev.innings):
        return TransitionVerdict.REJECT, "innings disappeared"

    for index, before in enumerate(prev.innings):
        if index >= len(nxt.innings):
            break
        after = nxt.innings[index]
        if after.balls < before.balls:
            return TransitionVerdict.REJECT, f"innings {index + 1} balls went backwards"
        if after.runs < before.runs or after.wickets < before.wickets:
            # Runs/wickets falling while the ball count holds or advances is
            # a scoring correction, not time travel. Re-baseline; never emit
            # a negative delivery.
            return TransitionVerdict.CORRECTION, f"innings {index + 1} score revised down"

    if nxt.ball_count == prev.ball_count and len(nxt.innings) == len(prev.innings):
        return TransitionVerdict.NO_CHANGE, "no new balls"
    return TransitionVerdict.ACCEPT, "ok"


# --- Reconstruction ----------------------------------------------------------


def reconstruct_innings(
    before: InningsSnapshot | None,
    after: InningsSnapshot,
    innings_no: int,
    over_offset: int = 0,
) -> list[Delivery]:
    """Turn the change between two snapshots of one innings into deliveries.

    Exactly one new ball means runs and any wicket attribute to it
    unambiguously (CONFIRMED). More than one means the split had to be
    assumed (INFERRED, every ball). Zero new balls with new runs is an
    extra, which correctly does not advance the legal ball count.
    """
    before = before or InningsSnapshot(0, 0, 0)
    delta_balls = after.balls - before.balls
    delta_runs = after.runs - before.runs
    delta_wickets = after.wickets - before.wickets
    if delta_balls <= 0 and delta_runs <= 0 and delta_wickets <= 0:
        return []

    if delta_balls == 0:
        # An extra: a wide or no-ball. Which of the two is unknowable from a
        # scorecard snapshot, and only legality matters downstream, so this
        # records 'wide' - the flag is what tells you not to trust the
        # specific type.
        return [
            Delivery(
                innings=innings_no,
                over_num=(before.balls + over_offset) // 6,
                ball_in_over=(before.balls % 6) + 1,
                legal_ball_num=before.balls,
                batter_id=None,
                non_striker_id=None,
                bowler_id=None,
                runs_batter=0,
                runs_extras=delta_runs,
                extra_type="wide",
                wicket_type="run out" if delta_wickets > 0 else None,
                player_out_id=None,
                wicket_count=max(1, delta_wickets) if delta_wickets > 0 else 1,
                confidence=ReconstructionConfidence.CONFIRMED
                if delta_runs <= 1 and delta_wickets == 0
                else ReconstructionConfidence.INFERRED,
            )
        ]

    confidence = (
        ReconstructionConfidence.CONFIRMED if delta_balls == 1 else ReconstructionConfidence.INFERRED
    )
    deliveries: list[Delivery] = []
    for offset in range(delta_balls):
        legal_ball_num = before.balls + offset + 1
        is_last = offset == delta_balls - 1
        deliveries.append(
            Delivery(
                innings=innings_no,
                over_num=(legal_ball_num - 1 + over_offset) // 6,
                ball_in_over=((legal_ball_num - 1) % 6) + 1,
                legal_ball_num=legal_ball_num,
                batter_id=None,
                non_striker_id=None,
                bowler_id=None,
                # Convention, measured rather than assumed correct: runs and
                # any wicket in a multi-ball span go on the span's last ball.
                runs_batter=delta_runs if is_last else 0,
                runs_extras=0,
                extra_type=None,
                wicket_type="caught" if (is_last and delta_wickets > 0) else None,
                player_out_id=None,
                wicket_count=delta_wickets if (is_last and delta_wickets > 0) else 1,
                confidence=confidence,
            )
        )
    return deliveries


def _renumber_within_over(existing: list[Delivery], fresh: list[Delivery]) -> list[Delivery]:
    """Give every delivery its position within the over, counting extras.

    `reconstruct` derives ball_in_over from the LEGAL ball count, so a wide
    and the legal ball after it both come out as ball 3 of the over. The
    corpus uses the other convention - supabase/SCHEMA.md spells out why:
    "the legal-ball x.y notation would collide with the UNIQUE(match_id,
    innings, over_num, ball_in_over) constraint on every over with an
    illegal delivery." It predicted this exact collision.

    Found in Phase 3 session 1 by a live match: the ball key on
    `predictions` is (innings, over_num, ball_in_over), so a colliding pair
    meant the delivery after every extra was silently discarded by ON
    CONFLICT DO NOTHING - a live win-probability curve quietly missing a
    ball, which is the failure mode the key exists to prevent, arriving
    through the key itself.

    Renumbering here rather than inside `reconstruct` keeps that function a
    pure delta between two snapshots; the position within an over is a fact
    about the accumulated stream, which only the caller holds.
    """
    if not fresh:
        return fresh
    counts: dict[tuple[int, int], int] = {}
    for delivery in existing:
        key = (delivery.innings, delivery.over_num)
        counts[key] = counts.get(key, 0) + 1
    renumbered = []
    for delivery in fresh:
        key = (delivery.innings, delivery.over_num)
        counts[key] = counts.get(key, 0) + 1
        renumbered.append(
            Delivery(**{**delivery.__dict__, "ball_in_over": counts[key]})
        )
    return renumbered


def reconstruct(prev: MatchSnapshot | None, nxt: MatchSnapshot) -> list[Delivery]:
    """Every delivery implied by the move from one snapshot to the next,
    across however many innings changed."""
    deliveries: list[Delivery] = []
    for index, after in enumerate(nxt.innings):
        before = prev.innings[index] if (prev and index < len(prev.innings)) else None
        deliveries.extend(reconstruct_innings(before, after, innings_no=index + 1))
    return deliveries


# --- The adapter -------------------------------------------------------------


class CricketDataClient:
    """A LiveClient over CricketData. Passes the same conformance suite as
    ReplayClient and _StaticLiveClient, unmodified.

    Entity resolution happens here, at the adapter boundary, so no
    provider-native name ever reaches the domain objects. Two rules the
    live path must not break (both learned from reading Phase 0's resolver
    rather than from a failure):
      - `source_id` is always None. A present-but-unknown source_id
        short-circuits straight to auto-create, which would mint a
        duplicate team/venue for every match on first sight.
      - `allow_create=False`. Minting a canonical entity with no human in
        the loop is the guess we would rather degrade than make.
    """

    def __init__(self, conn, transport: Transport, budget: LiveBudget | None = None) -> None:
        self._conn = conn
        self._transport = transport
        self.budget = budget or LiveBudget()
        self._snapshots: dict[int, MatchSnapshot] = {}
        self._deliveries: dict[int, list[Delivery]] = {}
        self._provider_ids: dict[int, str] = {}
        self._rejections: list[tuple[str, str]] = []

    # -- provider plumbing --

    def _call(self, endpoint: str, params: dict[str, str]) -> dict:
        body = self._transport.get(endpoint, params)
        self.budget.observe(body)
        if body.get("status") != "success":
            raise CricketDataError(f"{endpoint} failed: {body.get('reason')}")
        return body

    def _fetch_snapshots(self) -> list[MatchSnapshot]:
        body = self._call("currentMatches", {"offset": "0"})
        observed_at = datetime.now(timezone.utc)
        return [parse_match(raw, observed_at) for raw in (body.get("data") or [])]

    # -- entity resolution --

    def _resolve_team(self, name: str) -> int:
        result = resolve_team(self._conn, SOURCE, name, source_id=None, allow_create=False)
        if result.entity_id is None:
            raise UnsupportedMatch(
                f"team {name!r} unresolved ({result.outcome}, unresolved_id={result.unresolved_id}) - "
                "refusing to track: elo_diff is unobtainable and batting_team_id is NOT NULL"
            )
        return result.entity_id

    def _resolve_venue(self, venue: str) -> int | None:
        """A queued venue degrades instead of refusing: venue_id=None feeds
        NaN to both venue features, which is the identical cold-start path
        24-33% of training matches already took."""
        if not venue:
            return None
        name, _, city = venue.partition(",")
        result = resolve_venue(
            self._conn, SOURCE, name.strip(), source_id=None,
            city=city.split(",")[0].strip() or None, allow_create=False,
        )
        return result.entity_id

    def _ensure_match_row(self, snapshot: MatchSnapshot) -> int:
        """Provider UUIDs are strings; every domain object carries our own
        int match_id. Idempotent on external_ids->>'cricketdata', mirroring
        the loader's cricsheet key."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT match_id FROM matches WHERE external_ids->>'cricketdata' = %s",
                (snapshot.provider_id,),
            )
            row = cur.fetchone()
            if row is not None:
                return row[0]

            if snapshot.format is None:
                raise UnsupportedMatch(f"unsupported format for {snapshot.name!r}")
            team_a = self._resolve_team(snapshot.teams[0])
            team_b = self._resolve_team(snapshot.teams[1])
            venue_id = self._resolve_venue(snapshot.venue)
            # ON CONFLICT against the partial unique index on
            # external_ids->>'cricketdata' (migration 20260914000001): two
            # workers polling the same match, or one restarted mid-poll, must
            # converge on one canonical match_id rather than racing to insert
            # two. Same pattern cricsheet.py uses for its own idempotency.
            cur.execute(
                """
                INSERT INTO matches (external_ids, competition, format, venue_id, start_time,
                                      team_a, team_b, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ((external_ids->>'cricketdata')) WHERE external_ids->>'cricketdata' IS NOT NULL
                DO NOTHING
                RETURNING match_id
                """,
                (
                    json.dumps({"cricketdata": snapshot.provider_id}),
                    snapshot.name.split(",")[-1].strip() or "unknown",
                    snapshot.format,
                    venue_id,
                    snapshot.observed_at,
                    team_a,
                    team_b,
                    "live" if not snapshot.ended else "complete",
                ),
            )
            inserted = cur.fetchone()
            if inserted is not None:
                return inserted[0]

            # Lost the race: the other writer's row is authoritative.
            cur.execute(
                "SELECT match_id FROM matches WHERE external_ids->>'cricketdata' = %s",
                (snapshot.provider_id,),
            )
            return cur.fetchone()[0]

    # -- LiveClient --

    def list_live_matches(self) -> list[MatchSummary]:
        summaries: list[MatchSummary] = []
        for snapshot in self._fetch_snapshots():
            if not snapshot.started or snapshot.ended:
                continue
            try:
                match_id = self._ensure_match_row(snapshot)
            except UnsupportedMatch as exc:
                # Refusing is the designed degradation - recorded, not raised
                # into the caller's face, so one bad match can't stop a poll
                # that covers every live match at once.
                self._rejections.append((snapshot.provider_id, str(exc)))
                continue
            self._ingest(match_id, snapshot)
            state = self.get_match_state(match_id)
            summaries.append(
                MatchSummary(
                    match_id=match_id, status=state.status,
                    team_a=state.team_a, team_b=state.team_b, venue_id=state.venue_id,
                )
            )
        return summaries

    def get_match_state(self, match_id: int) -> MatchState:
        snapshot = self._snapshots.get(match_id)
        if snapshot is None:
            raise CricketDataError(f"match {match_id} not being tracked - call list_live_matches first")

        balls, _ = scheduled_balls(snapshot)
        target_runs = None
        if len(snapshot.innings) >= 2:
            target_runs = snapshot.dls_target or (snapshot.innings[0].runs + 1)

        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT venue_id, format, team_a, team_b, start_time::date "
                "FROM matches WHERE match_id = %s",
                (match_id,),
            )
            venue_id, format_, team_a, team_b, match_date = cur.fetchone()

        status = "complete" if snapshot.ended else ("live" if snapshot.started else "scheduled")
        return MatchState(
            match_id=match_id,
            status=status,
            innings=snapshot.current_innings,
            ball_count=len(self._deliveries.get(match_id, [])),
            target_runs=target_runs,
            target_overs=balls / 6 if len(snapshot.innings) >= 2 else None,
            venue_id=venue_id,
            format=format_,
            team_a=team_a,
            team_b=team_b,
            toss_winner=None,  # absent from currentMatches
            toss_decision=None,
            winner=None,
            match_date=match_date,
        )

    def current_teams(self, match_id: int) -> tuple[int | None, int | None]:
        """(batting_team_id, bowling_team_id) for the innings in progress.

        (None, None) when the provider has not said. That is a real and
        common case, not an error: `currentMatches` carries no toss, and
        `Delivery` carries no team, so the innings label parsed into
        InningsSnapshot.batting_team is the only signal. A caller must
        decline to predict rather than pick one - getting this backwards
        swaps the two Elo ratings feeding elo_diff and produces a confident
        number about the wrong team.
        """
        snapshot = self._snapshots.get(match_id)
        if snapshot is None or not snapshot.innings:
            return None, None
        batting_name = snapshot.innings[-1].batting_team
        if batting_name is None or len(snapshot.teams) != 2:
            return None, None
        others = [t for t in snapshot.teams if t != batting_name]
        if len(others) != 1:
            # The label did not match either team name we were given - a
            # provider rename mid-match, or a parse that looked fine and is
            # not. Refusing beats resolving a name nobody listed.
            return None, None
        try:
            return self._resolve_team(batting_name), self._resolve_team(others[0])
        except UnsupportedMatch:
            return None, None

    def get_deliveries_since(self, match_id: int, last_ball: int) -> list[Delivery]:
        return list(self._deliveries.get(match_id, [])[last_ball:])

    # -- reconstruction bookkeeping --

    def _ingest(self, match_id: int, snapshot: MatchSnapshot) -> TransitionVerdict:
        """Run one snapshot through the gate and, if it passes, extend the
        reconstructed delivery stream."""
        previous = self._snapshots.get(match_id)
        verdict, _reason = validate_transition(previous, snapshot)

        if verdict is TransitionVerdict.REJECT:
            return verdict  # last-good snapshot retained; nothing emitted
        if verdict is TransitionVerdict.CORRECTION:
            # Re-baseline on the corrected figures without emitting negative
            # deliveries. Already-emitted rows stay as they were - history is
            # never patched (the same rule the incremental builder follows).
            self._snapshots[match_id] = snapshot
            return verdict
        if verdict is TransitionVerdict.NO_CHANGE:
            self._snapshots[match_id] = snapshot
            return verdict

        new_deliveries = reconstruct(previous, snapshot)
        if previous is None and new_deliveries:
            # Catch-up: the worker started mid-match. Everything already
            # bowled is emitted as one inferred span so that
            # len(get_deliveries_since(id, 0)) == state.ball_count holds
            # without weakening the conformance suite.
            new_deliveries = [
                Delivery(**{**d.__dict__, "confidence": ReconstructionConfidence.INFERRED})
                for d in new_deliveries
            ]
        self._deliveries.setdefault(match_id, []).extend(
            _renumber_within_over(self._deliveries.get(match_id, []), new_deliveries)
        )
        self._snapshots[match_id] = snapshot
        self._provider_ids[match_id] = snapshot.provider_id
        return verdict

    def poll(self, match_id: int) -> list[Delivery]:
        """One iteration of SPEC.md section 7.1's loop for a tracked match.
        Returns only the newly reconstructed deliveries."""
        provider_id = self._provider_ids.get(match_id)
        before = len(self._deliveries.get(match_id, []))
        for snapshot in self._fetch_snapshots():
            if snapshot.provider_id == provider_id:
                self._ingest(match_id, snapshot)
                break
        return list(self._deliveries.get(match_id, [])[before:])

    def next_interval(self, match_id: int) -> float:
        """Adaptive: tighten during an over, back off between overs, and
        stretch further as the daily quota runs down."""
        snapshot = self._snapshots.get(match_id)
        between_overs = bool(snapshot and snapshot.innings and snapshot.innings[-1].balls % 6 == 0)
        return self.budget.interval(between_overs)
