"""The five tool definitions, defined once in Python.

§10.1a puts the agent LOOP in TypeScript and tool EXECUTION in Python. The
definitions - names, descriptions, schemas - belong to neither and are needed
by both: the TS route sends them to the API, and the Python eval harness must
send byte-identical ones or it is proving things about a different agent.

Two copies would be free to disagree, which is the same argument §10.1a makes
for not reimplementing the SQL guard in `node-sql-parser`. So they live here,
`emit_typescript` writes what the route imports, and they are folded into the
prompt fingerprint - a definition edited on one side and not regenerated on
the other shows up as a stale fingerprint in CI rather than as an eval result
that quietly describes a different tool surface.

The descriptions are load-bearing, not documentation. `query_ball_data`'s
tells the model the rows are a SAMPLE and that aggregates belong in SQL,
because a tool that makes row-reading convenient works against §10.4: a
number counted by eyeballing rows arrives with no sample size, since the
model never asked for one.
"""

from __future__ import annotations

# Kept in step with agent_tools.routes.MODEL_ROWS by
# test_the_tool_description_matches_the_route, so the description cannot
# promise a number the route stopped returning.
MODEL_ROWS = 50
MAX_ROWS = 1000

TOOLS: tuple[dict, ...] = (
    {
        "name": "resolve_entity",
        "description": (
            "Resolve a player, team or venue name to ranked candidates with match "
            "scores. Call this before any tool that takes a name: the corpus stores "
            "abbreviated Cricsheet names ('V Kohli', not 'Virat Kohli'). Returns "
            "candidates rather than one answer - when several are plausible, ask the "
            "user which they meant instead of taking the top score."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The name as the user wrote it."},
                "kind": {"type": "string", "enum": ["player", "team", "venue"]},
            },
            "required": ["name"],
        },
    },
    {
        "name": "query_ball_data",
        "description": (
            "Run one read-only SELECT against the ball-by-ball views: "
            "agent_deliveries, agent_matches, agent_players, agent_teams, "
            "agent_venues. Base tables are not readable.\n\n"
            f"AGGREGATE IN SQL. The `rows` field carries at most {MODEL_ROWS} rows and "
            "is a SAMPLE for inspection, not the result set. Counting or summing by "
            "reading rows gives a wrong answer whenever there are more, and leaves you "
            "with no sample size to quote - use count(*), sum() and group by, and the "
            "number comes back with its own denominator.\n\n"
            "Response fields: `row_count` is how many rows the query actually produced; "
            "`rows_shown` is how many are in `rows`; `truncated` is true when the "
            f"database held more rows than the {MAX_ROWS}-row limit allowed, meaning the "
            "result is not the whole answer and you must say so.\n\n"
            "A rejected query returns {error: 'query rejected', ref: <uuid>} and nothing "
            "else. Report that it was rejected; do not guess at which check refused it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "One SELECT statement."}},
            "required": ["sql"],
        },
    },
    {
        "name": "get_matchup",
        "description": (
            "Head-to-head record for one batter against one bowler over the whole "
            "corpus. Returns `balls`, `runs`, `dismissals`, `strike_rate` and "
            "`average`. `balls` is the sample size and must appear alongside any rate "
            "you quote from it. Names must be resolved with resolve_entity first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "batter": {"type": "string"},
                "bowler": {"type": "string"},
                "format": {"type": "string", "enum": ["ODI", "T20"]},
            },
            "required": ["batter", "bowler"],
        },
    },
    {
        "name": "get_player_form",
        "description": (
            "Current ability estimate for a player. This returns a structured "
            "unavailability: estimating present ability needs a calibrated posterior "
            "with uncertainty, and the table holding it is empty until Phase 5. When it "
            "reports unavailable, say so. You may report the historical record from "
            "query_ball_data instead, but you must state that it describes what happened "
            "and is not an estimate of present ability."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "kind": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "get_live_prediction",
        "description": (
            "The current full prediction set for a match, by its integer match id. "
            "Takes a bare integer only - a corpus reference like 'corpus:9337' is a "
            "different id space over the same fixtures and will not resolve here. If "
            "that is what you have, say so rather than converting it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"match_id": {"type": "integer"}},
            "required": ["match_id"],
        },
    },
)

TOOL_NAMES = tuple(t["name"] for t in TOOLS)

# The FastAPI path each tool executes against. The harness posts here so the
# eval exercises the real handlers - guard, logging, auth - rather than a
# reimplementation of what they do.
ENDPOINTS = {
    "resolve_entity": "/agent/resolve_entity",
    "query_ball_data": "/agent/query_ball_data",
    "get_matchup": "/agent/get_matchup",
    "get_player_form": "/agent/get_player_form",
    "get_live_prediction": "/agent/get_live_prediction",
}

TS_HEADER = """// GENERATED by api/src/agent_eval/tools.py - do not edit.
//
// Regenerate with:
//   python -m agent_eval.tools --emit-ts
//
// Definitions live in Python because the eval harness and the route both
// need byte-identical ones, and two copies are free to disagree - the same
// argument SPEC.md 10.1a makes against reimplementing the SQL guard in
// TypeScript. They are part of PROMPT_FINGERPRINT, so editing one without
// regenerating the other fails the staleness check in CI.
"""


def emit_typescript() -> str:
    import json

    return TS_HEADER + f"\nexport const AGENT_TOOLS = {json.dumps(TOOLS, indent=2)};\n"


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit-ts", action="store_true")
    args = parser.parse_args()
    if args.emit_ts:
        target = Path(__file__).resolve().parents[3] / "web" / "lib" / "agent-tools.ts"
        target.write_text(emit_typescript(), encoding="utf-8")
        print(f"wrote {target}")
    else:
        import json

        print(json.dumps(TOOLS, indent=2))
