"""Non-Python files under api/src/ must be declared as package data.

api/Dockerfile does `pip install .` - a real install, not an editable one.
setuptools ships .py files automatically and nothing else, so a data file
read at runtime via Path(__file__).with_name() works perfectly from the
local checkout (where a .pth puts api/src straight on sys.path) and raises
FileNotFoundError inside the container.

Found by building the wheel and listing it: agent_tools/views.sql, which
holds the read-only role's grants, was absent while the three .py files
beside it shipped fine. This test is the reason a second such file cannot
repeat it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "api" / "src"
PYPROJECT = REPO_ROOT / "api" / "pyproject.toml"

# Files that are not imported at runtime and are not expected in the wheel.
IGNORED_SUFFIXES = {".pyc", ".pyi", ".typed"}
IGNORED_PARTS = {"__pycache__", "cricket_api.egg-info"}


def _declared_patterns() -> dict[str, list[str]]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return config.get("tool", {}).get("setuptools", {}).get("package-data", {})


def test_every_data_file_under_src_is_declared_as_package_data():
    declared = _declared_patterns()
    missing: list[str] = []

    for path in SRC.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix == ".py" or path.suffix in IGNORED_SUFFIXES:
            continue
        if IGNORED_PARTS & set(path.relative_to(SRC).parts):
            continue

        relative = path.relative_to(SRC)
        if len(relative.parts) < 2:
            # A loose file at the src root is not inside a package at all.
            continue
        package = relative.parts[0]
        patterns = declared.get(package, []) + declared.get("*", [])
        if not any(path.match(pattern) for pattern in patterns):
            missing.append(str(relative).replace("\\", "/"))

    assert not missing, (
        "These files live under api/src/ but are not declared in "
        "[tool.setuptools.package-data], so `pip install ./api` will not ship "
        "them and any runtime read of them fails only in the container: "
        f"{missing}"
    )


def test_the_check_above_is_not_vacuous():
    """It only means anything if there IS a data file under src/ for it to
    find. If the last one is ever deleted the test above starts passing for
    a reason unrelated to packaging, and this says so."""
    data_files = [
        path
        for path in SRC.rglob("*")
        if path.is_file()
        and path.suffix not in IGNORED_SUFFIXES
        and path.suffix != ".py"
        and not (IGNORED_PARTS & set(path.relative_to(SRC).parts))
        and len(path.relative_to(SRC).parts) >= 2
    ]
    assert data_files, "no non-Python files under api/src/ - the check above proves nothing"
