"""The day's biggest match (session 3): one pure ranking function.

Men's Full Member internationals first, then other men's internationals,
then major leagues; the most recent start wins a tie. Women's, age-group and
A sides are never picked (owner's decision, 2026-09-28: the model and corpus
are men's senior only). Neither is anything the worker could not predict: a
format with no model, or a team that does not resolve.

Rows are `cricScore` rows - the recorded body where it serves, synthetic
rows in the same shape where the case needs one.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ingest.match_pick import Team, rank_candidates

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "cricketdata"
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

FULL_MEMBERS = {"India", "West Indies", "Australia", "South Africa", "England"}
KNOWN = FULL_MEMBERS | {"Nepal", "Oman", "Barbados Tridents", "Jamaica Kingsmen", "Middlesex", "Kent"}


def _resolve(name: str) -> Team | None:
    if name not in KNOWN:
        return None
    return Team(team_id=sorted(KNOWN).index(name) + 1, full_member=name in FULL_MEMBERS)


def _row(t1, t2, series, start="2026-09-29T13:30:00", match_type="t20", ms="fixture", id_=None):
    return {
        "id": id_ or f"{t1}-{t2}-{start}",
        "dateTimeGMT": start,
        "matchType": match_type,
        "ms": ms,
        "series": series,
        "t1": f"{t1} [{t1[:3].upper()}]",
        "t2": f"{t2} [{t2[:3].upper()}]",
        "status": "",
    }


def _ranked(rows):
    return [c.provider_id for c in rank_candidates(rows, _resolve, NOW)]


def test_the_recorded_body_picks_the_next_india_v_west_indies_odi():
    body = json.loads((FIXTURE_DIR / "cric_score_ind_wi.json").read_text(encoding="utf-8"))
    ranked = rank_candidates(body["data"], _resolve, datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc))

    assert ranked, "India v West Indies, 2nd ODI is in the recorded window"
    top = ranked[0]
    assert top.provider_id == "56d07759-19dd-4a8c-a3d5-58bfd6a142a1"
    assert (top.tier, top.format) == (0, "ODI")


def test_full_member_beats_associate_beats_league():
    rows = [
        _row("Barbados Tridents", "Jamaica Kingsmen", "Caribbean Premier League 2026", id_="league"),
        _row("Nepal", "Oman", "Oman tour of Nepal, 2026", id_="associate"),
        _row("India", "West Indies", "West Indies tour of India, 2026", id_="full"),
    ]
    assert _ranked(rows) == ["full", "associate", "league"]


def test_a_full_member_against_an_associate_is_the_second_tier():
    rows = [
        _row("India", "Nepal", "ICC Men's T20 World Cup 2026", id_="mixed"),
        _row("Barbados Tridents", "Jamaica Kingsmen", "Caribbean Premier League 2026", id_="league"),
    ]
    assert [(c.provider_id, c.tier) for c in rank_candidates(rows, _resolve, NOW)] == [
        ("mixed", 1),
        ("league", 2),
    ]


def test_the_most_recent_start_wins_a_tie():
    rows = [
        _row("India", "West Indies", "West Indies tour of India, 2026", start="2026-09-29T08:30:00", id_="early"),
        _row("Australia", "South Africa", "Australia tour of South Africa, 2026", start="2026-09-29T14:00:00", id_="late"),
    ]
    assert _ranked(rows) == ["late", "early"]


def test_womens_age_group_and_a_sides_are_never_picked():
    rows = [
        _row("India Women", "West Indies Women", "West Indies Women tour of India, 2026"),
        _row("India A", "Australia A", "Australia A tour of India 2026"),
        _row("India U19", "Australia U19", "Australia U19 tour of India 2026"),
        _row("Barbados Tridents", "Jamaica Kingsmen", "Womens Caribbean Premier League 2026"),
    ]
    assert _ranked(rows) == []


def test_what_the_worker_cannot_predict_is_never_picked():
    rows = [
        _row("England", "Australia", "Australia tour of England", match_type="test", id_="test"),
        _row("Afghanistan", "India", "Afghanistan tour of India", id_="unresolved"),
        _row("Middlesex", "Kent", "Vitality Blast 2026", id_="domestic"),
        _row("Barbados Tridents", "Jamaica Kingsmen", "The Hundred Men's Competition 2026", id_="hundred"),
    ]
    assert _ranked(rows) == []


def test_only_the_coming_day_counts_and_finished_matches_do_not():
    rows = [
        _row("India", "West Indies", "West Indies tour of India, 2026", start="2026-10-03T08:30:00", id_="later"),
        _row("India", "West Indies", "West Indies tour of India, 2026", start="2026-09-29T02:00:00", ms="live", id_="live"),
        _row("India", "West Indies", "West Indies tour of India, 2026", start="2026-09-28T08:30:00", ms="result", id_="done"),
    ]
    assert _ranked(rows) == ["live"]


def test_a_domestic_row_is_dropped_before_any_name_is_resolved():
    """Resolving queues an unknown name for review; the hourly check must
    not queue every county side on the fixture list."""
    asked: list[str] = []

    def resolve(name):
        asked.append(name)
        return _resolve(name)

    rank_candidates([_row("Middlesex", "Kent", "County Championship Division Two 2026")], resolve, NOW)
    assert asked == []
