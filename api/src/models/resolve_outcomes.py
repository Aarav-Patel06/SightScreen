"""Resolve logged predictions against what actually happened (SPEC.md
sections 5.4 and 8.5, Phase 3 session 1).

Fills `prediction_outcomes`, which has had zero writers since Phase 0
created it. Every resolved row carries the actual outcome plus this
prediction's own Brier and log loss, which is what §8.5's accuracy page and
§8.1's calibration monitor read.

Three rules, each of which is the difference between a reliability diagram
and a plausible-looking lie:

**The label is read, never derived.** It comes from
`match_states.batting_team_won`, the one place it is computed
(features/match_state.py:133, `m.winner = wb.batting_team_id`). Deriving it
here from `matches.winner` would be a second implementation, and the case it
would get wrong is the quiet one: `matches.winner` is NULL for a tie AND for
a no-result, so a naive comparison labels both as a loss for the chasing
side. Three-valued SQL already handles that upstream; this module just
declines to re-do it.

**An unresolvable prediction gets no row at all.** Not a row with NULL
metrics: `prediction_outcomes.actual` is NOT NULL, and a prediction on a
tied match has no actual. The unresolved count is reported with its reason
rather than being silently absent.

**Exclusions come from eval/splits.py.** `second_innings_labels` applies the
identical §9.1 predicate the training split uses. A prediction the training
split would have excluded is one this job must not resolve, and the way to
guarantee that is to ask the same code rather than to write the clauses
again - they were already hand-copied into five places before this session.

Idempotent: `ON CONFLICT (prediction_id) DO NOTHING`. Re-running resolves
only what is new.

Usage (from api/src, with api/.env configured):
    python -m models.resolve_outcomes
    python -m models.resolve_outcomes --manifest ../data/phase3_manifest.json
    python -m models.resolve_outcomes --match-id 9337
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import psycopg
from dotenv import dotenv_values

from eval.metrics import brier_score, log_loss
from eval.splits import second_innings_labels

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
MANIFEST_PATH = REPO_ROOT / "api" / "data" / "phase3_manifest.json"
INSERT_BATCH = 500

_PREDICTIONS_QUERY = """
    SELECT prediction_id, match_id, innings, over_num, ball_in_over,
           (payload->>'p')::float8
    FROM predictions p
    WHERE match_id = ANY(%s)
      AND prediction_type = 'win_prob'
      AND innings IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM prediction_outcomes o WHERE o.prediction_id = p.prediction_id
      )
    ORDER BY prediction_id
"""

# Why a match cannot be resolved, in the order the reasons are checked. Read
# from the local corpus, since Supabase's `matches` never receives `winner`
# or `result_method` - the live adapter has no field for them and the replay
# mirror deliberately does not copy them.
_MATCH_REASONS_QUERY = """
    SELECT match_id, winner IS NULL AS no_winner, result_method,
           has_reconciliation_anomaly
    FROM matches
    WHERE match_id = ANY(%s)
"""


class MatchIdentityMismatch(RuntimeError):
    """A Supabase match_id means something different in the local corpus."""


_IDENTITY_QUERY = """
    SELECT match_id, competition, format, start_time
    FROM matches WHERE match_id = ANY(%s)
"""


def assert_same_matches(local_conn, supabase_conn, match_ids: list[int]) -> None:
    """Refuse to resolve if an id does not mean the same match on both sides.

    `matches.match_id` carries two id spaces. The replay mirror copies corpus
    rows keeping their LOCAL ids; the live worker inserts without an id, so
    Supabase's SERIAL assigns one from 1 upwards. The result, found by
    looking rather than by reasoning: Supabase match 3 is a CPL 2026 match
    while LOCAL match 3 is a 2017 Pakistan-Australia ODI, complete, with a
    winner. Resolving the former against the latter would attach a real
    outcome to the wrong predictions and nothing downstream could tell -
    every row would look perfectly well formed.

    Migration 20260919000001 moved the sequence above the corpus so no NEW
    live match can collide. This guard covers the rows that already exist
    below that line, and any future way of reintroducing the problem.

    Compared on competition, format and start date rather than a single
    field: a live match and a corpus match agreeing on all three is not a
    coincidence worth worrying about, and start_time alone is not enough
    when two matches share a date.
    """
    with local_conn.cursor() as cur:
        cur.execute(_IDENTITY_QUERY, (match_ids,))
        local = {row[0]: (row[1], row[2], row[3].date()) for row in cur.fetchall()}
    with supabase_conn.cursor() as cur:
        cur.execute(_IDENTITY_QUERY, (match_ids,))
        remote = {row[0]: (row[1], row[2], row[3].date()) for row in cur.fetchall()}

    mismatched = []
    for match_id in match_ids:
        here, there = local.get(match_id), remote.get(match_id)
        if here is None or there is None or here != there:
            mismatched.append((match_id, there, here))
    if mismatched:
        detail = [
            f'    match_id {mid}: Supabase says {sup!r}, corpus says {loc!r}'
            for mid, sup, loc in mismatched[:5]
        ]
        raise MatchIdentityMismatch(
            f'{len(mismatched)} match id(s) do not refer to the same match in both '
            f'databases, so the outcome read from the corpus would be attached to '
            f'predictions about a different game:'
            + chr(10)
            + chr(10).join(detail)
            + chr(10)
            + '  A live match takes its id from Supabase and a replayed one keeps '
            + 'the corpus id. Resolving a live match needs a real crosswalk '
            + '(date, teams, venue), not a shared integer.'
        )


def _env() -> dict:
    env = dotenv_values(ENV_PATH)
    for key in ("LOCAL_DATABASE_URL", "SUPABASE_SESSION_POOLER_URL"):
        if not env.get(key):
            sys.exit(f"{key} must be set in {ENV_PATH}")
    return env


def _match_reasons(local_conn, match_ids: list[int]) -> dict[int, str]:
    """Per match, why its predictions cannot be resolved - or '' if they can."""
    with local_conn.cursor() as cur:
        cur.execute(_MATCH_REASONS_QUERY, (match_ids,))
        rows = cur.fetchall()
    reasons = {}
    for match_id, no_winner, result_method, anomaly in rows:
        if anomaly:
            reasons[match_id] = "match flagged has_reconciliation_anomaly"
        elif no_winner:
            reasons[match_id] = f"match has no winner ({result_method})"
        else:
            reasons[match_id] = ""
    return reasons


def _metrics(probability: float, label: int) -> tuple[float, float]:
    """This prediction's Brier and log loss.

    Deliberately calls eval/metrics.py's array functions on a single element
    rather than inlining `(p - y) ** 2`. The mean of one value is that value,
    so the result is identical - and it stays identical if those functions
    ever change, notably log_loss's epsilon clipping, which an inlined copy
    here would silently diverge from the moment anyone tuned it.
    """
    y = np.array([label], dtype=np.int8)
    p = np.array([probability], dtype=np.float64)
    return brier_score(y, p), log_loss(y, p)


def resolve(match_ids: list[int]) -> int:
    env = _env()
    with (
        psycopg.connect(env["LOCAL_DATABASE_URL"], connect_timeout=30) as local_conn,
        psycopg.connect(env["SUPABASE_SESSION_POOLER_URL"], connect_timeout=30) as supabase_conn,
    ):
        # Before anything reads a label: prove the ids mean the same matches.
        assert_same_matches(local_conn, supabase_conn, match_ids)
        labels = second_innings_labels(local_conn, match_ids)
        reasons = _match_reasons(local_conn, match_ids)

        with supabase_conn.cursor() as cur:
            cur.execute(_PREDICTIONS_QUERY, (match_ids,))
            pending = cur.fetchall()

        print(f"{len(pending)} prediction(s) without an outcome, over {len(match_ids)} match(es)")
        if not pending:
            print("nothing to resolve")
            return 0

        rows = []
        unresolved: dict[str, int] = {}
        for prediction_id, match_id, innings, over_num, ball_in_over, probability in pending:
            label = labels.get((match_id, innings, over_num, ball_in_over))
            if label is None:
                reason = reasons.get(match_id) or "ball excluded by section 9.1 (no required_run_rate)"
                unresolved[reason] = unresolved.get(reason, 0) + 1
                continue
            brier, logloss = _metrics(probability, label)
            rows.append(
                (
                    prediction_id,
                    json.dumps(
                        {
                            "batting_team_won": bool(label),
                            "source": "match_states.batting_team_won",
                        }
                    ),
                    brier,
                    logloss,
                )
            )

        with supabase_conn.cursor() as cur:
            for start in range(0, len(rows), INSERT_BATCH):
                cur.executemany(
                    """
                    INSERT INTO prediction_outcomes
                        (prediction_id, actual, brier, log_loss, resolved_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (prediction_id) DO NOTHING
                    """,
                    rows[start : start + INSERT_BATCH],
                )
        supabase_conn.commit()

    print(f"resolved {len(rows)}")
    for reason, count in sorted(unresolved.items(), key=lambda kv: -kv[1]):
        print(f"  left unresolved: {count:>6}  {reason}")
    if unresolved:
        print(
            "  (an unresolvable prediction gets no outcome row - it is visible as "
            "unresolved, never as a fabricated label)"
        )
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="resolve_outcomes", description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument(
        "--match-id", type=int, action="append", help="resolve these matches instead"
    )
    args = parser.parse_args(argv)

    if args.match_id:
        match_ids = args.match_id
    else:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        match_ids = [entry["match_id"] for entry in manifest["matches"]]
    raise SystemExit(resolve(match_ids))


if __name__ == "__main__":
    main()
