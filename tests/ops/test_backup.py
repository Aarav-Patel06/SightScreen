"""The backup's refusal paths.

Everything here tests a way the job could report success while the backup is
useless, because that is the only failure mode that matters: a backup that
fails loudly gets fixed the next morning, and one that succeeds quietly is
discovered the day it is needed.

No database. The dump checks read a file and the comparison is pure.
"""

from __future__ import annotations

import pytest

from ops.backup import (
    COMPLETION_MARKER,
    MIN_DUMP_BYTES,
    BackupInvalid,
    check_dump_file,
    compare,
)


def _write(path, body: str):
    path.write_text(body, encoding="utf-8")
    return path


def _valid_dump(tmp_path):
    body = "-- header\n" + ("INSERT INTO t VALUES (1);\n" * 20_000) + COMPLETION_MARKER + "\n"
    return _write(tmp_path / "ok.sql", body)


# --- the file ------------------------------------------------------------


def test_a_complete_dump_passes(tmp_path):
    assert check_dump_file(_valid_dump(tmp_path)) > MIN_DUMP_BYTES


def test_a_missing_dump_is_fatal(tmp_path):
    with pytest.raises(BackupInvalid, match="no dump at"):
        check_dump_file(tmp_path / "absent.sql")


def test_an_empty_dump_is_fatal(tmp_path):
    """The specific thing the job must never report success on. A zero-byte
    backup is worse than no backup, because the green tick says otherwise."""
    with pytest.raises(BackupInvalid, match="under the"):
        check_dump_file(_write(tmp_path / "empty.sql", ""))


def test_a_dump_that_is_really_an_error_message_is_fatal(tmp_path):
    """pg_dump writing a connection error to stdout produces a small,
    syntactically innocent file."""
    with pytest.raises(BackupInvalid, match="under the"):
        check_dump_file(_write(tmp_path / "err.sql", "pg_dump: error: connection failed\n"))


def test_a_truncated_dump_is_fatal(tmp_path):
    """Big enough to clear the size floor, missing the completion marker.

    This is the one that would otherwise get through: a dropped connection
    partway gives a large file of valid SQL that restores without error and
    is missing rows nobody counted.
    """
    body = "-- header\n" + ("INSERT INTO t VALUES (1);\n" * 20_000)
    with pytest.raises(BackupInvalid, match="truncated"):
        check_dump_file(_write(tmp_path / "cut.sql", body))


# --- the comparison ------------------------------------------------------


def test_an_exact_match_passes():
    digests = {"predictions": (12_617, "aaa"), "matches": (107, "bbb"), "model_versions": (1, "c")}
    compare(digests, dict(digests))


def test_a_short_restore_is_fatal():
    source = {"predictions": (12_617, "aaa"), "matches": (107, "bbb"), "model_versions": (1, "c")}
    restored = {**source, "predictions": (12_000, "zzz")}

    with pytest.raises(BackupInvalid, match="12,617 rows in source, 12,000 restored"):
        compare(source, restored)


def test_the_same_count_with_different_content_is_fatal():
    """The case a row-count check alone would pass, and the reason the digest
    exists at all."""
    source = {"predictions": (12_617, "aaa"), "matches": (107, "bbb"), "model_versions": (1, "c")}
    restored = {**source, "matches": (107, "DIFFERENT")}

    with pytest.raises(BackupInvalid, match="content digest differs"):
        compare(source, restored)


def test_a_table_missing_from_the_restore_is_fatal():
    source = {"predictions": (1, "a"), "matches": (1, "b"), "model_versions": (1, "c")}
    restored = {"predictions": (1, "a"), "model_versions": (1, "c")}

    with pytest.raises(BackupInvalid, match="absent after restore: matches"):
        compare(source, restored)


def test_an_empty_source_table_is_fatal_even_when_the_restore_matches():
    """Both sides agreeing on zero is a perfect restore of nothing. It means
    the dump ran against the wrong database, and the row-for-row comparison
    would otherwise call it a success."""
    digests = {"predictions": (0, "empty"), "matches": (107, "b"), "model_versions": (1, "c")}

    with pytest.raises(BackupInvalid, match="predictions is empty in the SOURCE"):
        compare(digests, dict(digests))


def test_every_problem_is_reported_not_just_the_first():
    """A job that fails on the first fault makes you run it again to find the
    second. These runs are nightly and unattended."""
    source = {"predictions": (10, "a"), "matches": (107, "b"), "model_versions": (1, "c")}
    restored = {"predictions": (9, "z"), "matches": (100, "y"), "model_versions": (1, "c")}

    with pytest.raises(BackupInvalid) as caught:
        compare(source, restored)

    assert "predictions" in str(caught.value)
    assert "matches" in str(caught.value)


def test_the_irreproducible_tables_are_the_ones_guarded():
    """Named explicitly so a future edit that drops one has to argue with a
    test. These three cannot be regenerated from the corpus: live predictions
    predate their outcomes, live-worker match rows have no corpus
    counterpart, and the model registry is the pin everything else cites."""
    from ops.backup import MUST_NOT_BE_EMPTY

    assert set(MUST_NOT_BE_EMPTY) == {"predictions", "matches", "model_versions"}
