"""The Full Member flag, asserted against the canonical twelve rather than a count.

Local-only, like the other tests in this directory: it needs live credentials
to both databases.

    pytest tests/db/test_full_member.py -v

WHY A SET AND NOT A COUNT. The migration asserts `count(*) = 11` because that
is what it can check at apply time, but a count is a weak claim: eleven wrong
teams would satisfy it. More importantly the count is *expected to change* -
Afghanistan is a genuine ICC Full Member with no row in this corpus, so the
day it is ingested the correct answer becomes twelve and a count assertion
becomes a false failure that someone will "fix" by editing the number.

So this asserts the two directions that stay true either way:

  1. every flagged team is one of the canonical twelve  (no over-flagging)
  2. every canonical name PRESENT in the table is flagged (no under-flagging)

Afghanistan's absence then shows up as a recorded fact rather than a
discrepancy, and if it is ever ingested this test starts requiring it to be
flagged without any edit.
"""

from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

# The twelve ICC Full Members. The same list the migration uses; duplicated
# here on purpose, because a test that imports its expectation from the thing
# under test asserts only that a copy succeeded.
FULL_MEMBERS = frozenset(
    {
        "Afghanistan",
        "Australia",
        "Bangladesh",
        "England",
        "India",
        "Ireland",
        "New Zealand",
        "Pakistan",
        "South Africa",
        "Sri Lanka",
        "West Indies",
        "Zimbabwe",
    }
)

# WHY AFGHANISTAN IS ABSENT. Not a spelling variant, not an entity-resolution
# split, not an ingestion bug - all three were checked on 2026-09-24 and all
# three are ruled out. `teams` has no row matching `%afg%`; `team_aliases` has
# none across its 354 rows; and the only `%ghan%` match in the whole table is
# Ghana.
#
# It is an upstream publishing policy, stated in the first paragraph of the
# Cricsheet archive's own README.txt:
#
#     "A further 374 matches have been withheld due to either featuring the
#      Afghanistan men's team or being played in the Afghanistan Premier
#      League, due to the Cricsheet policy to no longer feature matches
#      involving Afghanistan men or played in Afghanistan Premier League"
#      - https://cricsheet.org/withheld-matches
#
# Confirmed against the raw archive rather than taken on trust: of 22,734
# match files, `"Afghanistan"` appears as a team in zero of them, while the
# same search finds Zimbabwe in 646 and Ireland in 520.
#
# So this cannot be fixed by a name change or a re-ingest, and there are no
# Afghanistan matches to replay. If Cricsheet ever reverses the policy, 374
# matches arrive at once and this tripwire fires - which is the point of
# asserting it rather than leaving the absence as a silent assumption.
ABSENT_FROM_CORPUS = frozenset({"Afghanistan"})


def _urls() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    urls = {
        "local": env.get("LOCAL_DATABASE_URL"),
        "supabase": env.get("SUPABASE_SESSION_POOLER_URL"),
    }
    missing = [k for k, v in urls.items() if not v]
    if missing:
        pytest.skip(f"no credentials for {', '.join(missing)} in {ENV_PATH}")
    return urls


@pytest.fixture(scope="module")
def flagged() -> dict[str, tuple[set[str], set[str]]]:
    """(flagged names, all canonical names present) per database."""
    out = {}
    for label, url in _urls().items():
        with psycopg.connect(url, connect_timeout=30) as conn, conn.cursor() as cur:
            cur.execute("SELECT name FROM teams WHERE full_member")
            flagged_names = {r[0] for r in cur.fetchall()}
            cur.execute(
                "SELECT name FROM teams WHERE name = ANY(%s)", (sorted(FULL_MEMBERS),)
            )
            present = {r[0] for r in cur.fetchall()}
        out[label] = (flagged_names, present)
    return out


@pytest.mark.parametrize("db", ["local", "supabase"])
def test_no_team_is_flagged_that_should_not_be(db, flagged):
    flagged_names, _present = flagged[db]
    extra = flagged_names - FULL_MEMBERS
    assert not extra, (
        f"{db}: flagged teams that are not ICC Full Members: {sorted(extra)}. "
        f"Africa XI, Asia XI and ICC World XI are selections, not members."
    )


@pytest.mark.parametrize("db", ["local", "supabase"])
def test_every_full_member_present_is_flagged(db, flagged):
    flagged_names, present = flagged[db]
    missed = present - flagged_names
    assert not missed, f"{db}: present in teams but not flagged: {sorted(missed)}"


@pytest.mark.parametrize("db", ["local", "supabase"])
def test_the_recorded_absence_is_still_true(db, flagged):
    """Not a requirement - a tripwire on a documented fact.

    If this fails, Afghanistan has been ingested. That is good news, but the
    migration's comment and the count in it are now stale, and the landing
    page's Full Member ordering silently gained a team.
    """
    _flagged_names, present = flagged[db]
    assert FULL_MEMBERS - present == ABSENT_FROM_CORPUS, (
        f"{db}: the set of Full Members missing from this corpus has changed to "
        f"{sorted(FULL_MEMBERS - present)}. If Afghanistan has appeared, Cricsheet "
        f"has reversed its withholding policy and ~374 matches are now available - "
        f"re-run the Full Member replay for them. Update ABSENT_FROM_CORPUS and the "
        f"comment in 20260924000003_teams_full_member.sql together."
    )


def test_both_databases_agree(flagged):
    """The sync tuple in ingest/sync_reference_tables.py is the failure mode.

    A column absent from that tuple is never copied, so the flag would be
    correct locally and FALSE on Supabase - correct-looking on the database
    nobody queries by hand.
    """
    local, supabase = flagged["local"][0], flagged["supabase"][0]
    assert local == supabase, (
        f"flagged sets differ: local-only {sorted(local - supabase)}, "
        f"supabase-only {sorted(supabase - local)}. Check that 'full_member' is "
        f"in the teams tuple in api/src/ingest/sync_reference_tables.py."
    )
