"""The generated artifacts, and the numbers the tool descriptions promise.

Two failure modes this covers, both silent.

A tool DESCRIPTION that names a limit the route stopped returning misleads
the model rather than the reader - it would keep aggregating as if it saw 50
rows when it saw 20, and no test of either file alone would notice.

A stale `web/lib/agent-prompt.ts` or `agent-tools.ts` means the served route
and the eval harness are running different agents, so a green eval says
nothing about production. Same shape as the `types.ts` staleness check in
ci.yml - generate, diff, fail with something actionable - and the same gap it
had before it was extracted into its own job: a check that exists but never
gets read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_eval import prompt as prompt_module
from agent_eval import tools as tools_module
from agent_tools import routes
from agent_tools.sql_guard import MAX_ROWS

REPO = Path(__file__).resolve().parents[2]


def test_the_tool_description_matches_the_route():
    """MODEL_ROWS is stated in the description the model reads and enforced in
    the handler. Two places, so they are pinned together."""
    assert tools_module.MODEL_ROWS == routes.MODEL_ROWS
    assert tools_module.MAX_ROWS == MAX_ROWS
    description = next(
        t["description"] for t in tools_module.TOOLS if t["name"] == "query_ball_data"
    )
    assert str(routes.MODEL_ROWS) in description
    assert str(MAX_ROWS) in description


def test_the_query_tool_tells_the_model_to_aggregate_in_sql():
    """The behavioural half of the 50-row change. Capping rows without saying
    why leaves the model to infer that `rows` is the result set, which is the
    row-reading habit the cap exists to discourage."""
    description = next(
        t["description"] for t in tools_module.TOOLS if t["name"] == "query_ball_data"
    )
    assert "AGGREGATE IN SQL" in description
    assert "SAMPLE" in description


def test_every_tool_has_an_endpoint():
    assert set(tools_module.ENDPOINTS) == set(tools_module.TOOL_NAMES)


def test_every_endpoint_exists_on_the_router():
    """A typo in ENDPOINTS would surface as a 404 the harness records as a
    tool error, which reads like the model misbehaving."""
    served = {r.path for r in routes.router.routes}
    for name, path in tools_module.ENDPOINTS.items():
        assert path in served, f"{name} points at {path}, which the router does not serve"


@pytest.mark.parametrize(
    "artifact, emitter",
    [
        ("web/lib/agent-prompt.ts", prompt_module.emit_typescript),
        ("web/lib/agent-tools.ts", tools_module.emit_typescript),
    ],
)
def test_the_generated_typescript_is_not_stale(artifact, emitter):
    path = REPO / artifact
    assert path.exists(), f"{artifact} has never been generated"
    on_disk = path.read_text(encoding="utf-8")
    fresh = emitter()
    assert on_disk == fresh, (
        f"{artifact} is stale - regenerate with "
        f"`python -m agent_eval.{'prompt' if 'prompt' in artifact else 'tools'} --emit-ts` "
        "and commit it. Until then the served route and the eval harness are "
        "running different agents."
    )


def test_the_fingerprint_covers_tool_descriptions_not_just_names():
    """A description edited without regenerating the TS is exactly the drift
    the fingerprint is for, and a name-only hash would not see it."""
    before = prompt_module.fingerprint()
    original = tools_module.TOOLS
    edited = tuple(
        {**t, "description": t["description"] + " Extra."} if t["name"] == "get_matchup" else t
        for t in original
    )
    try:
        prompt_module.TOOLS = edited
        assert prompt_module.fingerprint() != before
    finally:
        prompt_module.TOOLS = original


def test_the_emitted_tools_parse_as_json():
    """The TS is `export const X = <json>;` - if the JSON were malformed the
    route would fail to build and CI's web job would be the first to know."""
    text = tools_module.emit_typescript()
    payload = text.split("=", 1)[1].rsplit(";", 1)[0].strip()
    assert json.loads(payload) == list(tools_module.TOOLS)
