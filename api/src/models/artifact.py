r"""Fetching and verifying a trained model artifact (Phase 2 session 4).

SPEC.md section 2.1: "Trained model artifacts are committed to GitHub
Releases (or Supabase Storage) and pulled by the serving container." That was
one sentence and no implementation. registry.py:11-17 flagged the gap
explicitly - "Actually deploying a version ... is a Phase 2 deploy-time
action, not something a training script should do as a side effect - not
built here, flagged instead." This is that action.

Three properties, in order of how badly their absence would hurt:

1. artifact_path must be a URL. The registry has been writing
   C:\Users\Aarav\...\winprob2-20260910.pkl into it since Phase 1 - an
   absolute Windows path that no container could ever resolve, and which
   looked fine because only one machine ever read it. `assert_artifact_url`
   makes that regression impossible rather than merely fixed: it runs at
   publish time AND at container startup.

2. The bytes are verified, not assumed. "It downloaded" and "it downloaded
   the right bytes" are different claims. The digest travels with the URL as
   a '#sha256=' fragment, the convention PEP 503 uses for exactly this - so
   the location and the expected content cannot be updated independently.

3. A pinned version that disagrees with Supabase's active row refuses to
   serve. A container quietly serving a different model than the one the
   accuracy page attributes results to is worse than a container that is
   down, because nothing about it looks wrong.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

DOWNLOAD_TIMEOUT_SECONDS = 60.0
_CHUNK = 1 << 16


class ArtifactError(RuntimeError):
    """The artifact could not be located, fetched, or verified."""


class ModelVersionMismatch(RuntimeError):
    """This container is pinned to a different version than Supabase says is active."""


def assert_artifact_url(artifact_path: str, *, field: str = "artifact_path") -> str:
    r"""Reject anything that is not an https URL, loudly and specifically.

    Windows paths are the case worth naming: urlparse('C:\x') parses as
    scheme='c', so a naive "does it have a scheme" check would wave the exact
    value that has been in the database since Phase 1 straight through.
    """
    value = (artifact_path or "").strip()
    if not value:
        raise ArtifactError(f"{field} is empty - nothing to fetch.")

    parsed = urlparse(value)

    if len(parsed.scheme) == 1 and parsed.scheme.isalpha():
        raise ArtifactError(
            f"{field} is a Windows filesystem path ({value!r}), not a URL. This is the "
            "value models/registry.py wrote locally through Phase 1; it resolves on "
            "exactly one machine and on no container. Publish the artifact with "
            "`python -m models.publish_model_version` so this column holds a release URL."
        )
    if parsed.scheme in ("", "file"):
        raise ArtifactError(
            f"{field} is a filesystem path ({value!r}), not a URL. A serving container "
            "has no access to the training machine's disk (SPEC.md section 2.1)."
        )
    if parsed.scheme != "https":
        raise ArtifactError(
            f"{field} must be an https URL, got scheme {parsed.scheme!r} in {value!r}."
        )
    if not parsed.netloc:
        raise ArtifactError(f"{field} has no host: {value!r}")
    return value


def split_digest(artifact_url: str) -> tuple[str, str | None]:
    """Separate the fetch URL from its expected digest.

    'https://host/a.pkl#sha256=abc' -> ('https://host/a.pkl', 'abc')

    PEP 503's fragment convention. Keeping the digest in the URL means a
    release cannot be re-pointed without also restating which bytes are
    expected.
    """
    parsed = urlparse(artifact_url)
    digest = None
    if parsed.fragment:
        found = parse_qs(parsed.fragment).get("sha256")
        if found:
            digest = found[0].lower()
    bare = urlunparse(parsed._replace(fragment=""))
    return bare, digest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_artifact(artifact_url: str, cache_dir: Path, *, filename: str | None = None) -> Path:
    """Download (or reuse a cached copy of) the artifact and verify its digest.

    A cached file whose digest already matches is reused without a network
    call, so a container restart during a GitHub outage still serves.
    """
    assert_artifact_url(artifact_url)
    url, expected = split_digest(artifact_url)
    if not expected:
        raise ArtifactError(
            f"{url} carries no '#sha256=' fragment. Refusing to load an unverified "
            "artifact - publish it with models.publish_model_version, which adds one."
        )

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / (filename or Path(urlparse(url).path).name or "artifact.pkl")

    if target.exists():
        if sha256_file(target) == expected:
            return target
        target.unlink()  # a corrupt or stale cache entry is worse than no cache

    # Close mkstemp's descriptor immediately and reopen by path. Holding it
    # open across the request means that if urlopen raises, the cleanup
    # unlink below hits "file in use" on Windows and masks the real error
    # with a PermissionError. Found by the 404 test.
    handle_fd, tmp_name = tempfile.mkstemp(dir=cache_dir, suffix=".part")
    os.close(handle_fd)
    tmp_path = Path(tmp_name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "sightscreen-serving"})
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            with open(tmp_path, "wb") as handle:
                shutil.copyfileobj(response, handle)
    except urllib.error.HTTPError as exc:
        tmp_path.unlink(missing_ok=True)
        hint = (
            " The release asset may not exist yet, or the repository may be private - "
            "this download is unauthenticated by design."
            if exc.code in (403, 404)
            else ""
        )
        raise ArtifactError(
            f"downloading {url} failed: HTTP {exc.code} {exc.reason}.{hint}"
        ) from exc
    except urllib.error.URLError as exc:
        tmp_path.unlink(missing_ok=True)
        raise ArtifactError(f"downloading {url} failed: {exc.reason}") from exc

    actual = sha256_file(tmp_path)
    if actual != expected:
        tmp_path.unlink(missing_ok=True)
        raise ArtifactError(
            f"{url} sha256 mismatch: expected {expected}, got {actual}. The release asset "
            "is not the file that was published; refusing to load it."
        )
    tmp_path.replace(target)
    return target


def active_model_row(conn) -> tuple[str, str, str | None]:
    """(model_version, artifact_path, notes) for Supabase's active model."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT model_version, artifact_path, notes FROM model_versions "
            "WHERE is_active ORDER BY trained_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        raise ArtifactError(
            "no active row in model_versions. A serving container cannot choose a model "
            "for itself - publish one with `python -m models.publish_model_version`."
        )
    return row[0], row[1], row[2]


def resolve_pinned_artifact(conn, pinned_version: str, cache_dir: Path) -> dict:
    """Startup path: check the pin, verify the URL, fetch, verify the bytes.

    Returns the loaded artifact plus the provenance a /health response needs.
    Raises rather than degrading - see the module docstring.
    """
    import joblib

    version, artifact_path, notes = active_model_row(conn)
    if version != pinned_version:
        raise ModelVersionMismatch(
            f"this container is pinned to MODEL_VERSION={pinned_version!r} but Supabase's "
            f"active model is {version!r}. Refusing to serve: predictions would be written "
            f"under one version and attributed to another. Update MODEL_VERSION or promote "
            f"the intended version."
        )
    assert_artifact_url(artifact_path)
    path = ensure_artifact(artifact_path, cache_dir, filename=f"{version}.pkl")
    artifact = joblib.load(path)

    missing = [key for key in ("booster", "calibrator", "feature_names") if key not in artifact]
    if missing:
        raise ArtifactError(
            f"artifact for {version} is missing {missing}. An older training-shape artifact "
            "uses 'isotonic' instead of 'calibrator' and cannot serve."
        )
    return {
        "artifact": artifact,
        "model_version": version,
        "sha256": sha256_file(path),
        "feature_names": list(artifact["feature_names"]),
        "notes": notes,
        "path": str(path),
    }
