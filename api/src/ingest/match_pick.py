"""The day's biggest match, from CricketData's fixture list (session 3).

One pure function, `rank_candidates`, so the ordering is testable without a
provider, a database or a clock. The worker feeds it `cricScore` rows (the
only endpoint that lists upcoming fixtures - `currentMatches` lists started
matches only, checked 2026-09-28) and a team resolver.

The order, set by the owner:
  0. men's international, both sides Full Members
  1. other men's internationals
  2. major leagues
and the most recent start wins a tie. Women's, age-group and A sides are
never picked (owner's decision, 2026-09-28: the corpus and the model are men's
senior only). Nor is anything the worker could not predict - a format with no
model, a team that does not resolve - because a pick it must then refuse
would cost the day's match. Everything else (county, state and other domestic
cricket) is never picked either.

The front page is a separate rule and is not decided here: `loadHeroMatch`
admits only Full Member v Full Member, so a tier 1 or 2 pick never takes it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ingest.cricketdata import FORMAT_BY_MATCH_TYPE, not_mens_senior

TIER_FULL_MEMBERS = 0
TIER_INTERNATIONAL = 1
TIER_LEAGUE = 2

# A match that started this long ago may still be in progress (an ODI runs
# about 8 hours); anything starting within LOOKAHEAD is "today's".
LOOKBACK = timedelta(hours=10)
LOOKAHEAD = timedelta(hours=24)

# cricScore's `ms`: "fixture", "live" or "result".
_PICKABLE_STATES = {"fixture", "live"}

# From the series name, which is all cricScore says about the competition.
_INTERNATIONAL_RE = re.compile(
    r"tour of|\bT20I\b|\bODI\b|World Cup|Qualifier|Tri-Series|Asia Cup|Champions Trophy",
    re.IGNORECASE,
)
_MAJOR_LEAGUES = (
    "Indian Premier League",
    "Big Bash League",
    "Pakistan Super League",
    "Caribbean Premier League",
    "SA20",
    "International League T20",
    "Major League Cricket",
    "Bangladesh Premier League",
    "Lanka Premier League",
)

# "India [IND]" -> "India"
_SHORT_CODE_RE = re.compile(r"\s*\[[^\]]*\]\s*$")


@dataclass(frozen=True)
class Team:
    team_id: int
    full_member: bool


@dataclass(frozen=True)
class Candidate:
    provider_id: str
    tier: int
    start: datetime
    format: str
    teams: tuple[str, str]
    team_ids: tuple[int, int]
    series: str


def team_name(label: str) -> str:
    return _SHORT_CODE_RE.sub("", label or "").strip()


def _start(row: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(row["dateTimeGMT"]).replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def _series_tier(series: str) -> int | None:
    """TIER_INTERNATIONAL (refined by the teams), TIER_LEAGUE, or None."""
    if _INTERNATIONAL_RE.search(series):
        return TIER_INTERNATIONAL
    if any(league.lower() in series.lower() for league in _MAJOR_LEAGUES):
        return TIER_LEAGUE
    return None


def rank_candidates(
    rows: list[dict], resolve: Callable[[str], Team | None], now: datetime
) -> list[Candidate]:
    """Every pickable row, biggest first. `resolve` returns None for a team
    the worker cannot resolve, which makes the row unpickable."""
    candidates: list[Candidate] = []
    for row in rows:
        if row.get("ms") not in _PICKABLE_STATES:
            continue
        start = _start(row)
        if start is None or not (now - LOOKBACK <= start <= now + LOOKAHEAD):
            continue
        format_ = FORMAT_BY_MATCH_TYPE.get((row.get("matchType") or "").lower())
        if format_ is None:
            continue
        series = row.get("series") or ""
        names = (team_name(row.get("t1", "")), team_name(row.get("t2", "")))
        if not all(names) or not_mens_senior(series) or any(not_mens_senior(n) for n in names):
            continue
        # The series decides before any name is resolved, so the hourly
        # check never queues every county side it sees for review.
        tier = _series_tier(series)
        if tier is None:
            continue
        a, b = resolve(names[0]), resolve(names[1])
        if a is None or b is None:
            continue
        if tier == TIER_INTERNATIONAL and a.full_member and b.full_member:
            tier = TIER_FULL_MEMBERS
        candidates.append(
            Candidate(
                provider_id=row["id"], tier=tier, start=start, format=format_,
                teams=names, team_ids=(a.team_id, b.team_id), series=series,
            )
        )
    # Biggest tier first; within a tier, the most recent start.
    candidates.sort(key=lambda c: (c.tier, -c.start.timestamp()))
    return candidates
