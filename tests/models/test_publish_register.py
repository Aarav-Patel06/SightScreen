"""Registering a version on Supabase without making it active.

publish_model_version used to have one mode: write the row, make it active,
and demote every other version. Registering P for its shadow run through that
path would have flipped production's active model, and every pinned container
would then refuse to predict (models/artifact.py ActiveVersionGuard). A
non-active registration must leave the active version exactly as it was.

Runs on the disposable test database (tests/conftest.py `conn`).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from models.publish_model_version import register_row

SERVED, CANDIDATE = "test-publish-served", "test-publish-candidate"
META = {"model_type": "win_prob_2nd", "trained_at": datetime(2026, 9, 27, tzinfo=timezone.utc),
        "train_end_date": date(2023, 12, 31), "test_brier": 0.12119, "test_log_loss": 0.37511}


@pytest.fixture()
def registry(conn):
    conn.execute(
        "INSERT INTO model_versions (model_version, model_type, trained_at, train_end_date, artifact_path, is_active) "
        "VALUES (%s, 'win_prob_2nd', now(), '2023-12-31', 'https://example.test/s.pkl', true)", (SERVED,))
    yield conn
    conn.execute("DELETE FROM model_versions WHERE model_version IN (%s, %s)", (SERVED, CANDIDATE))


def _rows(conn) -> dict:
    return {v: (a, s) for v, a, s in conn.execute(
        "SELECT model_version, is_active, is_shadow FROM model_versions WHERE model_version IN (%s, %s)",
        (SERVED, CANDIDATE)).fetchall()}


def test_an_inactive_registration_leaves_the_active_version_alone(registry):
    register_row(registry, CANDIDATE, META, "https://example.test/c.pkl#sha256=ab", "notes", active=False)
    assert _rows(registry) == {SERVED: (True, False), CANDIDATE: (False, False)}


def test_an_active_registration_still_leaves_exactly_one_active(registry):
    register_row(registry, CANDIDATE, META, "https://example.test/c.pkl#sha256=ab", "notes", active=True)
    assert _rows(registry) == {SERVED: (False, False), CANDIDATE: (True, False)}
