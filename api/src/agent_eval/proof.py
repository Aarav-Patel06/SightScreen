"""The non-vacuity proof for a prompt clause.

The claim is not "the eval goes red when the citation clause is removed". It
is the stronger one `sql_guard`'s layer proofs make: **red on that gate and
nothing else.** A run that fails on `must_call` because the model also skipped
`resolve_entity` establishes nothing about whether the citation clause was
load-bearing.

So the proof has three parts, and all three must hold:

    CONTROL   clause present  -> pass, no gates fired
    ABLATED   clause removed  -> fail, and the clause's own gate fired
    SPECIFIC  ablated failure -> that gate ALONE

and it prints the ablated ANSWER TEXT, not merely the rule name. If removing
the clause produces an answer that happens to cite anyway and fails for some
subtler reason, the transcript is the only thing that shows it - a rule name
would report the same red for a different world.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from agent_eval.checker import RULE_FOR_CLAUSE
from agent_eval.prompt import MODEL, fingerprint

PROOF_DIR = Path(__file__).resolve().parents[2] / "data" / "agent_eval"


def _console(text: str, limit: int = 600) -> str:
    """Model text, made safe for whatever codepage the console happens to have.

    An answer containing U+2011 crashed this with a UnicodeEncodeError on a
    cp1252 console - twice - each time AFTER the control run had been paid
    for. The console is a lossy renderer (standing rule 12), so it gets a
    lossy copy and the JSON file keeps the exact bytes. Losing a paid run to
    a print statement is worth one helper.
    """
    return text.strip()[:limit].encode("ascii", "replace").decode("ascii")

# One rep each side. This is not a flakiness measurement - it is an existence
# proof that the gate can fire - so a single clean demonstration of each of
# the three parts is what it needs. Reps live in the eval pass, not here.
REPS = 1


def _pick_case(eval_set, rule: str):
    """A case whose assertions can ONLY be tripped by this rule.

    Chosen rather than hardcoded, and preferring the case with the fewest
    other hard assertions: the specificity half of the proof is easiest to
    satisfy where nothing else can go red, and a case with several
    assertions invites exactly the wrong-reason failure the proof rejects.
    """
    predicate = {
        "cite": lambda c: bool(c.cite) or bool(c.cite_values_from_first_row),
        "disclose_unavailable": lambda c: c.must_disclose_unavailable,
        "disclose_truncation": lambda c: c.must_disclose_truncation,
        "no_guard_detail": lambda c: c.expect_rejection,
    }[rule]
    candidates = [c for c in eval_set.live if predicate(c)]
    if not candidates:
        raise SystemExit(f"no live case exercises {rule!r}, so nothing can prove it")
    return min(
        candidates,
        key=lambda c: len(c.must_call) + len(c.must_not_call) + len(c.cite),
    )


def prove(
    client, api, eval_set, secret, *, clause: str, out: str | None = None,
    case_id: str | None = None,
) -> int:
    from agent_eval.runner import run_case

    if clause not in RULE_FOR_CLAUSE:
        raise SystemExit(
            f"{clause!r} has no gate to fire. Provable clauses: {sorted(RULE_FOR_CLAUSE)}"
        )
    rule = RULE_FOR_CLAUSE[clause]
    if case_id:
        # Aimable, because "which case can demonstrate this" is an empirical
        # question. The default pick is the case with fewest other
        # assertions, which minimises wrong-reason failures but also tends to
        # pick the EASIEST case - and an easy case is one the model may get
        # right without being told, which is not the clause failing to matter
        # so much as the probe being too gentle to detect whether it does.
        case = next((c for c in eval_set.live if c.id == case_id), None)
        if case is None:
            raise SystemExit(f"no live case {case_id!r}")
    else:
        case = _pick_case(eval_set, rule)

    print(f"proving clause {clause!r} via rule {rule!r} on case {case.id!r}")
    print(f"question: {case.question}\n")

    control = run_case(
        client, api, case, rep=1, thinking=True, ablate=frozenset(), secret=secret
    )
    print(f"--- CONTROL (clause present) -> {control.verdict}, "
          f"gates {control.rules_fired or '[]'}, ${control.dollars:.4f}")
    print(f"    answer: {_console(control.answer)}\n")

    ablated = run_case(
        client, api, case, rep=1, thinking=True, ablate=frozenset({clause}), secret=secret
    )
    print(f"--- ABLATED ({clause} removed) -> {ablated.verdict}, "
          f"gates {ablated.rules_fired or '[]'}, ${ablated.dollars:.4f}")
    print(f"    answer: {_console(ablated.answer)}\n")
    for failure in ablated.failures:
        print(f"    {_console(str(failure), 300)}")

    checks = {
        "control_passes": control.verdict == "pass",
        "control_fires_no_gates": not control.rules_fired,
        "ablated_fails": ablated.verdict == "fail",
        "ablated_fires_the_clauses_gate": rule in ablated.rules_fired,
        "ablated_fires_that_gate_alone": ablated.rules_fired == [rule],
    }
    print("\n" + "-" * 62)
    for name, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    held = all(checks.values())
    print("-" * 62)
    print(
        f"clause {clause!r} is LOAD-BEARING: removing it turns {case.id} red on "
        f"{rule!r} alone."
        if held
        else f"PROOF DID NOT HOLD for {clause!r}. Read the answers above - a red "
             f"for the wrong reason means the gate is not measuring what it claims."
    )

    payload = {
        "clause": clause,
        "rule": rule,
        "case_id": case.id,
        "question": case.question,
        "model": MODEL,
        "prompt_fingerprint": fingerprint(),
        "run_at": datetime.now(timezone.utc).isoformat(),
        "held": held,
        "checks": checks,
        "cost_dollars": round(control.dollars + ablated.dollars, 4),
        # The answers themselves, in full. A rule name cannot distinguish "it
        # stopped citing" from "it cited but tripped something subtler", and
        # the second is the outcome worth seeing.
        "control": asdict(control),
        "ablated": asdict(ablated),
    }
    target = PROOF_DIR / (out or f"proof-{clause}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target}")
    return 0 if held else 1
