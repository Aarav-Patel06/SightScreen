"""Publish a trained model version to Supabase (Phase 2 session 4).

The deploy-time action models/registry.py:11-17 flagged and deliberately did
not build: "Actually deploying a version (uploading the artifact to GitHub
Releases/Supabase Storage and writing Supabase's own model_versions row) is a
Phase 2 deploy-time action, not something a training script should do as a
side effect."

It stays a separate CLI for that reason. docs/phase1-closeout.md:138-147 is
blunt about the failure mode: "Don't let a future session's registry.py grow
into quietly writing to Supabase from a training script - that's the exact
'training never touches Supabase' rule this session caught itself almost
breaking." Publishing is a human decision with a human's finger on it.

Why this is not optional plumbing: predictions.model_version is
REFERENCES model_versions(model_version), and Supabase's copy of that table
has zero rows. Until a version is published there, no prediction can be
written at all.

The row is only written AFTER the published URL has been downloaded and its
digest checked. Registering a pointer to bytes nobody has verified is how you
get a container that starts fine and fails at 3am.

Usage (from api/src, with api/.env configured):
    python -m models.publish_model_version winprob2-20260910 \
        --url https://github.com/Aarav6000/SightScreen/releases/download/winprob2-20260910/winprob2-20260910.pkl
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import psycopg

from db.env import env_value
from models.artifact import assert_artifact_url, ensure_artifact, sha256_file

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ARTIFACT_DIR = REPO_ROOT / "api" / "data" / "models" / "win_prob_2nd"

# Travels with the version into Supabase so the skew is visible from the
# serving side, not only in SPEC.md section 15. See that section's
# 2026-09-15 row; closed by the next retrain, not by anything else.
DEFAULT_NOTES = (
    "KNOWN TRAIN/SERVE SKEW (open until the next retrain): trained against the "
    "pre-fix elo_as_of, which resolved same-date Elo ties by heap order. Serving "
    "uses the fixed end-of-day ordering. Bounded at ~9.2 Elo points mean (max 24.2) "
    "on 162 of 12,916 matches (1.25%), on a feature Phase 1 measured as individually "
    "non-significant. See SPEC.md section 15."
)


def _local_row(conn, model_version: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT model_type, trained_at, train_end_date, test_brier, test_log_loss "
            "FROM model_versions WHERE model_version = %s",
            (model_version,),
        )
        row = cur.fetchone()
    if row is None:
        sys.exit(
            f"no local model_versions row for {model_version!r} - train and register it "
            f"first, or check the spelling."
        )
    return {
        "model_type": row[0],
        "trained_at": row[1],
        "train_end_date": row[2],
        "test_brier": row[3],
        "test_log_loss": row[4],
    }


def publish(model_version: str, url: str, *, notes: str, artifact_file: Path | None = None) -> dict:
    local_url = env_value("LOCAL_DATABASE_URL")
    supabase_url = env_value("SUPABASE_SESSION_POOLER_URL")
    if not local_url or not supabase_url:
        sys.exit("LOCAL_DATABASE_URL and SUPABASE_SESSION_POOLER_URL must both be set")

    artifact_file = artifact_file or (ARTIFACT_DIR / f"{model_version}.pkl")
    if not artifact_file.exists():
        sys.exit(f"local artifact not found at {artifact_file}")
    digest = sha256_file(artifact_file)

    assert_artifact_url(url, field="--url")
    if "#" in url:
        sys.exit("pass the bare release URL; the sha256 fragment is added for you")
    pinned = f"{url}#sha256={digest}"

    # Verify the published bytes BEFORE writing a row that points at them.
    with tempfile.TemporaryDirectory() as tmp:
        fetched = ensure_artifact(pinned, Path(tmp), filename=f"{model_version}.pkl")
        assert sha256_file(fetched) == digest
    print(f"verified {url}\n         sha256 {digest} matches the local artifact")

    with psycopg.connect(local_url) as local_conn:
        meta = _local_row(local_conn, model_version)

    with psycopg.connect(supabase_url) as conn:
        with conn.cursor() as cur:
            # Exactly one active version, always. Demote first so a failure
            # here leaves nothing active rather than two things active.
            cur.execute(
                "UPDATE model_versions SET is_active = FALSE WHERE is_active AND model_version <> %s",
                (model_version,),
            )
            cur.execute(
                """
                INSERT INTO model_versions
                    (model_version, model_type, trained_at, train_end_date,
                     test_brier, test_log_loss, is_active, is_shadow, artifact_path, notes)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE, FALSE, %s, %s)
                ON CONFLICT (model_version) DO UPDATE SET
                    model_type    = EXCLUDED.model_type,
                    trained_at    = EXCLUDED.trained_at,
                    test_brier    = EXCLUDED.test_brier,
                    test_log_loss = EXCLUDED.test_log_loss,
                    is_active     = TRUE,
                    is_shadow     = FALSE,
                    artifact_path = EXCLUDED.artifact_path,
                    notes         = EXCLUDED.notes
                """,
                (
                    model_version,
                    meta["model_type"],
                    meta["trained_at"],
                    meta["train_end_date"],
                    meta["test_brier"],
                    meta["test_log_loss"],
                    pinned,
                    notes,
                ),
            )
        conn.commit()

    print(f"published {model_version} -> Supabase model_versions (is_active)")
    print(f"  artifact_path {pinned}")
    return {"model_version": model_version, "artifact_path": pinned, "sha256": digest}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="publish_model_version")
    parser.add_argument("model_version")
    parser.add_argument("--url", required=True, help="bare GitHub Release asset URL")
    parser.add_argument("--notes", default=DEFAULT_NOTES)
    parser.add_argument("--artifact-file", type=Path, default=None)
    args = parser.parse_args(argv)
    publish(args.model_version, args.url, notes=args.notes, artifact_file=args.artifact_file)


if __name__ == "__main__":
    main()
