"""Measure snapshot-reconstruction error against ground truth (SPEC.md
section 4.2, Phase 2 session 2, Decision 5).

CricketData gives us no ball-by-ball feed, so the live path reconstructs a
delivery stream by diffing scorecard snapshots. This quantifies what that
costs, rather than asserting it's fine.

Method: take completed matches already in local Postgres (ground truth =
`deliveries` + the bulk-built `match_states`), synthesize the snapshots a
poller would have seen at a given interval, run them through the real
adapter reconstruction, rebuild state with the same
`IncrementalMatchStateBuilder` the live path uses, and compare row by row
against the bulk-built truth.

States are compared keyed by (innings, balls_bowled) - "the state before
the Nth legal ball" - which is well defined on both sides even though the
reconstructed stream can contain a different number of row objects (two
wides in one poll gap collapse into one reconstructed extra).

Usage (from the api/ directory, with api/.env configured):
    python -m eval.measure_reconstruction
    python -m eval.measure_reconstruction --matches 40
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from features.match_state import IncrementalMatchStateBuilder
from ingest.cricketdata import InningsSnapshot, reconstruct_innings
from ingest.live_client import ReconstructionConfidence

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
REPORTS_DIR = REPO_ROOT / "api" / "data" / "eval_reports"

POLL_INTERVALS = (10.0, 15.0, 30.0, 60.0, 120.0)
SHIPPED_INTERVAL = 15.0

# Timing model. A *uniform* 30s/ball would make every interval <= 30s look
# flawless by construction - at most one ball could ever land between polls -
# which would be an artifact of the model, not a property of the adapter. Real
# ball gaps are bursty: quick singles come 15s apart, a wicket or DRS review
# stops play for minutes. So gaps are drawn lognormally around the mean with a
# fixed seed (deterministic, re-runnable), and the uniform case is reported
# alongside as the best-case bound.
SECONDS_PER_BALL = 30.0
SECONDS_PER_OVER_BREAK = 60.0
JITTER_SIGMA = 0.55  # lognormal sigma: ~15s at the fast end, ~90s at the slow
JITTER_SEED = 20260914

# Pre-registered before any number was seen (see the session plan): above
# this, the three partnership features are not trustworthy enough to serve,
# and a degraded-feature variant gets trained and its Brier cost reported.
PARTNERSHIP_ERROR_THRESHOLD = 0.02


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def _pick_matches(conn, limit: int) -> list[tuple[int, str]]:
    """Completed T20s with a real chase, drawn deterministically."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.match_id, m.format
            FROM matches m
            WHERE m.format = 'T20' AND m.status = 'complete'
              AND NOT m.has_reconciliation_anomaly
              AND EXISTS (SELECT 1 FROM match_states ms WHERE ms.match_id = m.match_id AND ms.innings = 2)
            ORDER BY m.match_id
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def _ground_truth(conn, match_id: int):
    """Real deliveries in order, plus the bulk-built state keyed by
    (innings, balls_bowled)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT innings, legal_ball_num, runs_batter, runs_extras, extra_type, wicket_type, wicket_count
            FROM deliveries
            WHERE match_id = %s AND NOT is_super_over
            ORDER BY innings, over_num, ball_in_over
            """,
            (match_id,),
        )
        deliveries = cur.fetchall()
        cur.execute(
            """
            SELECT innings, balls_bowled, score, wickets,
                   partnership_runs, partnership_balls, balls_since_wicket
            FROM match_states WHERE match_id = %s
            """,
            (match_id,),
        )
        truth = {(r[0], r[1]): r[2:] for r in cur.fetchall()}
    return deliveries, truth


def _ball_times(deliveries, rng=None) -> list[float]:
    """Wall-clock offset for each delivery under the stated timing model.

    rng=None gives the uniform best case (every gap exactly
    SECONDS_PER_BALL). Passing a seeded Random draws bursty, realistic gaps,
    which is the number worth quoting.
    """
    import math

    times, clock, previous_over = [], 0.0, None
    for innings, legal_ball_num, *_rest in deliveries:
        over = (max(legal_ball_num, 1) - 1) // 6
        key = (innings, over)
        if previous_over is not None and key != previous_over:
            clock += SECONDS_PER_OVER_BREAK
        if rng is None:
            clock += SECONDS_PER_BALL
        else:
            # lognormal with the same mean as the uniform case
            clock += SECONDS_PER_BALL * math.exp(rng.gauss(-(JITTER_SIGMA**2) / 2, JITTER_SIGMA))
        times.append(clock)
        previous_over = key
    return times


def _snapshots_at(deliveries, times, interval: float) -> list[tuple[InningsSnapshot, ...]]:
    """Cumulative per-innings totals as a poller would have seen them."""
    snapshots: list[tuple[InningsSnapshot, ...]] = []
    totals: dict[int, list[int]] = {}
    index = 0
    poll_time = interval
    end = times[-1] if times else 0.0
    while poll_time <= end + interval:
        while index < len(deliveries) and times[index] <= poll_time:
            innings, legal_ball_num, runs_batter, runs_extras, _extra, wicket_type, wicket_count = deliveries[index]
            state = totals.setdefault(innings, [0, 0, 0])
            state[0] += runs_batter + runs_extras
            if wicket_type is not None:
                state[1] += wicket_count
            state[2] = legal_ball_num
            index += 1
        snapshots.append(
            tuple(InningsSnapshot(*totals[i]) for i in sorted(totals)) if totals else ()
        )
        poll_time += interval
    return snapshots


def _rebuild_from_snapshots(match_id: int, format_: str, snapshots, target_runs, target_overs):
    """Reconstruct deliveries from consecutive snapshots and run them
    through the same incremental builder the live path uses."""
    builder = IncrementalMatchStateBuilder(match_id, format_)
    if target_runs is not None and target_overs is not None:
        pass  # set on the innings-2 transition below
    rows: dict[tuple[int, int], tuple] = {}
    inferred = confirmed = 0
    previous: tuple[InningsSnapshot, ...] = ()
    seen_innings2 = False

    for current in snapshots:
        for index, after in enumerate(current):
            before = previous[index] if index < len(previous) else None
            innings_no = index + 1
            if innings_no == 2 and not seen_innings2:
                if target_runs is not None:
                    builder.set_target(target_runs, target_overs)
                seen_innings2 = True
            for delivery in reconstruct_innings(before, after, innings_no=innings_no):
                if delivery.confidence is ReconstructionConfidence.INFERRED:
                    inferred += 1
                else:
                    confirmed += 1
                row = builder.process(delivery)
                rows[(row.innings, row.balls_bowled)] = (
                    row.score, row.wickets, row.partnership_runs,
                    row.partnership_balls, row.balls_since_wicket,
                )
        previous = current
    return rows, inferred, confirmed


def measure(limit: int = 25, jitter: bool = True) -> dict:
    import random

    env = _env()
    report: dict = {"poll_intervals": {}, "n_matches": 0, "timing_model": {
        "seconds_per_ball": SECONDS_PER_BALL, "seconds_per_over_break": SECONDS_PER_OVER_BREAK,
        "jitter": jitter, "jitter_sigma": JITTER_SIGMA if jitter else 0.0}}

    with psycopg.connect(env["LOCAL_DATABASE_URL"]) as conn:
        matches = _pick_matches(conn, limit)
        report["n_matches"] = len(matches)

        for interval in POLL_INTERVALS:
            compared = 0
            wrong_partnership_balls = 0
            wrong_partnership_runs = 0
            wrong_since_wicket = 0
            wrong_score = 0
            wrong_wickets = 0
            max_partnership_balls_error = 0
            total_inferred = total_confirmed = 0

            for match_id, format_ in matches:
                deliveries, truth = _ground_truth(conn, match_id)
                if not deliveries:
                    continue
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT target_runs, target_overs FROM matches WHERE match_id = %s", (match_id,)
                    )
                    target_runs, target_overs = cur.fetchone()

                # Seeded per (match, interval) so every interval sees the
                # same match timeline - the sweep compares polling rates, not
                # different random worlds.
                rng = random.Random(JITTER_SEED + match_id) if jitter else None
                times = _ball_times(deliveries, rng)
                snapshots = _snapshots_at(deliveries, times, interval)
                rows, inferred, confirmed = _rebuild_from_snapshots(
                    match_id, format_, snapshots, target_runs, target_overs
                )
                total_inferred += inferred
                total_confirmed += confirmed

                for key, built in rows.items():
                    actual = truth.get(key)
                    if actual is None:
                        continue
                    compared += 1
                    score, wickets, p_runs, p_balls, since = built
                    if score != actual[0]:
                        wrong_score += 1
                    if wickets != actual[1]:
                        wrong_wickets += 1
                    if p_runs != actual[2]:
                        wrong_partnership_runs += 1
                    if p_balls != actual[3]:
                        wrong_partnership_balls += 1
                        max_partnership_balls_error = max(
                            max_partnership_balls_error, abs(p_balls - actual[3])
                        )
                    if since != actual[4]:
                        wrong_since_wicket += 1

            emitted = total_inferred + total_confirmed
            report["poll_intervals"][str(interval)] = {
                "states_compared": compared,
                "deliveries_emitted": emitted,
                "pct_inferred": (total_inferred / emitted) if emitted else 0.0,
                "pct_wrong_score": (wrong_score / compared) if compared else 0.0,
                "pct_wrong_wickets": (wrong_wickets / compared) if compared else 0.0,
                "pct_wrong_partnership_runs": (wrong_partnership_runs / compared) if compared else 0.0,
                "pct_wrong_partnership_balls": (wrong_partnership_balls / compared) if compared else 0.0,
                "pct_wrong_balls_since_wicket": (wrong_since_wicket / compared) if compared else 0.0,
                "max_partnership_balls_error": max_partnership_balls_error,
            }

    shipped = report["poll_intervals"].get(str(SHIPPED_INTERVAL), {})
    error = shipped.get("pct_wrong_partnership_balls", 1.0)
    report["shipped_interval"] = SHIPPED_INTERVAL
    report["partnership_error_threshold"] = PARTNERSHIP_ERROR_THRESHOLD
    report["retrain_required"] = error > PARTNERSHIP_ERROR_THRESHOLD

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"reconstruction_{int(time.time())}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"matches: {report['n_matches']}  (timing model: {SECONDS_PER_BALL}s/ball mean, "
          f"{SECONDS_PER_OVER_BREAK}s/over break, "
          f"{'bursty lognormal sigma=' + str(JITTER_SIGMA) if jitter else 'UNIFORM (best case)'})\n")
    header = f"{'interval':>9} {'states':>8} {'inferred':>9} {'score':>8} {'wickets':>8} {'p_runs':>8} {'p_balls':>8} {'since_wkt':>10} {'max_err':>8}"
    print(header)
    print("-" * len(header))
    for interval, row in report["poll_intervals"].items():
        print(
            f"{interval:>9} {row['states_compared']:>8} "
            f"{row['pct_inferred']:>8.1%} {row['pct_wrong_score']:>8.2%} "
            f"{row['pct_wrong_wickets']:>8.2%} {row['pct_wrong_partnership_runs']:>8.2%} "
            f"{row['pct_wrong_partnership_balls']:>8.2%} {row['pct_wrong_balls_since_wicket']:>10.2%} "
            f"{row['max_partnership_balls_error']:>8}"
        )
    print(
        f"\nshipped interval {SHIPPED_INTERVAL}s: partnership_balls wrong on "
        f"{error:.2%} of states (pre-registered threshold {PARTNERSHIP_ERROR_THRESHOLD:.0%}) "
        f"-> retrain {'REQUIRED' if report['retrain_required'] else 'not required'}"
    )
    print(f"Report written to {path}")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="measure_reconstruction")
    parser.add_argument("--matches", type=int, default=25)
    parser.add_argument("--uniform", action="store_true",
                        help="uniform ball gaps (best-case bound) instead of bursty ones")
    args = parser.parse_args(argv)
    measure(args.matches, jitter=not args.uniform)


if __name__ == "__main__":
    main()
