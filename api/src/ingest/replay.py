"""Replay mode (SPEC.md section 7.3, Phase 2 session 1).

Takes an already-loaded historical match (local Postgres - the only place
the full corpus exists; see this module's own top-level note below) and
feeds it through the identical LiveClient interface a real provider would,
at a configurable pace. Deterministic by construction: a fixed match's
deliveries in a fixed (over_num, ball_in_over) order, no shuffling.

Why replay reads from LOCAL Postgres, not Supabase: SPEC.md section 2.1
says "serving never touches local Postgres," which sounds like it should
apply here. It doesn't, in spirit - that rule exists to keep the
*production* serving path off a database Railway doesn't have, and to keep
the full historical corpus local-only (section 2.1's actual sizing
argument). Replay is explicitly a development/testing tool (section 7.3:
"develop and demo without waiting for a live match"), and the match it
needs to replay only exists locally in the first place. A deliberate,
narrow, documented exception - not a quiet violation.

Usage (from the api/ directory, with api/.env configured):
    python -m ingest.replay <match_id> --speed=instant
    python -m ingest.replay <match_id> --speed=real_time --start-ball=180 --with-predictions
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Literal

import numpy as np
import psycopg
from dotenv import dotenv_values

# Re-exported, not redefined: compute_as_of_features moved to features/as_of.py
# in Phase 2 session 3 so training could call it without importing from
# ingest. Existing callers and tests import it from here unchanged.
from features.as_of import compute_as_of_features
from features.match_state import IncrementalMatchStateBuilder, MatchStateRow
from ingest.live_client import Delivery, MatchState, MatchSummary, PhaseTransitionEvent

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

Speed = Literal["instant", "real_time", "accelerated"]
REAL_TIME_SECONDS_PER_BALL = 25  # SPEC.md section 7.3's own figure

# Deliveries are excluded from replay the same way match_states excludes
# them from training (Phase 0 session 6's base CTE): a super over is a
# separate mini-match, not part of the normal win-prob narrative.
_DELIVERIES_QUERY = """
    SELECT innings, over_num, ball_in_over, legal_ball_num, batter_id,
           non_striker_id, bowler_id, runs_batter, runs_extras, extra_type,
           wicket_type, player_out_id, wicket_count
    FROM deliveries
    WHERE match_id = %s AND NOT is_super_over
    ORDER BY innings, over_num, ball_in_over
"""

_MATCH_QUERY = """
    SELECT format, venue_id, team_a, team_b, toss_winner, toss_decision,
           winner, target_runs, target_overs, status, start_time
    FROM matches WHERE match_id = %s
"""


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def write_with_retry(fn, *args, max_attempts: int = 3, base_delay: float = 1.0, **kwargs) -> bool:
    """A small retry-with-backoff wrapper for any write targeting Supabase
    (Decision 6) - your free-tier project pauses after a week idle, and the
    worker must fail gracefully and retry, not crash. Logs and gives up
    after max_attempts rather than re-raising, so one failed write doesn't
    take down a process tracking a live match."""
    for attempt in range(max_attempts):
        try:
            fn(*args, **kwargs)
            return True
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any write failure must not crash the worker
            if attempt < max_attempts - 1:
                time.sleep(base_delay * (2**attempt))
            else:
                print(f"write failed after {max_attempts} attempts, giving up for this poll: {exc}")
    return False


class ReplayClient:
    """Replays a completed match from local Postgres through the LiveClient
    interface. Speed controls how quickly deliveries become "visible" to a
    caller polling in a loop - it does not sleep inside its own methods
    (that would violate the polling model section 7.1 describes); the
    caller's own loop sleeps between polls, and each call here just reports
    how many balls elapsed wall-clock time says should be visible by now.
    """

    def __init__(
        self,
        conn,
        match_id: int,
        speed: Speed = "instant",
        start_ball: int = 0,
        accelerated_multiplier: float = 20.0,
        seed: int = 0,
    ) -> None:
        self.match_id = match_id
        self.speed = speed
        self.start_ball = start_ball
        self.seed = seed  # reserved for simulated provider jitter - unused today, see module docstring

        with conn.cursor() as cur:
            cur.execute(_MATCH_QUERY, (match_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"no match {match_id} in local Postgres - is it loaded?")
            (self.format, self.venue_id, self.team_a, self.team_b, self.toss_winner,
             self.toss_decision, self.winner, self.target_runs, self.target_overs,
             self._db_status, self.start_time) = row

            cur.execute(_DELIVERIES_QUERY, (match_id,))
            self._deliveries = [
                Delivery(
                    innings=r[0], over_num=r[1], ball_in_over=r[2], legal_ball_num=r[3],
                    batter_id=r[4], non_striker_id=r[5], bowler_id=r[6],
                    runs_batter=r[7], runs_extras=r[8], extra_type=r[9],
                    wicket_type=r[10], player_out_id=r[11], wicket_count=r[12],
                )
                for r in cur.fetchall()
            ]

        if speed == "instant":
            self._seconds_per_ball = 0.0
        elif speed == "real_time":
            self._seconds_per_ball = REAL_TIME_SECONDS_PER_BALL
        elif speed == "accelerated":
            self._seconds_per_ball = REAL_TIME_SECONDS_PER_BALL / accelerated_multiplier
        else:
            raise ValueError(f"unknown speed {speed!r}")

        self._clock_start = time.monotonic()

    @property
    def total_balls(self) -> int:
        return len(self._deliveries)

    def _visible_count(self) -> int:
        total = len(self._deliveries)
        if self._seconds_per_ball == 0.0:
            advanced = total
        else:
            elapsed = time.monotonic() - self._clock_start
            advanced = int(elapsed // self._seconds_per_ball)
        return min(total, self.start_ball + advanced)

    def list_live_matches(self) -> list[MatchSummary]:
        status = "complete" if self._visible_count() >= len(self._deliveries) else "live"
        return [MatchSummary(match_id=self.match_id, status=status, team_a=self.team_a,
                              team_b=self.team_b, venue_id=self.venue_id)]

    def get_match_state(self, match_id: int) -> MatchState:
        assert match_id == self.match_id
        visible = self._visible_count()
        total = len(self._deliveries)
        status = "scheduled" if visible == 0 else ("complete" if visible >= total else "live")
        innings = self._deliveries[visible - 1].innings if visible > 0 else None
        target_runs = self.target_runs if innings == 2 else None
        target_overs = self.target_overs if innings == 2 else None
        return MatchState(
            match_id=self.match_id, status=status, innings=innings, ball_count=visible,
            target_runs=target_runs, target_overs=target_overs, venue_id=self.venue_id,
            format=self.format, team_a=self.team_a, team_b=self.team_b,
            toss_winner=self.toss_winner, toss_decision=self.toss_decision,
            winner=self.winner if status == "complete" else None,
        )

    def get_deliveries_since(self, match_id: int, last_ball: int) -> list[Delivery]:
        assert match_id == self.match_id
        return list(self._deliveries[last_ball : self._visible_count()])


def detect_phase_transition(prev_innings: int | None, delivery: Delivery, is_last_ball: bool) -> PhaseTransitionEvent | None:
    """innings_break is detected from the innings field's own transition in
    the delivery stream - a structural signal every provider must supply,
    never inferred from ball/wicket counts (which a rain-shortened innings
    would break)."""
    if prev_innings is None:
        return PhaseTransitionEvent.INNINGS1_START if delivery.innings == 1 else PhaseTransitionEvent.INNINGS_BREAK
    if delivery.innings != prev_innings:
        return PhaseTransitionEvent.INNINGS_BREAK if delivery.innings == 2 else None
    if is_last_ball:
        return PhaseTransitionEvent.MATCH_END
    return PhaseTransitionEvent.INNINGS2_BALL if delivery.innings == 2 else None


def predict_win_prob(artifact: dict, row: MatchStateRow, as_of_features: dict) -> float | None:
    """Innings-2 only (Decision 5) - no first-innings score-projection
    model exists yet (section 6.3/Phase 4). Reuses the exact feature order
    the artifact itself was trained with (models/win_prob_2nd.py's
    STATE_FEATURES + VENUE_FEATURES + ELO_FEATURES), rather than
    hardcoding a column order a future retrain could silently invalidate.
    """
    if row.innings != 2:
        return None
    phase_code = {"powerplay": 0, "middle": 1, "death": 2}[row.phase]
    values = {
        "balls_remaining": row.balls_remaining,
        "wickets_in_hand": 10 - row.wickets,
        "runs_required": row.runs_required,
        "required_run_rate": row.required_run_rate,
        "current_run_rate": row.current_run_rate,
        "rrr_minus_crr": row.rrr_minus_crr,
        "target": row.target,
        "partnership_runs": row.partnership_runs,
        "partnership_balls": row.partnership_balls,
        "balls_since_wicket": row.balls_since_wicket,
        "phase_code": phase_code,
        **as_of_features,
    }
    X = np.array([[values[name] if values[name] is not None else np.nan for name in artifact["feature_names"]]])
    raw = artifact["booster"].predict(X, num_iteration=artifact["booster"].best_iteration)
    return float(artifact["calibrator"].predict(raw, phase=np.array([row.phase]))[0])


def run_cli(match_id: int, speed: Speed, start_ball: int, with_predictions: bool, poll_interval: float) -> None:
    import joblib

    from models.registry import load_model_version

    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
        client = ReplayClient(conn, match_id, speed=speed, start_ball=start_ball)
        builder = IncrementalMatchStateBuilder(client.match_id, client.format)

        artifact = None
        if with_predictions:
            with conn.cursor() as cur:
                cur.execute("SELECT model_version FROM model_versions WHERE is_active ORDER BY trained_at DESC LIMIT 1")
                row = cur.fetchone()
            if row is None:
                print("no active model_version found - predictions disabled")
            else:
                artifact = load_model_version(conn, row[0])
                print(f"loaded model {row[0]!r} for predictions")

        last_seen = start_ball
        prev_innings = None
        while True:
            new_deliveries = client.get_deliveries_since(match_id, last_seen)
            if not new_deliveries:
                if client.get_match_state(match_id).status == "complete":
                    break
                time.sleep(poll_interval)
                continue

            for d in new_deliveries:
                is_last = (last_seen + 1) == client.total_balls
                event = detect_phase_transition(prev_innings, d, is_last)
                if event:
                    print(f"  [{event.value}]")
                if d.innings == 2 and prev_innings != 2:
                    builder.set_target(client.target_runs, client.target_overs)
                row = builder.process(d)
                last_seen += 1
                prev_innings = d.innings

                line = f"ball {last_seen}: innings {row.innings} {row.score}/{row.wickets} phase={row.phase}"
                if artifact is not None and row.innings == 2:
                    as_of = compute_as_of_features(
                        conn, client.venue_id, client.team_a, client.team_b, client.format, client.start_time
                    )
                    prob = predict_win_prob(artifact, row, as_of)
                    line += f" P(batting team wins)={prob:.3f}"
                print(line)

            if speed != "instant":
                time.sleep(poll_interval)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="replay")
    parser.add_argument("match_id", type=int)
    parser.add_argument("--speed", choices=["instant", "real_time", "accelerated"], default="instant")
    parser.add_argument("--start-ball", type=int, default=0)
    parser.add_argument("--with-predictions", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    args = parser.parse_args(argv)
    run_cli(args.match_id, args.speed, args.start_ball, args.with_predictions, args.poll_interval)


if __name__ == "__main__":
    main()
