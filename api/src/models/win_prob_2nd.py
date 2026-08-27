"""Second-innings win probability model (SPEC.md section 6.2, Phase 1 session 2).

LightGBM binary classifier + isotonic calibration, `batting_team_won`, one
prediction per delivery. Player-ability features (`striker_ability` etc.,
section 6.2's table) are deferred to the Phase 5 retrain - `player_state`
doesn't exist until then (section 6.5) and a rushed proxy would just be
something Phase 5 has to compete against and undo. `dls_resources_pct` is
deferred for the same reason (Phase 0's own deferral - the column is NULL
for every row in the real corpus today).

This session's feature set:
  - 11 state features straight from `eval.splits.get_second_innings_split`
    plus a per-delivery-id enrichment join (this module's own concern, NOT
    grown into splits.py - see splits.py's module docstring on why every
    new column beyond its original eight lives here, keyed by delivery_id,
    not by a new independent temporal filter).
  - 2 venue as-of features (`features.venue_stats`).
  - 1 Elo feature (`features.elo.elo_as_of`, already leak-safe from Phase 0).

`elo_diff`/venue features are constant per match, not per ball - computed
ONCE per unique match (~12,600 across all three splits combined), not once
per row (~1.7M) - see `_match_level_features`.

Usage (from the api/ directory, with api/.env configured):
    python -m models.win_prob_2nd train
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import lightgbm as lgb
import numpy as np
import psycopg
from dotenv import dotenv_values
from sklearn.isotonic import IsotonicRegression

from eval.splits import SecondInningsDataset
from features.elo import elo_as_of
from features.venue_stats import venue_avg_first_innings_as_of, venue_chase_win_rate_as_of

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = REPO_ROOT / "api" / ".env"

SEED = 42

PHASE_CODE = {"powerplay": 0, "middle": 1, "death": 2}

# Order matters: phase_code is always last within STATE_FEATURES, and is
# the only categorical column - its index (10) is passed to LightGBM's
# categorical_feature regardless of variant, since state features always
# occupy the same leading positions in every variant's matrix.
STATE_FEATURES = [
    "balls_remaining", "wickets_in_hand", "runs_required", "required_run_rate",
    "current_run_rate", "rrr_minus_crr", "target", "partnership_runs",
    "partnership_balls", "balls_since_wicket", "phase_code",
]
PHASE_CODE_INDEX = STATE_FEATURES.index("phase_code")
VENUE_FEATURES = ["venue_chase_win_rate", "venue_avg_first_innings"]
ELO_FEATURES = ["elo_diff"]

Variant = Literal["state", "state_venue", "state_venue_elo"]
VARIANT_FEATURES: dict[Variant, list[str]] = {
    "state": STATE_FEATURES,
    "state_venue": STATE_FEATURES + VENUE_FEATURES,
    "state_venue_elo": STATE_FEATURES + VENUE_FEATURES + ELO_FEATURES,
}

LGB_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbosity": -1,
    "seed": SEED,
    # Reproducible from a seed, bitwise, not just "close enough" - costs
    # some speed, worth it at this data scale (SPEC.md section 6.2 doesn't
    # ask for speed here, unlike the section 6.4 simulation).
    "deterministic": True,
    "force_row_wise": True,
}
NUM_BOOST_ROUND = 2000
EARLY_STOPPING_ROUNDS = 50


@dataclass
class FeatureBundle:
    """Every column any variant might need, aligned row-for-row with the
    SecondInningsDataset it was built from. `select(variant)` slices out
    exactly the columns that variant uses."""

    columns: dict[str, np.ndarray]
    label: np.ndarray
    match_id: np.ndarray

    def select(self, variant: Variant) -> tuple[np.ndarray, list[str]]:
        names = VARIANT_FEATURES[variant]
        X = np.column_stack([self.columns[name] for name in names]).astype(np.float64)
        return X, names


def _env() -> dict[str, str]:
    env = dotenv_values(ENV_PATH)
    if not env.get("LOCAL_DATABASE_URL"):
        sys.exit(f"LOCAL_DATABASE_URL must be set in {ENV_PATH}")
    return env


def _none_to_nan(values: list) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=np.float64)


def _fetch_enrichment(conn, delivery_ids: np.ndarray) -> dict[str, np.ndarray]:
    """Per-row columns beyond splits.py's original eight, keyed by
    delivery_id and re-aligned to the input array's own order - never an
    independent temporal filter, purely enrichment of rows splits.py already
    gated (see splits.py's module docstring)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ms.delivery_id, ms.target, ms.current_run_rate, ms.rrr_minus_crr,
                   ms.partnership_runs, ms.partnership_balls, ms.balls_since_wicket,
                   m.venue_id, d.batting_team_id, d.bowling_team_id, m.format
            FROM match_states ms
            JOIN matches m ON m.match_id = ms.match_id
            JOIN deliveries d ON d.delivery_id = ms.delivery_id
            WHERE ms.delivery_id = ANY(%s)
            """,
            (delivery_ids.tolist(),),
        )
        rows = cur.fetchall()

    by_id = {row[0]: row[1:] for row in rows}
    missing = set(delivery_ids.tolist()) - set(by_id)
    assert not missing, f"{len(missing)} delivery_id(s) from splits.py had no enrichment row - investigate"

    ordered = [by_id[d] for d in delivery_ids.tolist()]
    cols = list(zip(*ordered))
    return {
        "target": _none_to_nan(cols[0]),
        "current_run_rate": _none_to_nan(cols[1]),
        "rrr_minus_crr": _none_to_nan(cols[2]),
        "partnership_runs": _none_to_nan(cols[3]),
        "partnership_balls": _none_to_nan(cols[4]),
        "balls_since_wicket": _none_to_nan(cols[5]),
        "venue_id": np.array(cols[6], dtype=object),  # may hold None
        "batting_team_id": np.array(cols[7], dtype=np.int64),
        "bowling_team_id": np.array(cols[8], dtype=np.int64),
        "format": np.array(cols[9], dtype=object),
    }


def _match_level_features(
    conn,
    match_id: np.ndarray,
    match_date: np.ndarray,
    venue_id: np.ndarray,
    batting_team_id: np.ndarray,
    bowling_team_id: np.ndarray,
    format_: np.ndarray,
    min_venue_matches: int = 10,
) -> dict[str, np.ndarray]:
    """elo_diff/venue features are constant per match - compute once per
    unique match_id (looping the as-of primitives directly; see the plan's
    assumption 3 for why a batched SQL alternative isn't built preemptively),
    then broadcast back onto every row of that match."""
    _, first_idx = np.unique(match_id, return_index=True)
    per_match_elo_diff: dict[int, float] = {}
    per_match_venue_rate: dict[int, float] = {}
    per_match_venue_avg: dict[int, float] = {}

    for idx in first_idx:
        mid = int(match_id[idx])
        d = match_date[idx].item()  # numpy datetime64[D] -> python date via .item()
        vid = venue_id[idx]
        bat_id, bowl_id, fmt = int(batting_team_id[idx]), int(bowling_team_id[idx]), format_[idx]

        bat_elo = elo_as_of(conn, bat_id, fmt, d)
        bowl_elo = elo_as_of(conn, bowl_id, fmt, d)
        per_match_elo_diff[mid] = bat_elo - bowl_elo

        if vid is None:
            per_match_venue_rate[mid] = np.nan
            per_match_venue_avg[mid] = np.nan
        else:
            rate = venue_chase_win_rate_as_of(conn, int(vid), d, min_matches=min_venue_matches)
            avg = venue_avg_first_innings_as_of(conn, int(vid), d, min_matches=min_venue_matches)
            per_match_venue_rate[mid] = np.nan if rate is None else rate
            per_match_venue_avg[mid] = np.nan if avg is None else avg

    elo_diff = np.array([per_match_elo_diff[int(m)] for m in match_id], dtype=np.float64)
    venue_rate = np.array([per_match_venue_rate[int(m)] for m in match_id], dtype=np.float64)
    venue_avg = np.array([per_match_venue_avg[int(m)] for m in match_id], dtype=np.float64)
    return {"elo_diff": elo_diff, "venue_chase_win_rate": venue_rate, "venue_avg_first_innings": venue_avg}


def build_feature_bundle(conn, ds: SecondInningsDataset) -> FeatureBundle:
    """The one place this model's full feature set gets assembled - state
    (splits.py + enrichment), venue, and Elo, all as-of correctly."""
    enrichment = _fetch_enrichment(conn, ds.delivery_id)
    match_level = _match_level_features(
        conn,
        ds.match_id,
        ds.match_date,
        enrichment["venue_id"],
        enrichment["batting_team_id"],
        enrichment["bowling_team_id"],
        enrichment["format"],
    )

    phase_code = np.array([PHASE_CODE[p] for p in ds.phase], dtype=np.float64)

    columns = {
        "balls_remaining": ds.balls_remaining.astype(np.float64),
        "wickets_in_hand": ds.wickets_in_hand.astype(np.float64),
        "runs_required": ds.runs_required.astype(np.float64),
        "required_run_rate": ds.required_run_rate.astype(np.float64),
        "current_run_rate": enrichment["current_run_rate"],
        "rrr_minus_crr": enrichment["rrr_minus_crr"],
        "target": enrichment["target"],
        "partnership_runs": enrichment["partnership_runs"],
        "partnership_balls": enrichment["partnership_balls"],
        "balls_since_wicket": enrichment["balls_since_wicket"],
        "phase_code": phase_code,
        **match_level,
    }
    return FeatureBundle(columns=columns, label=ds.label.astype(np.float64), match_id=ds.match_id)


def train_lgb(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_names: list[str],
) -> lgb.Booster:
    categorical = [PHASE_CODE_INDEX] if "phase_code" in feature_names else []
    train_set = lgb.Dataset(X_train, label=y_train, feature_name=feature_names, categorical_feature=categorical)
    val_set = lgb.Dataset(X_val, label=y_val, feature_name=feature_names, categorical_feature=categorical,
                           reference=train_set)
    booster = lgb.train(
        LGB_PARAMS,
        train_set,
        num_boost_round=NUM_BOOST_ROUND,
        valid_sets=[val_set],
        callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False), lgb.log_evaluation(period=0)],
    )
    return booster


def fit_isotonic(y_val: np.ndarray, p_val_raw: np.ndarray) -> IsotonicRegression:
    """Fit on VALIDATION predictions only (SPEC.md section 6.2/9.3) - never
    train (the model is overfit there, so the predicted-vs-true mapping
    would itself be wrong), never test."""
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(p_val_raw, y_val)
    return iso


def predict_calibrated(booster: lgb.Booster, iso: IsotonicRegression, X: np.ndarray) -> np.ndarray:
    raw = booster.predict(X, num_iteration=booster.best_iteration)
    return iso.predict(raw)


if __name__ == "__main__":
    import argparse
    import json
    import time

    from eval.splits import get_second_innings_split

    parser = argparse.ArgumentParser(prog="win_prob_2nd")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("train", help="train the full (state+venue+elo) model and persist the artifact")
    args = parser.parse_args()

    if args.command == "train":
        import joblib

        ARTIFACT_DIR = REPO_ROOT / "api" / "data" / "models" / "win_prob_2nd"
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()
        with psycopg.connect(_env()["LOCAL_DATABASE_URL"]) as conn:
            train_ds = get_second_innings_split(conn, "train")
            val_ds = get_second_innings_split(conn, "val")
            train_bundle = build_feature_bundle(conn, train_ds)
            val_bundle = build_feature_bundle(conn, val_ds)

        X_train, names = train_bundle.select("state_venue_elo")
        X_val, _ = val_bundle.select("state_venue_elo")
        booster = train_lgb(X_train, train_bundle.label, X_val, val_bundle.label, names)
        p_val_raw = booster.predict(X_val, num_iteration=booster.best_iteration)
        iso = fit_isotonic(val_bundle.label, p_val_raw)

        artifact_path = ARTIFACT_DIR / f"seed{SEED}_{int(time.time())}.pkl"
        joblib.dump(
            {"booster": booster, "isotonic": iso, "feature_names": names, "seed": SEED},
            artifact_path,
        )
        elapsed = time.monotonic() - start
        print(f"Trained in {elapsed:.1f}s, best_iteration={booster.best_iteration}, artifact={artifact_path}")
