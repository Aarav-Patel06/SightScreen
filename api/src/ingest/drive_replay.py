"""Drive a historical match through the DEPLOYED prediction service
(SPEC.md sections 7.3 and 11, Phase 2 session 5, Decision 3).

Phase 2's acceptance criterion is that a replayed match renders a full,
smooth win-probability curve in the deployed Vercel app, updating within 60s
- and it explicitly permits a replay when no live match is on. This is the
thing that drives it.

    laptop                      Railway api          Supabase        Vercel
    ------                      -----------          --------        ------
    read innings-2 balls for
    one match from the LOCAL
    corpus - section 2.1, no
    container can reach it
       |
       |  POST /predict/win-prob, paced
       +------------------------> score with the
                                  pinned model
                                  INSERT predictions --> row
                                                          |
                                                     Realtime
                                                          +---------> curve
                                                                      advances

WHAT THIS PROVES: the deployed service scores with its pinned model and
writes to Supabase; RLS and Realtime deliver to the deployed browser app;
the curve renders end to end within 60s of a ball.

WHAT IT DOES NOT PROVE: the provider-fed worker path. No CricketData call is
involved, and the cadence comes from this laptop rather than from cricket.
The worker's live path stays unproven until a real match is on, which is a
calendar problem rather than an engineering one. Stated here so nobody reads
a green demo as more than it is.

The alternative - a replay mode on the deployed worker fed by recorded
fixtures - was rejected: session 2's fixtures are provider snapshots for
matches that are not in Supabase, the corpus is unreachable from Railway, and
it would put never-exercised code on a demo's critical path to prove strictly
less.

Usage (from api/src, with api/.env configured):
    python -m ingest.drive_replay --list
    python -m ingest.drive_replay --match-id 13143 --speed accelerated \
        --base-url https://api-production-5fa3.up.railway.app
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from eval.splits import second_innings_predicate
from ingest.replay_log import _MIRRORED_COLUMNS, _REFRESHED_COLUMNS

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

# Seconds between balls. "real_time" is SPEC.md section 7.3's own figure for a
# T20; "accelerated" is what you want for a demo someone is watching.
SPEEDS = {"instant": 0.0, "accelerated": 1.25, "real_time": 25.0}
REQUEST_TIMEOUT = 30.0

# The whole innings-2 sequence, not the first N. The smoke test takes 12 balls
# because it only needs to prove rows land; a curve needs the whole chase, and
# the interesting part is the end.
_BALLS_QUERY = """
    SELECT ms.score, ms.wickets, ms.balls_bowled, ms.balls_remaining,
           ms.target, ms.runs_required, ms.current_run_rate, ms.required_run_rate,
           ms.rrr_minus_crr, ms.partnership_runs, ms.partnership_balls,
           ms.balls_since_wicket, ms.phase, ms.match_date,
           m.venue_id, m.format, d.batting_team_id, d.bowling_team_id,
           d.over_num, d.ball_in_over
    FROM match_states ms
    JOIN matches m ON m.match_id = ms.match_id
    JOIN deliveries d ON d.delivery_id = ms.delivery_id
    WHERE ms.match_id = %(match_id)s AND {predicate}
    -- By the ball key, NOT by balls_bowled. An extra does not advance
    -- balls_bowled, so an over containing a wide comes back with two rows
    -- sharing a sort value and posts in arbitrary order. replay_log.py
    -- orders the same way for the same reason.
    ORDER BY d.over_num, d.ball_in_over
""".format(predicate=second_innings_predicate("ms"))

# Candidates worth demoing: a completed chase that went to the wire. A curve
# that sits at 0.95 for 120 balls demonstrates nothing.
_CANDIDATES_QUERY = """
    WITH finishes AS (
        SELECT ms.match_id,
               count(*) AS balls,
               min(ms.runs_required) FILTER (WHERE ms.balls_remaining <= 12) AS runs_at_death,
               bool_or(ms.batting_team_won) AS chase_won
        FROM match_states ms
        WHERE {predicate}
        GROUP BY ms.match_id
        HAVING count(*) >= 100
    )
    SELECT f.match_id, f.balls, f.runs_at_death, f.chase_won,
           m.competition, m.start_time::date, m.venue_id
    FROM finishes f
    JOIN matches m ON m.match_id = f.match_id
    WHERE f.runs_at_death BETWEEN 1 AND 12 AND m.venue_id IS NOT NULL
    ORDER BY m.start_time DESC
    LIMIT 15
""".format(predicate=second_innings_predicate("ms"))


def _env() -> dict:
    env = dotenv_values(ENV_PATH)
    for key in ("LOCAL_DATABASE_URL", "SUPABASE_SESSION_POOLER_URL"):
        if not env.get(key):
            sys.exit(f"{key} must be set in {ENV_PATH}")
    return env


def list_candidates(local_url: str) -> None:
    with psycopg.connect(local_url, connect_timeout=20) as conn, conn.cursor() as cur:
        cur.execute(_CANDIDATES_QUERY)
        rows = cur.fetchall()
    if not rows:
        sys.exit("no close finishes found in the local corpus")
    print(f"{'match_id':>9}  {'balls':>5}  {'needed@death':>12}  {'won':>5}  date        competition")
    for match_id, balls, runs, won, competition, date, _venue in rows:
        print(f"{match_id:>9}  {balls:>5}  {runs:>12}  {str(won):>5}  {date}  {competition}")
    print("\nPick one with a low 'needed@death' - that is where the curve moves.")


def load_balls(local_url: str, match_id: int) -> list[dict]:
    with psycopg.connect(local_url, connect_timeout=20) as conn, conn.cursor() as cur:
        cur.execute(_BALLS_QUERY, {"match_id": match_id})
        rows = cur.fetchall()
    if not rows:
        sys.exit(
            f"no trainable innings-2 rows for match {match_id}. Try --list, or check "
            f"that the match is in the local corpus."
        )
    return [
        {
            "match_id": match_id,
            "innings": 2,
            "score": r[0],
            "wickets": r[1],
            "balls_bowled": r[2],
            "balls_remaining": r[3],
            "target": r[4],
            "runs_required": r[5],
            "current_run_rate": float(r[6]) if r[6] is not None else None,
            "required_run_rate": float(r[7]) if r[7] is not None else None,
            "rrr_minus_crr": float(r[8]) if r[8] is not None else None,
            "partnership_runs": r[9],
            "partnership_balls": r[10],
            "balls_since_wicket": r[11],
            "phase": r[12],
            "match_date": r[13].isoformat(),
            "venue_id": r[14],
            "format": r[15],
            "batting_team_id": r[16],
            "bowling_team_id": r[17],
            # THE BALL KEY. Without these the endpoint writes innings NULL,
            # which sits outside the partial unique index - so its
            # ON CONFLICT target is unsatisfiable, every retried POST writes
            # a second row, and `logged: false` can never come back. That is
            # what produced match 13143's 121 rows carrying 13 distinct
            # payloads, and it is why those rows are invisible to /matches
            # and to every calibration bin.
            "over_num": r[18],
            "ball_in_over": r[19],
        }
        for r in rows
    ]


def mirror_match_row(local_url: str, supabase_url: str, match_id: int) -> None:
    """predictions.match_id is a FK into a table holding only live/recent
    matches, so the one row has to exist on Supabase. Exactly what
    cricketdata.py's _ensure_match_row does for a real live match."""
    # Column list and conflict behaviour come from replay_log so the two
    # mirrors cannot drift: a demo driver that wrote fewer columns than the
    # backfill would make a match look different depending on which tool
    # last touched it.
    columns = ", ".join(_MIRRORED_COLUMNS)
    placeholders = ", ".join(["%s"] * len(_MIRRORED_COLUMNS))
    refresh = ", ".join(f"{c} = EXCLUDED.{c}" for c in _REFRESHED_COLUMNS)
    selected = ", ".join(c for c in _MIRRORED_COLUMNS if c != "match_id")

    with psycopg.connect(local_url, connect_timeout=20) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {selected} FROM matches WHERE match_id = %s",
            (match_id,),
        )
        row = cur.fetchone()
    if row is None:
        sys.exit(f"match {match_id} not found in the local corpus")
    with psycopg.connect(supabase_url, connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO matches ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT (match_id) DO UPDATE SET {refresh}",
                (match_id, *row),
            )
        conn.commit()


class ModelVersionRefused(RuntimeError):
    """The service refused because its model pin moved mid-run."""


def post_ball(base_url: str, ball: dict) -> tuple[bool, str, int | None]:
    request = urllib.request.Request(
        f"{base_url}/predict/win-prob",
        data=json.dumps(ball).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            body = json.loads(response.read())
        return True, f"p={body['win_probability']:.3f}", body["prediction_id"]
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:200]
        if exc.code == 409:
            # A 409 is ActiveVersionGuard refusing because the container's
            # pin no longer matches Supabase's active model. Treating that
            # as "one bad ball" and carrying on is how a prediction log ends
            # up split across two model versions - which the accuracy page
            # groups by, so the result is a reliability diagram that is
            # wrong and renders perfectly. Stop.
            raise ModelVersionRefused(detail)
        # Any other HTTP error IS one bad ball. A 503 mid-run is the
        # degraded-Supabase path session 4b built, and the demo should show
        # it recovering rather than stopping.
        return False, f"HTTP {exc.code} {detail[:120]}", None
    except Exception as exc:  # noqa: BLE001 - network flake, keep going
        return False, f"{type(exc).__name__}: {exc}", None


def run(match_id: int, speed: str, base_url: str, timing_log: Path | None = None) -> int:
    env = _env()
    local_url = env["LOCAL_DATABASE_URL"]
    balls = load_balls(local_url, match_id)
    mirror_match_row(local_url, env["SUPABASE_SESSION_POOLER_URL"], match_id)

    interval = SPEEDS[speed]
    print(
        f"match {match_id}: {len(balls)} innings-2 balls, speed={speed} "
        f"({interval}s/ball), target {base_url}"
    )
    sent = failed = 0
    # One line per ball, so the browser-side watcher can be paired with the
    # posts it is watching for. Wall-clock ms, because the thing being
    # measured spans two processes on this machine.
    timings: list[dict] = []
    started = time.monotonic()
    for index, ball in enumerate(balls, start=1):
        try:
            ok, detail, prediction_id = post_ball(base_url, ball)
        except ModelVersionRefused as exc:
            print("")
            print(f"  ABORTED at ball {index} of {len(balls)}: the service refused with 409.")
            print(f"  {exc}")
            print(
                f"  {sent} ball(s) were written before the refusal and the rest were "
                f"not attempted. Restart the container onto the new pin, then re-run: "
                f"the write is idempotent, so replaying costs nothing."
            )
            return 1
        if timing_log is not None:
            timings.append(
                {
                    "index": index,
                    "balls_bowled": ball["balls_bowled"],
                    "prediction_id": prediction_id,
                    "responded_at_ms": round(time.time() * 1000),
                    "ok": ok,
                }
            )
        if ok:
            sent += 1
        else:
            failed += 1
        if index % 10 == 0 or not ok or index == len(balls):
            print(
                f"  ball {ball['balls_bowled']:>3}  {ball['score']}/{ball['wickets']}  "
                f"need {ball['runs_required']} off {ball['balls_remaining']}  {detail}"
            )
        if interval and index < len(balls):
            time.sleep(interval)

    elapsed = time.monotonic() - started
    print(f"\n{sent} posted OK, {failed} reported failed, {elapsed:.0f}s elapsed")
    if timing_log is not None:
        timing_log.write_text(json.dumps(timings), encoding="utf-8")
        print(f"timing log written to {timing_log}")

    # Count what actually landed. A client-side failure does not mean the
    # server failed to commit, and a success does not mean it committed once:
    # driving match 9339 produced 128 rows from 125 posts - three
    # byte-identical pairs with consecutive ids, a retried POST against a
    # non-idempotent endpoint, plus one "failure" that had in fact been
    # written. Reporting what the database holds beats trusting the loop's own
    # tally. See SPEC.md section 15 (2026-09-18).
    verify_written(env["SUPABASE_SESSION_POOLER_URL"], match_id, len(balls))
    return 1 if failed else 0


def verify_written(supabase_url: str, match_id: int, posted: int) -> None:
    with psycopg.connect(supabase_url, connect_timeout=20) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(DISTINCT payload) FROM predictions WHERE match_id = %s",
            (match_id,),
        )
        rows, distinct = cur.fetchone()
    print(f"supabase holds {rows} rows for match {match_id}, {distinct} distinct payloads")
    if rows > distinct:
        print(
            f"  {rows - distinct} EXACT DUPLICATE row(s) - a retried POST against a "
            f"non-idempotent endpoint. Invisible on the curve, which plots in "
            f"prediction_id order, but Phase 3's calibration bins would double-count."
        )
    if distinct < posted:
        print(f"  {posted - distinct} ball(s) never landed - the curve has gaps")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="drive_replay", description=__doc__)
    parser.add_argument("--match-id", type=int, help="match to replay")
    parser.add_argument("--speed", choices=sorted(SPEEDS), default="accelerated")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="the prediction service; point this at Railway for the deployed demo",
    )
    parser.add_argument(
        "--list", action="store_true", help="show close finishes worth demoing, then exit"
    )
    parser.add_argument(
        "--timing-log",
        type=Path,
        default=None,
        help="write per-ball post timestamps here, to pair against a browser-side watcher",
    )
    args = parser.parse_args(argv)

    if args.list:
        list_candidates(_env()["LOCAL_DATABASE_URL"])
        return
    if args.match_id is None:
        parser.error("--match-id is required (or use --list to pick one)")
    raise SystemExit(
        run(args.match_id, args.speed, args.base_url.rstrip("/"), args.timing_log)
    )


if __name__ == "__main__":
    main()
