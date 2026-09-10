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
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg
from dotenv import dotenv_values

from ingest.live_client import Delivery

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


def _f32(x: float | None) -> float | None:
    """Postgres's match_states rate columns are REAL (float32) - casting
    here keeps every value this class returns at float32 precision, the
    same as the bulk builder's `::real` casts (not bit-identical to what
    psycopg reads back, since Postgres's wire format for `real` is a
    ~6-significant-digit decimal string rather than the raw 4 bytes - the
    parity test compares these with a small tolerance for exactly that
    reason, not because the values are allowed to be wrong)."""
    return None if x is None else float(np.float32(x))


def _round_half_up(x: float) -> int:
    """Postgres's ROUND(numeric) rounds half AWAY FROM ZERO (confirmed
    empirically: ROUND(22.5::numeric) = 23), unlike Python's built-in
    round(), which rounds half TO EVEN (round(22.5) = 22) - and unlike
    Postgres's OWN round(double precision), which also rounds half to
    even. REBUILD_SQL casts powerplay_frac/middle_frac to ::numeric before
    this specific ROUND() call, so this half-up variant is the one that
    must be used here - using plain round() silently disagreed with the
    bulk builder at exactly the .5 boundary a 30-ball reduced innings hits
    (found by the parity test, not assumed)."""
    return int(math.floor(x + 0.5)) if x >= 0 else int(math.ceil(x - 0.5))


@dataclass(frozen=True)
class MatchStateRow:
    """One incrementally-built row, field names matching match_states'
    own columns exactly - the parity test compares these directly against
    a fetched bulk-built row, field by field, not through any translation
    layer that could itself hide a mismatch."""

    match_id: int
    innings: int
    score: int
    wickets: int
    balls_bowled: int
    balls_remaining: int
    target: int | None
    runs_required: int | None
    current_run_rate: float | None
    required_run_rate: float | None
    rrr_minus_crr: float | None
    partnership_runs: int
    partnership_balls: int
    balls_since_wicket: int
    phase: str
    batter_runs_so_far: int
    batter_balls_faced: int


class IncrementalMatchStateBuilder:
    """Builds match_states rows ball by ball - Phase 2's answer to Phase
    0's REBUILD_SQL, maintained as running Python state across `Delivery`
    objects (ingest/live_client.py) instead of window functions over a
    complete table. Every formula below is the identical one REBUILD_SQL
    uses - see this module's top docstring for the off-by-one reasoning
    both builders must honor. `PHASE_FRACTIONS` is imported from this same
    module, not re-declared, so the two builders can never silently drift
    apart on phase boundaries.

    `target_runs`/`target_overs` are None until `set_target()` is called
    (innings 2 starting, or a later provider-reported revision mid-chase).
    Re-supplying them recomputes scheduled_balls/required_run_rate for
    every SUBSEQUENT ball - already-returned rows are never rewritten,
    matching the project's "never patch history" discipline.
    """

    def __init__(self, match_id: int, format_: str) -> None:
        self.match_id = match_id
        self.format = format_
        self.target_runs: int | None = None
        self.target_overs: float | None = None
        self._current_innings: int | None = None
        self._reset_innings_state()

    def _reset_innings_state(self) -> None:
        self._score = 0
        self._wickets = 0
        self._partnership_runs = 0
        self._partnership_balls = 0
        self._batter_runs: dict[int, int] = {}
        self._batter_balls: dict[int, int] = {}

    def set_target(self, target_runs: int, target_overs: float) -> None:
        self.target_runs = target_runs
        self.target_overs = target_overs

    def _scheduled_balls(self, innings: int) -> int:
        nominal = {"T20": 120, "ODI": 300}[self.format]
        if innings == 2 and self.target_overs is not None:
            floor_overs = int(self.target_overs)
            frac_balls = round((self.target_overs - floor_overs) * 10)
            return floor_overs * 6 + frac_balls
        return nominal

    def process(self, d: Delivery) -> MatchStateRow:
        """Returns the state BEFORE this ball, then updates running totals
        to include it - the same "exclusive of current row" discipline
        REBUILD_SQL's window frames enforce."""
        if d.innings != self._current_innings:
            self._current_innings = d.innings
            self._reset_innings_state()

        is_legal = d.extra_type is None or d.extra_type not in ("wide", "noball")
        balls_bowled_before = d.legal_ball_num - (1 if is_legal else 0)
        scheduled_balls = self._scheduled_balls(d.innings)
        balls_remaining = scheduled_balls - balls_bowled_before

        target = self.target_runs if d.innings == 2 else None
        runs_required = (self.target_runs - self._score) if (d.innings == 2 and self.target_runs is not None) else None
        current_run_rate = (self._score / (balls_bowled_before / 6.0)) if balls_bowled_before > 0 else None
        required_run_rate = None
        if d.innings == 2 and balls_remaining > 0 and self.target_runs is not None:
            required_run_rate = (self.target_runs - self._score) / (balls_remaining / 6.0)
        rrr_minus_crr = (
            required_run_rate - current_run_rate
            if (required_run_rate is not None and current_run_rate is not None)
            else None
        )

        pp_frac, mid_frac = PHASE_FRACTIONS[self.format]
        if balls_bowled_before < _round_half_up(scheduled_balls * pp_frac):
            phase = "powerplay"
        elif balls_bowled_before < _round_half_up(scheduled_balls * (pp_frac + mid_frac)):
            phase = "middle"
        else:
            phase = "death"

        batter_runs_before = self._batter_runs.get(d.batter_id, 0)
        batter_balls_before = self._batter_balls.get(d.batter_id, 0)

        row = MatchStateRow(
            match_id=self.match_id,
            innings=d.innings,
            score=self._score,
            wickets=self._wickets,
            balls_bowled=balls_bowled_before,
            balls_remaining=balls_remaining,
            target=target,
            runs_required=runs_required,
            current_run_rate=_f32(current_run_rate),
            required_run_rate=_f32(required_run_rate),
            rrr_minus_crr=_f32(rrr_minus_crr),
            partnership_runs=self._partnership_runs,
            partnership_balls=self._partnership_balls,
            balls_since_wicket=self._partnership_balls,
            phase=phase,
            batter_runs_so_far=batter_runs_before,
            batter_balls_faced=batter_balls_before,
        )

        # Update running totals to include THIS ball, becoming "before" for
        # the next one processed.
        runs_this_ball = d.runs_batter + d.runs_extras
        self._score += runs_this_ball
        if d.wicket_type is not None:
            self._wickets += d.wicket_count
            # The wicket ball's own runs are dropped, never contributing to
            # any future partnership sum - matches REBUILD_SQL's behavior
            # exactly (the partition key changes on THIS row's wicket, so
            # no later row's window sum ever includes it; see this
            # module's parity test for the empirical proof).
            self._partnership_runs = 0
            self._partnership_balls = 0
        else:
            self._partnership_runs += runs_this_ball
            if is_legal:
                self._partnership_balls += 1

        self._batter_runs[d.batter_id] = batter_runs_before + d.runs_batter
        self._batter_balls[d.batter_id] = batter_balls_before + (1 if is_legal else 0)

        return row


if __name__ == "__main__":
    main()
