"""Live-vs-training as-of feature parity (SPEC.md section 6.2, Phase 2
session 1, Decision 3).

`ingest.replay.compute_as_of_features` must produce IDENTICAL elo_diff/
venue values to `models.win_prob_2nd.build_feature_bundle` for the same
match - not just "close," since they're supposed to call the exact same
underlying functions (features.elo.elo_as_of, features.venue_stats.*).
Guarantees a cold-start venue (24-33% of matches, Phase 1's measured rate)
gets the identical fallback at serving time that the model was trained
with, by construction rather than by re-implementing the rule twice.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import psycopg
import pytest
from dotenv import dotenv_values

from eval.splits import SecondInningsDataset, get_second_innings_split
from ingest.replay import compute_as_of_features
from models.win_prob_2nd import build_feature_bundle

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

FIXTURE_MATCH_ID = 6290  # Sussex v Gloucestershire, 2024-09-14 - falls in the val split


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        pytest.skip(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def test_live_asof_features_match_training_asof_features():
    conn = psycopg.connect(_env()["LOCAL_DATABASE_URL"])
    try:
        val_ds = get_second_innings_split(conn, "val")
        mask = val_ds.match_id == FIXTURE_MATCH_ID
        if not mask.any():
            pytest.skip(f"match {FIXTURE_MATCH_ID} has no trainable innings-2 rows in val")

        fields = ("delivery_id", "match_id", "match_date", "required_run_rate", "wickets_in_hand",
                  "balls_remaining", "runs_required", "phase", "label")
        match_ds = SecondInningsDataset(**{f: getattr(val_ds, f)[mask] for f in fields})

        training_bundle = build_feature_bundle(conn, match_ds)
        training_elo_diff = training_bundle.columns["elo_diff"][0]
        training_venue_rate = training_bundle.columns["venue_chase_win_rate"][0]
        training_venue_avg = training_bundle.columns["venue_avg_first_innings"][0]

        with conn.cursor() as cur:
            cur.execute(
                "SELECT venue_id, format, start_time FROM matches WHERE match_id = %s",
                (FIXTURE_MATCH_ID,),
            )
            venue_id, format_, start_time = cur.fetchone()
            # The actual batting/bowling teams for innings 2 - not team_a/
            # team_b directly, since elo_diff's sign depends on which team
            # was chasing, and only the deliveries table denormalizes that.
            cur.execute(
                "SELECT DISTINCT batting_team_id, bowling_team_id FROM deliveries "
                "WHERE match_id = %s AND innings = 2",
                (FIXTURE_MATCH_ID,),
            )
            batting_team_id, bowling_team_id = cur.fetchone()

        live_features = compute_as_of_features(conn, venue_id, batting_team_id, bowling_team_id, format_, start_time)

        assert live_features["elo_diff"] == pytest.approx(training_elo_diff)
        _assert_matches_or_both_nan(live_features["venue_chase_win_rate"], training_venue_rate)
        _assert_matches_or_both_nan(live_features["venue_avg_first_innings"], training_venue_avg)
    finally:
        conn.close()


def _assert_matches_or_both_nan(live_value, training_value) -> None:
    training_is_nan = training_value is None or (isinstance(training_value, float) and np.isnan(training_value))
    if live_value is None:
        assert training_is_nan, f"live returned None but training had {training_value!r}"
    else:
        assert not training_is_nan, f"training was NaN but live returned {live_value!r}"
        assert live_value == pytest.approx(training_value)
