"""Alias ids: three allocators, three bands, no overlap - and a sync that
never overwrites an alias it did not create.

Three processes create alias rows, each from its own counter:
  * the corpus (local serial sequences)          ids 1 - 499,999
  * the live worker on Supabase (Supabase's       ids 500,000 - 999,999
    serial sequences, via entity_resolution._create_alias on a fuzzy match)
  * the daily Cricsheet job (explicit ids,        ids >= 1,000,000
    promote_entities)
and `ingest.sync_reference_tables` copies the corpus's rows onto Supabase by
alias_id. Before this, the corpus and the live worker drew from the same
range: on 2026-09-27 Supabase held live-worker venue alias 543 while the
corpus's venue counter stood at 542, so the next corpus alias plus a sync
would have silently replaced it.

Scratch databases on the corpus's server stand in for the corpus, Supabase
and the daily job's stage. Skips without npx or LOCAL_DATABASE_URL.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NPX = shutil.which("npx")

VENUE_ALIAS_COLUMNS = ("alias_id", "venue_id", "source", "source_name", "source_id")


def _url(name: str) -> str:
    base = os.environ.get("LOCAL_DATABASE_URL") or dotenv_values(REPO_ROOT / "api" / ".env").get(
        "LOCAL_DATABASE_URL"
    )
    if not base or NPX is None:
        pytest.skip("LOCAL_DATABASE_URL and npx are needed")
    parts = urlsplit(base)
    url = urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))
    with psycopg.connect(admin, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is None:
            conn.execute(f'CREATE DATABASE "{name}"')
    subprocess.run([NPX, "supabase", "db", "push", "--db-url", url], cwd=REPO_ROOT, check=True, capture_output=True)
    with psycopg.connect(url, autocommit=True) as conn:
        tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
        conn.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
    return url


@pytest.fixture()
def corpus():
    with psycopg.connect(_url("cricket_alias_corpus_test")) as conn:
        yield conn


@pytest.fixture()
def supabase():
    with psycopg.connect(_url("cricket_alias_supabase_test")) as conn:
        yield conn


@pytest.fixture()
def stage():
    with psycopg.connect(_url("cricket_alias_stage_test")) as conn:
        yield conn


def _venue(conn, venue_id: int, name: str) -> None:
    conn.execute("INSERT INTO venues (venue_id, name) VALUES (%s, %s)", (venue_id, name))


def _alias(conn, alias_id: int, venue_id: int, source: str, name: str) -> None:
    conn.execute(
        "INSERT INTO venue_aliases (alias_id, venue_id, source, source_name) VALUES (%s, %s, %s, %s)",
        (alias_id, venue_id, source, name),
    )


def _seed_like_2026_09_27(corpus, supabase) -> None:
    """Both databases as they stood: the corpus's venue aliases up to 542,
    Supabase with those plus the live worker's 543."""
    for conn in (corpus, supabase):
        _venue(conn, 25, "Kensington Oval, Bridgetown")
        _venue(conn, 26, "Arnos Vale Ground")
        _alias(conn, 542, 26, "cricsheet", "Arnos Vale Ground")
        conn.execute("SELECT setval(pg_get_serial_sequence('venue_aliases', 'alias_id'), 542)")
    _alias(supabase, 543, 25, "cricketdata", "Kensington Oval")
    supabase.execute("SELECT setval(pg_get_serial_sequence('venue_aliases', 'alias_id'), 543)")
    corpus.commit()
    supabase.commit()


def _row(conn, alias_id: int):
    return conn.execute(
        "SELECT venue_id, source, source_name FROM venue_aliases WHERE alias_id = %s", (alias_id,)
    ).fetchone()


def test_an_alias_only_on_supabase_survives_a_sync(corpus, supabase):
    """The corpus allocates 543 for a different alias - the collision that was
    one venue away - and syncs. The live worker's 543 must still be there,
    and the sync must say why it stopped rather than overwrite it."""
    from ingest.sync_reference_tables import AliasCollision, sync_table

    _seed_like_2026_09_27(corpus, supabase)
    _alias(corpus, 543, 26, "cricsheet", "Arnos Vale, Kingstown")
    corpus.commit()

    with pytest.raises(AliasCollision):
        sync_table(corpus, supabase, "venue_aliases", "alias_id", VENUE_ALIAS_COLUMNS)
    supabase.rollback()
    assert _row(supabase, 543) == (25, "cricketdata", "Kensington Oval")


def test_an_alias_only_on_supabase_survives_a_sync_with_no_clash(corpus, supabase):
    from ingest.sync_reference_tables import sync_table

    _seed_like_2026_09_27(corpus, supabase)
    sync_table(corpus, supabase, "venue_aliases", "alias_id", VENUE_ALIAS_COLUMNS)
    assert _row(supabase, 543) == (25, "cricketdata", "Kensington Oval")


def test_the_next_alias_from_each_creator_lands_in_its_own_range(corpus, supabase, stage):
    """After a sync, the corpus, the live worker and the daily job each
    allocate in their own band, and none can reach another's."""
    from db.defaults import SUPABASE_ID_FLOOR, SUPABASE_LIVE_ALIAS_FLOOR
    from ingest.daily_cricsheet import VENUE_ENTITY, promote_entities
    from ingest.entity_resolution import VENUE_CONFIG, _create_alias
    from ingest.sync_reference_tables import sync_table

    _seed_like_2026_09_27(corpus, supabase)
    sync_table(corpus, supabase, "venue_aliases", "alias_id", VENUE_ALIAS_COLUMNS)

    # The corpus: its next alias, as the loader would create it.
    _create_alias(corpus, VENUE_CONFIG, "cricsheet", "Arnos Vale, Kingstown", None, 26)
    corpus.commit()
    corpus_id = corpus.execute(
        "SELECT alias_id FROM venue_aliases WHERE source_name = 'Arnos Vale, Kingstown'"
    ).fetchone()[0]

    # The live worker: a fuzzy match recorded on Supabase.
    _create_alias(supabase, VENUE_CONFIG, "cricketdata", "Kensington Oval, Barbados", None, 25)
    supabase.commit()
    live_id = supabase.execute(
        "SELECT alias_id FROM venue_aliases WHERE source_name = 'Kensington Oval, Barbados'"
    ).fetchone()[0]

    # The daily job: a first-seen venue and its alias, promoted from stage.
    _venue(stage, 900, "Gahanga B Ground, Rwanda")
    _alias(stage, 7000, 900, "cricsheet", "Gahanga B Ground, Rwanda")
    stage.commit()
    promote_entities(stage, supabase, VENUE_ENTITY, {900})
    daily_id = supabase.execute(
        "SELECT alias_id FROM venue_aliases WHERE source_name = 'Gahanga B Ground, Rwanda'"
    ).fetchone()[0]

    assert 543 < corpus_id < SUPABASE_LIVE_ALIAS_FLOOR, corpus_id  # past the live worker's legacy 543
    assert SUPABASE_LIVE_ALIAS_FLOOR <= live_id < SUPABASE_ID_FLOOR, live_id
    assert daily_id >= SUPABASE_ID_FLOOR, daily_id
