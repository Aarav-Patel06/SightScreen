"""The agent's get_live_prediction keeps to one model version per match
(SPEC.md section 12.2, the same rule as web/lib/model-version.ts).

A match can hold a full chase from two models. LIVE_PREDICTION_SQL took the
newest row of ANY version, so with a shadow model writing after the served
one the agent would quote the shadow's probability while every page showed
the served one's. The rule: the active version if the match has rows from
it, otherwise the newest (latest trained_at).

Runs against the disposable test database (tests/conftest.py `conn`); in CI
that is the migrated, empty service database.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
from psycopg.rows import dict_row

from agent_tools.routes import LIVE_PREDICTION_SQL

OLD, NEW = "test-live-old", "test-live-new"
MATCH_ID = 424242


@pytest.fixture()
def seeded(conn):
    with conn.cursor() as cur:
        for version, trained in ((OLD, datetime(2026, 9, 10, tzinfo=timezone.utc)),
                                 (NEW, datetime(2026, 9, 27, tzinfo=timezone.utc))):
            cur.execute(
                "INSERT INTO model_versions (model_version, model_type, trained_at, train_end_date, "
                "artifact_path, is_active) VALUES (%s, 'win_prob_2nd', %s, %s, 'https://example.test/a.pkl', false)",
                (version, trained, date(2023, 12, 31)),
            )
        cur.execute(
            "INSERT INTO matches (match_id, competition, format, start_time, status) "
            "VALUES (%s, 'Test', 'T20', now(), 'complete')",
            (MATCH_ID,),
        )
        # The old model's chase first, the new one's after it - so the newest
        # row of any version is always NEW's.
        for version, p, first_id in ((OLD, 0.3, 1), (NEW, 0.7, 101)):
            for ball in range(3):
                cur.execute(
                    "INSERT INTO predictions (prediction_id, match_id, model_version, prediction_type, "
                    "payload, match_phase, innings, over_num, ball_in_over, source) "
                    "VALUES (%s, %s, %s, 'win_prob', %s, 'second_innings', 2, 0, %s, 'backfill')",
                    (first_id + ball, MATCH_ID, version, json.dumps({"p": p}), ball + 1),
                )
    yield conn
    with conn.cursor() as cur:
        cur.execute("DELETE FROM predictions WHERE match_id = %s", (MATCH_ID,))
        cur.execute("DELETE FROM model_versions WHERE model_version IN (%s, %s)", (OLD, NEW))


def _activate(conn, version: str | None) -> None:
    conn.execute("UPDATE model_versions SET is_active = COALESCE(model_version = %s, false) WHERE model_version IN (%s, %s)",
                 (version, OLD, NEW))


def _answer(conn) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(LIVE_PREDICTION_SQL, (MATCH_ID,)).fetchone()


@pytest.mark.parametrize("active, p", [(OLD, 0.3), (NEW, 0.7)])
def test_it_quotes_the_active_version_when_the_match_has_it(seeded, active, p):
    _activate(seeded, active)
    row = _answer(seeded)
    assert row["model_version"] == active
    assert row["win_probability"] == pytest.approx(p)


def test_without_an_active_version_it_quotes_the_newest(seeded):
    _activate(seeded, None)
    assert _answer(seeded)["model_version"] == NEW
