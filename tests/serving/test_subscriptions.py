"""Which matches the worker subscribes to, and whether it can afford them
(session 3).

The worker picks from CricketData's fixture list each hour. A pick is
subscribed only if the provider's own remaining count, less a reserve,
covers every subscribed match to its end. A started match is never dropped
for a bigger one. The quota is read from the provider's responses, never
counted: before any response the worker subscribes to nothing.

No database, no network: the fixture list and quota are the provider's shape,
served by a fake.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ingest.cricketdata import (
    CALLS_TO_FINISH,
    RESERVE_CALLS,
    InningsSnapshot,
    LiveBudget,
    MatchSnapshot,
)
from ingest.match_pick import Team
from serving.live_loop import PRE_START_LEAD, Subscriptions

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)
ODI = sum(CALLS_TO_FINISH["ODI"])
T20 = sum(CALLS_TO_FINISH["T20"])

TEAMS = {"India": 1, "West Indies": 2, "Australia": 3, "South Africa": 4, "Nepal": 5, "Oman": 6}


def _resolve(name):
    team_id = TEAMS.get(name)
    return None if team_id is None else Team(team_id, full_member=name not in ("Nepal", "Oman"))


def _row(id_, t1, t2, start, match_type="odi", ms="fixture", series="West Indies tour of India, 2026"):
    return {
        "id": id_, "dateTimeGMT": start.strftime("%Y-%m-%dT%H:%M:%S"), "matchType": match_type,
        "ms": ms, "series": series, "t1": f"{t1} [X]", "t2": f"{t2} [Y]", "status": "",
    }


class _Client:
    """The fixture list, the provider's counter, and what is being tracked."""

    def __init__(self, rows, hits_today: int | None, clock=lambda: NOW):
        self.rows = rows
        self.hits_today = hits_today
        self.budget = LiveBudget(clock=clock)
        self.subscribed = None
        self.snapshots: dict[str, MatchSnapshot] = {}
        self.fixture_calls = 0

    def list_fixtures(self):
        self.fixture_calls += 1
        if self.hits_today is not None:
            self.budget.observe({"info": {"hitsToday": self.hits_today, "hitsLimit": 2000}})
        return self.rows

    def snapshot_of(self, provider_id):
        return self.snapshots.get(provider_id)


def _started(provider_id, *, ended=False):
    return MatchSnapshot(
        provider_id=provider_id, name="", format="ODI", status="", venue="", teams=("India", "West Indies"),
        innings=(InningsSnapshot(40, 1, 60, "India"),), started=True, ended=ended,
        reduced_overs=None, dls_target=None, observed_at=NOW,
    )


def _subs():
    return Subscriptions(_resolve, log=lambda _m: None)


def test_before_any_response_nothing_is_subscribed():
    client = _Client([_row("odi", "India", "West Indies", NOW + timedelta(hours=2))], hits_today=None)
    subs = _subs()
    subs.check(client, NOW)
    assert subs.provider_ids == set()


def test_a_low_quota_refuses_an_odi_but_takes_a_t20():
    hits = 2000 - RESERVE_CALLS - T20  # exactly a T20's worth left above the reserve
    start = NOW + timedelta(hours=2)
    odi = _Client([_row("odi", "India", "West Indies", start)], hits_today=hits)
    t20 = _Client([_row("t20", "India", "West Indies", start, match_type="t20")], hits_today=hits)

    refused, taken = _subs(), _subs()
    record = refused.check(odi, NOW)
    taken.check(t20, NOW)

    assert refused.provider_ids == set()
    assert record["refused"][0]["provider_id"] == "odi"
    assert "reserve" in record["refused"][0]["reason"]
    assert taken.provider_ids == {"t20"}


def test_two_odis_do_not_fit_one_day_but_an_odi_and_a_t20_do():
    early, late = NOW + timedelta(hours=2), NOW + timedelta(hours=9)
    two_odis = _Client(
        [_row("odi-1", "India", "West Indies", early),
         _row("odi-2", "Australia", "South Africa", late, series="Australia tour of South Africa, 2026")],
        hits_today=0,
    )
    odi_t20 = _Client(
        [_row("odi-1", "India", "West Indies", early),
         _row("t20", "Australia", "South Africa", late, match_type="t20",
              series="Australia tour of South Africa, 2026")],
        hits_today=0,
    )
    assert 2 * ODI > 2000 - RESERVE_CALLS >= ODI + T20, "the arithmetic the test relies on"

    a, b = _subs(), _subs()
    a.check(two_odis, NOW)
    b.check(odi_t20, NOW)

    assert a.provider_ids == {"odi-2"}, "the bigger-ranked (latest start) ODI is kept"
    assert b.provider_ids == {"odi-1", "t20"}


def test_at_most_two_matches_are_subscribed():
    rows = [
        _row(f"t20-{i}", "India", "West Indies", NOW + timedelta(hours=i + 1), match_type="t20")
        for i in range(3)
    ]
    subs = _subs()
    subs.check(_Client(rows, hits_today=0), NOW)
    assert len(subs.provider_ids) == 2


def test_a_started_match_is_never_dropped_for_a_bigger_one():
    client = _Client(
        [_row("assoc", "Nepal", "Oman", NOW - timedelta(hours=1), ms="live", series="Oman tour of Nepal")],
        hits_today=0,
    )
    subs = _subs()
    subs.check(client, NOW)
    assert subs.provider_ids == {"assoc"}
    client.snapshots["assoc"] = _started("assoc")

    # Later a Full Member ODI appears, and the quota now fits only one match.
    client.rows = client.rows + [_row("full", "India", "West Indies", NOW + timedelta(hours=2))]
    client.hits_today = 2000 - RESERVE_CALLS - ODI
    subs.check(client, NOW + timedelta(hours=1))

    assert subs.provider_ids == {"assoc"}


def test_an_unstarted_pick_gives_way_to_a_bigger_one():
    client = _Client(
        [_row("assoc", "Nepal", "Oman", NOW + timedelta(hours=3), series="Oman tour of Nepal")],
        hits_today=2000 - RESERVE_CALLS - ODI,
    )
    subs = _subs()
    subs.check(client, NOW)
    client.rows = client.rows + [_row("full", "India", "West Indies", NOW + timedelta(hours=2))]
    subs.check(client, NOW + timedelta(hours=1))

    assert subs.provider_ids == {"full"}


def test_a_decision_is_recorded_only_when_it_changes():
    client = _Client([_row("odi", "India", "West Indies", NOW + timedelta(hours=2))], hits_today=0)
    subs = _subs()
    first = subs.check(client, NOW)
    second = subs.check(client, NOW + timedelta(hours=1))

    assert first["picked"][0]["provider_id"] == "odi"
    assert first["picked"][0]["projected_calls"] == ODI
    assert first["hits_today"] == 0 and first["reserve"] == RESERVE_CALLS
    assert second is None


def test_the_client_tracks_only_what_is_subscribed():
    client = _Client([_row("odi", "India", "West Indies", NOW + timedelta(hours=2))], hits_today=0)
    subs = _subs()
    subs.check(client, NOW)
    assert client.subscribed == {"odi"}


def test_polling_starts_shortly_before_the_scheduled_start():
    start = NOW + timedelta(hours=2)
    client = _Client([_row("odi", "India", "West Indies", start)], hits_today=0)
    subs = _subs()
    subs.check(client, NOW)

    assert not subs.polling_due(start - PRE_START_LEAD - timedelta(seconds=1))
    assert subs.polling_due(start - PRE_START_LEAD)
    assert subs.seconds_until_due(NOW) == (start - PRE_START_LEAD - NOW).total_seconds()


def test_a_finished_match_is_released():
    client = _Client([_row("odi", "India", "West Indies", NOW, ms="live")], hits_today=0)
    subs = _subs()
    subs.check(client, NOW)
    client.snapshots["odi"] = _started("odi", ended=True)

    subs.prune(client)

    assert subs.provider_ids == set()
    assert client.subscribed == set()
