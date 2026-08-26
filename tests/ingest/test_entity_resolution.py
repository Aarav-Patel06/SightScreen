"""Golden test set for entity resolution (SPEC.md section 4.4), written
before the implementation. Governing principle: a wrong merge is far worse
than a missed match - every "must queue" / "must not auto-resolve" case
here is load-bearing, not incidental.

Runs against the dedicated cricket_training_test database (see
tests/ingest/conftest.py), never the real training corpus.
"""

import json
from datetime import date
from unittest.mock import patch

import pytest

from ingest.entity_resolution import resolve_player, resolve_team, resolve_venue


def _insert_player(conn, name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,)
        )
        return cur.fetchone()[0]


def _insert_team(conn, name: str) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING team_id", (name,))
        return cur.fetchone()[0]


def _insert_venue(conn, name: str, city: str | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO venues (name, city) VALUES (%s, %s) RETURNING venue_id", (name, city)
        )
        return cur.fetchone()[0]


def _insert_match(conn, start_time: str, team_a: int, team_b: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO matches (competition, format, start_time, team_a, team_b, status)
            VALUES ('Test Competition', 'T20', %s, %s, %s, 'complete')
            RETURNING match_id
            """,
            (start_time, team_a, team_b),
        )
        return cur.fetchone()[0]


def _insert_delivery_for_player(conn, match_id, team_a, team_b, batter_id, match_date_: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO deliveries
              (match_id, innings, over_num, ball_in_over, legal_ball_num,
               batter_id, batting_team_id, bowling_team_id, match_date, is_super_over)
            VALUES (%s, 1, 0, 1, 1, %s, %s, %s, %s, false)
            """,
            (match_id, batter_id, team_a, team_b, match_date_),
        )


def _unresolved_row(conn, entity_kind: str, source_name: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT reason, candidates, status FROM unresolved_entities "
            "WHERE entity_kind=%s AND source_name=%s",
            (entity_kind, source_name),
        )
        row = cur.fetchone()
    if row is None:
        return None
    reason, candidates, status = row
    # psycopg normally adapts jsonb to a Python list already; tolerate a
    # raw string too rather than assuming one or the other.
    parsed = json.loads(candidates) if isinstance(candidates, str) else candidates
    return reason, parsed, status


# 1. Registry-ID exact match (baseline)
def test_registry_id_exact_match(conn):
    existing = _insert_player(conn, "Virat Kohli")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO player_aliases (player_id, source, source_name, source_id) "
            "VALUES (%s, 'cricsheet', 'V Kohli', 'reg-123')",
            (existing,),
        )
    result = resolve_player(conn, "cricsheet", "V Kohli", source_id="reg-123")
    assert result.outcome == "auto_resolved"
    assert result.entity_id == existing
    assert result.method == "registry_id"


# 2. Existing-alias exact match (baseline, no registry id)
def test_existing_alias_exact_match(conn):
    existing = _insert_player(conn, "Rohit Sharma")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO player_aliases (player_id, source, source_name) "
            "VALUES (%s, 'cricsheet', 'Rohit Sharma')",
            (existing,),
        )
    result = resolve_player(conn, "cricsheet", "Rohit Sharma", source_id=None)
    assert result.outcome == "auto_resolved"
    assert result.entity_id == existing
    assert result.method == "name_alias"


# 3. Initials-only name
def test_initials_only_name_resolves(conn):
    existing = _insert_player(conn, "Virat Kohli")
    result = resolve_player(conn, "cricsheet", "V Kohli", source_id=None)
    assert result.outcome == "auto_resolved"
    assert result.entity_id == existing
    assert result.method == "fuzzy"


# 4. Reordered "Surname, Initial" form
def test_reordered_name_resolves(conn):
    existing = _insert_player(conn, "Virat Kohli")
    result = resolve_player(conn, "cricsheet", "Kohli, V", source_id=None)
    assert result.outcome == "auto_resolved"
    assert result.entity_id == existing


# 5. Same surname, same squad - must NEVER auto-resolve
def test_same_surname_same_squad_never_auto_resolves(conn):
    _insert_player(conn, "Rohit Sharma")
    _insert_player(conn, "Ishan Sharma")
    result = resolve_player(
        conn, "cricsheet", "R Sharma", source_id=None,
        squad_names=["R Sharma", "I Sharma", "Some Other Player"],
    )
    assert result.outcome == "queued"
    row = _unresolved_row(conn, "player", "R Sharma")
    assert row is not None
    reason, candidates, status = row
    assert reason == "same_surname_collision"
    assert status == "pending"
    # Even a same-surname squadmate must show up as a candidate for the
    # reviewer, not be silently dropped.
    candidate_names = [c["candidate_name"] for c in candidates]
    assert "Rohit Sharma" in candidate_names


# 6. Case-only variation
def test_case_only_variation_auto_resolves(conn):
    existing = _insert_player(conn, "de Villiers")
    result = resolve_player(conn, "cricsheet", "De Villiers", source_id=None)
    assert result.outcome == "auto_resolved"
    assert result.entity_id == existing


# 7. Diacritics stripped
def test_diacritics_stripped_auto_resolves(conn):
    existing = _insert_player(conn, "Şen")
    result = resolve_player(conn, "cricsheet", "Sen", source_id=None)
    assert result.outcome == "auto_resolved"
    assert result.entity_id == existing


# 8. Long name truncated differently by two sources
def test_long_name_truncated_differently(conn):
    existing = _insert_player(conn, "Wanindu Hasaranga de Silva")
    result = resolve_player(conn, "cricsheet", "Hasaranga de Silva", source_id=None)
    # Either a confident match, or a safe queue - never a silent non-match
    # that creates a spurious duplicate.
    assert result.outcome in ("auto_resolved", "queued")
    if result.outcome == "auto_resolved":
        assert result.entity_id == existing


# 9. Garbage name, zero plausible candidates -> auto-create
def test_no_plausible_candidate_auto_creates(conn):
    _insert_player(conn, "Virat Kohli")
    result = resolve_player(conn, "cricsheet", "Zzqxw Plonkbat", source_id=None)
    assert result.outcome == "auto_created"
    assert result.entity_id is not None
    row = _unresolved_row(conn, "player", "Zzqxw Plonkbat")
    assert row is None


# 10. Same name, different eras, no shared team -> temporal_implausible
def test_temporal_implausibility_queues(conn):
    team_a = _insert_team(conn, "Old Team")
    team_b = _insert_team(conn, "Old Opponent")
    old_player = _insert_player(conn, "S Sharma")
    old_match = _insert_match(conn, "1994-01-01T00:00:00Z", team_a, team_b)
    _insert_delivery_for_player(conn, old_match, team_a, team_b, old_player, "1994-01-01")

    result = resolve_player(
        conn, "cricsheet", "S Sharma", source_id=None,
        match_date=date(2024, 1, 1),
    )
    assert result.outcome == "queued"
    row = _unresolved_row(conn, "player", "S Sharma")
    assert row is not None
    reason, _candidates, _status = row
    assert reason == "temporal_implausible"


# 11. Common surname, no strong match -> never auto-resolves to any of them
def test_common_surname_no_strong_match(conn):
    _insert_player(conn, "Kumar Sangakkara")
    _insert_player(conn, "Praveen Kumar")
    _insert_player(conn, "Akshar Kumar")
    result = resolve_player(conn, "cricsheet", "Kumar", source_id=None)
    assert result.outcome != "auto_resolved"


# 12. Idempotent re-run: same input twice -> same player_id, no duplicates
def test_idempotent_rerun_same_player_id(conn):
    _insert_player(conn, "Virat Kohli")
    first = resolve_player(conn, "cricsheet", "V Kohli", source_id="reg-999")
    second = resolve_player(conn, "cricsheet", "V Kohli", source_id="reg-999")
    assert first.entity_id == second.entity_id
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM player_aliases WHERE source=%s AND source_id=%s",
            ("cricsheet", "reg-999"),
        )
        assert cur.fetchone()[0] == 1


# 13. Manual "resolve as existing" makes the next identical input short-circuit
def test_manual_resolve_as_existing_is_permanent(conn):
    from ingest.review_queue import resolve_as_existing

    # Same reliably-ambiguous setup as test 19 (two near-duplicate spellings
    # in the same surname bucket) - guaranteed to queue rather than depend
    # on luck, unlike a single unrelated garbage name (which correctly
    # auto-creates instead, per the governing principle).
    existing = _insert_player(conn, "Ravindra Jadeja")
    _insert_player(conn, "Ravindra Jadejaa")
    queued = resolve_player(conn, "cricsheet", "Ravindra Jadej", source_id=None)
    assert queued.outcome == "queued"

    resolve_as_existing(conn, unresolved_id=queued.unresolved_id, entity_id=existing)

    with patch("ingest.entity_resolution._score_candidates") as mock_score:
        second = resolve_player(conn, "cricsheet", "Ravindra Jadej", source_id=None)
    mock_score.assert_not_called()
    assert second.outcome == "auto_resolved"
    assert second.entity_id == existing


# 14. Manual "resolve as new" creates canonical + alias; next input resolves directly
def test_manual_resolve_as_new_is_permanent(conn):
    from ingest.review_queue import resolve_as_new

    first = resolve_player(conn, "cricsheet", "Totally Unknown Player Q", source_id=None)
    if first.outcome == "queued":
        new_id = resolve_as_new(conn, unresolved_id=first.unresolved_id)
    else:
        new_id = first.entity_id

    second = resolve_player(conn, "cricsheet", "Totally Unknown Player Q", source_id=None)
    assert second.outcome == "auto_resolved"
    assert second.entity_id == new_id


# 15. Team abbreviation only -> must not auto-resolve on such a weak match
def test_team_abbreviation_does_not_auto_resolve(conn):
    _insert_team(conn, "Mumbai Indians")
    result = resolve_team(conn, "cricsheet", "MI", source_id=None)
    assert result.outcome != "auto_resolved"


# 16. Venue punctuation/city-suffix variance -> auto-resolves
def test_venue_punctuation_and_city_suffix_auto_resolves(conn):
    existing = _insert_venue(conn, "M Chinnaswamy Stadium", city="Bengaluru")
    result = resolve_venue(conn, "cricsheet", "M. Chinnaswamy Stadium", city="Bengaluru")
    assert result.outcome == "auto_resolved"
    assert result.entity_id == existing


# 17. Two different venues sharing a name fragment but different cities -> never collapse
def test_venue_same_name_different_city_never_collapses(conn):
    springfield = _insert_venue(conn, "Cricket Ground", city="Springfield")
    result = resolve_venue(conn, "cricsheet", "Cricket Ground", city="Shelbyville")
    if result.outcome == "auto_resolved":
        assert result.entity_id != springfield
    else:
        assert result.outcome in ("queued", "auto_created")


# 18. Blocking correctness: candidate outside the surname bucket is never considered
def test_blocking_excludes_different_surname_bucket(conn):
    _insert_player(conn, "Aaron Finch")
    result = resolve_player(conn, "cricsheet", "Zorblex Finchington", source_id=None)
    # "Finchington" shares no surname bucket with "Finch" (different last
    # token) - must not be treated as a candidate at all.
    assert result.outcome != "auto_resolved"
    row = _unresolved_row(conn, "player", "Zorblex Finchington")
    if row is not None:
        _reason, candidates, _status = row
        names = [c["candidate_name"] for c in candidates]
        assert "Aaron Finch" not in names


# 19. Ambiguous margin: both scores high, margin too thin -> queues
def test_ambiguous_margin_queues(conn):
    _insert_player(conn, "Ravindra Jadeja")
    _insert_player(conn, "Ravindra Jadejaa")  # deliberately near-duplicate spelling
    result = resolve_player(conn, "cricsheet", "Ravindra Jadej", source_id=None)
    assert result.outcome != "auto_resolved"


# 20. Whitespace/case noise -> auto-resolves
def test_whitespace_and_case_noise_auto_resolves(conn):
    existing = _insert_player(conn, "Virat Kohli")
    result = resolve_player(conn, "cricsheet", "  virat kohli  ", source_id=None)
    assert result.outcome == "auto_resolved"
    assert result.entity_id == existing


# 21. Same surname, different registry IDs, same squad - the exact case that
# was failing: an authoritative, never-before-seen registry ID must be
# terminal and must never be second-guessed by the same-surname-in-squad
# heuristic, which exists only for when there's no ID to rely on. Confirmed
# against real Cricsheet data as the actual cause of an entire sample's
# player queue (100% of queued players had a registry ID present).
def test_same_surname_different_registry_ids_never_queue(conn):
    squad = ["BOL Mendis", "BKG Mendis"]
    first = resolve_player(conn, "cricsheet", "BOL Mendis", source_id="reg-mendis-1", squad_names=squad)
    second = resolve_player(conn, "cricsheet", "BKG Mendis", source_id="reg-mendis-2", squad_names=squad)

    assert first.outcome != "queued"
    assert second.outcome != "queued"
    assert first.entity_id is not None
    assert second.entity_id is not None
    assert first.entity_id != second.entity_id

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM unresolved_entities WHERE entity_kind='player'")
        assert cur.fetchone()[0] == 0
