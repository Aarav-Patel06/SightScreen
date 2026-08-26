"""SPEC.md section 11 acceptance check: five randomly chosen matches must
match their Cricsheet source JSON exactly, verified by a test - not by
eyeballing.

Local-only, like the schema-parity test: needs the real bulk-loaded corpus
in LOCAL_DATABASE_URL and the retained raw JSON in CRICSHEET_DATA_DIR,
neither of which CI has. Skips cleanly when either is missing.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

from ingest.cricsheet import _extra_type

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

SAMPLE_SIZE = 5
SAMPLE_SEED = 20260826  # fixed, so the same 5 matches are picked every run


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL") or not env.get("CRICSHEET_DATA_DIR"):
        pytest.skip(f"LOCAL_DATABASE_URL and CRICSHEET_DATA_DIR must both be set in {ENV_PATH}")
    return env


@pytest.fixture(scope="module")
def conn():
    connection = psycopg.connect(_env()["LOCAL_DATABASE_URL"])
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def data_dir() -> Path:
    path = Path(_env()["CRICSHEET_DATA_DIR"])
    if not path.is_absolute():
        path = REPO_ROOT / "api" / path
    return path


def _sample_matches(conn, n: int, seed: int) -> list[tuple[int, str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT match_id, external_ids->>'cricsheet' FROM matches ORDER BY match_id")
        rows = cur.fetchall()
    if len(rows) < n:
        pytest.skip(f"only {len(rows)} matches loaded, need at least {n} for the spot-check")
    return random.Random(seed).sample(rows, n)


def _expected_delivery_rows(source: dict, name_to_player_id: dict[str, int]) -> list[tuple]:
    rows = []
    for innings_index, innings in enumerate(source["innings"]):
        for over in innings.get("overs", []):
            for ball_in_over, delivery in enumerate(over.get("deliveries", []), start=1):
                extras = delivery.get("extras", {})
                wickets = delivery.get("wickets", [])
                runs = delivery.get("runs", {})
                player_out_name = wickets[0].get("player_out") if wickets else None
                rows.append(
                    (
                        innings_index + 1,
                        over["over"],
                        ball_in_over,
                        name_to_player_id.get(delivery.get("batter")),
                        name_to_player_id.get(delivery.get("non_striker")),
                        name_to_player_id.get(delivery.get("bowler")),
                        runs.get("batter", 0),
                        runs.get("extras", 0),
                        _extra_type(extras),
                        wickets[0]["kind"] if wickets else None,
                        name_to_player_id.get(player_out_name) if player_out_name else None,
                    )
                )
    return rows


def test_five_random_matches_match_cricsheet_source_exactly(conn, data_dir):
    with conn.cursor() as cur:
        cur.execute("SELECT source_name, player_id FROM player_aliases WHERE source = 'cricsheet'")
        name_to_player_id = dict(cur.fetchall())

    for match_id, cricsheet_id in _sample_matches(conn, SAMPLE_SIZE, SAMPLE_SEED):
        source_path = data_dir / f"{cricsheet_id}.json"
        assert source_path.exists(), f"retained source JSON missing for match {match_id} ({cricsheet_id})"
        source = json.loads(source_path.read_text(encoding="utf-8"))

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT innings, over_num, ball_in_over, batter_id, non_striker_id, bowler_id,
                       runs_batter, runs_extras, extra_type, wicket_type, player_out_id
                FROM deliveries WHERE match_id = %s
                ORDER BY innings, over_num, ball_in_over
                """,
                (match_id,),
            )
            db_rows = cur.fetchall()

        expected_rows = _expected_delivery_rows(source, name_to_player_id)
        assert db_rows == expected_rows, f"delivery mismatch for match {match_id} (cricsheet {cricsheet_id})"

        with conn.cursor() as cur:
            cur.execute("SELECT competition, format, team_a, team_b FROM matches WHERE match_id = %s", (match_id,))
            competition, db_format, team_a, team_b = cur.fetchone()
        info = source["info"]
        assert db_format == info["match_type"]
        assert competition == info.get("event", {}).get("name", "Unknown")

        with conn.cursor() as cur:
            cur.execute("SELECT name FROM teams WHERE team_id IN (%s, %s)", (team_a, team_b))
            db_team_names = {row[0] for row in cur.fetchall()}
        # Team names can differ if this exact match was resolved to a
        # canonical name via an alias (e.g. a rebrand) - what must hold is
        # that both source team names are *known aliases* of the two teams
        # actually recorded against this match, not necessarily identical
        # strings.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_name FROM team_aliases WHERE team_id IN (%s, %s)", (team_a, team_b)
            )
            known_aliases = {row[0] for row in cur.fetchall()}
        for source_team_name in info["teams"]:
            assert source_team_name in db_team_names or source_team_name in known_aliases, (
                f"source team {source_team_name!r} not traceable to match {match_id}'s recorded teams"
            )
