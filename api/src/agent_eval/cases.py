"""The Phase 6 session 2 eval set, loaded and schema-checked.

SPEC.md §11 makes a 20-question eval set Phase 6's acceptance criterion, and
§10.4 makes one of those questions load-bearing rather than illustrative: the
`get_player_form` refusal is only not theatre if the agent reliably declines
to dress a computed average as an ability estimate, and that is a behaviour,
so it belongs in the eval set and not only in the system prompt. See
`docs/phase6-session1.md` §5, Gap 3.

Two kinds of case live here, and the split is the point.

**Live cases** are questions put to the real agent. They are the acceptance
criterion, they cost money, and they are nondeterministic - so they run as a
deliberate command and their results are committed, never in CI.

**Canned cases** are a fixed tool transcript plus a fixed answer plus the
verdict the checker must reach. They call no model, cost nothing, and run in
CI on every push. They are the checker's specification, written before the
checker exists, for the same reason session 1 wrote the adversarial suite
before the guard: a checker written first and specified afterwards is
specified to whatever it already does.

**Every canned case has a failing twin, and that is not symmetry for its own
sake.** A set containing only passing examples cannot tell a checker that
works from a checker that returns `pass` unconditionally - which is exactly
the "check that never fires" failure §10.3's non-vacuity proofs exist to
prevent, one level up. `form-trap-failing-answer` is the important one: a
fluent, correct-looking average presented as an ability estimate, asserted
as a FAILURE. Without it the eval rewards precisely the output Gap 3's
resolution exists to prevent.

No expected VALUES are stored. The checker compares an answer against what
the tools actually returned in that run, so this file does not go stale when
the corpus grows - and a case cannot quietly start passing because a number
it hardcoded drifted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CASES_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_eval" / "cases.json"

SCHEMA_VERSION = 1

# §11's acceptance criterion names the four areas the set must cover. Checked
# rather than trusted: a set that drifted to nineteen SQL cases and one lookup
# would still be twenty questions.
CATEGORIES = ("stats_lookup", "form", "live_match", "adversarial_sql")

REQUIRED_CASE_COUNT = 20

# The tools the agent may call, per §10.2. A case naming anything else is a
# typo, and a typo in `must_call` is an assertion that silently never fires.
TOOL_NAMES = (
    "query_ball_data",
    "get_live_prediction",
    "get_player_form",
    "get_matchup",
    "resolve_entity",
)

VERDICTS = ("pass", "fail")


@dataclass(frozen=True)
class LiveCase:
    """A question for the real agent, and what its answer must satisfy."""

    id: str
    category: str
    question: str
    why: str
    must_call: tuple[str, ...] = ()
    must_not_call: tuple[str, ...] = ()
    # Dotted paths into a tool result whose VALUE must appear in the answer -
    # the whole of the citation check. The harness knows get_matchup returned
    # balls=225, so it can assert "225" is in the prose rather than trying to
    # judge in English whether a sample size was given.
    cite: tuple[str, ...] = ()
    must_say_any: tuple[str, ...] = ()
    must_not_say_any: tuple[str, ...] = ()
    # The agent must ASK rather than answer. A permanent case, not a
    # workaround for the resolver bug: once resolve_entity stops tying four
    # namesakes together that conflict disappears, and if the only
    # clarification case were the broken one, the eval would silently lose
    # its ability to REWARD the behaviour resolve_first requires. A rule
    # nothing exercises is unprovable, not merely unproven.
    expect_clarification: bool = False
    must_disclose_unavailable: bool = False
    must_disclose_truncation: bool = False
    expect_rejection: bool = False


@dataclass(frozen=True)
class CannedCase:
    """A fixed transcript and answer, with the verdict the checker must reach.

    `of_case` ties it to the live case it is a worked example of, so a canned
    case cannot drift into testing a rule no live question exercises.
    """

    id: str
    of_case: str
    expect: str
    answer: str
    why: str
    tool_results: tuple[dict, ...] = field(default_factory=tuple)
    # For `expect: fail` only: substrings the checker's stated reason must
    # contain. Without this, "the checker failed it" is satisfied by failing
    # for ANY reason - and one of these cases did exactly that, agreeing on
    # the verdict while tripping on an incomplete transcript instead of the
    # rule it exists to test. Same shape as replica.py refusing to count a
    # 55000 as a privilege denial: denied for the wrong reason is not denied.
    expect_failure_mentions: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalSet:
    live: tuple[LiveCase, ...]
    canned: tuple[CannedCase, ...]

    def by_category(self, category: str) -> tuple[LiveCase, ...]:
        return tuple(c for c in self.live if c.category == category)

    def canned_for(self, case_id: str) -> tuple[CannedCase, ...]:
        return tuple(c for c in self.canned if c.of_case == case_id)


class EvalSetError(Exception):
    """The eval set is malformed.

    Raised rather than warned. A case with a misspelled field is an assertion
    that never runs, and an eval set that loads with assertions missing is
    worse than one that refuses to load - it reports green.
    """


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvalSetError(message)


def load(path: Path | None = None) -> EvalSet:
    """Read the eval set, or raise EvalSetError naming what is wrong."""
    path = path or CASES_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))

    _require(
        raw.get("schema_version") == SCHEMA_VERSION,
        f"schema_version is {raw.get('schema_version')!r}, expected {SCHEMA_VERSION}",
    )

    live_fields = {f for f in LiveCase.__dataclass_fields__}
    canned_fields = {f for f in CannedCase.__dataclass_fields__}

    live: list[LiveCase] = []
    for entry in raw.get("live_cases", []):
        unknown = set(entry) - live_fields
        _require(not unknown, f"live case {entry.get('id')!r} has unknown field(s) {sorted(unknown)}")
        _require(
            entry.get("category") in CATEGORIES,
            f"live case {entry.get('id')!r} has category {entry.get('category')!r}, "
            f"not one of {CATEGORIES}",
        )
        for key in ("must_call", "must_not_call"):
            for tool in entry.get(key, ()):
                _require(
                    tool in TOOL_NAMES,
                    f"live case {entry['id']!r} {key} names {tool!r}, not a tool in {TOOL_NAMES}",
                )
        for path_expr in entry.get("cite", ()):
            tool = path_expr.split(".", 1)[0]
            _require(
                tool in TOOL_NAMES,
                f"live case {entry['id']!r} cites {path_expr!r}, whose tool {tool!r} is not in {TOOL_NAMES}",
            )
        live.append(
            LiveCase(
                **{
                    k: (tuple(v) if isinstance(v, list) else v)
                    for k, v in entry.items()
                }
            )
        )

    canned: list[CannedCase] = []
    live_ids = {c.id for c in live}
    for entry in raw.get("canned_cases", []):
        unknown = set(entry) - canned_fields
        _require(not unknown, f"canned case {entry.get('id')!r} has unknown field(s) {sorted(unknown)}")
        _require(
            entry.get("expect") in VERDICTS,
            f"canned case {entry.get('id')!r} expects {entry.get('expect')!r}, not one of {VERDICTS}",
        )
        _require(
            not (entry.get("expect") == "pass" and entry.get("expect_failure_mentions")),
            f"canned case {entry.get('id')!r} expects pass but names failure reasons",
        )
        _require(
            entry.get("expect") != "fail" or entry.get("expect_failure_mentions"),
            f"canned case {entry.get('id')!r} expects fail but does not say WHICH rule "
            "must fire - a failing verdict for an unrelated reason would pass this case",
        )
        _require(
            entry.get("of_case") in live_ids,
            f"canned case {entry.get('id')!r} is of_case {entry.get('of_case')!r}, "
            "which is not a live case id",
        )
        canned.append(
            CannedCase(
                **{
                    k: (tuple(v) if isinstance(v, list) else v)
                    for k, v in entry.items()
                }
            )
        )

    ids = [c.id for c in live] + [c.id for c in canned]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    _require(not duplicates, f"duplicate case ids: {duplicates}")

    return EvalSet(live=tuple(live), canned=tuple(canned))
