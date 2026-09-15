"""Model artifact fetch, verification and pinning (Phase 2 session 4,
Decision 3).

The download is exercised against a real HTTP server on localhost rather than
a mock, so the urllib path, the chunked copy, the temp-file rename and the
digest check are all genuinely run. The GitHub Release itself is exercised
separately in the session write-up - a test may not depend on a network
service.
"""

from __future__ import annotations

import hashlib
import http.server
import threading
from pathlib import Path

import pytest

from models.artifact import (
    ArtifactError,
    assert_artifact_url,
    ensure_artifact,
    sha256_file,
    split_digest,
)

PAYLOAD = b"not really a model, but it hashes like one" * 50
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture(scope="module")
def server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib's naming
            if self.path.endswith("missing.pkl"):
                self.send_error(404, "Not Found")
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)

        def log_message(self, *_args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


# --- assert_artifact_url: the regression that must stay impossible --------


def test_the_windows_path_the_registry_actually_wrote_is_rejected():
    """models/registry.py has been writing this shape since Phase 1. It
    resolves on one machine and no container.

    urlparse('C:\\\\x') yields scheme='c', so a naive scheme check would wave
    it straight through - which is why this case is pinned by name.
    """
    windows_path = (
        r"C:\Users\Aarav\Documents\Documents\SightScreen\api\data\models"
        r"\win_prob_2nd\winprob2-20260910.pkl"
    )
    with pytest.raises(ArtifactError, match="Windows filesystem path"):
        assert_artifact_url(windows_path)


@pytest.mark.parametrize(
    "value",
    [
        "/app/artifacts/winprob2.pkl",
        "api/data/models/win_prob_2nd/winprob2.pkl",
        "file:///app/winprob2.pkl",
        "",
        "   ",
    ],
)
def test_filesystem_paths_are_rejected(value):
    with pytest.raises(ArtifactError):
        assert_artifact_url(value)


def test_plain_http_is_rejected():
    with pytest.raises(ArtifactError, match="https"):
        assert_artifact_url("http://example.com/a.pkl")


def test_a_release_url_is_accepted():
    url = (
        "https://github.com/Aarav6000/SightScreen/releases/download/"
        "winprob2-20260910/winprob2-20260910.pkl#sha256=abc123"
    )
    assert assert_artifact_url(url) == url


def test_split_digest():
    bare, digest = split_digest("https://h/a.pkl#sha256=DEADBEEF")
    assert bare == "https://h/a.pkl"
    assert digest == "deadbeef"
    assert split_digest("https://h/a.pkl") == ("https://h/a.pkl", None)


# --- ensure_artifact ------------------------------------------------------
#
# The local test server speaks http, which assert_artifact_url correctly
# refuses. Rather than weaken that guard with a localhost loophole - a
# production hole opened for a test's convenience - these tests bypass the
# URL check, which has its own dedicated coverage above. What is under test
# here is the download, the digest and the cache.


@pytest.fixture()
def allow_http(monkeypatch):
    monkeypatch.setattr("models.artifact.assert_artifact_url", lambda value, **_: value)


def test_download_and_verify(server, tmp_path, allow_http):
    path = ensure_artifact(f"{server}/a.pkl#sha256={DIGEST}", tmp_path)
    assert path.read_bytes() == PAYLOAD
    assert sha256_file(path) == DIGEST


def test_refuses_an_artifact_with_no_digest(tmp_path):
    # deliberately NOT using allow_http: an https URL with no fragment
    with pytest.raises(ArtifactError, match="no '#sha256=' fragment"):
        ensure_artifact("https://example.com/a.pkl", tmp_path)


def test_digest_mismatch_refuses_and_leaves_nothing_behind(server, tmp_path, allow_http):
    wrong = "0" * 64
    with pytest.raises(ArtifactError, match="sha256 mismatch"):
        ensure_artifact(f"{server}/a.pkl#sha256={wrong}", tmp_path)
    assert list(tmp_path.iterdir()) == [], "a rejected download must not be cached"


def test_http_404_is_reported_with_the_private_repo_hint(server, tmp_path, allow_http):
    with pytest.raises(ArtifactError, match="404"):
        ensure_artifact(f"{server}/missing.pkl#sha256={DIGEST}", tmp_path)


def test_a_verified_cache_entry_is_reused_without_the_network(server, tmp_path, allow_http):
    """A restart during a GitHub outage must still serve."""
    url = f"{server}/a.pkl#sha256={DIGEST}"
    first = ensure_artifact(url, tmp_path)
    dead = "https://127.0.0.1:1/a.pkl#sha256=" + DIGEST
    second = ensure_artifact(dead, tmp_path, filename=first.name)
    assert second == first


def test_a_corrupt_cache_entry_is_replaced(server, tmp_path, allow_http):
    target = tmp_path / "a.pkl"
    target.write_bytes(b"corrupted")
    path = ensure_artifact(f"{server}/a.pkl#sha256={DIGEST}", tmp_path)
    assert path.read_bytes() == PAYLOAD


# --- the real artifact ----------------------------------------------------

REAL_ARTIFACT = (
    Path(__file__).resolve().parent.parent.parent
    / "api" / "data" / "models" / "win_prob_2nd" / "winprob2-20260910.pkl"
)


def test_the_registered_artifact_has_the_serving_shape():
    """The training-shape artifact uses 'isotonic' where the serving shape
    uses 'calibrator'; shipping the wrong one KeyErrors at the first
    prediction rather than at load."""
    if not REAL_ARTIFACT.exists():
        pytest.skip(f"{REAL_ARTIFACT} not present")
    import joblib

    artifact = joblib.load(REAL_ARTIFACT)
    assert {"booster", "calibrator", "feature_names"} <= set(artifact)
    assert "isotonic" not in artifact
