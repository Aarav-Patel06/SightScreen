"""Cricsheet bulk loader (SPEC.md sections 4.1, 5.2, 11).

Downloads Cricsheet's combined archive, filters to men's T20 + ODI
matches, and loads them into `matches` + `deliveries`. Session 3's
denormalized columns (batting_team_id, bowling_team_id, match_date,
is_super_over, wicket_count) are populated at load time; ball_in_over is
the 1-indexed position within the over counting every delivery, legal and
illegal - confirmed against real Cricsheet data, where a wide and its
re-bowled replacement share the same "actual_delivery" over.ball notation
but are two distinct list entries.

Idempotent and resumable: each match is one transaction (the matches row
and every one of its deliveries), gated by the unique index on
matches.external_ids->>'cricsheet' (session 3). A crash anywhere leaves at
most one match half-done, which rolls back entirely - a restart just
reprocesses every file; already-committed matches skip via that index.

Usage (from the api/ directory, with api/.env configured):
    python -m ingest.cricsheet run
    python -m ingest.cricsheet run --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from ingest.entity_resolution import resolve_player, resolve_team, resolve_venue

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"
KNOWN_TEAM_ALIASES_PATH = REPO_ROOT / "api" / "data" / "known_team_aliases.json"
LOAD_REPORTS_DIR = REPO_ROOT / "api" / "data" / "load_reports"

ARCHIVE_URL = "https://cricsheet.org/downloads/all_json.zip"
PEOPLE_CSV_URL = "https://cricsheet.org/register/people.csv"
TARGET_FORMATS = {"T20", "ODI"}
TARGET_GENDER = "male"

# T20: 20 overs/innings max = 120 legal balls. ODI: 50 overs = 300.
MAX_LEGAL_BALLS = {"T20": 120, "ODI": 300}
# Umpires occasionally miscount and let an over run to 7 legal balls; once
# bowled it stands (confirmed against a real Vitality Blast match this
# loader initially, wrongly, rejected). One extra over's worth of tolerance
# distinguishes that from genuine corruption/truncation, which would be off
# by far more than this.
LEGAL_BALLS_TOLERANCE = 6

# Extras dict can carry multiple keys (e.g. a bye off a no-ball); the schema
# only has room for one extra_type per delivery (SPEC.md section 5.2) - the
# delivery-type extras (which make the ball illegal) take priority over the
# byes/legbyes that can co-occur with them.
EXTRA_TYPE_PRIORITY = ["wides", "noballs", "legbyes", "byes", "penalty"]
EXTRA_TYPE_LABEL = {
    "wides": "wide",
    "noballs": "noball",
    "byes": "bye",
    "legbyes": "legbye",
    "penalty": "penalty",
}


class RejectMatch(Exception):
    """Raised anywhere during a single match's load; caught by run(), which
    rolls back that match's transaction and records the rejection."""


@dataclass
class LoadReport:
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    matches_seen: int = 0
    matches_loaded: int = 0
    matches_skipped_already_loaded: int = 0
    matches_out_of_scope: int = 0
    matches_rejected: int = 0
    matches_with_reconciliation_anomaly: int = 0
    deliveries_loaded: int = 0
    rejections: list[dict] = field(default_factory=list)
    resolution_counts: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "player": {"registry_id": 0, "name_alias": 0, "fuzzy": 0, "auto_created": 0, "queued": 0},
            "team": {"registry_id": 0, "name_alias": 0, "fuzzy": 0, "auto_created": 0, "queued": 0},
            "venue": {"registry_id": 0, "name_alias": 0, "fuzzy": 0, "auto_created": 0, "queued": 0},
        }
    )
    fuzzy_auto_resolves: list[dict] = field(default_factory=list)
    finished_at: str | None = None
    elapsed_seconds: float | None = None

    def record_resolution(self, kind: str, result, source_name: str, cricsheet_id: str) -> None:
        counts = self.resolution_counts[kind]
        if result.outcome == "auto_resolved":
            counts[result.method] += 1
            if result.method == "fuzzy":
                self.fuzzy_auto_resolves.append(
                    {
                        "kind": kind,
                        "source_name": source_name,
                        "matched_entity_id": result.entity_id,
                        "match_file": cricsheet_id,
                    }
                )
        elif result.outcome == "auto_created":
            counts["auto_created"] += 1
        elif result.outcome == "queued":
            counts["queued"] += 1

    def to_dict(self) -> dict:
        total_fuzzy = sum(counts["fuzzy"] for counts in self.resolution_counts.values())
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": self.elapsed_seconds,
            "matches_seen": self.matches_seen,
            "matches_loaded": self.matches_loaded,
            "matches_skipped_already_loaded": self.matches_skipped_already_loaded,
            "matches_out_of_scope": self.matches_out_of_scope,
            "matches_rejected": self.matches_rejected,
            "matches_with_reconciliation_anomaly": self.matches_with_reconciliation_anomaly,
            "deliveries_loaded": self.deliveries_loaded,
            "resolution_counts": self.resolution_counts,
            "fuzzy_auto_resolves": self.fuzzy_auto_resolves,
            "rejections": self.rejections,
            "note": (
                f"{total_fuzzy} resolutions went through the fuzzy path on this run. "
                "Cricsheet's own registry (people.csv, seeded up front) resolves the "
                "large majority of players by exact ID, so a near-zero fuzzy count here "
                "is correct, not evidence the fuzzy path was validated - it means it "
                "essentially didn't fire. Its only real coverage is the golden test set "
                "(tests/ingest/test_entity_resolution.py). Phase 2's live-API name "
                "reconciliation is where it gets its first real workout against data "
                "this loader never exercises it against."
            ),
        }


def _env() -> dict:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL") or not env.get("CRICSHEET_DATA_DIR"):
        sys.exit(f"LOCAL_DATABASE_URL and CRICSHEET_DATA_DIR must be set in {ENV_PATH}")
    return env


def download(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    zip_path = data_dir / "all_json.zip"
    extracted_marker = data_dir / ".extracted"
    if extracted_marker.exists():
        return
    if not zip_path.exists():
        print(f"Downloading {ARCHIVE_URL} -> {zip_path}")
        urllib.request.urlretrieve(ARCHIVE_URL, zip_path)
    print(f"Extracting {zip_path}...")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(data_dir)
    extracted_marker.write_text("done", encoding="utf-8")


def download_people_csv(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / "people.csv"
    if not csv_path.exists():
        print(f"Downloading {PEOPLE_CSV_URL} -> {csv_path}")
        urllib.request.urlretrieve(PEOPLE_CSV_URL, csv_path)
    return csv_path


def iter_all_match_files(data_dir: Path):
    for path in sorted(data_dir.glob("*.json")):
        yield path


def seed_people_registry(conn, csv_path: Path) -> int:
    """Populates the registry up front, before any match parsing, from
    Cricsheet's own register/people.csv - one row per real human, globally
    unique `identifier` across the whole corpus. This is what makes registry
    IDs available immediately on the very first match encountered, instead
    of being discovered incrementally match-by-match - the order-dependence
    that made the token_set_ratio subset-matching bug (session 5, fix 4)
    possible in the first place.

    unique_name (not the bare name) is used as canonical_name - Cricsheet's
    own disambiguation for the rare case of two different real people
    sharing an identical name (e.g. "Abdullah Al Mamun (2)"). Using the
    ambiguous bare name here would risk two different player_ids showing
    the same display name for no good reason.
    """
    if not csv_path.exists():
        return 0

    with conn.cursor() as cur:
        cur.execute("SELECT source_id FROM player_aliases WHERE source = 'cricsheet' AND source_id IS NOT NULL")
        existing_ids = {row[0] for row in cur.fetchall()}

    seeded = 0
    with open(csv_path, encoding="utf-8", newline="") as f, conn.cursor() as cur:
        for row in csv.DictReader(f):
            identifier = row["identifier"]
            if identifier in existing_ids:
                continue
            canonical_name = row["unique_name"] or row["name"]
            cur.execute(
                "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (canonical_name,)
            )
            player_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO player_aliases (player_id, source, source_id, source_name)
                VALUES (%s, 'cricsheet', %s, %s)
                ON CONFLICT (source, source_id) WHERE source_id IS NOT NULL DO NOTHING
                """,
                (player_id, identifier, canonical_name),
            )
            seeded += 1
    return seeded


def _merge_team(cur, keep_id: int, absorb_id: int) -> None:
    """Only needed when the corpus was already loaded before a rename was
    recognized (session 6), so the old name got its own team_id with real
    match/delivery history attached - repoint everything to the canonical
    team_id, then drop the now-orphaned row. Safe to re-run: once absorb_id
    is gone, every UPDATE here affects zero rows and the DELETE is a no-op.
    elo_ratings isn't touched here - it doesn't exist yet the first time
    this runs in the normal pipeline order (session 6 builds it after the
    load), so it will only ever see the canonical team_id.
    """
    for table, columns in (
        ("matches", ["team_a", "team_b", "toss_winner", "winner"]),
        ("deliveries", ["batting_team_id", "bowling_team_id"]),
    ):
        for column in columns:
            cur.execute(f"UPDATE {table} SET {column} = %s WHERE {column} = %s", (keep_id, absorb_id))
    cur.execute("UPDATE team_aliases SET team_id = %s WHERE team_id = %s", (keep_id, absorb_id))
    cur.execute("DELETE FROM teams WHERE team_id = %s", (absorb_id,))


def seed_known_team_aliases(conn) -> None:
    if not KNOWN_TEAM_ALIASES_PATH.exists():
        return
    entries = json.loads(KNOWN_TEAM_ALIASES_PATH.read_text(encoding="utf-8"))
    with conn.cursor() as cur:
        for entry in entries:
            canonical_name = entry["canonical_name"]
            cur.execute("SELECT team_id FROM teams WHERE name = %s", (canonical_name,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO teams (name) VALUES (%s) RETURNING team_id", (canonical_name,)
                )
                row = cur.fetchone()
            team_id = row[0]
            for old_name in entry.get("old_names", []):
                cur.execute("SELECT team_id FROM teams WHERE name = %s", (old_name,))
                old_row = cur.fetchone()
                if old_row is not None and old_row[0] != team_id:
                    _merge_team(cur, keep_id=team_id, absorb_id=old_row[0])
                cur.execute(
                    """
                    INSERT INTO team_aliases (team_id, source, source_name)
                    VALUES (%s, 'cricsheet', %s)
                    ON CONFLICT (source, source_name) DO NOTHING
                    """,
                    (team_id, old_name),
                )
    conn.commit()


def _extra_type(extras: dict) -> str | None:
    for key in EXTRA_TYPE_PRIORITY:
        if key in extras:
            return EXTRA_TYPE_LABEL[key]
    return None


def _parse_start_time(info: dict) -> datetime:
    first_date = info["dates"][0]
    return datetime.fromisoformat(first_date).replace(tzinfo=timezone.utc)


def _resolve_teams(conn, report: LoadReport, cricsheet_id: str, team_names: list[str]) -> dict[str, int]:
    team_ids: dict[str, int] = {}
    for name in team_names:
        result = resolve_team(conn, "cricsheet", name)
        report.record_resolution("team", result, name, cricsheet_id)
        if result.entity_id is None:
            raise RejectMatch(f"team '{name}' resolution queued (unresolved_id={result.unresolved_id})")
        team_ids[name] = result.entity_id
    return team_ids


def _resolve_players(
    conn,
    report: LoadReport,
    cricsheet_id: str,
    match_id: int,
    team_names: list[str],
    team_ids: dict[str, int],
    squads: dict[str, list[str]],
    registry: dict[str, str],
    match_date_: date,
) -> dict[str, int | None]:
    player_ids: dict[str, int | None] = {}
    for team_name in team_names:
        squad = squads.get(team_name, [])
        for name in squad:
            if name in player_ids:
                continue
            result = resolve_player(
                conn,
                "cricsheet",
                name,
                source_id=registry.get(name),
                match_id=match_id,
                team_id=team_ids[team_name],
                match_date=match_date_,
                squad_names=squad,
            )
            report.record_resolution("player", result, name, cricsheet_id)
            player_ids[name] = result.entity_id
    return player_ids


def _resolve_venue(conn, report: LoadReport, cricsheet_id: str, info: dict) -> int | None:
    venue_name = info.get("venue")
    if not venue_name:
        return None
    result = resolve_venue(conn, "cricsheet", venue_name, city=info.get("city"))
    report.record_resolution("venue", result, venue_name, cricsheet_id)
    return result.entity_id


def _match_outcome(info: dict, team_names: list[str], team_ids: dict[str, int]) -> tuple[int | None, str]:
    outcome = info.get("outcome", {})
    if outcome.get("result") == "tie":
        return None, "tie"
    if outcome.get("result") in ("no result", "no_result"):
        return None, "no_result"
    winner_name = outcome.get("winner")
    if winner_name is None:
        # Schema drift / genuinely unexpected outcome shape.
        raise RejectMatch(f"unrecognised outcome shape: {outcome!r}")
    if winner_name not in team_ids:
        raise RejectMatch(f"outcome winner {winner_name!r} not in match teams {team_names!r}")
    result_method = "dls" if "method" in outcome else "normal"
    return team_ids[winner_name], result_method


def _extract_target(
    innings_list: list[dict],
    result_method: str,
    innings_1_total: int | None,
    format_nominal_overs: int,
) -> tuple[int | None, float | None]:
    """The chasing innings' own recorded target (session 6, Decision 3) -
    authoritative even when DLS-revised (confirmed against a real match:
    innings 1 scored 165, DLS target was 70 off 6 overs - nowhere near
    innings_1_total + 1). Scans rather than assuming index 1, defensively.

    Cricsheet sometimes omits the target key even for a normal, properly-
    decided chase - confirmed against real data: 761 of ~12,100 "normal"
    matches and all 7 tied matches missing it, discovered via match_states'
    own build-time diagnostic, not assumed away. Derived as
    innings_1_total + 1 ONLY for "normal"/"tie" results, where that
    derivation is provably correct (a tie means the chase landed exactly
    one run short of it). Never derived for a DLS-decided result missing
    this field (10 such matches) - there is no safe fallback for those;
    target stays NULL rather than guessed.
    """
    for innings in innings_list:
        target = innings.get("target")
        if target:
            return target.get("runs"), target.get("overs")
    if result_method in ("normal", "tie") and innings_1_total is not None:
        return innings_1_total + 1, float(format_nominal_overs)
    return None, None


def _insert_match_row(
    conn,
    cricsheet_id: str,
    info: dict,
    venue_id: int | None,
    team_ids: dict[str, int],
    has_reconciliation_anomaly: bool,
    innings_list: list[dict],
    innings_1_total: int | None,
) -> int:
    team_names = info["teams"]
    toss = info.get("toss", {})
    toss_winner_name = toss.get("winner")
    toss_winner_id = team_ids.get(toss_winner_name)
    winner_id, result_method = _match_outcome(info, team_names, team_ids)
    format_nominal_overs = MAX_LEGAL_BALLS[info["match_type"]] // 6
    target_runs, target_overs = _extract_target(innings_list, result_method, innings_1_total, format_nominal_overs)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO matches
              (external_ids, competition, format, venue_id, start_time,
               team_a, team_b, toss_winner, toss_decision, winner, result_method,
               status, has_reconciliation_anomaly, target_runs, target_overs)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'complete', %s, %s, %s)
            RETURNING match_id
            """,
            (
                json.dumps({"cricsheet": cricsheet_id}),
                info.get("event", {}).get("name", "Unknown"),
                info["match_type"],
                venue_id,
                _parse_start_time(info),
                team_ids[team_names[0]],
                team_ids[team_names[1]],
                toss_winner_id,
                toss.get("decision"),
                winner_id,
                result_method,
                has_reconciliation_anomaly,
                target_runs,
                target_overs,
            ),
        )
        return cur.fetchone()[0]


def _build_innings_rows(
    innings: dict,
    innings_index: int,
    match_id: int,
    match_type: str,
    match_date_: date,
    team_ids: dict[str, int],
    player_ids: dict[str, int | None],
) -> tuple[list[tuple], int]:
    is_super_over = bool(innings.get("super_over")) or innings_index >= 2
    batting_team_name = innings["team"]
    other_team = next((t for t in team_ids if t != batting_team_name), None)
    if other_team is None:
        raise RejectMatch(f"innings {innings_index + 1}: batting team {batting_team_name!r} not recognised")
    batting_team_id = team_ids[batting_team_name]
    bowling_team_id = team_ids[other_team]

    rows: list[tuple] = []
    legal_ball_num = 0
    for over in innings.get("overs", []):
        over_num = over["over"]
        for ball_in_over, delivery in enumerate(over.get("deliveries", []), start=1):
            extras = delivery.get("extras", {})
            is_legal = "wides" not in extras and "noballs" not in extras
            if is_legal:
                legal_ball_num += 1

            wickets = delivery.get("wickets", [])
            wicket_type = wickets[0]["kind"] if wickets else None
            player_out_name = wickets[0].get("player_out") if wickets else None

            runs = delivery.get("runs", {})

            rows.append(
                (
                    match_id,
                    innings_index + 1,
                    over_num,
                    ball_in_over,
                    legal_ball_num,
                    player_ids.get(delivery.get("batter")),
                    player_ids.get(delivery.get("non_striker")),
                    player_ids.get(delivery.get("bowler")),
                    batting_team_id,
                    bowling_team_id,
                    match_date_,
                    is_super_over,
                    runs.get("batter", 0),
                    runs.get("extras", 0),
                    _extra_type(extras),
                    wicket_type,
                    player_ids.get(player_out_name) if player_out_name else None,
                    len(wickets),
                )
            )

    if not is_super_over and legal_ball_num > MAX_LEGAL_BALLS[match_type] + LEGAL_BALLS_TOLERANCE:
        raise RejectMatch(
            f"innings {innings_index + 1}: {legal_ball_num} legal balls exceeds "
            f"{MAX_LEGAL_BALLS[match_type]} max for {match_type}"
        )

    return rows, legal_ball_num


DELIVERY_COLUMNS = (
    "match_id",
    "innings",
    "over_num",
    "ball_in_over",
    "legal_ball_num",
    "batter_id",
    "non_striker_id",
    "bowler_id",
    "batting_team_id",
    "bowling_team_id",
    "match_date",
    "is_super_over",
    "runs_batter",
    "runs_extras",
    "extra_type",
    "wicket_type",
    "player_out_id",
    "wicket_count",
)


def _copy_deliveries(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    column_list = ", ".join(DELIVERY_COLUMNS)
    with conn.cursor().copy(f"COPY deliveries ({column_list}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def _has_reconciliation_anomaly(
    info: dict, all_innings: list[dict], real_innings_rows: list[list[tuple]]
) -> bool:
    """Decision 4, check 3: where Cricsheet gives us a second source (the
    chasing innings' declared target), the first innings' summed runs
    should reconcile to it. Only checked for a normal, straightforwardly-
    decided two-innings match - a rain interruption can revise the target
    mid-match without the final outcome ever being decided by that method
    (confirmed against a real 2007 ICC World Cup match this loader
    initially, wrongly, rejected: outcome.method == "D/L"), and a "no
    result" match can have a revised target with no method key at all,
    since there's no final result to attribute a method to (confirmed
    against a real 2006 Zimbabwe-West Indies ODI abandoned mid-chase:
    target revised to 30 overs/191 runs, outcome = {"result": "no
    result"}, no "method" key). Only trust the reconciliation when there's
    a plain winner and no method - anything else means the target's
    provenance isn't the simple +1 rule. Super overs have their own much
    smaller target relationship, not worth the extra complexity here.

    Returns True (flag, don't reject) rather than raising - a genuine
    mismatch here is rare but real (e.g. a slow-over-rate penalty run not
    reflected in any delivery event), and rejecting a whole match loses
    data that needs a re-parse to recover. matches.has_reconciliation_anomaly
    is a WHERE clause; eval/splits.py (Phase 1) must exclude flagged
    matches from training, same as ties/no-results.
    """
    outcome = info.get("outcome", {})
    if "winner" not in outcome or "method" in outcome:
        return False
    if len(all_innings) < 2:
        return False
    first, second = all_innings[0], all_innings[1]
    if first.get("super_over") or second.get("super_over"):
        return False
    target = second.get("target")
    if not target:
        return False
    first_innings_total = sum(row[12] + row[13] for row in real_innings_rows[0])  # runs_batter + runs_extras
    return first_innings_total + 1 != target.get("runs")


def load_match(bulk_conn, catalog_conn, report: LoadReport, path: Path, dry_run: bool = False) -> None:
    """bulk_conn holds the per-match transaction for matches+deliveries -
    all-or-nothing, per Decision 2. catalog_conn is a separate,
    always-autocommitted connection used for every entity_resolution call.

    These must never share a connection: entity resolution's writes
    (aliases, new entities, and critically the unresolved_entities queue)
    have to survive even when the match they were discovered in gets
    rejected and its own transaction rolls back - otherwise a team that
    can't resolve would be queued and then have that queue entry silently
    erased by the rollback, forever re-attempted and never actually
    reviewable. Confirmed as a real bug against real data before this
    split existed - see the session 5 plan/commit history.

    dry_run skips the matches/deliveries write entirely (never opens
    bulk_conn's transaction) but still runs and persists entity
    resolution via catalog_conn - that's small, idempotent, additive
    catalog data, and front-loading it makes the real run afterward
    mostly exact-alias lookups instead of repeated fuzzy comparisons.
    """
    cricsheet_id = path.stem
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        info = data["info"]
        match_type = info["match_type"]
        gender = info["gender"]
        team_names = info["teams"]
        innings_list = data["innings"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        report.matches_rejected += 1
        report.rejections.append({"file": cricsheet_id, "reason": f"unparseable: {exc}"})
        return

    if match_type not in TARGET_FORMATS or gender != TARGET_GENDER:
        report.matches_out_of_scope += 1
        return

    # Own transaction scope even for this read - dry-run mode never opens
    # bulk_conn.transaction() later, and a bare read under psycopg3's
    # default (non-autocommit) mode leaves an implicit transaction open
    # indefinitely otherwise, accumulating across every file in the corpus.
    with bulk_conn.transaction(), bulk_conn.cursor() as cur:
        cur.execute("SELECT 1 FROM matches WHERE external_ids->>'cricsheet' = %s", (cricsheet_id,))
        if cur.fetchone():
            report.matches_skipped_already_loaded += 1
            return

    if len(team_names) != 2:
        report.matches_rejected += 1
        report.rejections.append({"file": cricsheet_id, "reason": f"expected 2 teams, got {len(team_names)}"})
        return

    try:
        # All entity resolution happens first, entirely outside the bulk
        # transaction, via catalog_conn - its results (including queue
        # entries) must persist even if this match later gets rejected.
        # match_id doesn't exist yet at this point (the matches row isn't
        # inserted, let alone committed, until below) - passed as None to
        # _resolve_players rather than risk a cross-connection foreign key
        # reference to a row catalog_conn's session cannot yet see. That
        # only costs first_seen_match_id context on a player queue entry
        # (a review nicety), never correctness.
        team_ids = _resolve_teams(catalog_conn, report, cricsheet_id, team_names)
        venue_id = _resolve_venue(catalog_conn, report, cricsheet_id, info)
        registry = info.get("registry", {}).get("people", {})
        squads = info.get("players", {})
        match_date_ = date.fromisoformat(info["dates"][0])
        player_ids = _resolve_players(
            catalog_conn, report, cricsheet_id, None, team_names, team_ids, squads, registry, match_date_
        )

        # Every innings' rows are built once, up front, with a placeholder
        # match_id - the real one doesn't exist until the matches row is
        # inserted below, and inserting that row needs to already know
        # has_reconciliation_anomaly. Patched to the real match_id right
        # before COPY; cheap, and avoids building the rows twice.
        all_rows = [
            _build_innings_rows(innings, i, None, match_type, match_date_, team_ids, player_ids)[0]
            for i, innings in enumerate(innings_list)
        ]
        total_deliveries = sum(len(rows) for rows in all_rows)
        has_anomaly = _has_reconciliation_anomaly(info, innings_list, all_rows)
        innings_1_total = sum(row[12] + row[13] for row in all_rows[0]) if all_rows else None

        if not dry_run:
            with bulk_conn.transaction():
                match_id = _insert_match_row(
                    bulk_conn, cricsheet_id, info, venue_id, team_ids, has_anomaly, innings_list, innings_1_total
                )
                for rows in all_rows:
                    patched_rows = [(match_id, *row[1:]) for row in rows]
                    _copy_deliveries(bulk_conn, patched_rows)
    except RejectMatch as exc:
        report.matches_rejected += 1
        report.rejections.append({"file": cricsheet_id, "reason": str(exc)})
        return

    report.matches_loaded += 1
    report.deliveries_loaded += total_deliveries
    if has_anomaly:
        report.matches_with_reconciliation_anomaly += 1


def run(dry_run: bool = False, limit: int | None = None) -> LoadReport:
    """limit stops once matches_loaded reaches that many *in-scope* matches
    (not files scanned) - used to prove resumability on a fixed-size slice
    without waiting for the full corpus (session 5)."""
    env = _env()
    data_dir = Path(env["CRICSHEET_DATA_DIR"])
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / "api" / data_dir

    download(data_dir)
    people_csv_path = download_people_csv(data_dir)

    report = LoadReport()
    start = time.monotonic()

    # Deliberately two connections - see load_match's docstring for why
    # they must never be merged into one.
    bulk_conn = psycopg.connect(env["LOCAL_DATABASE_URL"])
    catalog_conn = psycopg.connect(env["LOCAL_DATABASE_URL"], autocommit=True)
    try:
        seeded = seed_people_registry(catalog_conn, people_csv_path)
        print(f"Seeded {seeded} new players from people.csv")
        seed_known_team_aliases(catalog_conn)

        files = list(iter_all_match_files(data_dir))
        for i, path in enumerate(files, start=1):
            report.matches_seen += 1
            load_match(bulk_conn, catalog_conn, report, path, dry_run=dry_run)
            reached_limit = limit is not None and report.matches_loaded >= limit

            if i % 500 == 0 or i == len(files) or reached_limit:
                elapsed = time.monotonic() - start
                print(
                    f"[{i}/{len(files)}] loaded={report.matches_loaded} "
                    f"skipped={report.matches_skipped_already_loaded} "
                    f"out_of_scope={report.matches_out_of_scope} "
                    f"rejected={report.matches_rejected} "
                    f"deliveries={report.deliveries_loaded} "
                    f"elapsed={elapsed:.0f}s"
                )
            if reached_limit:
                break

        if not dry_run:
            bulk_conn.execute("ANALYZE deliveries")
            bulk_conn.execute("ANALYZE matches")
            bulk_conn.commit()
    finally:
        bulk_conn.close()
        catalog_conn.close()

    report.elapsed_seconds = time.monotonic() - start
    report.finished_at = datetime.now(timezone.utc).isoformat()

    LOAD_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOAD_REPORTS_DIR / f"{'dry_run_' if dry_run else ''}{int(time.time())}.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    print(f"Load report written to {report_path}")

    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cricsheet")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="download (if needed) and load the corpus")
    run_parser.add_argument(
        "--dry-run", action="store_true", help="parse/validate/resolve without writing to the database"
    )
    run_parser.add_argument(
        "--limit", type=int, default=None, help="stop after this many matches are loaded (not files scanned)"
    )
    args = parser.parse_args(argv)
    if args.command == "run":
        run(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
