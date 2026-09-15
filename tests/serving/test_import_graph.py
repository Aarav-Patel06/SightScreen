"""The serving container must not be able to train (Phase 2 session 4,
Decision 3).

SPEC.md section 2.1: "Training never touches Supabase; serving never touches
local Postgres." config.py's boundary validator enforces the second half at
runtime by refusing to start when LOCAL_DATABASE_URL is present. This test
enforces the first half structurally: the serving entry points must not even
reach the training modules, all of which assume a local corpus.

Static analysis rather than importing and inspecting sys.modules, because
importing config triggers full environment validation - so an import-based
version of this test would pass or fail for reasons unrelated to what it
claims to check.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent.parent / "api" / "src"

# Modules that read the historical corpus, train, or rebuild features. None
# of them can work without local Postgres, so their presence in a serving
# import graph means either dead weight in the image or a latent boundary
# violation.
TRAINING_MODULES = {
    "models.win_prob_2nd",
    "models.registry",
    "eval.splits",
    "eval.baselines",
    "eval.metrics",
    "ingest.cricsheet",
    "ingest.sync_reference_tables",
}

# Deliberately NOT in the set above, with the reason, because this test flagged
# it on its first run and the flag was wrong:
#
#   features.asof_summary is dual-purpose. Its rebuild_* functions are training
#   only, but DerivedTable / DERIVED_TABLES / content_hash are what
#   features.as_of's freshness check needs at serving time - and importing the
#   module pulls nothing heavier than psycopg. The risk worth guarding is a
#   serving path CALLING rebuild(), which is a runtime question this static
#   walk cannot answer and config.py's boundary check already does: rebuild()
#   needs LOCAL_DATABASE_URL, which a deployed container is forbidden to have.
SHARED_BY_DESIGN = {"features.asof_summary"}

SERVING_ENTRY_POINTS = ("serving.entrypoint", "serving.app", "serving.live_loop")


def _module_path(module: str) -> Path | None:
    candidate = SRC / (module.replace(".", "/") + ".py")
    return candidate if candidate.exists() else None


def _direct_imports(module: str) -> set[str]:
    """Top-level imports only - imports inside a function body are deferred
    and do not run in the serving path unless that function is called."""
    path = _module_path(module)
    if path is None:
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:  # module scope only, deliberately
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _transitive_imports(root: str) -> set[str]:
    seen: set[str] = set()
    queue = [root]
    while queue:
        module = queue.pop()
        for imported in _direct_imports(module):
            if imported in seen:
                continue
            seen.add(imported)
            if _module_path(imported) is not None:
                queue.append(imported)
    return seen


@pytest.mark.parametrize("entry_point", SERVING_ENTRY_POINTS)
def test_serving_entry_points_do_not_import_training_code(entry_point):
    reachable = _transitive_imports(entry_point)
    leaked = reachable & TRAINING_MODULES
    assert not leaked, (
        f"{entry_point} transitively imports training modules {sorted(leaked)} at module "
        "scope. Those need the local corpus, which a serving container must not have "
        "(SPEC.md section 2.1). Defer the import into the function that needs it."
    )


def test_the_check_would_actually_catch_a_violation():
    """A guard on the guard. If _transitive_imports silently returned nothing,
    every assertion above would pass vacuously."""
    reachable = _transitive_imports("serving.app")
    assert "features.as_of" in reachable, "the walker found nothing; the test is vacuous"
    assert _transitive_imports("eval.run_baselines") & TRAINING_MODULES, (
        "eval.run_baselines is expected to reach eval.splits - if it no longer does, "
        "this canary needs a different subject and the walker needs re-verifying"
    )
