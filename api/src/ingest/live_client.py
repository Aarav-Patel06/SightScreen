"""LiveClient interface and provider-neutral domain objects (SPEC.md
sections 4.2, 7.1, 7.3, Phase 2 session 1).

No live provider is chosen yet (SPEC.md section 15) - nothing here is
shaped around CricketData/Sportmonks/Roanuz's actual schemas. `Delivery`
and `MatchState` carry only what a real provider could plausibly expose (a
raw ball event, a scorecard snapshot) - never match_states columns
(required_run_rate, phase, ...), which are *derived*, not something any
provider hands you. Deriving them from this raw stream is
`features.match_state.IncrementalMatchStateBuilder`'s job, not this
module's.

Both domain objects carry resolved canonical IDs (batter_id, venue_id, ...),
never provider-native name strings. Entity resolution (section 4.4, already
built in Phase 0 - `ingest/entity_resolution.py`) happens INSIDE a real
provider's adapter, before it ever constructs a Delivery - this module
never sees an unresolved name.

`LiveClient` is a Protocol, not an ABC: `ReplayClient` (ingest/replay.py)
and any future real-provider adapter satisfy it structurally, with no
shared base class forcing a particular implementation shape on either.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ReconstructionConfidence(str, Enum):
    """How a Delivery came to exist (Phase 2 session 2).

    A provider with a real ball-by-ball feed always yields CONFIRMED. A
    provider we can only poll for scorecard snapshots yields CONFIRMED when
    exactly one ball landed between two polls (so runs and any wicket
    attribute unambiguously to it) and INFERRED when several did and the
    split between them had to be assumed. Never silently attributed - the
    flag travels with the ball so anything downstream can tell the
    difference.
    """

    CONFIRMED = "confirmed"
    INFERRED = "inferred"


class PhaseTransitionEvent(str, Enum):
    """SPEC.md section 7.2's exact event names - the worker's own event
    stream, distinct from `predictions.match_phase`'s snapshot-label
    values (a separate, already-defined schema column)."""

    PRE_TOSS = "pre_toss"
    TOSS = "toss"
    INNINGS1_START = "innings1_start"
    INNINGS_BREAK = "innings_break"
    INNINGS2_BALL = "innings2_ball"
    MATCH_END = "match_end"


@dataclass(frozen=True)
class Delivery:
    """One ball, exactly what a provider could plausibly expose - the raw
    event, not any derived state.

    The three player IDs are Optional (Phase 2 session 2). They were
    non-optional in session 1, which was stricter than reality on both
    sides: `deliveries.batter_id`/`non_striker_id`/`bowler_id` are all
    nullable in the schema, and `ingest/cricsheet.py` already writes None
    into them when a player resolution queues. A provider that supplies no
    per-ball player identity at all (CricketData, via snapshot
    reconstruction) is the third case the original type couldn't express.
    Widening here, rather than relaxing any conformance assertion, is why
    tests/ingest/test_live_client.py stays untouched.
    """

    innings: int
    over_num: int
    ball_in_over: int
    legal_ball_num: int
    batter_id: int | None
    non_striker_id: int | None
    bowler_id: int | None
    runs_batter: int
    runs_extras: int
    extra_type: str | None
    wicket_type: str | None
    player_out_id: int | None
    wicket_count: int = 1  # rare double-dismissal ball; matches deliveries.wicket_count's default
    confidence: ReconstructionConfidence = ReconstructionConfidence.CONFIRMED


@dataclass(frozen=True)
class MatchState:
    """A scorecard snapshot - not a match_states row. `ball_count` is the
    ONLY thing a caller needs to detect "anything new happened since I last
    polled" (SPEC.md section 7.1 step 2) - a monotonic count of deliveries
    bowled so far in the match, regardless of how any given provider likes
    to number overs/balls itself."""

    match_id: int
    status: str  # 'scheduled' | 'live' | 'complete'
    innings: int | None
    ball_count: int
    target_runs: int | None
    target_overs: float | None
    venue_id: int | None
    format: str
    team_a: int
    team_b: int
    toss_winner: int | None
    toss_decision: str | None
    winner: int | None


@dataclass(frozen=True)
class MatchSummary:
    """One row of list_live_matches() - just enough to decide whether to
    start polling a match, not its full state."""

    match_id: int
    status: str
    team_a: int
    team_b: int
    venue_id: int | None


class LiveClient(Protocol):
    """The only contract the rest of the system may depend on (SPEC.md
    section 7.3: "the live client and the replay client must implement the
    same interface, so the rest of the system cannot tell them apart").
    Structurally enforced by tests/ingest/test_live_client.py's shared
    conformance suite, run against every implementation - not just a
    convention two implementations happen to follow today."""

    def list_live_matches(self) -> list[MatchSummary]: ...

    def get_match_state(self, match_id: int) -> MatchState: ...

    def get_deliveries_since(self, match_id: int, last_ball: int) -> list[Delivery]: ...


class _StaticLiveClient:
    """A minimal, deliberately test-only LiveClient built from a
    hand-written in-memory sequence of domain objects - no database, no
    file parsing, nothing shared with ReplayClient's own internals. Its
    only purpose is to prove the conformance suite in
    tests/ingest/test_live_client.py is genuinely implementation-agnostic,
    not secretly tied to how ReplayClient happens to be built. Not a second
    production adapter - never imported outside tests.
    """

    def __init__(self, match_state: MatchState, deliveries: list[Delivery]) -> None:
        self._match_state = match_state
        self._deliveries = deliveries

    def list_live_matches(self) -> list[MatchSummary]:
        m = self._match_state
        return [MatchSummary(match_id=m.match_id, status=m.status, team_a=m.team_a, team_b=m.team_b, venue_id=m.venue_id)]

    def get_match_state(self, match_id: int) -> MatchState:
        assert match_id == self._match_state.match_id
        return self._match_state

    def get_deliveries_since(self, match_id: int, last_ball: int) -> list[Delivery]:
        assert match_id == self._match_state.match_id
        return self._deliveries[last_ball:]
