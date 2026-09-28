# Python tests CI skips (measured 2026-09-27)

CI's `api` job ran `pytest tests` on `d000dc8` and got **627 passed, 110
skipped**. CI doesn't print skip reasons, so they were reproduced locally
under the same conditions:
- a clean worktree with no `api/.env`;
- `CI=1`, plus the job's placeholder `SUPABASE_URL`, `SUPABASE_SECRET_KEY`
  and `CRICSHEET_DATA_DIR`;
- `LOCAL_DATABASE_URL` pointing at a freshly migrated, empty database.

The totals came out identical, 627 passed and 110 skipped:
`pytest tests -rs`.

**Every test that needs the real corpus, a trained artifact, or live
credentials is skipped in CI.** The leak canaries are among them, which is
why a failing canary went unnoticed. They run only locally; SPEC.md §9.1
records when they last passed.

| Skipped | File | Reason |
|---|---|---|
| 14 | `tests/features/test_match_state.py` | `LOCAL_DATABASE_URL` read from `api/.env` only |
| 14 | `tests/ingest/test_daily_cricsheet.py` | the corpus is empty |
| 11 | `tests/db/test_asof_parity.py` | `api/.env` not found (live credentials) |
| 7 | `tests/db/test_full_member.py` | no credentials for local, supabase |
| 7 | `tests/db/test_schema_parity.py` | `api/.env` not found (both databases) |
| 7 | `tests/models/test_resolve_outcomes.py` | reference matches not loaded (the synthetic twin runs) |
| **6** | **`tests/eval/test_splits.py`** | `LOCAL_DATABASE_URL` read from `api/.env` only; **includes the leak canary** |
| 6 | `tests/db/test_tool_queries_compile.py` | `api/.env` not found |
| 6 | `tests/serving/test_live_predictor.py` | no active `model_versions` row |
| 5 | `tests/db/test_float_boundary.py` | `LOCAL_DATABASE_URL` read from `api/.env` only |
| 5 | `tests/features/test_asof_summary.py` | `LOCAL_DATABASE_URL` read from `api/.env` only |
| 5 | `tests/features/test_elo.py` | `LOCAL_DATABASE_URL` read from `api/.env` only |
| 4 | `tests/db/test_tool_values.py` | `api/.env` not found (the replica) |
| **3** | **`tests/models/test_win_prob_2nd.py`** | `LOCAL_DATABASE_URL` read from `api/.env` only; **includes the LightGBM canary** |
| 3 | `tests/deploy/test_smoke.py` | `SMOKE_BASE_URL` not set |
| 2 | `tests/ingest/test_replay.py` | `LOCAL_DATABASE_URL` read from `api/.env` only |
| 1 | `tests/ingest/test_cricsheet_exact_match.py` | `LOCAL_DATABASE_URL` and `CRICSHEET_DATA_DIR` |
| 1 | `tests/ingest/test_live_asof_parity.py` | `LOCAL_DATABASE_URL` read from `api/.env` only |
| 1 | `tests/ingest/test_live_client.py` | `LOCAL_DATABASE_URL` read from `api/.env` only |
| 1 | `tests/serving/test_artifact.py` | the artifact `.pkl` is not in a checkout |
| 1 | `tests/test_shared_secret_parity.py` | `AGENT_TOOL_SHARED_SECRET` not set |
