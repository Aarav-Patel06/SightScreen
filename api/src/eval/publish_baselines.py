"""Fit the two §9.2 baselines and publish them (Phase 3 session 2).

§8.5 wants the accuracy page to compare the model against both baselines
"so users can see the model actually adds value". Doing that honestly means
scoring the baselines on the SAME matches as the model, not citing Phase 1's
test-split figures beside a number computed on a different population.

Which runs into a wall: both baselines are fitted objects. `LogisticBaseline`
carries a `StandardScaler` and coefficients; `HistoricalBaseRateBaseline`
carries three lookup tables. Both are fit on the TRAIN split, which lives in
`match_states` in the local corpus - and the daily monitor runs in a GitHub
Action, which has no corpus and never will (§2.1).

So the fit happens here, once, on a laptop, and the result is published the
way the model is: bytes plus a `#sha256=` fragment, verified before use. The
monitor then downloads and scores. Same shape as
`models/publish_model_version.py`, deliberately - that path is proven, and a
second way of shipping an artifact would be a second thing to get wrong.

Usage (from api/src, with api/.env configured):
    python -m eval.publish_baselines --out ../data/models/baselines.pkl
    # upload that file to a GitHub Release, then:
    python -m eval.publish_baselines --register \
        --url https://github.com/Aarav-Patel06/SightScreen/releases/download/baselines-20260919/baselines.pkl
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from db.env import env_value
from eval.baselines import HistoricalBaseRateBaseline, LogisticBaseline
from eval.splits import get_second_innings_split
from models.artifact import assert_artifact_url, ensure_artifact, sha256_file

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_OUT = REPO_ROOT / "api" / "data" / "models" / "baselines.pkl"
STATE_PATH = REPO_ROOT / "api" / "data" / "baselines_published.json"


def fit_baselines(local_conn) -> dict:
    """Both §9.2 baselines, fit on train exactly as Phase 1 fit them."""
    import joblib  # noqa: F401 - imported for the same reason models/ does

    train = get_second_innings_split(local_conn, "train")
    if len(train) == 0:
        sys.exit("the train split is empty - is the corpus loaded?")

    logistic = LogisticBaseline().fit(train)
    base_rate = HistoricalBaseRateBaseline().fit(train)
    return {
        "logistic": logistic,
        "historical_base_rate": base_rate,
        "n_train_rows": int(len(train)),
        "n_train_matches": int(len(set(train.match_id.tolist()))),
        "fit_at": datetime.now(timezone.utc).isoformat(),
    }


def write(out_path: Path, bundle: dict) -> str:
    import joblib

    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out_path)
    digest = sha256_file(out_path)
    print(f"wrote {out_path}")
    print(f"  fit on {bundle['n_train_rows']} rows / {bundle['n_train_matches']} matches")
    print(f"  sha256 {digest}")
    print()
    print("Next: upload this file to a GitHub Release, then re-run with")
    print(f"  python -m eval.publish_baselines --register --url <asset url>")
    return digest


def register(url: str, artifact_file: Path) -> dict:
    """Verify the published bytes, then record where they are.

    The digest is checked against a download BEFORE anything is recorded -
    the same order `publish_model_version.py` uses, and for the same reason:
    registering a pointer to bytes nobody has fetched is how you get a job
    that works for a month and fails at 3am.
    """
    if not artifact_file.exists():
        sys.exit(f"local artifact not found at {artifact_file} - run without --register first")
    digest = sha256_file(artifact_file)
    assert_artifact_url(url, field="--url")
    if "#" in url:
        sys.exit("pass the bare release URL; the sha256 fragment is added for you")
    pinned = f"{url}#sha256={digest}"

    with tempfile.TemporaryDirectory() as tmp:
        fetched = ensure_artifact(pinned, Path(tmp), filename="baselines.pkl")
        if sha256_file(fetched) != digest:
            sys.exit("the published bytes do not match the local file")
    print(f"verified {url}")
    print(f"         sha256 {digest}")

    state = {"artifact_path": pinned, "registered_at": datetime.now(timezone.utc).isoformat()}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"recorded in {STATE_PATH}")
    return state


def published_path() -> str | None:
    """Where the monitor looks. Environment first so a workflow can override
    without a commit, then the committed record."""
    from_env = env_value("BASELINES_ARTIFACT_URL")
    if from_env:
        return from_env
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))["artifact_path"]
    return None


def load_baselines(cache_dir: Path):
    """Fetch and verify the published baselines, or return None.

    None rather than raising: §8.5's baseline comparison is a section of the
    accuracy page, not the whole of it. A monitor that cannot reach the
    artifact should still report the reliability table and say the
    comparison is unavailable - reporting less beats reporting nothing.
    """
    import joblib

    url = published_path()
    if url:
        path = ensure_artifact(url, cache_dir, filename="baselines.pkl")
        return joblib.load(path)
    # No registered URL. On a laptop the file this module just wrote is
    # right there, and using it means a local monitor run produces the whole
    # report instead of a hole where the comparison should be. In a GitHub
    # Action the file does not exist, so the caller still gets None and says
    # the comparison is unavailable - which is the honest answer there,
    # because nothing has verified any bytes.
    if DEFAULT_OUT.exists():
        return joblib.load(DEFAULT_OUT)
    return None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="publish_baselines", description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--register", action="store_true", help="verify a published URL and record it")
    parser.add_argument("--url", help="the bare GitHub Release asset URL")
    args = parser.parse_args(argv)

    if args.register:
        if not args.url:
            parser.error("--register needs --url")
        register(args.url, args.out)
        return

    local_url = env_value("LOCAL_DATABASE_URL")
    if not local_url:
        sys.exit("LOCAL_DATABASE_URL must be set - the baselines are fit on the local corpus")
    with psycopg.connect(local_url, connect_timeout=30) as conn:
        bundle = fit_baselines(conn)
    write(args.out, bundle)


if __name__ == "__main__":
    main()
