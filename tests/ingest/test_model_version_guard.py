"""The mid-run model-version guard, proven to fire.

WHY THIS FILE EXISTS. `confirm_model_version` is called once per match inside
a 248-match replay. It exists because `/accuracy` groups calibration BY
`model_version`: a log split across two of them is not a mixed-up number, it is
a reliability diagram computed from two different models and labelled as one.
The docstring in replay_log says exactly that - "a log split across two of them
is a reliability diagram" - and then the guard had no test.

Before 2026-09-25, `ModelVersionChanged` appeared twice in api/src and zero
times in tests/. Only ever observed passing, which is the state every safeguard
in this repository was in on the day it turned out not to work.

No database, so this is provable in CI where Supabase is unreachable.
"""

import pytest

from ingest.replay_log import ModelVersionChanged, confirm_model_version
from models.artifact import ArtifactError

ACTIVE = "winprob2-20260910"


class _Cursor:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, _sql, _params=None):
        pass

    def fetchone(self):
        return self._row


class _Conn:
    """Answers the model_versions lookup with one row, or None."""

    def __init__(self, version):
        self._row = None if version is None else (version, "artifacts/m.joblib", None)

    def cursor(self):
        return _Cursor(self._row)


def test_an_unchanged_version_passes():
    confirm_model_version(_Conn(ACTIVE), ACTIVE)  # must not raise


def test_a_version_that_moved_mid_run_halts():
    """The failure. A publish landing between match 40 and match 41 would
    otherwise leave 40 matches under one version and 208 under another, with
    nothing in the data saying where the seam is."""
    with pytest.raises(ModelVersionChanged) as caught:
        confirm_model_version(_Conn("winprob2-20261001"), ACTIVE)

    message = str(caught.value)
    assert ACTIVE in message and "winprob2-20261001" in message, "name both versions"
    assert "Halting" in message


def test_the_message_says_what_happens_to_the_rows_already_written():
    """The operator's first question on seeing this is 'did I just corrupt the
    log?'. Answering it in the exception is the difference between a halt and
    a panic."""
    with pytest.raises(ModelVersionChanged) as caught:
        confirm_model_version(_Conn("other"), ACTIVE)
    assert "are kept" in str(caught.value)
    assert "refuse to mix" in str(caught.value)


def test_no_active_model_is_a_different_error_and_is_not_swallowed():
    """An empty model_versions table must not be reported as a version change
    - the fix is completely different, and a guard that reports the wrong
    failure sends someone to the wrong file."""
    with pytest.raises(ArtifactError, match="no active row"):
        confirm_model_version(_Conn(None), ACTIVE)
