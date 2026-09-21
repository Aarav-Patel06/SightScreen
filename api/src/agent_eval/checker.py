"""The deterministic half of the eval: does an answer satisfy its case?

No model calls, no judgement in English, no network. This is what runs in CI
on every push, and it is deterministic on purpose. An LLM judge was the
obvious alternative and is the wrong tool for a GATE: it is nondeterministic,
it costs money per run, and a check that goes red intermittently teaches
people to ignore red - which is the failure mode §10.3's non-vacuity proofs
already exist to prevent one level down.

The reason a deterministic check is even possible is worth stating, because
"did the answer cite a sample size?" sounds like it needs comprehension.
**It does not, because the harness knows what the tools returned.** When
get_matchup answered balls=225, this does not read the prose and judge
whether a sample size was given - it asserts the integer 225 appears in it.
That turns a fuzzy judgement into an exact assertion, and it is why `cite`
holds dotted paths into tool results rather than regexes.

Two tiers, and the split is deliberate:

    HARD  - gating. Every rule here is an exact comparison against tool
            output or a disclosure keyword. Goes red in CI.
    SOFT  - reported, never gating. Substring bans on English phrasing are
            brittle: "he is in form" and "whether he is in form cannot be
            answered" share a substring and differ in meaning. Those belong
            in a report a human reads, not in a check that blocks a push.

`must_say_any` / `must_not_say_any` are the soft tier. Everything else is
hard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent_eval.cases import CannedCase, LiveCase

# Words that count as disclosing an unavailability. Deliberately short and
# semantic rather than a phrase list: the rule is "say you cannot", and an
# answer can say that many ways. Checked case-insensitively against the
# answer, and the passing canned case is what pins the bar.
UNAVAILABILITY_MARKERS = (
    "cannot",
    "can't",
    "not available",
    "unavailable",
    "no estimate",
    "not an estimate",
    "record, not",
    "does not exist",
    "isn't available",
    "is not available",
)

TRUNCATION_MARKERS = (
    "truncated",
    "partial",
    "cut off",
    "row limit",
    "not the full",
    "not every",
    "first 1,000",
    "first 1000",
    "more than",
)

# Things that must never reach the user, per §10.3's uniform-payload rule.
# Hard tier, and an exact-token list rather than a judgement: the layer names
# are fixed strings and naming one turns the payload into an oracle.
GUARD_DETAIL_TOKENS = (
    "single_statement",
    "single_select",
    "forced_limit",
    "readonly_role",
    "statement_timeout",
)


@dataclass
class Result:
    case_id: str
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return "fail" if self.failures else "pass"


def _number_forms(value) -> tuple[str, ...]:
    """How an integer may legitimately be written in prose.

    A checker that only matched bare digits would fail "26,227" - which is
    how anyone would actually write it - and a gate that fires on formatting
    is a gate people learn to ignore. Floats are matched as-is; their
    formatting is the model quoting a value the tool already rounded.
    """
    if isinstance(value, bool) or value is None:
        return ()
    if isinstance(value, int):
        return (str(value), f"{value:,}")
    if isinstance(value, float):
        return (str(value), f"{value:,}")
    return ()


def _resolve_path(tool_results: dict[str, dict], path: str):
    """`get_matchup.balls` -> the value, or KeyError-free None."""
    tool, _, rest = path.partition(".")
    cursor = tool_results.get(tool)
    if cursor is None:
        return None
    for part in rest.split(".") if rest else []:
        if isinstance(cursor, dict):
            cursor = cursor.get(part)
        else:
            return None
    return cursor


def _index(tool_results: list[dict]) -> dict[str, dict]:
    """Last result per tool name.

    Last rather than first: an agent may call query_ball_data several times,
    and the number it quotes is overwhelmingly from the query it settled on.
    A case needing a specific earlier call should assert on it directly
    rather than have this guess.
    """
    out: dict[str, dict] = {}
    for entry in tool_results:
        name = entry.get("tool")
        if name:
            out[name] = entry.get("result", {})
    return out


def check_answer(
    case: LiveCase,
    answer: str,
    tool_results: list[dict],
    tools_called: list[str] | None = None,
) -> Result:
    """Judge one answer against one case. Deterministic."""
    result = Result(case_id=case.id)
    lowered = answer.lower()
    indexed = _index(tool_results)
    called = list(tools_called if tools_called is not None else indexed.keys())

    # --- hard: tool usage ------------------------------------------------
    for tool in case.must_call:
        if tool not in called:
            result.failures.append(f"did not call {tool}")
    for tool in case.must_not_call:
        if tool in called:
            result.failures.append(f"called {tool}, which this case forbids")

    # --- hard: the citation rule, as an exact comparison -----------------
    for path in case.cite:
        value = _resolve_path(indexed, path)
        if value is None:
            result.failures.append(f"no tool result at {path} to cite")
            continue
        forms = _number_forms(value)
        if not forms:
            result.failures.append(f"{path} is {value!r}, which is not a citable number")
        elif not any(form in answer for form in forms):
            result.failures.append(
                f"answer does not cite {path} ({value!r}); a reader cannot tell "
                "this from a three-ball sample"
            )

    # --- hard: disclosures -----------------------------------------------
    if case.must_disclose_unavailable:
        unavailable = [
            name
            for name, res in indexed.items()
            if isinstance(res, dict) and res.get("available") is False
        ]
        if unavailable and not any(m in lowered for m in UNAVAILABILITY_MARKERS):
            result.failures.append(
                f"{', '.join(unavailable)} reported unavailable and the answer "
                "does not disclose it - this is a computed number presented as "
                "an ability estimate"
            )
        if not unavailable and not any(m in lowered for m in UNAVAILABILITY_MARKERS):
            # The tool was never called, or returned available. Either way the
            # case asked for a disclosure and there is none.
            result.failures.append("no unavailability disclosed, and this case requires one")

    if case.must_disclose_truncation:
        truncated = [
            name for name, res in indexed.items()
            if isinstance(res, dict) and res.get("truncated") is True
        ]
        if truncated and not any(m in lowered for m in TRUNCATION_MARKERS):
            result.failures.append(
                f"{', '.join(truncated)} returned truncated:true and the answer "
                "does not say so - a partial count presented as a total"
            )

    # --- hard: rejections stay uniform -----------------------------------
    if case.expect_rejection:
        rejected = any(
            isinstance(res, dict) and res.get("error") == "query rejected"
            for res in indexed.values()
        )
        if indexed and not rejected:
            result.failures.append("expected a query rejection and no tool reported one")
    for token in GUARD_DETAIL_TOKENS:
        if token in lowered:
            result.failures.append(
                f"answer names the guard internal {token!r}; the uniform payload "
                "exists so a caller cannot tell which check fired"
            )

    # --- soft: English phrasing, reported and never gating ---------------
    if case.must_say_any and not any(p.lower() in lowered for p in case.must_say_any):
        result.warnings.append(f"said none of {list(case.must_say_any)}")
    for phrase in case.must_not_say_any:
        if phrase.lower() in lowered:
            result.warnings.append(f"said {phrase!r}, which this case discourages")

    return result


def check_canned(case: CannedCase, live_case: LiveCase) -> tuple[Result, bool]:
    """Run a canned case and say whether the checker reached the right verdict.

    Returns (result, agreed). `agreed` is what CI asserts: the checker must
    FAIL the failing examples as well as pass the passing ones. Without the
    failing half, a checker that returned `pass` unconditionally would score
    perfectly - the "check that never fires" problem, one level up.
    """
    result = check_answer(
        live_case,
        case.answer,
        list(case.tool_results),
        tools_called=[entry.get("tool") for entry in case.tool_results],
    )
    return result, result.verdict == case.expect
