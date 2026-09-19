"""Populate the prediction log from the test split (SPEC.md section 11,
Phase 3 session 1).

§11's Phase 3 acceptance is a reliability diagram computed *entirely* from
logged predictions. This is what fills the log: 100 historical matches from
the §9.1 test split, scored by the pinned model, written to Supabase.

    manifest   choose the matches, deterministically, and write them down
    run        score and log them; safe to re-run, safe to interrupt
    verify     reconcile what landed against what should have

Three properties it is built around, each because of something measured:

**Idempotent.** Every row carries the (innings, over_num, ball_in_over) key
added by migration 20260918000003, and every insert is ON CONFLICT DO
NOTHING. A run that dies half way is resumed by running it again. This is
not hypothetical tidiness: driving match 9339 through the deployed service
in Phase 2 posted 125 balls and produced 128 rows.

**One as-of computation per match, not per ball.** The as-of features depend
on (venue, batting team, bowling team, format, date), all of which are
constant across a chase - verified against the corpus, where zero test-split
matches have more than one (batting, bowling) pair in innings 2. So this is
100 computations rather than 11,860.

**Hybrid by default, with a parity gate.** A few matches go through the
DEPLOYED service so the log contains rows the production path actually
produced; the rest are scored locally through `predict_win_prob`, the same
function the service calls. The gate is that for the deployed matches, the
local recomputation must agree to the bit. Phase 2 measured the deployed
round trip at ~1.6 s/ball: all 100 matches that way is ~5.5 hours to
re-prove a path already measured end to end, and the parity assertion is
stronger evidence than the wall time.

Usage (from api/src, with api/.env configured):
    python -m ingest.replay_log manifest --count 100
    python -m ingest.replay_log run
    python -m ingest.replay_log verify
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from eval.splits import second_innings_predicate
from features.as_of import compute_as_of_features
from features.match_state import MatchStateRow
from ingest.replay import predict_win_prob
from models.artifact import active_model_row, resolve_pinned_artifact

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
MANIFEST_PATH = REPO_ROOT / "api" / "data" / "phase3_manifest.json"
CACHE_DIR = REPO_ROOT / "api" / "data" / "models" / "cache"

# How many of the manifest's matches go through the deployed service.
DEFAULT_DEPLOYED = 5
# How far the deployed and local probabilities for the same ball may differ.
#
# The first version of this demanded bit-identity and failed on the very
# first run: 0.1227525101067121 from the container against
# 0.12275251010671213 locally - one unit in the last place. That is not a
# divergence, it is LightGBM's floating-point accumulation differing between
# a Linux/amd64 container and a Windows host, and no amount of shared code
# removes it.
#
# 1e-9 is chosen to be far below anything that could change a decision and
# far above the noise: it is seven orders of magnitude tighter than the four
# decimal places any report shows, and a real divergence - a different
# model, different features, stale as-of data - moves probabilities by 1e-2
# or more, not 1e-9. The MAXIMUM observed difference is always printed, so
# the gate passing never hides the number it passed on.
PARITY_TOLERANCE = 1e-9
DEFAULT_BASE_URL = "https://api-production-5fa3.up.railway.app"
INSERT_BATCH = 500

# Deterministic, reproducible, and spread across the whole test window rather
# than clustered at one end - md5 over the id with a fixed salt orders the
# eligible set arbitrarily but identically on every run. The chosen ids are
# written to the manifest anyway, so the selection survives even if this
# query changes; the ordering only has to be stable enough to regenerate an
# equivalent set.
_MANIFEST_QUERY = f"""
    SELECT ms.match_id, count(*) AS balls, min(ms.match_date) AS match_date
    FROM match_states ms
    WHERE {second_innings_predicate("ms")} AND ms.match_date >= %(test_start)s
    GROUP BY ms.match_id
    ORDER BY md5(ms.match_id::text || 'phase3-session1')
    LIMIT %(count)s
"""

# Everything predict_win_prob needs, plus the ball key. Ordered by the true
# bowling order, which is (over, ball-within-over) and NOT balls_bowled:
# balls_bowled does not advance on an extra, so ordering by it puts the
# deliveries of an over containing a wide into an arbitrary order.
_BALLS_QUERY = f"""
    SELECT ms.score, ms.wickets, ms.balls_bowled, ms.balls_remaining, ms.target,
           ms.runs_required, ms.current_run_rate, ms.required_run_rate,
           ms.rrr_minus_crr, ms.partnership_runs, ms.partnership_balls,
           ms.balls_since_wicket, ms.phase, ms.match_date, ms.innings,
           d.over_num, d.ball_in_over,
           m.venue_id, m.format, d.batting_team_id, d.bowling_team_id
    FROM match_states ms
    JOIN matches m ON m.match_id = ms.match_id
    JOIN deliveries d ON d.delivery_id = ms.delivery_id
    WHERE ms.match_id = %(match_id)s AND {second_innings_predicate("ms")}
    ORDER BY d.over_num, d.ball_in_over
"""

_COLUMNS = (
    "score wickets balls_bowled balls_remaining target runs_required current_run_rate "
    "required_run_rate rrr_minus_crr partnership_runs partnership_balls balls_since_wicket "
    "phase match_date innings over_num ball_in_over venue_id format batting_team_id "
    "bowling_team_id"
).split()


def _env() -> dict:
    env = dotenv_values(ENV_PATH)
    for key in ("LOCAL_DATABASE_URL", "SUPABASE_SESSION_POOLER_URL"):
        if not env.get(key):
            sys.exit(f"{key} must be set in {ENV_PATH}")
    return env


def load_balls(local_conn, match_id: int) -> list[dict]:
    """Every includable innings-2 ball of one match, in bowling order.

    The WHERE clause is eval/splits.py's shared predicate, not a local
    rewrite: a ball logged here that the training split would have excluded
    is a ball outcome resolution can never label.
    """
    with local_conn.cursor() as cur:
        cur.execute(_BALLS_QUERY, {"match_id": match_id})
        rows = cur.fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


def mirror_match_rows(local_conn, supabase_conn, match_ids: list[int]) -> int:
    """Copy the `matches` rows for these ids to Supabase.

    `predictions.match_id` is a FK into a table that by §2.1 holds only live
    and recent matches, so the row has to exist before any prediction for it
    can be written. Exactly what cricketdata.py's `_ensure_match_row` does
    for a real live match, in bulk. Deliberately does NOT copy `winner` or
    `result_method`: the label stays local, and outcome resolution reads it
    from the corpus (see models/resolve_outcomes.py).
    """
    with local_conn.cursor() as cur:
        cur.execute(
            "SELECT match_id, competition, format, venue_id, start_time, team_a, team_b, "
            "status FROM matches WHERE match_id = ANY(%s)",
            (match_ids,),
        )
        rows = cur.fetchall()
    with supabase_conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO matches (match_id, competition, format, venue_id, start_time, "
            "team_a, team_b, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (match_id) DO NOTHING",
            rows,
        )
    supabase_conn.commit()
    return len(rows)


def build_manifest(local_conn, count: int, test_start: str = "2025-01-01") -> dict:
    with local_conn.cursor() as cur:
        cur.execute(_MANIFEST_QUERY, {"count": count, "test_start": test_start})
        rows = cur.fetchall()
    if len(rows) < count:
        sys.exit(f"only {len(rows)} eligible matches, asked for {count}")
    return {
        "test_start": test_start,
        "selection": "ORDER BY md5(match_id::text || 'phase3-session1')",
        "predicate": second_innings_predicate("ms"),
        "matches": [
            {"match_id": m, "balls": b, "match_date": d.isoformat()} for m, b, d in rows
        ],
        "expected_rows": sum(b for _, b, _ in rows),
    }


def _row_for(ball: dict, match_id: int) -> MatchStateRow:
    return MatchStateRow(
        match_id=match_id,
        innings=ball["innings"],
        score=ball["score"],
        wickets=ball["wickets"],
        balls_bowled=ball["balls_bowled"],
        balls_remaining=ball["balls_remaining"],
        target=ball["target"],
        runs_required=ball["runs_required"],
        current_run_rate=ball["current_run_rate"],
        required_run_rate=ball["required_run_rate"],
        rrr_minus_crr=ball["rrr_minus_crr"],
        partnership_runs=ball["partnership_runs"],
        partnership_balls=ball["partnership_balls"],
        balls_since_wicket=ball["balls_since_wicket"],
        phase=ball["phase"],
        batter_runs_so_far=0,
        batter_balls_faced=0,
    )


def score_match(artifact: dict, supabase_conn, match_id: int, balls: list[dict]) -> list[dict]:
    """Probabilities for every ball of one match, through the same
    `predict_win_prob` the service calls.

    The as-of features are read from Supabase's summaries - the same tables
    and the same helper the deployed service uses (session 3's Decision 3),
    so "scored locally" differs from "scored by the container" only in which
    process holds the connection.
    """
    first = balls[0]
    as_of = compute_as_of_features(
        supabase_conn,
        first["venue_id"],
        first["batting_team_id"],
        first["bowling_team_id"],
        first["format"],
        first["match_date"],
    )
    scored = []
    for ball in balls:
        probability = predict_win_prob(artifact["artifact"], _row_for(ball, match_id), as_of)
        if probability is None:
            sys.exit(f"match {match_id} ball {ball['balls_bowled']}: no prediction produced")
        scored.append({**ball, "p": probability})
    return scored


def _payload(ball: dict) -> str:
    """The same nine keys serving/app.py writes, so a row logged here and a
    row logged by the service are indistinguishable downstream."""
    return json.dumps(
        {
            "p": ball["p"],
            "innings": ball["innings"],
            "balls_bowled": ball["balls_bowled"],
            "balls_remaining": ball["balls_remaining"],
            "runs_required": ball["runs_required"],
            "score": ball["score"],
            "wickets": ball["wickets"],
            "target": ball["target"],
            "phase": ball["phase"],
        }
    )


def insert_predictions(supabase_conn, match_id: int, model_version: str, scored: list[dict]) -> int:
    """Write the rows, skipping any ball key already present. Returns how
    many were new, which is how the caller reports a resumed run honestly."""
    rows = [
        (
            match_id,
            model_version,
            _payload(ball),
            ball["innings"],
            ball["over_num"],
            ball["ball_in_over"],
        )
        for ball in scored
    ]
    inserted = 0
    with supabase_conn.cursor() as cur:
        for start in range(0, len(rows), INSERT_BATCH):
            chunk = rows[start : start + INSERT_BATCH]
            cur.executemany(
                """
                INSERT INTO predictions
                    (match_id, delivery_id, model_version, prediction_type, payload,
                     match_phase, created_at, innings, over_num, ball_in_over)
                VALUES (%s, NULL, %s, 'win_prob', %s, 'innings2', now(), %s, %s, %s)
                ON CONFLICT (match_id, model_version, prediction_type, innings, over_num, ball_in_over)
                    WHERE innings IS NOT NULL
                    DO NOTHING
                """,
                chunk,
            )
            inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    supabase_conn.commit()
    return inserted


def logged_ball_keys(supabase_conn, match_id: int, model_version: str) -> set[tuple[int, int, int]]:
    """Ball keys already present for this match and model.

    Lets a resumed DEPLOYED match skip the balls that landed before it was
    interrupted. Without this, resuming a 301-ball ODI re-posts every ball
    at ~1.6s each just to be told `logged: false` - eight minutes to get
    back to where it was, which is how a resumable job stops being one in
    practice. The consequence is that parity is then measured on the balls
    actually posted, which is what a sampling check measures anyway.
    """
    with supabase_conn.cursor() as cur:
        cur.execute(
            "SELECT innings, over_num, ball_in_over FROM predictions "
            "WHERE match_id = %s AND model_version = %s AND prediction_type = 'win_prob' "
            "AND innings IS NOT NULL",
            (match_id, model_version),
        )
        return {tuple(row) for row in cur.fetchall()}


def already_logged(supabase_conn, match_id: int, model_version: str) -> int:
    with supabase_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM predictions WHERE match_id = %s AND model_version = %s "
            "AND prediction_type = 'win_prob' AND innings IS NOT NULL",
            (match_id, model_version),
        )
        return cur.fetchone()[0]


class ModelVersionChanged(RuntimeError):
    """Raised when Supabase's active model moves mid-run.

    Not a warning and not a skip. The accuracy page groups calibration by
    model_version; a log split across two of them is a reliability diagram
    that is wrong and looks fine. Halting leaves a partial log, which the
    next run resumes - a partial log is recoverable, a mixed one is not.
    """


def confirm_model_version(supabase_conn, expected: str) -> None:
    version, _path, _notes = active_model_row(supabase_conn)
    if version != expected:
        raise ModelVersionChanged(
            f"Supabase's active model moved from {expected!r} to {version!r} mid-run. "
            f"Halting rather than writing rows under two versions. Re-run to resume; "
            f"rows already written under {expected!r} are kept and the new run will "
            f"refuse to mix, so decide which version the log should carry first."
        )


# A transient network failure must not end a run of thousands of posts. The
# first full run died on an unhandled SSL read timeout in the third match,
# which is exactly the failure this retry exists for - and retrying is free
# precisely BECAUSE the write is keyed: a ball that did commit before the
# timeout comes back as `logged: false` rather than as a second row.
POST_ATTEMPTS = 4
POST_BACKOFF_SECONDS = 2.0


def post_ball(base_url: str, match_id: int, ball: dict) -> tuple[float, bool]:
    """One ball through the DEPLOYED service. Returns (probability, logged).

    Raises on 409, never retrying it. `ActiveVersionGuard` returns 409 when
    the container's pin no longer matches Supabase's active model, and
    Phase 2's driver counted that as one bad ball and carried on - which is
    precisely how a log ends up split across two models without anyone
    noticing. Retrying a 409 would be worse still: it cannot succeed, and
    the delay would hide the reason.
    """
    import time as _time
    import urllib.error
    import urllib.request

    body = {
        "match_id": match_id,
        "innings": ball["innings"],
        "score": ball["score"],
        "wickets": ball["wickets"],
        "balls_bowled": ball["balls_bowled"],
        "balls_remaining": ball["balls_remaining"],
        "target": ball["target"],
        "runs_required": ball["runs_required"],
        "current_run_rate": _maybe_float(ball["current_run_rate"]),
        "required_run_rate": _maybe_float(ball["required_run_rate"]),
        "rrr_minus_crr": _maybe_float(ball["rrr_minus_crr"]),
        "partnership_runs": ball["partnership_runs"],
        "partnership_balls": ball["partnership_balls"],
        "balls_since_wicket": ball["balls_since_wicket"],
        "phase": ball["phase"],
        "batting_team_id": ball["batting_team_id"],
        "bowling_team_id": ball["bowling_team_id"],
        "venue_id": ball["venue_id"],
        "format": ball["format"],
        "match_date": ball["match_date"].isoformat(),
        "over_num": ball["over_num"],
        "ball_in_over": ball["ball_in_over"],
    }
    request = urllib.request.Request(
        f"{base_url}/predict/win-prob",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last: Exception | None = None
    for attempt in range(POST_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=30.0) as response:
                payload = json.loads(response.read())
            return payload["win_probability"], payload.get("logged", True)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:300]
            if exc.code == 409:
                raise ModelVersionChanged(
                    f"the deployed service refused with 409: {detail}"
                ) from exc
            raise RuntimeError(f"HTTP {exc.code} on match {match_id}: {detail}") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            last = exc
            if attempt < POST_ATTEMPTS - 1:
                _time.sleep(POST_BACKOFF_SECONDS * (attempt + 1))
    raise RuntimeError(
        f"match {match_id} over {ball['over_num']}.{ball['ball_in_over']}: "
        f"{POST_ATTEMPTS} attempts all failed, last was "
        f"{type(last).__name__}: {last}"
    ) from last


def _maybe_float(value) -> float | None:
    return None if value is None else float(value)


def run(manifest: dict, deployed_count: int, base_url: str, limit: int | None = None) -> int:
    env = _env()
    failures = 0
    with (
        psycopg.connect(env["LOCAL_DATABASE_URL"], connect_timeout=30) as local_conn,
        psycopg.connect(env["SUPABASE_SESSION_POOLER_URL"], connect_timeout=30) as supabase_conn,
    ):
        version, _path, _notes = active_model_row(supabase_conn)
        # resolve_pinned_artifact downloads from model_versions.artifact_path
        # and verifies the digest before returning, so the bytes scored here
        # are provably the bytes the registry points at.
        artifact = resolve_pinned_artifact(supabase_conn, version, CACHE_DIR)
        print(f"model {version}  sha256 {artifact['sha256'][:16]}...")

        entries = manifest["matches"][:limit] if limit else manifest["matches"]
        mirrored = mirror_match_rows(
            local_conn, supabase_conn, [e["match_id"] for e in entries]
        )
        print(f"mirrored {mirrored} match rows to Supabase\n")

        total_new = total_skipped = 0
        for index, entry in enumerate(entries):
            match_id = entry["match_id"]
            confirm_model_version(supabase_conn, version)

            existing = already_logged(supabase_conn, match_id, version)
            if existing >= entry["balls"]:
                total_skipped += 1
                print(f"  [{index + 1:>3}] match {match_id}: already logged ({existing})")
                continue

            balls = load_balls(local_conn, match_id)
            if len(balls) != entry["balls"]:
                # The manifest counted with the same predicate, so a mismatch
                # means the corpus changed underneath it. Report, do not adapt.
                print(
                    f"  [{index + 1:>3}] match {match_id}: MANIFEST DRIFT - "
                    f"manifest says {entry['balls']} balls, corpus has {len(balls)}"
                )
                failures += 1
                continue

            via_service = index < deployed_count
            scored = score_match(artifact, supabase_conn, match_id, balls)

            if via_service:
                # The deployed service writes its own rows; this loop only
                # posts, then checks the local recomputation agrees.
                max_diff = 0.0
                worst = None
                have = logged_ball_keys(supabase_conn, match_id, version)
                posted = 0
                for ball, local in zip(balls, scored):
                    if (ball["innings"], ball["over_num"], ball["ball_in_over"]) in have:
                        continue
                    posted += 1
                    remote_p, _logged = post_ball(base_url, match_id, ball)
                    diff = abs(remote_p - local["p"])
                    if diff > max_diff:
                        max_diff, worst = diff, (ball, remote_p, local["p"])
                if max_diff > PARITY_TOLERANCE:
                    failures += 1
                    ball, remote_p, local_p = worst
                    print(
                        f"        PARITY FAILURE at over {ball['over_num']}."
                        f"{ball['ball_in_over']}: deployed {remote_p!r} vs "
                        f"local {local_p!r} (diff {max_diff:.3e})"
                    )
                new = already_logged(supabase_conn, match_id, version) - existing
                verdict = "OK" if max_diff <= PARITY_TOLERANCE else "FAILED"
                print(
                    f"  [{index + 1:>3}] match {match_id}: {posted} of {len(balls)} posted "
                    f"via DEPLOYED ({len(balls) - posted} already present), {new} new rows, "
                    f"parity {verdict} (max diff {max_diff:.3e}, n={posted})"
                )
            else:
                new = insert_predictions(supabase_conn, match_id, version, scored)
                print(f"  [{index + 1:>3}] match {match_id}: {len(balls)} scored, {new} new rows")
            total_new += new

    print(f"\n{total_new} rows written, {total_skipped} matches already complete")
    if failures:
        print(f"{failures} match(es) reported a problem - see above")
    return 1 if failures else 0


_VERIFY_LOGGED = """
    SELECT match_id, count(*) AS rows,
           count(DISTINCT (innings, over_num, ball_in_over)) AS distinct_keys
    FROM predictions
    WHERE match_id = ANY(%s) AND model_version = %s AND prediction_type = 'win_prob'
      AND innings IS NOT NULL
    GROUP BY match_id
"""


def _check(problems: list[str], label: str, ok: bool, detail: str) -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  -  {detail}"))
    if not ok:
        problems.append(label)


def verify(manifest: dict) -> int:
    """Reconcile the log against the manifest.

    Reports every check and fails on any of them. A log that is 99 percent
    right is not a log you can compute a reliability diagram from, and the
    failure mode is a diagram that renders.
    """
    env = _env()
    expected = {e["match_id"]: e["balls"] for e in manifest["matches"]}
    match_ids = list(expected)
    problems: list[str] = []

    with psycopg.connect(env["SUPABASE_SESSION_POOLER_URL"], connect_timeout=30) as conn:
        version, _path, _notes = active_model_row(conn)
        with conn.cursor() as cur:
            cur.execute(_VERIFY_LOGGED, (match_ids, version))
            logged = {m: (rows, keys) for m, rows, keys in cur.fetchall()}

            missing = [m for m in match_ids if m not in logged]
            short = [
                (m, logged[m][0], expected[m])
                for m in match_ids
                if m in logged and logged[m][0] != expected[m]
            ]
            dupes = [(m, r, k) for m, (r, k) in logged.items() if r != k]

            print(f"manifest: {len(match_ids)} matches, {manifest['expected_rows']} expected rows")
            print(f"logged:   {len(logged)} matches, {sum(r for r, _ in logged.values())} rows")

            _check(problems, "every manifest match is logged", not missing, f"missing {missing[:5]}")
            _check(
                problems,
                "row count matches the manifest per match",
                not short,
                f"{len(short)} differ, e.g. {short[:3]}",
            )
            _check(
                problems,
                "no duplicate ball keys",
                not dupes,
                f"{len(dupes)} matches with duplicates, e.g. {dupes[:3]}",
            )

            cur.execute(
                "SELECT DISTINCT model_version FROM predictions WHERE match_id = ANY(%s) "
                "AND innings IS NOT NULL",
                (match_ids,),
            )
            versions = [r[0] for r in cur.fetchall()]
            _check(
                problems,
                "exactly one model_version across the log",
                len(versions) == 1,
                f"found {versions}",
            )

            cur.execute(
                "SELECT count(*) FROM predictions p "
                "LEFT JOIN matches m ON m.match_id = p.match_id WHERE m.match_id IS NULL"
            )
            orphans = cur.fetchone()[0]
            _check(
                problems, "no predictions without a matches row", orphans == 0, f"{orphans} orphans"
            )

            cur.execute(
                """
                SELECT count(*) FILTER (WHERE o.prediction_id IS NOT NULL),
                       count(*) FILTER (WHERE o.prediction_id IS NULL)
                FROM predictions p
                LEFT JOIN prediction_outcomes o ON o.prediction_id = p.prediction_id
                WHERE p.match_id = ANY(%s) AND p.innings IS NOT NULL
                """,
                (match_ids,),
            )
            resolved, unresolved = cur.fetchone()
            print(f"outcomes: {resolved} resolved, {unresolved} unresolved")
            _check(
                problems,
                "every logged prediction has an outcome",
                unresolved == 0,
                f"{unresolved} unresolved (run models.resolve_outcomes)",
            )

    if problems:
        print(f"\n{len(problems)} check(s) FAILED")
        return 1
    print("\nall checks passed")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="replay_log", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("manifest", help="choose the matches and write the manifest")
    m.add_argument("--count", type=int, default=100)
    m.add_argument("--out", type=Path, default=MANIFEST_PATH)

    r = sub.add_parser("run", help="score and log the manifest's matches")
    r.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    r.add_argument("--deployed", type=int, default=DEFAULT_DEPLOYED)
    r.add_argument("--base-url", default=DEFAULT_BASE_URL)
    r.add_argument("--limit", type=int, default=None, help="first N matches only (testing)")

    v = sub.add_parser("verify", help="reconcile the log against the manifest")
    v.add_argument("--manifest", type=Path, default=MANIFEST_PATH)

    args = parser.parse_args(argv)

    if args.command == "manifest":
        with psycopg.connect(_env()["LOCAL_DATABASE_URL"], connect_timeout=30) as conn:
            manifest = build_manifest(conn, args.count)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(
            f"{len(manifest['matches'])} matches, {manifest['expected_rows']} expected rows "
            f"-> {args.out}"
        )
        return

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.command == "run":
        raise SystemExit(run(manifest, args.deployed, args.base_url.rstrip("/"), args.limit))
    raise SystemExit(verify(manifest))


if __name__ == "__main__":
    main()
