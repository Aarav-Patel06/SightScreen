"""The raw Cricsheet JSON of every match the daily job ingests, kept.

WHY. A re-parse - a column the loader did not read the first time, like the
result margin in migration 20260927000001 - otherwise means downloading
Cricsheet's full 147MB archive, and even that only works while Cricsheet
still publishes the file unchanged. The local corpus keeps its files on disk
(api/data/cricsheet); the GitHub runner has no disk that survives the run.

WHERE, AND WHY THERE. A private Supabase Storage bucket, gzipped, one object
per match (`<cricsheet_id>.json.gz`). Measured on the 313 files of the
30-day bundle: 5.2 KB mean compressed (88 KB median raw), so ~1,500 matches
a year is ~8 MB a year against the free plan's 1 GB of storage. Considered
and rejected:
  * Actions artifacts - retention is capped at 90 days, so a re-parse next
    season would find nothing.
  * Committing to the repository - ~130 MB a year of raw JSON in public git
    history, unremovable.
  * A GitHub release - free and permanent, but uploading needs
    `contents: write`, a token that can push code, on a job that otherwise
    only reads the repository.
The bucket needs only the Supabase secret key, which reaches nothing the
session-pooler URL the job already holds does not.

Upsert, so a re-run re-stores the same bytes and changes nothing.
"""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request

from db.env import require_env

BUCKET = "cricsheet-raw"


class RawStore:
    """Supabase Storage over its REST API - no client library for three calls."""

    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        self._url = (url or require_env("SUPABASE_URL")).rstrip("/")
        self._key = key or require_env("SUPABASE_SECRET_KEY")

    def _request(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None):
        headers = {"apikey": self._key, "Authorization": f"Bearer {self._key}"}
        if content_type:
            headers["Content-Type"] = content_type
        if method == "POST" and path.startswith("/storage/v1/object/"):
            headers["x-upsert"] = "true"
        request = urllib.request.Request(f"{self._url}{path}", data=body, method=method, headers=headers)
        return urllib.request.urlopen(request, timeout=60)

    def ensure_bucket(self) -> None:
        try:
            with self._request("GET", f"/storage/v1/bucket/{BUCKET}"):
                return
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 404):
                raise
        body = json.dumps({"id": BUCKET, "name": BUCKET, "public": False}).encode()
        with self._request("POST", "/storage/v1/bucket", body, "application/json"):
            pass

    def put(self, cricsheet_id: str, raw: bytes) -> None:
        with self._request(
            "POST",
            f"/storage/v1/object/{BUCKET}/{cricsheet_id}.json.gz",
            gzip.compress(raw, 9),
            "application/gzip",
        ):
            pass

    def get(self, cricsheet_id: str) -> bytes | None:
        try:
            with self._request("GET", f"/storage/v1/object/{BUCKET}/{cricsheet_id}.json.gz") as response:
                return gzip.decompress(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404):
                return None
            raise
