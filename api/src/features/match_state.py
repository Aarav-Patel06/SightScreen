"""match_states builder (SPEC.md section 5.3, Phase 0 session 6).

One row per delivery, holding state BEFORE that ball is bowled - the whole
point of this table, and the one thing every model in section 6 depends on
being exactly right. Rebuilt entirely via SQL window functions in a single
statement - 3.78M rows must never go through Python row-by-row.

Key off-by-one insight: `deliveries.legal_ball_num` (session 5) already
stores "legal balls completed through this row inclusive" for a legal
delivery, unchanged for an illegal one - so `balls_bowled` *before* a
delivery needs no window function at all, just
`legal_ball_num - (1 if this delivery is itself legal else 0)`. Only
score/wickets/partnership/balls-since-wicket/batter-cumulative genuinely
need running totals, computed with an exclusive-of-current-row window frame
(`ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING`) - the frame bound that
makes this "before the ball," not "through the ball." Getting that frame
bound wrong by one row is exactly the kind of bug that produces a model
that looks excellent and is reading the future.

Partnership/balls-since-wicket reset by partitioning on `wickets_before`
itself (computed one CTE earlier) rather than a separate reset flag -
`balls_since_wicket` and `partnership_balls` are the same value by
definition (both count legal balls faced by the current partnership since
the last wicket), so only one is computed and used for both columns.

Phase boundaries (Decision 5) are format-dependent fractions of each
innings' own *scheduled* length (Decision 2), not a hardcoded absolute over
number - a reduced-overs chase gets proportionally scaled boundaries
automatically. `target`/`runs_required` (Decision 3) come directly from
`matches.target_runs`/`target_overs`, persisted from Cricsheet's own
recorded figure (session 6 migration + backfill) - critically NOT derived
as innings_1_total + 1, which is wrong by dozens of runs for a DLS-revised
match.

Required Phase 1 splits.py filter (documented here, not enforced here -
section 9.1 says splits.py is the only place split logic may live):
    WHERE batting_team_won IS NOT NULL AND NOT has_reconciliation_anomaly
`is_dls_decided` is available for a sensitivity re-run but is NOT excluded
by default - the result is real.

Usage (from the api/ directory, with api/.env configured):
    python -m features.match_state rebuild
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
REPORTS_DIR = REPO_ROOT / "api" / "data" / "load_reports"

# Fraction of the innings' own scheduled length in each phase (Decision 5).
# T20 nominal (20 overs = 120 balls): 6/9/5 overs -> exactly overs 1-6/7-15/
# 16-20 when unreduced. ODI nominal (50 overs = 300 balls): 10/30/10 overs
# -> exactly overs 1-10/11-40/41-50. A reduced-overs innings scales these
# fractions against its own scheduled_balls, not the nominal length -
# approximate proportional scaling, not the exact ICC reduced-overs
# playing-conditions table (same "fiddly to source, defer" reasoning as
# dls_resources_pct - Decision 6).
PHASE_FRACTIONS = {
    "T20": (0.30, 0.45),  # (powerplay_frac, middle_frac); death = remainder
    "ODI": (0.20, 0.60),
}

REBUILD_SQL = """
WITH base AS (
    SELECT
        delivery_id, match_id, innings, over_num, ball_in_over,
        legal_ball_num, batter_id, batting_team_id,
        runs_batter, runs_extras, extra_type, wicket_type, wicket_count,
        match_date,
        (extra_type IS NULL OR extra_type NOT IN ('wide', 'noball')) AS is_legal
    FROM deliveries
    WHERE NOT is_super_over
),
with_totals AS (
    SELECT
        *,
        COALESCE(SUM(runs_batter + runs_extras) OVER w, 0)::smallint AS score_before,
        COALESCE(SUM(CASE WHEN wicket_type IS NOT NULL THEN wicket_count ELSE 0 END) OVER w, 0)::smallint AS wickets_before,
        (legal_ball_num - CASE WHEN is_legal THEN 1 ELSE 0 END)::smallint AS balls_bowled_before
    FROM base
    WINDOW w AS (
        PARTITION BY match_id, innings
        ORDER BY over_num, ball_in_over
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    )
),
with_partnership AS (
    SELECT
        *,
        COALESCE(SUM(runs_batter + runs_extras) OVER wp, 0)::smallint AS partnership_runs_before,
        COALESCE(SUM(CASE WHEN is_legal THEN 1 ELSE 0 END) OVER wp, 0)::smallint AS partnership_balls_before
    FROM with_totals
    WINDOW wp AS (
        PARTITION BY match_id, innings, wickets_before
        ORDER BY over_num, ball_in_over
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    )
),
with_batter AS (
    SELECT
        *,
        COALESCE(SUM(runs_batter) OVER wb, 0)::smallint AS batter_runs_before,
        COALESCE(SUM(CASE WHEN is_legal THEN 1 ELSE 0 END) OVER wb, 0)::smallint AS batter_balls_before
    FROM with_partnership
    WINDOW wb AS (
        PARTITION BY match_id, innings, batter_id
        ORDER BY over_num, ball_in_over
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    )
),
with_scheduled AS (
    SELECT
        wb.*,
        m.target_runs,
        m.has_reconciliation_anomaly AS m_has_anomaly,
        (m.result_method = 'dls') AS m_is_dls,
        (m.winner = wb.batting_team_id) AS m_batting_team_won,
        (CASE
            WHEN wb.innings = 2 AND m.target_overs IS NOT NULL THEN
                FLOOR(m.target_overs)::int * 6 + ROUND((m.target_overs - FLOOR(m.target_overs)) * 10)::int
            ELSE
                (CASE m.format WHEN 'T20' THEN 20 WHEN 'ODI' THEN 50 END) * 6
        END)::int AS scheduled_balls,
        (CASE m.format WHEN 'T20' THEN %(t20_pp)s WHEN 'ODI' THEN %(odi_pp)s END)::numeric AS powerplay_frac,
        (CASE m.format WHEN 'T20' THEN %(t20_mid)s WHEN 'ODI' THEN %(odi_mid)s END)::numeric AS middle_frac
    FROM with_batter wb
    JOIN matches m ON m.match_id = wb.match_id
),
with_rates AS (
    SELECT
        delivery_id, match_id, innings,
        score_before, wickets_before, balls_bowled_before,
        (scheduled_balls - balls_bowled_before)::smallint AS balls_remaining,
        (CASE WHEN innings = 2 THEN target_runs ELSE NULL END)::smallint AS target,
        (CASE WHEN innings = 2 THEN target_runs - score_before ELSE NULL END)::smallint AS runs_required,
        (CASE WHEN balls_bowled_before > 0 THEN score_before / (balls_bowled_before / 6.0) ELSE NULL END)::real AS current_run_rate,
        (CASE
            WHEN innings = 2 AND (scheduled_balls - balls_bowled_before) > 0
                THEN (target_runs - score_before) / ((scheduled_balls - balls_bowled_before) / 6.0)
            ELSE NULL
        END)::real AS required_run_rate,
        partnership_runs_before, partnership_balls_before,
        (CASE
            WHEN balls_bowled_before < ROUND(scheduled_balls * powerplay_frac) THEN 'powerplay'
            WHEN balls_bowled_before < ROUND(scheduled_balls * (powerplay_frac + middle_frac)) THEN 'middle'
            ELSE 'death'
        END) AS phase,
        batter_runs_before, batter_balls_before,
        m_batting_team_won, match_date, m_has_anomaly, m_is_dls
    FROM with_scheduled
)
INSERT INTO match_states (
    delivery_id, match_id, innings, score, wickets, balls_bowled, balls_remaining,
    target, runs_required, current_run_rate, required_run_rate, rrr_minus_crr,
    partnership_runs, partnership_balls, balls_since_wicket, phase,
    batter_runs_so_far, batter_balls_faced, dls_resources_pct, batting_team_won,
    match_date, has_reconciliation_anomaly, is_dls_decided
)
SELECT
    delivery_id, match_id, innings, score_before, wickets_before, balls_bowled_before, balls_remaining,
    target, runs_required, current_run_rate, required_run_rate,
    (required_run_rate - current_run_rate)::real AS rrr_minus_crr,
    partnership_runs_before, partnership_balls_before, partnership_balls_before AS balls_since_wicket,
    phase, batter_runs_before, batter_balls_before,
    NULL AS dls_resources_pct,
    m_batting_team_won, match_date, m_has_anomaly, m_is_dls
FROM with_rates
"""


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def rebuild() -> dict:
    """Truncates match_states and rebuilds it from deliveries + matches in
    one pass. Idempotent: running it twice in a row produces byte-identical
    output, since it's a pure function of deliveries/matches, never
    hand-edited or incrementally patched (section 2).
    """
    db_url = _env()["LOCAL_DATABASE_URL"]
    start = time.monotonic()

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE match_states")
            cur.execute(
                REBUILD_SQL,
                {
                    "t20_pp": PHASE_FRACTIONS["T20"][0],
                    "t20_mid": PHASE_FRACTIONS["T20"][1],
                    "odi_pp": PHASE_FRACTIONS["ODI"][0],
                    "odi_mid": PHASE_FRACTIONS["ODI"][1],
                },
            )
            rows_inserted = cur.rowcount
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("ANALYZE match_states")
            conn.commit()

            # Diagnostic, not a correctness gate: target should be NULL on an
            # innings-2 row only for the genuinely unresolvable case -
            # matches.target_runs is itself NULL because the match was
            # DLS-decided (real winner/method) but never carried an explicit
            # target, and innings_1_total + 1 is provably wrong for a
            # DLS-revised chase (confirmed: 165 vs a real target of 70 -
            # Decision 3). Cricsheet omitting the target key even for a
            # *normal* chase turned out to be common enough (761 matches)
            # that _extract_target derives it there; only the DLS-without-
            # target case (10 matches, 984 rows against the real corpus at
            # the time this was written) has no safe derivation and stays
            # NULL. If this count grows meaningfully beyond what a handful
            # of new DLS-without-target matches would explain, something
            # changed in the loader or the source data - investigate before
            # trusting the build.
            cur.execute("SELECT count(*) FROM match_states WHERE innings = 2 AND target IS NULL")
            innings2_missing_target = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM deliveries WHERE NOT is_super_over")
            expected_rows = cur.fetchone()[0]

    elapsed = time.monotonic() - start
    report = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        "rows_inserted": rows_inserted,
        "expected_rows": expected_rows,
        "row_count_matches_expected": rows_inserted == expected_rows,
        "innings2_rows_missing_target": innings2_missing_target,
    }

    print(f"match_states rebuilt: {rows_inserted} rows in {elapsed:.1f}s")
    print(f"  expected (deliveries WHERE NOT is_super_over): {expected_rows} - {'MATCH' if report['row_count_matches_expected'] else 'MISMATCH'}")
    print(
        f"  innings-2 rows with no target: {innings2_missing_target} "
        "(expected: a small number of DLS-decided matches with no explicit target - "
        "unresolvable without the real DLS calculation, correctly left NULL rather than guessed)"
    )

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"match_states_{int(time.time())}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report written to {report_path}")

    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="match_state")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("rebuild", help="truncate and rebuild match_states from deliveries + matches")
    args = parser.parse_args(argv)
    if args.command == "rebuild":
        rebuild()


if __name__ == "__main__":
    main()
