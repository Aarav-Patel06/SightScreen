"""The daily calibration monitor (SPEC.md section 8.1, Phase 3 session 2).

Runs as `.github/workflows/calibration.yml` at 03:00 UTC and writes one row
to `calibration_runs`, which is what `/accuracy` renders.

§8.1 is explicit that this job has two halves and they must not be
conflated:

    Steps 1-4 are the monitoring, which always runs and always has value.
    Steps 5-8 are the refit, which usually should decline to act.
    ... a job that reports honestly and changes nothing is succeeding.

So a green run that changes nothing is the expected outcome, and the log
says so in those words rather than looking like a no-op failure.

**Two populations, never mixed.** `predictions.source` separates them:

    backfill  replayed from the corpus after the fact. The 100 matches are
              the §9.1 TEST split, which Phase 1 used to SELECT the shipped
              calibrator - so these numbers are in-sample with respect to
              model selection. There is no held-out data behind them.
    live      predicted before the result existed. The only population whose
              accuracy is an honest estimate of anything.

**Reads Supabase and nothing else.** Deliberately does not import
`config.settings`: its §2.1 validator requires `LOCAL_DATABASE_URL` and
`CRICSHEET_DATA_DIR` off Railway, and a GitHub runner is neither Railway nor
a laptop. `db/env.py`'s `env_value` is the environment-first pattern that
works in a container, which is why the serving path uses it too. It also
does not import LightGBM - the §8.1 candidates are scikit-learn - so the job
never touches the one dependency that fails at import rather than install.

Usage:
    python -m eval.calibration_monitor --dry-run
    python -m eval.calibration_monitor
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import psycopg

from db.env import env_value
from serving.db import OTHER, classify_connection_error, describe
from eval.metrics import brier_match_clustered_ci, calibration_report, paired_brier_match_clustered_ci

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
REPORTS_DIR = REPO_ROOT / "api" / "data" / "eval_reports"
CACHE_DIR = REPO_ROOT / "api" / "data" / "models" / "cache"

POPULATIONS = ("backfill", "live")
N_RESAMPLES = 2000

# Below these, the refit branch does not run. Measured rather than asserted:
# the match-clustered CI on the 100 backfilled matches is [0.0738, 0.1330],
# a half-width of ~0.03, while a calibration refit that helps moves Brier by
# roughly 0.002-0.01. Resolving that needs a half-width nearer 0.005, and
# since the interval narrows with the square root of the match count that is
# about 36x the matches - call it 3,600. 500 is therefore already generous;
# it is set where a refit could plausibly be SELECTED rather than where one
# would be significant, and the job says which it is.
MIN_WINDOW_MATCHES = 500
MIN_SELECT_MATCHES = 200

_LOG_QUERY = """
    SELECT p.match_id,
           (p.payload->>'p')::float8                    AS probability,
           (o.actual->>'batting_team_won')::bool::int   AS label,
           p.payload->>'phase'                          AS phase,
           (p.payload->>'balls_remaining')::int         AS balls_remaining,
           (p.payload->>'runs_required')::int           AS runs_required,
           (p.payload->>'wickets')::int                 AS wickets,
           p.created_at,
           m.start_time::date                           AS match_date
    FROM predictions p
    JOIN prediction_outcomes o ON o.prediction_id = p.prediction_id
    JOIN matches m ON m.match_id = p.match_id
    WHERE p.prediction_type = 'win_prob'
      AND p.innings IS NOT NULL
      AND p.model_version = %(model_version)s
      AND p.source = %(source)s
    ORDER BY p.prediction_id
"""

# Logged but not yet scored. Reported per population, because for `live` it
# is currently the entire story and a page that showed nothing there would
# be hiding the most interesting fact about the system.
_UNRESOLVED_QUERY = """
    SELECT count(*) AS logged, count(DISTINCT p.match_id) AS matches
    FROM predictions p
    LEFT JOIN prediction_outcomes o ON o.prediction_id = p.prediction_id
    WHERE p.prediction_type = 'win_prob'
      AND p.innings IS NOT NULL
      AND p.source = %(source)s
      AND o.prediction_id IS NULL
"""


class Rows:
    """The logged predictions for one population, as arrays."""

    def __init__(self, records: list[tuple]) -> None:
        self.match_id = np.array([r[0] for r in records], dtype=np.int64)
        self.p = np.array([r[1] for r in records], dtype=np.float64)
        self.y = np.array([r[2] for r in records], dtype=np.int8)
        self.phase = np.array([r[3] for r in records], dtype=object)
        self.balls_remaining = np.array([r[4] for r in records], dtype=np.int16)
        self.runs_required = np.array([r[5] for r in records], dtype=np.int16)
        self.wickets = np.array([r[6] for r in records], dtype=np.int16)
        self.created_at = [r[7] for r in records]
        self.match_date = np.array(
            [np.datetime64(r[8]) for r in records], dtype="datetime64[D]"
        )

    def __len__(self) -> int:
        return len(self.y)

    @property
    def n_matches(self) -> int:
        return int(len(np.unique(self.match_id)))


def fetch(conn, model_version: str, source: str) -> Rows:
    with conn.cursor() as cur:
        cur.execute(_LOG_QUERY, {"model_version": model_version, "source": source})
        return Rows(cur.fetchall())


def unresolved(conn, source: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(_UNRESOLVED_QUERY, {"source": source})
        logged, matches = cur.fetchone()
    return {"predictions": int(logged), "matches": int(matches)}


def by_phase(rows: Rows) -> dict:
    """Brier per phase, each with its own clustered CI.

    §12.2: every number gets an uncertainty specific to it. A phase bucket's
    Brier beside the overall CI would be the aggregate-derived interval that
    rule exists to forbid.
    """
    out = {}
    for phase in sorted({str(p) for p in rows.phase.tolist()}):
        mask = rows.phase == phase
        if not mask.any():
            continue
        ci = brier_match_clustered_ci(
            rows.y[mask], rows.p[mask], rows.match_id[mask], n_resamples=N_RESAMPLES
        )
        out[phase] = {
            "n": int(mask.sum()),
            "n_matches": ci["n_matches"],
            "brier": ci["point"],
            "ci_low": ci["ci_low"],
            "ci_high": ci["ci_high"],
        }
    return out


def _dataset(rows: Rows):
    """A SecondInningsDataset built from logged payloads, for the baselines.

    Every input the two §9.2 baselines read is recoverable from what was
    logged: `balls_remaining` and `runs_required` verbatim, `wickets_in_hand`
    as `10 - wickets`, and `required_run_rate` as `runs_required /
    (balls_remaining / 6)` - the identical formula the SQL rebuild and the
    incremental builder both use. `delivery_id` is a placeholder because
    neither baseline reads it and the log does not carry one.
    """
    from eval.splits import SecondInningsDataset

    balls = rows.balls_remaining.astype(np.float64)
    rrr = np.divide(
        rows.runs_required.astype(np.float64),
        np.where(balls > 0, balls / 6.0, np.nan),
        out=np.full(len(rows), np.nan),
        where=balls > 0,
    )
    return SecondInningsDataset(
        delivery_id=np.zeros(len(rows), dtype=np.int64),
        match_id=rows.match_id,
        match_date=rows.match_date,
        required_run_rate=rrr.astype(np.float32),
        wickets_in_hand=(10 - rows.wickets).astype(np.int8),
        balls_remaining=rows.balls_remaining,
        runs_required=rows.runs_required,
        phase=rows.phase,
        label=rows.y,
    )


def vs_baselines(rows: Rows) -> dict:
    """The §9.2 comparison, on the SAME matches as the model.

    Returns a reason rather than raising when the artifact is unavailable:
    §8.5's baseline comparison is one section of the accuracy page, and a
    monitor that reported nothing because one section could not be computed
    would be trading everything for something.
    """
    from eval.publish_baselines import load_baselines

    try:
        bundle = load_baselines(CACHE_DIR)
    except Exception as exc:  # noqa: BLE001 - reported, not fatal
        return {"unavailable": f"could not load the baseline artifact: {exc}"}
    if bundle is None:
        return {
            "unavailable": "no baseline artifact registered - run "
            "`python -m eval.publish_baselines` and register the URL"
        }

    dataset = _dataset(rows)
    out: dict = {}
    for name in ("logistic", "historical_base_rate"):
        try:
            baseline_p = bundle[name].predict_proba(dataset)
        except ValueError as exc:
            # HistoricalBaseRateBaseline refuses to score matches it was fit
            # on - it is a lookup of historical outcomes, so that would be
            # reading back the answer. Expected if a window ever includes
            # train matches; reported rather than crashed.
            out[name] = {"unavailable": str(exc)[:200]}
            continue
        # a = baseline, b = model, by convention: `significant` means the
        # model beats the baseline. Swapping these silently inverts it.
        paired = paired_brier_match_clustered_ci(
            rows.y, baseline_p, rows.p, rows.match_id, n_resamples=N_RESAMPLES
        )
        out[name] = {
            "baseline_brier": paired["brier_a"],
            "model_brier": paired["brier_b"],
            "improvement": paired["point_diff"],
            "ci_low": paired["ci_low"],
            "ci_high": paired["ci_high"],
            "n_matches": paired["n_matches"],
            "model_is_better": paired["significant"],
        }
    return out


def biggest_misses(conn, model_version: str, source: str, limit: int = 5) -> list[dict]:
    """§8.5's five most confident wrong predictions.

    §8.5 says "in the last month", which is meaningless for a backfilled
    population: every one of those predictions was made yesterday about a
    match played in 2025. So the window is defined by what the population
    means - most recent MATCH dates for backfill, genuinely recent
    `created_at` for live - and the page labels which it used.

    A miss requires an outcome row, and a prediction on a tie, a no-result
    or a flagged match never gets one (session 1's resolution rules). So
    excluded matches cannot appear here structurally, rather than by a
    filter someone could forget.
    """
    order = "start_time DESC" if source == "backfill" else "created_at DESC"
    window = "" if source == "backfill" else "AND p.created_at > now() - interval '30 days'"
    # DISTINCT ON the match: the worst five BALLS are, in practice, five
    # consecutive deliveries of the same over of the same match - the model
    # was wrong about one chase and each ball of it scores terribly. That is
    # one miss displayed five times. Section 8.5 wants five things to go and
    # look at, so this takes each match's worst ball and then the five worst
    # matches.
    query = f"""
        SELECT match_id, over_num, ball_in_over, probability, actual_won,
               brier, phase, competition, match_date
        FROM (
            SELECT DISTINCT ON (p.match_id)
                   p.match_id, p.over_num, p.ball_in_over,
                   (p.payload->>'p')::float8              AS probability,
                   (o.actual->>'batting_team_won')::bool  AS actual_won,
                   o.brier, p.payload->>'phase'           AS phase,
                   m.competition, m.start_time::date      AS match_date,
                   m.start_time, p.created_at
            FROM predictions p
            JOIN prediction_outcomes o ON o.prediction_id = p.prediction_id
            JOIN matches m ON m.match_id = p.match_id
            WHERE p.prediction_type = 'win_prob' AND p.innings IS NOT NULL
              AND p.model_version = %(model_version)s AND p.source = %(source)s
              {window}
            ORDER BY p.match_id, o.brier DESC
        ) worst
        ORDER BY brier DESC, {order}
        LIMIT %(limit)s
    """
    with conn.cursor() as cur:
        cur.execute(
            query, {"model_version": model_version, "source": source, "limit": limit}
        )
        rows = cur.fetchall()
    return [
        {
            "match_id": r[0],
            "over": f"{r[1]}.{r[2]}",
            "predicted": float(r[3]),
            "actual_won": bool(r[4]),
            "brier": float(r[5]),
            "phase": r[6],
            "competition": r[7],
            "match_date": r[8].isoformat(),
        }
        for r in rows
    ]


def refit_decision(rows: Rows, log=print) -> dict:
    """SPEC.md section 8.1 steps 5-8. Usually declines, and says why.

    The floor is on MATCHES, not rows. 12,081 balls across 100 matches is
    100 effective observations, and the whole reason this project computes
    match-clustered intervals is that treating balls as independent
    understates the standard error by about an order of magnitude.

    When it does run, selection follows step 7 rather than
    run_calibration_selection.py's plain argmin on Brier: a challenger must
    beat identity by a PAIRED match-clustered margin whose CI excludes zero.
    Argmin would promote a calibrator that won by noise, which on this
    project's own Phase 1 evidence is the likely case.
    """
    n_matches = rows.n_matches
    if n_matches < MIN_WINDOW_MATCHES:
        reason = (
            f"{n_matches} matches in the window, floor is {MIN_WINDOW_MATCHES}. "
            f"Identity retained. This is the expected outcome, not a failure: "
            f"section 8.1's refit is a candidate that must earn its place, and on "
            f"this much data no candidate could be distinguished from noise."
        )
        log(f"  refit: SKIPPED - {reason}")
        return {"ran": False, "winner": "identity", "reason": reason}

    # Temporal split of the window itself: fit candidates on the earlier
    # part, select among them on the later part (steps 5-6). Selecting on
    # the data a map was fit to is circular, which section 9.3 spells out.
    order = np.argsort(rows.match_date)
    cut = rows.match_date[order][len(order) // 2]
    fit_mask = rows.match_date <= cut
    select_mask = ~fit_mask
    n_select = int(len(np.unique(rows.match_id[select_mask]))) if select_mask.any() else 0
    if n_select < MIN_SELECT_MATCHES:
        reason = (
            f"the select chunk holds {n_select} matches, floor is {MIN_SELECT_MATCHES}. "
            f"Identity retained."
        )
        log(f"  refit: SKIPPED - {reason}")
        return {"ran": False, "winner": "identity", "reason": reason}

    from models.calibration import CANDIDATES

    identity_p = rows.p[select_mask]
    comparison: dict = {}
    for name, factory in CANDIDATES.items():
        if name == "identity":
            comparison[name] = {"brier": float(np.mean((identity_p - rows.y[select_mask]) ** 2))}
            continue
        try:
            calibrator = factory()
            calibrator.fit(rows.p[fit_mask], rows.y[fit_mask], phase=rows.phase[fit_mask])
            candidate_p = calibrator.predict(rows.p[select_mask], phase=rows.phase[select_mask])
        except Exception as exc:  # noqa: BLE001 - a candidate failing is data, not a crash
            comparison[name] = {"failed": str(exc)[:200]}
            continue
        paired = paired_brier_match_clustered_ci(
            rows.y[select_mask], identity_p, candidate_p, rows.match_id[select_mask],
            n_resamples=N_RESAMPLES,
        )
        comparison[name] = {
            "brier": paired["brier_b"],
            "improvement_over_identity": paired["point_diff"],
            "ci_low": paired["ci_low"],
            "ci_high": paired["ci_high"],
            "beats_identity": paired["significant"],
        }
        log(
            f"  candidate={name} brier={paired['brier_b']:.4f} "
            f"vs identity {paired['brier_a']:.4f} "
            f"delta={paired['point_diff']:+.4f} "
            f"CI[{paired['ci_low']:+.4f},{paired['ci_high']:+.4f}] "
            f"{'BEATS IDENTITY' if paired['significant'] else 'not significant'}"
        )

    winners = [n for n, c in comparison.items() if c.get("beats_identity")]
    if not winners:
        log("  refit: identity won again - no candidate beat it by a margin excluding zero")
        return {
            "ran": True, "winner": "identity", "n_select_matches": n_select,
            "comparison": comparison,
            "reason": "no candidate beat identity by a paired match-clustered margin excluding zero",
        }
    best = min(winners, key=lambda n: comparison[n]["brier"])
    log(f"  refit: {best} beats identity - PROMOTION CANDIDATE, not applied automatically")
    return {
        "ran": True, "winner": best, "n_select_matches": n_select,
        "comparison": comparison,
        "reason": f"{best} beat identity; promotion is a deliberate act (section 8.4), not this job's",
    }


def build_report(conn, model_version: str, log=print) -> dict:
    started = time.monotonic()
    populations: dict = {}
    for source in POPULATIONS:
        log(f"population={source}")
        rows = fetch(conn, model_version, source)
        pending = unresolved(conn, source)
        if len(rows) == 0:
            log(
                f"  {pending['predictions']} logged, 0 scored - nothing to report yet"
            )
            populations[source] = {
                "n": 0, "n_matches": 0, "unresolved": pending,
                "reason_unscored": (
                    "Outcomes are read from the match archive, which does not yet "
                    "contain these matches. Nothing can be scored until it does."
                ),
            }
            continue

        report = calibration_report(rows.y, rows.p, rows.match_id, n_resamples=N_RESAMPLES)
        overall = brier_match_clustered_ci(rows.y, rows.p, rows.match_id, n_resamples=N_RESAMPLES)
        log(
            f"  n={len(rows)} over {rows.n_matches} matches  brier={report['brier']:.4f} "
            f"CI[{overall['ci_low']:.4f},{overall['ci_high']:.4f}]  "
            f"{report['n_deciles_failed']}/{report['n_deciles_populated']} deciles fail"
        )
        populations[source] = {
            "n": len(rows),
            "n_matches": rows.n_matches,
            "unresolved": pending,
            "brier": report["brier"],
            "brier_ci_low": overall["ci_low"],
            "brier_ci_high": overall["ci_high"],
            "log_loss": report["log_loss"],
            "n_deciles_populated": report["n_deciles_populated"],
            "n_deciles_failed": report["n_deciles_failed"],
            "reliability": report["reliability"],
            "by_phase": by_phase(rows),
            "vs_baselines": vs_baselines(rows),
            "biggest_misses": biggest_misses(conn, model_version, source),
            "refit": refit_decision(rows, log=log),
        }

    return {
        "model_version": model_version,
        "populations": populations,
        "n_resamples": N_RESAMPLES,
        "floors": {
            "window_matches": MIN_WINDOW_MATCHES,
            "select_matches": MIN_SELECT_MATCHES,
        },
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def active_model(conn) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT model_version FROM model_versions WHERE is_active "
            "ORDER BY trained_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        sys.exit("no active row in model_versions")
    return row[0]


def store(conn, model_version: str, report: dict) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO calibration_runs (model_version, report) VALUES (%s, %s) "
            "RETURNING run_id",
            (model_version, json.dumps(report, default=str)),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def write_step_summary(report: dict) -> None:
    """Put the outcome on the Actions run page, not only in the log.

    The distinction that matters for a job designed to decline: a run that
    SKIPPED the refit because the window was too thin and a run that
    EVALUATED every candidate and rejected them are both green ticks in the
    Actions UI. They are very different facts - the first means "not enough
    data yet", the second means "we checked, and doing nothing still wins" -
    and someone glancing at a list of green runs should not have to open the
    log to tell which happened.

    GitHub renders $GITHUB_STEP_SUMMARY on the run page itself. No-op
    anywhere else, so a laptop run is unaffected.
    """
    path = env_value("GITHUB_STEP_SUMMARY")
    if not path:
        return

    lines = ["## Calibration monitor", ""]
    lines.append(f"Model `{report['model_version']}`")
    lines.append("")
    for source, pop in report["populations"].items():
        if pop.get("n"):
            lines.append(
                f"**{source}** - {pop['n']:,} predictions over {pop['n_matches']} matches, "
                f"Brier {pop['brier']:.4f} "
                f"[{pop['brier_ci_low']:.4f}, {pop['brier_ci_high']:.4f}], "
                f"{pop['n_deciles_failed']}/{pop['n_deciles_populated']} deciles off"
            )
            refit = pop.get("refit", {})
            if refit.get("ran"):
                verdict = (
                    f"evaluated every candidate and kept identity"
                    if refit.get("winner") == "identity"
                    else f"found a promotion candidate: {refit.get('winner')}"
                )
                lines.append(f"  - refit RAN and {verdict}")
            else:
                lines.append("  - refit **SKIPPED** (not enough data) - " + refit.get("reason", ""))
        else:
            lines.append(
                f"**{source}** - {pop['unresolved']['predictions']} logged, none scored yet"
            )
        lines.append("")
    Path(path).write_text(chr(10).join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="calibration_monitor", description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    parser.add_argument("--out", type=Path, default=None, help="also write the report here")
    args = parser.parse_args(argv)

    url = env_value("SUPABASE_SESSION_POOLER_URL")
    if not url:
        sys.exit(
            "SUPABASE_SESSION_POOLER_URL is not set. The monitor reads the prediction "
            "log from Supabase; in GitHub Actions it must come from a repository secret."
        )

    # Connect inside a controlled failure path (standing rule 5).
    #
    # GitHub retains Actions logs, and on a public repo anyone can read them.
    # An unhandled psycopg failure prints its whole exception repr there -
    # host, resolved IPs, username, and whatever a future version of the
    # driver decides to include. Today psycopg redacts the password, which
    # was verified rather than assumed by connecting with a canary value and
    # grepping the output; that is a property of this version of a library,
    # not a guarantee. Session 4a learned the same lesson from pydantic,
    # whose ValidationError repr embedded the entire environment.
    #
    # So the message below is one this module composes, and the classifier
    # is the same one the serving path uses - PAUSED is reported as PAUSED
    # rather than as the auth failure it disguises itself as.
    try:
        conn = psycopg.connect(url, connect_timeout=30)
    except Exception as exc:  # noqa: BLE001 - reported deliberately, never re-raised
        kind = classify_connection_error(exc) or OTHER
        sys.exit(
            f"could not reach the prediction log: {describe(kind)} "
            f"[{type(exc).__name__}]. The connection string is not echoed here on "
            f"purpose - this output is retained and public."
        )

    with conn:
        model_version = active_model(conn)
        print(f"calibration monitor - model {model_version}")
        print()
        report = build_report(conn, model_version)
        print()
        if args.dry_run:
            print("--dry-run: nothing written")
        else:
            run_id = store(conn, model_version, report)
            print(f"wrote calibration_runs row {run_id}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"report written to {args.out}")

    write_step_summary(report)
    # A run that changes nothing is a successful run (section 8.1).
    print(f"done in {report['elapsed_seconds']}s")


if __name__ == "__main__":
    main()
