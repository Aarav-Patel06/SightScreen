"""The tool endpoints fail closed (Phase 6 session 1).

Three ways the shared secret can be wrong, and all three must be a refusal:

    absent       nobody set AGENT_TOOL_SHARED_SECRET
    placeholder  somebody left the committed example value in place
    mismatched   a caller guessed

The placeholder case is the one that needs a test rather than an argument.
AGENT_TOOL_SHARED_SECRET is `str | None` and the literal "changeme" loads
fine today - tests/test_config.py asserts that it does, because the field
was declared in a phase that had not started yet. So "the config validates"
has never implied "the secret is real", and the dependency is the only place
that can close the gap.

Also here: the Gap 2 guarantee. resolve_entity must never reach
ingest.entity_resolution.resolve_player, which writes even when told not to.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_tools import routes

REAL_SECRET = "an-actual-secret-value-that-is-not-a-placeholder"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def configured(monkeypatch) -> None:
    monkeypatch.setattr(routes.settings, "agent_tool_shared_secret", REAL_SECRET)


ENDPOINTS = (
    ("/agent/query_ball_data", {"sql": "SELECT 1"}),
    ("/agent/get_matchup", {"batter": "a", "bowler": "b"}),
    ("/agent/resolve_entity", {"name": "a"}),
    ("/agent/get_live_prediction", {"match_id": 1}),
    ("/agent/get_player_form", {"name": "a"}),
)


@pytest.mark.parametrize("path,payload", ENDPOINTS)
def test_every_endpoint_refuses_a_request_with_no_secret(client, configured, path, payload):
    """Parametrised over ALL of them, not spot-checked on one. A dependency
    that is easy to forget on a new route is exactly the kind of thing that
    gets forgotten on a new route."""
    assert client.post(path, json=payload).status_code == 401


@pytest.mark.parametrize("path,payload", ENDPOINTS)
def test_every_endpoint_refuses_a_wrong_secret(client, configured, path, payload):
    response = client.post(path, json=payload, headers={"X-Agent-Secret": "wrong"})
    assert response.status_code == 401


@pytest.mark.parametrize("path,payload", ENDPOINTS)
def test_every_endpoint_refuses_when_the_secret_is_unset(client, monkeypatch, path, payload):
    monkeypatch.setattr(routes.settings, "agent_tool_shared_secret", None)
    # 503, not 401: this is a misconfigured service, not a bad caller, and
    # the distinction is what makes the failure diagnosable by the operator.
    assert client.post(path, json=payload).status_code == 503


@pytest.mark.parametrize(
    "weak",
    [
        # The value actually committed in api/.env.example.
        "changeme",
        "CHANGEME",
        # Short enough to be guessable, and caught by the length floor
        # rather than by anyone having enumerated them.
        "change-me",
        "hunter2",
        "your-secret-here",
        "a" * (routes.MIN_SECRET_LENGTH - 1),
    ],
)
def test_a_weak_secret_is_refused_even_when_the_caller_sends_it(client, monkeypatch, weak):
    """Knowing the secret must not be enough when the secret is weak. The
    placeholder is committed in .env.example, so "the caller sent the right
    value" is worthless when the right value is public - and an enumerated
    list of placeholders only catches the ones someone already imagined,
    which is why there is a length floor behind it."""
    monkeypatch.setattr(routes.settings, "agent_tool_shared_secret", weak)
    response = client.post(
        "/agent/get_player_form",
        json={"name": "a"},
        headers={"X-Agent-Secret": weak},
    )
    assert response.status_code == 503


def test_a_secret_of_exactly_the_minimum_length_is_accepted(client, monkeypatch):
    """The boundary, in the direction that matters. A floor set one
    character too high would reject secrets.token_urlsafe(24) output and be
    discovered in production."""
    import secrets

    generated = secrets.token_urlsafe(24)
    assert len(generated) >= routes.MIN_SECRET_LENGTH
    monkeypatch.setattr(routes.settings, "agent_tool_shared_secret", generated)
    response = client.post(
        "/agent/get_player_form", json={"name": "a"}, headers={"X-Agent-Secret": generated}
    )
    assert response.status_code == 200


def test_a_correct_secret_is_accepted(client, configured):
    """The anti-vacuity half. Without this, every test above would still
    pass if the dependency rejected unconditionally - which would be a
    perfectly secure service that does nothing."""
    response = client.post(
        "/agent/get_player_form",
        json={"name": "V Kohli"},
        headers={"X-Agent-Secret": REAL_SECRET},
    )
    assert response.status_code == 200
    assert response.json()["available"] is False


def test_the_secret_is_compared_in_constant_time(configured):
    """A plain == on a secret leaks its length and prefix through timing.
    hmac.compare_digest is the fix and the only way to keep it is to assert
    on it - this is not observable from the endpoint's behaviour."""
    source = inspect.getsource(routes.require_agent_secret)
    assert "compare_digest" in source
    assert "== configured" not in source


# --- Gap 2: the read-only resolver must stay read-only --------------------


def test_resolve_entity_never_calls_the_writing_resolver():
    """ingest.entity_resolution.resolve_player writes even when asked not
    to: with allow_create=False it still INSERTs an alias row on a fuzzy
    match and INSERTs into unresolved_entities on a miss. Wired to a tool,
    an agent asking about a misspelled name would mint permanent corpus rows
    from a model-generated string.

    The tool reuses the PURE helpers instead. This asserts the separation
    rather than trusting it, because the writing function has an inviting
    name and sits one import away.

    Parsed, not grepped. The first version of this test searched the source
    text and failed on routes.py's own docstring, which names
    resolve_player in order to explain why it is not used - a check that
    cannot tell an explanation from a call is a check that punishes
    documenting the reasoning."""
    import ast

    tree = ast.parse(inspect.getsource(routes))
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            referenced.update(alias.name for alias in node.names)

    for writer in ("resolve_player", "resolve_team", "resolve_venue", "_create_alias", "_enqueue"):
        assert writer not in referenced, (
            f"agent_tools/routes.py references {writer}, which writes to the "
            "corpus. The agent's resolver must be read-only - see Gap 2."
        )


def test_the_helpers_the_resolver_reuses_take_no_connection():
    """The structural reason the tool cannot write: every helper it imports
    from entity_resolution is pure. If one of them grew a `conn` parameter,
    the read-only guarantee would become a convention instead of a fact."""
    from ingest.entity_resolution import (
        _score_candidates,
        normalize_name,
        surname_blocks,
        surname_key,
    )

    for helper in (normalize_name, surname_key, surname_blocks, _score_candidates):
        parameters = inspect.signature(helper).parameters
        assert "conn" not in parameters, f"{helper.__name__} now takes a connection"
