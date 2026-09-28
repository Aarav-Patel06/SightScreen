"""Training and serving build the same model input, bit for bit.

Training assembles features in `models.win_prob_2nd.build_feature_bundle`;
every serving path assembles them in `ingest.replay.serving_features`, fed by
one of two row builders:

  backfill - `ingest.replay_log.load_balls`, reading match_states (the daily
             job and the backfill logger)
  live     - `features.match_state.IncrementalMatchStateBuilder`, ball by ball
             (the live worker)

For P's 11 features (`state_venue_elo_no_partnership`), every innings-2 ball
of three real chases must produce the identical float64 vector on all three
paths - compared with np.array_equal, not a tolerance. A tolerance is what let
serving and training drift before (Phase 2 session 3's as-of date type).

Needs the local corpus, so CI skips it (docs/ci-skipped-tests.md).
"""

from __future__ import annotations

import os

import numpy as np
import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_PATH = os.path.join(REPO_ROOT, "api", ".env")

VARIANT = "state_venue_elo_no_partnership"
# 8429 India v England ODI; 8002 Panama v Turks and Caicos T20 (cold-start
# venue and teams); 9337 a CPL T20 chase.
MATCH_IDS = (8429, 8002, 9337)


@pytest.fixture(scope="module")
def conn():
    url = dotenv_values(ENV_PATH).get("LOCAL_DATABASE_URL")
    if not url:
        pytest.skip("LOCAL_DATABASE_URL not set")
    with psycopg.connect(url) as connection:
        if connection.execute("SELECT count(*) FROM matches WHERE match_id = ANY(%s)", (list(MATCH_IDS),)).fetchone()[0] != 3:
            pytest.skip("the reference matches are not loaded")
        yield connection


@pytest.fixture(scope="module")
def training(conn):
    """Training's own path: the split, then build_feature_bundle, then the
    variant's columns - keyed by (match, over, ball)."""
    from eval.splits import SecondInningsDataset, get_second_innings_split
    from models.win_prob_2nd import build_feature_bundle

    test_ds = get_second_innings_split(conn, "test")
    mask = np.isin(test_ds.match_id, MATCH_IDS)
    fields = ("delivery_id", "match_id", "match_date", "required_run_rate", "wickets_in_hand",
              "balls_remaining", "runs_required", "phase", "label")
    ds = SecondInningsDataset(**{f: getattr(test_ds, f)[mask] for f in fields})
    X, names = build_feature_bundle(conn, ds).select(VARIANT)
    keys = {d: (m, o, b) for d, m, o, b in conn.execute(
        "SELECT delivery_id, match_id, over_num, ball_in_over FROM deliveries WHERE delivery_id = ANY(%s)",
        (ds.delivery_id.tolist(),),
    ).fetchall()}
    return names, {keys[d]: X[i] for i, d in enumerate(ds.delivery_id.tolist())}


def _as_of(conn, match_id: int) -> dict:
    from features.as_of import compute_as_of_features

    venue, fmt, date, bat, bowl = conn.execute(
        "SELECT m.venue_id, m.format, m.start_time::date, d.batting_team_id, d.bowling_team_id "
        "FROM matches m JOIN deliveries d USING (match_id) WHERE m.match_id = %s AND d.innings = 2 LIMIT 1",
        (match_id,),
    ).fetchone()
    return compute_as_of_features(conn, venue, bat, bowl, fmt, date)


def backfill_vectors(conn, names) -> dict:
    from ingest.replay import serving_features
    from ingest.replay_log import _row_for, load_balls

    out = {}
    for match_id in MATCH_IDS:
        as_of = _as_of(conn, match_id)
        for ball in load_balls(conn, match_id):
            row = _row_for(ball, match_id)
            out[(match_id, ball["over_num"], ball["ball_in_over"])] = serving_features(
                {"feature_names": names}, row, as_of)[0]
    return out


def live_vectors(conn, names) -> dict:
    from features.match_state import IncrementalMatchStateBuilder
    from ingest.replay import ReplayClient, serving_features

    out = {}
    for match_id in MATCH_IDS:
        as_of = _as_of(conn, match_id)
        client = ReplayClient(conn, match_id)
        builder = IncrementalMatchStateBuilder(match_id, client.format)
        builder.set_target(client.target_runs, client.target_overs)  # as ingest/replay.py's run_cli does
        for delivery in client.get_deliveries_since(match_id, 0):
            row = builder.process(delivery)
            if row.innings == 2:
                out[(match_id, delivery.over_num, delivery.ball_in_over)] = serving_features(
                    {"feature_names": names}, row, as_of)[0]
    return out


def _mismatches(expected: dict, actual: dict, names) -> list[str]:
    problems = []
    for key, want in expected.items():
        got = actual.get(key)
        if got is None:
            problems.append(f"{key}: no serving row")
            continue
        for name, a, b in zip(names, want, got):
            if not (a == b or (np.isnan(a) and np.isnan(b))):
                problems.append(f"{key} {name}: training {a!r} vs serving {b!r}")
    return problems


@pytest.mark.parametrize("path", ["backfill", "live"])
def test_serving_builds_the_training_vector_bit_for_bit(conn, training, path):
    names, expected = training
    assert len(expected) > 300, "the three reference chases should give several hundred balls"
    actual = (backfill_vectors if path == "backfill" else live_vectors)(conn, names)
    problems = _mismatches(expected, actual, names)
    assert not problems, f"{len(problems)} mismatches on the {path} path, e.g. {problems[:5]}"


def test_a_different_serving_computation_is_caught(conn, training, monkeypatch):
    """The check is only worth something if it can fail: an off-by-one-ball
    run rate in serving - the kind of drift this exists for - must be seen."""
    import ingest.replay as replay

    names, expected = training
    original = replay.serving_features

    def drifted(artifact, row, as_of):
        X = original(artifact, row, as_of).copy()
        crr = artifact["feature_names"].index("current_run_rate")
        balls = row.balls_bowled + 1
        X[0, crr] = row.score / (balls / 6.0)
        return X

    monkeypatch.setattr(replay, "serving_features", drifted)
    problems = _mismatches(expected, backfill_vectors(conn, names), names)
    assert problems and all("current_run_rate" in p for p in problems if "no serving row" not in p)
