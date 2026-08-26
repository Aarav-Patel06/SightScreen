"""Entity resolution (SPEC.md section 4.4).

Governing principle: a wrong merge is far worse than a missed match. Every
threshold here is biased toward "queue for a human" over "guess" -
including going well past section 4.4's literal "~85" suggestion, per an
explicit Phase 0 session 4 decision.

Resolution order, cheapest/safest first:
  1. Exact registry-ID alias match (source, source_id) - the primary path
     for players; Cricsheet's own registry already deduplicates people.
  2. Exact name alias match (source, source_name) - catches sources/older
     records with no registry ID, and every manual review decision (once
     recorded as an alias, never asked again).
  3. Blocked (players only) + scored fuzzy match against canonical entities.
  4. Tri-state decision: auto-resolve / auto-create (new entity) / queue.

Import `resolve_player` / `resolve_team` / `resolve_venue` - not the
private `_resolve` core directly.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal

from rapidfuzz import fuzz

Outcome = Literal["auto_resolved", "auto_created", "queued"]

# Bias toward missed-match over wrong-merge (this session's governing
# principle) - well above section 4.4's literal "~85".
AUTO_RESOLVE_FLOOR = 95
AUTO_RESOLVE_MARGIN = 10
# Below this, nothing is even plausible - treat as a genuinely new entity
# rather than cluttering the review queue with obvious non-matches.
AUTO_CREATE_CEILING = 60
# Surname-level fuzz.ratio threshold for blocking (players only) - tolerant
# of a small typo in the surname itself, while still excluding anything
# genuinely unrelated from ever being scored.
SURNAME_BLOCK_THRESHOLD = 80
# A candidate whose reduced "first-initial + surname" key matches the query
# is treated as at least this confident, since plain token_set_ratio
# undervalues "V Kohli" vs "Virat Kohli" (the tokens "V" and "Virat" barely
# overlap as strings even though this is the same person).
INITIALS_MATCH_SCORE = 96.0
# No corroborating signal (e.g. shared team) and the top candidate's last
# known appearance is this many years before the current match -> treat as
# a different person who happens to share a name, not the same one aging.
TEMPORAL_IMPLAUSIBLE_YEARS = 25


@dataclass
class ResolutionResult:
    entity_id: int | None
    outcome: Outcome
    unresolved_id: int | None = None


@dataclass
class _Candidate:
    entity_id: int
    canonical_name: str
    score: float


@dataclass(frozen=True)
class _KindConfig:
    kind: str
    table: str
    id_column: str
    name_column: str
    alias_table: str


PLAYER_CONFIG = _KindConfig("player", "players", "player_id", "canonical_name", "player_aliases")
TEAM_CONFIG = _KindConfig("team", "teams", "team_id", "name", "team_aliases")
VENUE_CONFIG = _KindConfig("venue", "venues", "venue_id", "name", "venue_aliases")


# --- Normalization -----------------------------------------------------

def normalize_name(name: str) -> str:
    """Strip diacritics/case/punctuation noise before any comparison.

    players.canonical_name (etc.) still stores the original best-available
    spelling for display - only comparisons run on this normalized form.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    without_punctuation = re.sub(r"[.,]", " ", without_marks)
    return " ".join(without_punctuation.lower().split())


def surname_key(normalized_name: str) -> str:
    """Best-effort surname bucket, tolerant of "First Last" and "Last,
    Initial" order and of a bare single-letter initial appearing on either
    side (e.g. "v kohli" and "kohli v" both key to "kohli").
    """
    tokens = normalized_name.split()
    if not tokens:
        return ""
    non_initial = [t for t in tokens if len(t) > 1]
    if non_initial and len(non_initial) < len(tokens):
        return non_initial[-1]
    return tokens[-1]


def _is_bare_initial(token: str) -> bool:
    return len(token) == 1


def _initials_match(normalized_query: str, normalized_candidate: str) -> bool:
    """True if the query looks like "<initial> <surname>" (in either
    order) and the candidate's own initial+surname reduction agrees.
    """
    q_tokens = normalized_query.split()
    c_tokens = normalized_candidate.split()
    if len(q_tokens) < 2 or len(c_tokens) < 2:
        return False
    if not any(_is_bare_initial(t) for t in q_tokens):
        return False
    if surname_key(normalized_query) != surname_key(normalized_candidate):
        return False
    q_initial = next((t[0] for t in q_tokens if _is_bare_initial(t)), None)
    q_surname = surname_key(normalized_query)
    c_first_letter = next((t[0] for t in c_tokens if t != q_surname), None)
    return q_initial is not None and c_first_letter is not None and q_initial == c_first_letter


# --- Candidate generation -----------------------------------------------

def _player_candidates(conn, normalized_query: str) -> list[tuple[int, str]]:
    target_surname = surname_key(normalized_query)
    with conn.cursor() as cur:
        cur.execute("SELECT player_id, canonical_name FROM players")
        rows = cur.fetchall()
    return [
        (player_id, name)
        for player_id, name in rows
        if fuzz.ratio(target_surname, surname_key(normalize_name(name))) >= SURNAME_BLOCK_THRESHOLD
    ]


def _team_candidates(conn, _normalized_query: str) -> list[tuple[int, str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT team_id, name FROM teams")
        return cur.fetchall()


def _venue_candidates(conn, _normalized_query: str) -> list[tuple[int, str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT venue_id, name, city FROM venues")
        rows = cur.fetchall()
    return [(venue_id, f"{name} {city or ''}".strip()) for venue_id, name, city in rows]


# --- Scoring --------------------------------------------------------------

def _score_candidates(normalized_query: str, candidates: list[tuple[int, str]]) -> list[_Candidate]:
    scored = []
    for entity_id, comparison_text in candidates:
        normalized_candidate = normalize_name(comparison_text)
        score = float(fuzz.token_set_ratio(normalized_query, normalized_candidate))
        if _initials_match(normalized_query, normalized_candidate):
            score = max(score, INITIALS_MATCH_SCORE)
        scored.append(_Candidate(entity_id=entity_id, canonical_name=comparison_text, score=score))
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


# --- Collision checks (players only) --------------------------------------

def _same_surname_collision(query_surname: str, squad_names: list[str] | None) -> bool:
    """Two or more squad members share the query's surname bucket -> never
    auto-resolve, regardless of score. This is the one rule that overrides
    everything else.
    """
    if not squad_names:
        return False
    matches = sum(1 for name in squad_names if surname_key(normalize_name(name)) == query_surname)
    return matches >= 2


def _temporal_implausible(conn, candidate_id: int, query_date: date | None) -> bool:
    if query_date is None:
        return False
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(match_date) FROM deliveries "
            "WHERE batter_id = %s OR bowler_id = %s OR non_striker_id = %s",
            (candidate_id, candidate_id, candidate_id),
        )
        last_seen = cur.fetchone()[0]
    if last_seen is None:
        return False
    gap_years = (query_date - last_seen).days / 365.25
    return gap_years > TEMPORAL_IMPLAUSIBLE_YEARS


# --- Persistence -----------------------------------------------------------

def _lookup_alias(conn, config: _KindConfig, source: str, source_name: str, source_id: str | None) -> int | None:
    with conn.cursor() as cur:
        if source_id is not None:
            cur.execute(
                f"SELECT {config.id_column} FROM {config.alias_table} WHERE source = %s AND source_id = %s",
                (source, source_id),
            )
            row = cur.fetchone()
            if row:
                return row[0]
        cur.execute(
            f"SELECT {config.id_column} FROM {config.alias_table} WHERE source = %s AND source_name = %s",
            (source, source_name),
        )
        row = cur.fetchone()
        return row[0] if row else None


def _create_alias(conn, config: _KindConfig, source: str, source_name: str, source_id: str | None, entity_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {config.alias_table} (source, source_name, source_id, {config.id_column})
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, source_name) DO NOTHING
            """,
            (source, source_name, source_id, entity_id),
        )


def _create_new_entity_and_alias(
    conn,
    config: _KindConfig,
    source: str,
    source_name: str,
    source_id: str | None,
    extra_columns: dict[str, object] | None = None,
) -> int:
    columns = [config.name_column]
    values: list[object] = [source_name]
    if extra_columns:
        for column, value in extra_columns.items():
            columns.append(column)
            values.append(value)
    column_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(values))
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {config.table} ({column_list}) VALUES ({placeholders}) RETURNING {config.id_column}",
            values,
        )
        entity_id = cur.fetchone()[0]
    _create_alias(conn, config, source, source_name, source_id, entity_id)
    return entity_id


def _enqueue(
    conn,
    entity_kind: str,
    source: str,
    source_name: str,
    source_id: str | None,
    candidates: list[_Candidate],
    match_id: int | None,
    reason: str,
) -> int:
    payload = json.dumps(
        [
            {"candidate_id": c.entity_id, "candidate_name": c.canonical_name, "score": c.score}
            for c in candidates[:3]
        ]
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO unresolved_entities
              (entity_kind, source, source_name, source_id, candidates, first_seen_match_id, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (entity_kind, source, source_name) DO NOTHING
            RETURNING unresolved_id
            """,
            (entity_kind, source, source_name, source_id, payload, match_id, reason),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "SELECT unresolved_id FROM unresolved_entities "
                "WHERE entity_kind = %s AND source = %s AND source_name = %s",
                (entity_kind, source, source_name),
            )
            row = cur.fetchone()
    return row[0]


# --- Core ------------------------------------------------------------------

def _resolve(
    conn,
    config: _KindConfig,
    source: str,
    source_name: str,
    source_id: str | None,
    comparison_query: str,
    candidates_fn: Callable[[object, str], list[tuple[int, str]]],
    match_id: int | None = None,
    match_date: date | None = None,
    squad_names: list[str] | None = None,
    extra_columns: dict[str, object] | None = None,
) -> ResolutionResult:
    existing = _lookup_alias(conn, config, source, source_name, source_id)
    if existing is not None:
        return ResolutionResult(entity_id=existing, outcome="auto_resolved")

    normalized_query = normalize_name(comparison_query)
    raw_candidates = candidates_fn(conn, normalized_query)
    scored = _score_candidates(normalized_query, raw_candidates)

    collision_reason: str | None = None
    if config.kind == "player":
        if _same_surname_collision(surname_key(normalized_query), squad_names):
            collision_reason = "same_surname_collision"
        elif scored and _temporal_implausible(conn, scored[0].entity_id, match_date):
            collision_reason = "temporal_implausible"

    top = scored[0] if scored else None
    second = scored[1] if len(scored) > 1 else None
    margin = (top.score - second.score) if (top and second) else None

    if (
        collision_reason is None
        and top is not None
        and top.score >= AUTO_RESOLVE_FLOOR
        and (margin is None or margin >= AUTO_RESOLVE_MARGIN)
    ):
        _create_alias(conn, config, source, source_name, source_id, top.entity_id)
        return ResolutionResult(entity_id=top.entity_id, outcome="auto_resolved")

    if collision_reason is None and (top is None or top.score < AUTO_CREATE_CEILING):
        new_id = _create_new_entity_and_alias(conn, config, source, source_name, source_id, extra_columns)
        return ResolutionResult(entity_id=new_id, outcome="auto_created")

    if collision_reason is not None:
        reason = collision_reason
    elif top is not None and top.score >= AUTO_RESOLVE_FLOOR:
        reason = "ambiguous_margin"
    else:
        reason = "below_threshold"

    unresolved_id = _enqueue(conn, config.kind, source, source_name, source_id, scored, match_id, reason)
    return ResolutionResult(entity_id=None, outcome="queued", unresolved_id=unresolved_id)


# --- Public API --------------------------------------------------------------

def resolve_player(
    conn,
    source: str,
    source_name: str,
    source_id: str | None = None,
    match_id: int | None = None,
    team_id: int | None = None,
    match_date: date | None = None,
    squad_names: list[str] | None = None,
) -> ResolutionResult:
    """squad_names should be every other name in this delivery's match for
    the same team, as raw source names - Cricsheet's own info.players[team]
    block. Required for the same-surname-in-same-squad guard to work;
    without it, two same-surname squadmates could otherwise both look
    individually unambiguous.
    """
    return _resolve(
        conn,
        PLAYER_CONFIG,
        source,
        source_name,
        source_id,
        comparison_query=source_name,
        candidates_fn=_player_candidates,
        match_id=match_id,
        match_date=match_date,
        squad_names=squad_names,
    )


def resolve_team(conn, source: str, source_name: str, source_id: str | None = None) -> ResolutionResult:
    return _resolve(
        conn,
        TEAM_CONFIG,
        source,
        source_name,
        source_id,
        comparison_query=source_name,
        candidates_fn=_team_candidates,
    )


def resolve_venue(
    conn,
    source: str,
    source_name: str,
    source_id: str | None = None,
    city: str | None = None,
) -> ResolutionResult:
    """Comparison text combines name + city (when known) so two different
    venues sharing a generic name in different cities don't collapse into
    one venue_id - the alias itself still stores the bare source_name.
    """
    comparison_query = f"{source_name} {city or ''}".strip()
    return _resolve(
        conn,
        VENUE_CONFIG,
        source,
        source_name,
        source_id,
        comparison_query=comparison_query,
        candidates_fn=_venue_candidates,
        extra_columns={"city": city} if city else None,
    )
