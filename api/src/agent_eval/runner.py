"""The model-calling half of the eval. A deliberate command, never CI.

    python -m agent_eval.runner --cases form            the honesty subset
    python -m agent_eval.runner --thinking-ab           both ways, 8 cases
    python -m agent_eval.runner --prove citation        the ablation proof
    python -m agent_eval.runner --all                   a full pass

**Every invocation spends real money.** Cost is measured from `usage` on each
response rather than estimated, and written into the results file with the
model id, the date and the prompt fingerprint - so "why has this not been
re-run since October" is answerable by reading the file instead of
reconstructing it.

The loop is here in Python while §10.1a puts the served loop in TypeScript.
That is a real duplication and it is bounded deliberately: the tool
DEFINITIONS come from `agent_eval.tools`, which also emits the TypeScript the
route imports, and the prompt comes from `agent_eval.prompt`, which does the
same. Both are in the fingerprint. What is duplicated is the mechanical
send-execute-append cycle; what is not duplicated is anything that decides an
answer. Tools execute through the real FastAPI handlers in-process, so the
guard, the query log and the shared-secret auth are the production ones and
not a description of them.

Prompt caching is on. The system prompt plus tool definitions is a 1,512-token
prefix re-sent every turn, which is about half the uncached bill; Sonnet 5's
minimum cacheable prefix is 512 tokens, so it qualifies comfortably.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

from agent_eval.cases import LiveCase, load
from agent_eval.checker import Result, check_answer
from agent_eval.heartbeat import Heartbeat, status
from agent_eval.prompt import (
    CACHE_READ_RATE,
    CACHE_WRITE_RATE,
    INPUT_RATE,
    MODEL,
    OUTPUT_RATE,
    build,
    fingerprint,
)
from agent_eval.tools import ENDPOINTS, TOOLS

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
RESULTS_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_eval" / "results.json"

MAX_TURNS = 8
MAX_TOKENS = 4096

# Cases where intermittency is the risk the case exists to detect, so a
# majority is not a pass. Set by the user 2026-09-21: "2-of-3 is a failure,
# not a flake."
HONESTY_REPS = 3
OTHER_REPS = 1


def _is_honesty(case: LiveCase) -> bool:
    return case.category == "form" or bool(case.cite)


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY") or dotenv_values(_ENV_PATH).get("ANTHROPIC_API_KEY")
    if not key or not key.strip():
        raise SystemExit(f"ANTHROPIC_API_KEY not set (checked environment and {_ENV_PATH})")
    return key.strip()


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, u) -> None:
        self.input_tokens += getattr(u, "input_tokens", 0) or 0
        self.output_tokens += getattr(u, "output_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_creation_input_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0

    @property
    def dollars(self) -> float:
        return (
            self.input_tokens * INPUT_RATE
            + self.output_tokens * OUTPUT_RATE
            + self.cache_read_input_tokens * CACHE_READ_RATE
            + self.cache_creation_input_tokens * CACHE_WRITE_RATE
        )


@dataclass
class Run:
    case_id: str
    rep: int
    thinking: bool
    ablated: list[str] = field(default_factory=list)
    answer: str = ""
    tools_called: list[str] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    verdict: str = ""
    rules_fired: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    dollars: float = 0.0
    turns: int = 0


def _tool_client():
    """The real tool handlers, in-process.

    TestClient rather than a live server: it routes through the actual
    handlers - the SQL guard, the query log, the shared-secret dependency -
    with nothing to start and no port to guess. A hand-rolled stub would be
    the parallel copy this file exists to avoid.

    Only `agent_tools.routes` is mounted, not `serving.app`. The serving app's
    lifespan runs the deployment checks - MODEL_VERSION pinning, reference
    freshness, database boundary - which belong to the prediction service and
    have nothing to do with executing a tool; requiring them here would mean
    the eval could not run without a pinned model version. This mirrors
    tests/agent/test_tool_auth.py, which mounts the router for the same
    reason.

    What this therefore does NOT exercise is the mounting itself. That is
    covered by test_tool_auth's own assertions over all five routes.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_tools import routes  # noqa: PLC0415

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app, raise_server_exceptions=False)


def _secret() -> str:
    value = os.environ.get("AGENT_TOOL_SHARED_SECRET") or dotenv_values(_ENV_PATH).get(
        "AGENT_TOOL_SHARED_SECRET"
    )
    if not value:
        raise SystemExit("AGENT_TOOL_SHARED_SECRET not set; the tool endpoints refuse calls")
    return value


def run_case(
    client,
    api,
    case: LiveCase,
    *,
    rep: int,
    thinking: bool,
    ablate: frozenset[str],
    secret: str,
) -> Run:
    """One conversation. Returns the transcript, the verdict and the cost."""
    system = [
        {
            "type": "text",
            "text": build(ablate=ablate),
            # The breakpoint goes on the LAST stable block, so tools + system
            # are cached and the varying question sits after it.
            "cache_control": {"type": "ephemeral"},
        }
    ]
    messages: list[dict] = [{"role": "user", "content": case.question}]
    usage = Usage()
    run = Run(case_id=case.id, rep=rep, thinking=thinking, ablated=sorted(ablate))

    for turn in range(MAX_TURNS):
        kwargs = dict(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=list(TOOLS),
            messages=messages,
        )
        if thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        response = api.messages.create(**kwargs)
        usage.add(response.usage)
        run.turns = turn + 1

        text = "".join(b.text for b in response.content if b.type == "text")
        if text:
            run.answer = text
        messages.append({"role": "assistant", "content": response.content})

        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            break

        results = []
        for call in calls:
            run.tools_called.append(call.name)
            path = ENDPOINTS.get(call.name)
            if path is None:
                payload = {"error": f"unknown tool {call.name}"}
            else:
                reply = client.post(
                    path, json=dict(call.input), headers={"X-Agent-Secret": secret}
                )
                try:
                    payload = reply.json()
                except Exception:
                    payload = {"error": "non-json response", "status": reply.status_code}
            run.tool_results.append({"tool": call.name, "result": payload})
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps(payload)[:20_000],
                }
            )
        # All results in ONE user message: splitting them teaches the model to
        # stop making parallel calls.
        messages.append({"role": "user", "content": results})

    checked: Result = check_answer(case, run.answer, run.tool_results, run.tools_called)
    run.verdict = checked.verdict
    run.rules_fired = sorted(checked.rules_fired())
    run.failures = [str(f) for f in checked.failures]
    run.warnings = list(checked.warnings)
    run.usage = asdict(usage)
    run.dollars = usage.dollars
    return run


def _write_results(payload: dict) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {RESULTS_PATH}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=None, help="only this category")
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--thinking-ab", action="store_true", help="honesty subset, both ways")
    parser.add_argument("--prove", default=None, metavar="CLAUSE", help="ablate and prove")
    parser.add_argument("--thinking", default="on", choices=["on", "off"])
    parser.add_argument("--reps", type=int, default=None)
    parser.add_argument("--out", default=None, help="results file name under data/agent_eval/")
    parser.add_argument("--status", action="store_true", help="report the heartbeat and exit")
    parser.add_argument("--force", action="store_true", help="start even if one looks live")
    args = parser.parse_args(argv)

    if args.status:
        print(status()[1])
        return 0

    import anthropic

    api = anthropic.Anthropic(api_key=_api_key())
    client = _tool_client()
    secret = _secret()
    eval_set = load()

    if args.prove:
        from agent_eval.proof import prove

        return prove(client, api, eval_set, secret, clause=args.prove, out=args.out)

    cases = list(eval_set.live)
    if args.cases:
        cases = [c for c in cases if c.category == args.cases]
    if args.case_id:
        cases = [c for c in cases if c.id == args.case_id]
    if not cases:
        raise SystemExit("no cases selected")

    configs = [True, False] if args.thinking_ab else [args.thinking == "on"]
    if args.thinking_ab:
        cases = [c for c in eval_set.live if _is_honesty(c)]

    # Refuse to start a second paid job on top of a live one. The $0.70 that
    # motivated the heartbeat was spent re-running an eval that was still
    # going, because a buffered pipe and `ps` both said it was not.
    state, line = status()
    if state == "RUNNING" and not args.force:
        raise SystemExit(
            f"a paid job is already running: {line}. "
            "Wait for it, or pass --force if you are certain it is not."
        )

    total_planned = sum(
        (args.reps or (HONESTY_REPS if _is_honesty(c) else OTHER_REPS)) for c in cases
    ) * len(configs)

    runs: list[Run] = []
    started = time.perf_counter()
    with Heartbeat.start(args.out or "results", total=total_planned) as beat:
        for thinking in configs:
            for case in cases:
                reps = args.reps or (HONESTY_REPS if _is_honesty(case) else OTHER_REPS)
                for rep in range(1, reps + 1):
                    run = run_case(
                        client, api, case, rep=rep, thinking=thinking,
                        ablate=frozenset(), secret=secret,
                    )
                    runs.append(run)
                    mark = "ok  " if run.verdict == "pass" else "FAIL"
                    think = "think" if thinking else "     "
                    print(
                        f"{mark} {think} {case.id:<36} rep{rep} "
                        f"{run.turns}t ${run.dollars:.4f} {','.join(run.rules_fired) or ''}"
                    )
                    beat.tick(
                        done=len(runs),
                        dollars=sum(r.dollars for r in runs),
                        note=f"{case.id} rep{rep}",
                    )

    total = sum(r.dollars for r in runs)
    payload = {
        "model": MODEL,
        "prompt_fingerprint": fingerprint(),
        "run_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        # Recorded so a figure read months later is interpretable without
        # looking up what pricing was at the time.
        "rates_per_mtok": {"input": INPUT_RATE * 1e6, "output": OUTPUT_RATE * 1e6},
        "cost_dollars": round(total, 4),
        "runs": [asdict(r) for r in runs],
    }
    if args.out:
        global RESULTS_PATH
        RESULTS_PATH = RESULTS_PATH.parent / args.out
    _write_results(payload)

    failed = [r for r in runs if r.verdict != "pass"]
    print(f"\n{len(runs) - len(failed)}/{len(runs)} passed | ${total:.4f} | "
          f"{payload['elapsed_seconds']}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
