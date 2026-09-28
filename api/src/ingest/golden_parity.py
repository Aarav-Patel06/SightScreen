"""Golden parity: match 8429, recomputed in production every night.

tests/ingest/test_daily_cricsheet.py proves the daily job's scoring equals the
local replay, bit for bit - but it needs the corpus, so CI skips it and it
only ever ran on the owner's machine. This check runs in the daily Action
itself, against the real Supabase:

  write   the reference. Match 8429 (Cricsheet 1496581) scored by the LOCAL
          REPLAY (`replay_log.score_match` over the corpus's match_states) for
          every version in SERVED_VERSIONS, keyed by model_version. Written
          in Linux, installed as the Action installs (`pip install -e
          ./api[dev]`), and it records the library versions it used. On
          2026-09-28 a Windows and a Linux replay agreed on all 305 balls
          exactly, so the platform is not what moves the last bits - the
          library build is: production's stored rows for 8429, written on
          2026-09-18 by an earlier install, differ from today's in the last
          one or two bits on 298 of 305 balls. So an upgrade that changes a
          probability's last bit fails this check, and should: regenerate the
          reference deliberately, and say why.

  check   the daily job's own path, run fresh: 8429's committed raw JSON loaded
          into a scratch database with the reference tables copied from
          Supabase, match_states rebuilt, the as-of summaries rebuilt from
          Supabase's feature_ledger, every reference version's artifact
          fetched from its published URL and digest-checked, and scored. Any
          probability that differs from the reference by any amount fails the
          run, as does a missing ball, or an active version with no reference.

Usage (from api/src):
    python -m ingest.golden_parity write --local-url URL      # the reference
    python -m ingest.golden_parity check --stage-url URL      # the Action
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
REFERENCE_PATH = REPO_ROOT / "api" / "data" / "golden" / "match_8429.json"
RAW_PATH = REPO_ROOT / "tests" / "fixtures" / "cricsheet" / "1496581.json"
GOLDEN_MATCH_ID = 8429
GOLDEN_CRICSHEET_ID = "1496581"
ARTIFACT_DIR = REPO_ROOT / "api" / "data" / "models" / "win_prob_2nd"

# Every version the daily job may serve. The reference must hold each one.
SERVED_VERSIONS = ("winprob2-20260910", "winprob2-20260927")


class GoldenParityFailure(RuntimeError):
    """Production scored the golden match differently from the reference."""


def _key(ball: dict) -> str:
    return f"{ball['innings']}-{ball['over_num']}-{ball['ball_in_over']}"


def _environment() -> dict:
    import lightgbm
    import numpy
    import sklearn

    return {
        "platform": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
        "lightgbm": lightgbm.__version__,
        "numpy": numpy.__version__,
        "scikit-learn": sklearn.__version__,
    }


def write(local_url: str) -> dict:
    """The reference, from the local replay over the corpus."""
    from ingest.replay_log import load_balls, score_match

    predictions = {}
    with psycopg.connect(local_url) as conn:
        balls = load_balls(conn, GOLDEN_MATCH_ID)
        for version in SERVED_VERSIONS:
            artifact = joblib.load(ARTIFACT_DIR / f"{version}.pkl")
            scored = score_match({"artifact": artifact}, conn, GOLDEN_MATCH_ID, balls)
            predictions[version] = {_key(b): b["p"] for b in scored}
    reference = {
        "match_id": GOLDEN_MATCH_ID,
        "cricsheet_id": GOLDEN_CRICSHEET_ID,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "written_by": "local replay (ingest.replay_log.score_match over the corpus)",
        "environment": _environment(),
        "predictions": predictions,
    }
    REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE_PATH.write_text(json.dumps(reference, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return reference


def _artifact(supabase_conn, version: str, cache_dir: Path) -> dict:
    """A version's published artifact, digest-checked - active or not."""
    from models.artifact import ensure_artifact

    row = supabase_conn.execute(
        "SELECT artifact_path FROM model_versions WHERE model_version = %s", (version,)).fetchone()
    if row is None:
        raise GoldenParityFailure(f"{version} has a reference but no model_versions row on Supabase")
    return joblib.load(ensure_artifact(row[0], cache_dir, filename=f"{version}.pkl"))


def recompute(stage_url: str, supabase_conn, versions, cache_dir: Path) -> dict[str, dict[str, float]]:
    """The daily job's path for one match, from scratch (ingest/daily_cricsheet.run_daily)."""
    from features.feature_ledger import rebuild_summaries_from_ledger
    from features.match_state import rebuild_match_states
    from ingest.cricsheet import LoadReport, load_match
    from ingest.daily_cricsheet import assert_stage_empty, copy_reference_tables, supabase_state
    from ingest.replay_log import load_balls, score_match

    with psycopg.connect(stage_url) as stage, psycopg.connect(stage_url, autocommit=True) as catalog:
        assert_stage_empty(stage)
        copy_reference_tables(supabase_conn, stage)
        report = LoadReport()
        load_match(stage, catalog, report, RAW_PATH, match_id=GOLDEN_MATCH_ID)
        if report.rejections:
            raise GoldenParityFailure(f"the golden match did not load: {report.rejections}")
        rebuild_match_states(stage)
        stage.commit()
        rebuild_summaries_from_ledger(stage, supabase_state(supabase_conn)["ledger"])
        balls = load_balls(stage, GOLDEN_MATCH_ID)
        out = {}
        for version in versions:
            scored = score_match({"artifact": _artifact(supabase_conn, version, cache_dir)}, stage,
                                 GOLDEN_MATCH_ID, balls)
            out[version] = {_key(b): b["p"] for b in scored}
    return out


def differences(reference: dict[str, dict[str, float]], computed: dict[str, dict[str, float]]) -> list[str]:
    """Every way `computed` differs from `reference`. Exact: no tolerance."""
    problems = []
    for version, expected in reference.items():
        got = computed.get(version)
        if got is None:
            problems.append(f"{version}: not recomputed")
            continue
        for key, p in expected.items():
            if key not in got:
                problems.append(f"{version} ball {key}: missing")
            elif got[key] != p:
                problems.append(f"{version} ball {key}: reference {p!r}, production {got[key]!r}")
        problems += [f"{version} ball {key}: not in the reference" for key in got.keys() - expected.keys()]
    return problems


def check(stage_url: str, supabase_conn, cache_dir: Path, *, drill: bool = False) -> dict:
    from models.artifact import active_model_row

    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    expected = reference["predictions"]
    active, _path, _notes = active_model_row(supabase_conn)
    if active not in expected:
        raise GoldenParityFailure(
            f"the active model {active!r} has no golden reference; regenerate "
            f"{REFERENCE_PATH.relative_to(REPO_ROOT)} with `golden_parity write` before serving it")
    if drill:
        # Failure drill: prove this goes red. One reference value, nudged by
        # one part in a billion - far below anything a person would notice.
        version = sorted(expected)[0]
        key = sorted(expected[version])[0]
        expected[version][key] = expected[version][key] * (1 + 1e-9)
    computed = recompute(stage_url, supabase_conn, sorted(expected), cache_dir)
    problems = differences(expected, computed)
    summary = {
        "versions": sorted(expected),
        "balls": {v: len(expected[v]) for v in expected},
        "reference_written_at": reference["written_at"],
        "reference_environment": reference["environment"],
        "environment": _environment(),
        "differences": len(problems),
    }
    print(json.dumps(summary, indent=1))
    if problems:
        raise GoldenParityFailure(f"{len(problems)} difference(s) from the golden reference, e.g. {problems[:5]}")
    return summary


def main(argv: list[str] | None = None) -> int:
    from db.env import require_env

    parser = argparse.ArgumentParser(prog="golden_parity")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("write", help="regenerate the reference from the local replay")
    w.add_argument("--local-url", required=True)
    c = sub.add_parser("check", help="recompute the golden match on the daily job's path and compare")
    c.add_argument("--stage-url", required=True)
    c.add_argument("--cache-dir", type=Path, default=Path("/tmp/golden-models"))
    c.add_argument("--drill", action="store_true", help="alter one reference value; must fail")
    args = parser.parse_args(argv)

    if args.command == "write":
        reference = write(args.local_url)
        print(f"wrote {REFERENCE_PATH} for {sorted(reference['predictions'])} in {reference['environment']}")
        return 0
    with psycopg.connect(require_env("SUPABASE_SESSION_POOLER_URL")) as supabase_conn:
        try:
            check(args.stage_url, supabase_conn, args.cache_dir, drill=args.drill)
        except GoldenParityFailure as failure:
            print(f"GOLDEN PARITY FAILED: {failure}", file=sys.stderr)
            return 1
    print("golden parity: every reference probability reproduced exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
