"""The target-URL validator and the load stamp's source string.

These are the two pure functions behind scripts/setup-replica.ps1, and they
exist because provisioning the replica by hand cost an hour to three paste
errors, not to three bugs: a database name truncated to `/ra`, a trailing
newline off the clipboard, and Railway's internal host - which resolves only
inside Railway's network and so hangs rather than refusing.

A paste error is only catchable once, at the boundary, which is why the
validator is tested here rather than exercised only by the PowerShell script
that calls it. Each case below asserts on the SPECIFIC message, not merely
that something was raised: "could not connect" is the failure mode these
replace, and a validator that rejects everything with one vague sentence has
reproduced it one layer up.
"""

from __future__ import annotations

import pytest

from agent_tools.replica import (
    LOCAL_DB_NAME,
    REPLICA_DATABASES,
    _sanitise_source,
    validate_target_url,
)

GOOD = "postgresql://postgres:s3cret@localhost:54321/railway"


def test_a_well_formed_target_url_is_returned_unchanged():
    assert validate_target_url(GOOD) == GOOD


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_both_local_hosts_are_accepted(host):
    validate_target_url(f"postgresql://postgres:s3cret@{host}:54321/railway")


@pytest.mark.parametrize("scheme", ["postgresql", "postgres"])
def test_both_postgres_schemes_are_accepted(scheme):
    validate_target_url(f"{scheme}://postgres:s3cret@localhost:54321/railway")


# --- the three mistakes that actually happened ---------------------------

def test_a_truncated_database_name_is_named_as_truncated():
    """The original hour: `.../ra` instead of `.../railway`.

    `ra` is a prefix of `railway`, so the message must say "cut short" rather
    than "wrong database" - the two have different fixes, and the second
    sends you looking for a database that was never there.
    """
    with pytest.raises(SystemExit) as err:
        validate_target_url("postgresql://postgres:s3cret@localhost:54321/ra")
    message = str(err.value)
    assert "'ra'" in message
    assert "cut short" in message


def test_a_database_name_that_is_not_a_prefix_gets_the_plain_message():
    with pytest.raises(SystemExit) as err:
        validate_target_url("postgresql://postgres:s3cret@localhost:54321/postgres")
    assert "cut short" not in str(err.value)
    assert "'postgres'" in str(err.value) and "not one of" in str(err.value)


@pytest.mark.parametrize("suffix", ["\n", " ", "\r\n", "\t"])
def test_surrounding_whitespace_is_rejected_not_trimmed(suffix):
    """Standing rule 11: round-tripping is not sanitising.

    Trimming would work and would also mean the string this script uses and
    the string in the clipboard are no longer the same string.
    """
    with pytest.raises(SystemExit) as err:
        validate_target_url(GOOD + suffix)
    assert "whitespace" in str(err.value)


def test_leading_whitespace_is_rejected_too():
    with pytest.raises(SystemExit) as err:
        validate_target_url(" " + GOOD)
    assert "whitespace" in str(err.value)


def test_the_railway_internal_host_is_named_as_internal():
    with pytest.raises(SystemExit) as err:
        validate_target_url("postgresql://postgres:s3cret@postgres.railway.internal:5432/railway")
    assert "INTERNAL" in str(err.value)


def test_the_public_tcp_proxy_is_distinguished_from_the_tunnel():
    """A different mistake with a different fix: this host WORKS, which is
    why a generic "wrong host" message would send you debugging the tunnel."""
    with pytest.raises(SystemExit) as err:
        validate_target_url("postgresql://postgres:s3cret@junction.proxy.rlwy.net:41234/railway")
    assert "public TCP proxy" in str(err.value)


# --- the rest of the shape ------------------------------------------------

def test_an_unknown_host_is_rejected_by_the_allowlist():
    """Allowlist, not a blocklist, for the same reason config.py's pooler
    check is one - see _require_pooler_host. Rejecting the two Railway hosts
    by name would catch two mistakes; requiring a local host catches the
    class."""
    with pytest.raises(SystemExit) as err:
        validate_target_url("postgresql://postgres:s3cret@10.0.0.7:54321/railway")
    assert "not a local host" in str(err.value)


def test_a_missing_port_is_rejected_because_the_tunnel_port_moves():
    with pytest.raises(SystemExit) as err:
        validate_target_url("postgresql://postgres:s3cret@localhost/railway")
    assert "no port" in str(err.value)


def test_a_missing_database_name_is_rejected():
    with pytest.raises(SystemExit) as err:
        validate_target_url("postgresql://postgres:s3cret@localhost:54321")
    assert "no database name" in str(err.value)


def test_a_missing_username_is_rejected_before_psycopg_guesses_one():
    with pytest.raises(SystemExit) as err:
        validate_target_url("postgresql://localhost:54321/railway")
    assert "no username" in str(err.value)


def test_a_missing_password_is_rejected():
    with pytest.raises(SystemExit) as err:
        validate_target_url("postgresql://postgres@localhost:54321/railway")
    assert "no password" in str(err.value)


def test_a_non_postgres_scheme_is_rejected():
    with pytest.raises(SystemExit) as err:
        validate_target_url("https://postgres:s3cret@localhost:54321/railway")
    assert "scheme" in str(err.value)


def test_an_empty_url_is_rejected():
    with pytest.raises(SystemExit) as err:
        validate_target_url("")
    assert "empty" in str(err.value)


# --- both supported targets, and the one that must never be one ----------

@pytest.mark.parametrize("database", REPLICA_DATABASES)
def test_every_allowlisted_replica_database_is_accepted(database):
    """A tunnelled Railway target and the local docker target take the same
    code path, which is the point of building the agent against docker while
    the hosting decision is deferred."""
    validate_target_url(f"postgresql://postgres:s3cret@localhost:5433/{database}")


def test_the_corpus_itself_is_refused_as_a_target():
    """The expensive one, and the only rejection here that has never happened.

    While the target was a tunnel and the source was local they could not be
    confused. Pointing this at local docker puts both on localhost:5433 one
    database name apart, and --load TRUNCATEs its target before copying: this
    paste would destroy the Phase 0 bulk load.
    """
    with pytest.raises(SystemExit) as err:
        validate_target_url(f"postgresql://postgres:s3cret@localhost:5433/{LOCAL_DB_NAME}")
    message = str(err.value)
    assert "CORPUS" in message
    assert "truncates" in message


def test_the_corpus_rejection_does_not_rely_on_a_hardcoded_name():
    """LOCAL_DB_NAME comes from db/defaults.py, the single source of truth the
    loader and ci.yml already share. A second spelling here could drift."""
    assert LOCAL_DB_NAME not in REPLICA_DATABASES


# --- the load stamp's source string --------------------------------------

def test_the_stamped_source_drops_the_credentials():
    """The stamp is a row in a database, and rows get dumped, backed up and
    pasted into bug reports."""
    stamped = _sanitise_source("postgresql://postgres:hunter2@localhost:5433/cricket_training?sslmode=disable")
    assert stamped == "localhost:5433/cricket_training"
    assert "hunter2" not in stamped
