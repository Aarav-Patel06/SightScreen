#!/usr/bin/env python3
"""Applies every migration in supabase/migrations/ to both the local
training Postgres and the linked Supabase project, in order.

Safe to re-run: `supabase db push` tracks applied migrations in a
schema_migrations table and only applies new ones.

Run from anywhere: python supabase/apply_migrations.py

Uses python-dotenv's dotenv_values() to read api/.env rather than a shell
`source` - a real rotated Supabase password broke `source api/.env` outright
(special shell characters get parsed, not just substituted), so this reads
the file as data, never as a script.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

# shutil.which() resolves "npx" to "npx.cmd" on Windows via PATHEXT; passing
# a bare "npx" to subprocess.run() without shell=True fails there with
# FileNotFoundError. Deliberately not using shell=True instead - the db_url
# can contain arbitrary password characters that a shell would reinterpret
# (the exact bug that broke a plain `source api/.env` earlier in this repo's
# history).
NPX = shutil.which("npx")
if NPX is None:
    sys.exit("npx not found on PATH - install Node.js")


def push(label: str, db_url: str) -> None:
    print(f"Applying migrations to {label}...")
    subprocess.run(
        [NPX, "supabase", "db", "push", "--db-url", db_url],
        cwd=REPO_ROOT,
        check=True,
    )


def main() -> None:
    if not ENV_PATH.exists():
        sys.exit(f"{ENV_PATH} not found - copy api/.env.example and fill in real values first")
    env = dotenv_values(ENV_PATH)

    local_url = env.get("LOCAL_DATABASE_URL")
    if not local_url:
        sys.exit(f"LOCAL_DATABASE_URL not set in {ENV_PATH}")
    push("local training DB", local_url)

    # Supavisor's transaction-mode pooler (6543) doesn't support prepared
    # statements and doesn't reliably reset session state between clients -
    # migrations (which use SET and temp tables) must go through session
    # mode (5432) instead. See SPEC.md section 2.4. Never point this at
    # SUPABASE_TRANSACTION_POOLER_URL.
    session_url = env.get("SUPABASE_SESSION_POOLER_URL")
    if not session_url:
        sys.exit(f"SUPABASE_SESSION_POOLER_URL not set in {ENV_PATH}")
    push("Supabase (session pooler)", session_url)

    print("Done. Both databases are on the same migration set.")


if __name__ == "__main__":
    main()
