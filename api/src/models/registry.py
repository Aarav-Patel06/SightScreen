"""Model versioning: save/load a trained artifact by version (SPEC.md
section 5.4, Phase 1 session 3).

Local-only, on purpose: SPEC.md section 2.1 states "training never touches
Supabase; serving never touches local Postgres," and model_versions is a
Supabase-hosted table in that split (serving reads it, not training). This
module writes to the LOCAL training Postgres's copy of model_versions
(present there too, since Phase 0's migrations apply identically to both
databases) purely for this environment's own record-keeping - "loadable by
version" within training/evaluation, not a live serving registry. Actually
deploying a version (uploading the artifact to GitHub Releases/Supabase
Storage and writing Supabase's own model_versions row) is a Phase 2
deploy-time action, not something a training script should do as a side
effect - not built here, flagged instead.

No promotion/shadow-deployment logic (section 8.4) - that's Phase 2+ scope
and would be speculative to build before anything serves a model at all.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import joblib
import psycopg
from dotenv import dotenv_values

# KNOWN TRAIN/SERVE SKEW - open until the next retrain (SPEC.md section 15,
# 2026-09-15). Recorded here because this is the module someone is looking at
# when they promote a version, and the retrain is what closes it.
#
#   winprob2-20260910 was trained against the pre-fix elo_as_of, which
#   resolved same-date Elo ties by heap order and returned the EARLIER
#   match's rating for 137 of 372 tied groups. Serving now uses the fixed
#   end-of-day ordering. 162 of 12,916 matches (1.25%) have a different
#   elo_diff, mean 9.2 Elo points, max 24.2, on a feature Phase 1 measured as
#   individually non-significant.
#
# This was NOT closed by retraining on the finding alone - the effect is small
# and an unplanned retrain would confound the next scheduled comparison. The
# next retrain must close it DELIBERATELY and say so in its report, rather
# than closing it as a side effect nobody notices.
KNOWN_SKEW = (
    "winprob2-20260910 trained against the pre-fix elo_as_of tie ordering; "
    "serving uses the fixed one. 1.25% of matches, ~9.2 Elo points. Closed by "
    "the next retrain - see SPEC.md section 15 (2026-09-15)."
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
ARTIFACT_DIR = REPO_ROOT / "api" / "data" / "models" / "win_prob_2nd"


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def save_model_version(
    conn,
    model_version: str,
    artifact: dict,
    train_end_date: date,
    test_brier: float,
    test_log_loss: float,
    is_active: bool = True,
) -> Path:
    """Writes the artifact file, then the local model_versions row pointing
    at it. Both or neither - if the DB write fails, the caller sees the
    exception; the artifact file existing without a registry row is a
    harmless orphan (re-running training regenerates it), the reverse
    (a row with no file) is the actually-confusing state, so the file is
    always written first."""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACT_DIR / f"{model_version}.pkl"
    joblib.dump(artifact, artifact_path)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO model_versions
                (model_version, model_type, trained_at, train_end_date,
                 test_brier, test_log_loss, is_active, is_shadow, artifact_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s, false, %s)
            ON CONFLICT (model_version) DO UPDATE SET
                trained_at = EXCLUDED.trained_at,
                test_brier = EXCLUDED.test_brier,
                test_log_loss = EXCLUDED.test_log_loss,
                is_active = EXCLUDED.is_active,
                artifact_path = EXCLUDED.artifact_path
            """,
            (
                model_version, "win_prob_2nd", datetime.now(timezone.utc), train_end_date,
                test_brier, test_log_loss, is_active, str(artifact_path),
            ),
        )
    conn.commit()
    return artifact_path


def load_model_version(conn, model_version: str) -> dict:
    """The only sanctioned way to load a persisted artifact by version -
    looks up artifact_path in model_versions rather than assuming a
    filename convention, so a future rename/relocation only needs the DB
    row updated, not every caller."""
    with conn.cursor() as cur:
        cur.execute("SELECT artifact_path FROM model_versions WHERE model_version = %s", (model_version,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"no model_versions row for {model_version!r}")
    return joblib.load(row[0])


if __name__ == "__main__":
    with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as _conn:
        with _conn.cursor() as _cur:
            _cur.execute("SELECT model_version, trained_at, test_brier, test_log_loss, is_active FROM model_versions ORDER BY trained_at")
            for _row in _cur.fetchall():
                print(_row)
