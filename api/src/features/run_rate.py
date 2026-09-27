"""The run-rate features a chase model sees (SPEC.md section 11, "Next model
task - the run rate before a chase has one").

`match_states.current_run_rate` is score / overs, missing at 0 balls. A
one-ball run rate is 0 or 6 and means nothing, and the model read the switch
from missing to 0.0 as "scoring nothing" - on 8429 the first legal ball moved
India 45% -> 25% with nothing about the match having changed.

This module is the ONE place the model-facing `current_run_rate` and
`rrr_minus_crr` are computed. Training (`models.win_prob_2nd`) and serving
(`ingest.replay.predict_win_prob`) both call `run_rate_features` with the
spec stored in the artifact, so a serving process cannot pick a different N
or k from the one the model was trained with. `match_states` keeps the raw
columns unchanged; they are an audit record, not a model input.

Kinds:
  raw           score / overs, missing at 0 balls (today's behaviour)
  min_balls     raw, but missing until `n` legal balls have been bowled
  shrunk_prior  (6 * runs + k * prior) / (balls + k), prior = a per-format
                innings-2 run rate fitted on the train split only
  shrunk_target the same, shrunk toward the chase's own initial required
                rate, target / scheduled overs - nothing fitted
  none          both features dropped from the model
"""

from __future__ import annotations

import numpy as np

RUN_RATE_FEATURES = ("current_run_rate", "rrr_minus_crr")


def run_rate_features(
    spec: dict,
    score,
    balls_bowled,
    balls_remaining,
    target,
    required_run_rate,
    format_,
) -> tuple[np.ndarray, np.ndarray]:
    """(current_run_rate, rrr_minus_crr) as float64 arrays, NaN where undefined.

    Scalar callers pass one-element sequences. `required_run_rate` is taken
    at float32, which is what match_states stores and what the incremental
    builder returns, so the two input paths cannot differ in its last bits.
    """
    kind = spec["kind"]
    if kind == "none":
        raise ValueError("kind 'none' has no run-rate features to compute")

    score = np.asarray(score, dtype=np.float64)
    balls = np.asarray(balls_bowled, dtype=np.float64)
    rrr = np.asarray(required_run_rate, dtype=np.float32).astype(np.float64)

    with np.errstate(divide="ignore", invalid="ignore"):
        if kind in ("raw", "min_balls"):
            min_balls = 1 if kind == "raw" else spec["n"]
            crr = np.where(balls >= min_balls, 6.0 * score / balls, np.nan)
        elif kind == "shrunk_prior":
            fmt = np.asarray(format_, dtype=object)
            prior = np.array([spec["prior"][f] for f in fmt], dtype=np.float64)
            crr = (6.0 * score + spec["k"] * prior) / (balls + spec["k"])
        elif kind == "shrunk_target":
            scheduled = balls + np.asarray(balls_remaining, dtype=np.float64)
            prior = 6.0 * np.asarray(target, dtype=np.float64) / scheduled
            crr = (6.0 * score + spec["k"] * prior) / (balls + spec["k"])
        else:
            raise ValueError(f"unknown run-rate kind {kind!r}")

    return crr, rrr - crr


def feature_names_for(spec: dict, base_names: list[str]) -> list[str]:
    """The model's feature list under `spec`: `base_names` unchanged, except
    that kind 'none' drops both run-rate features."""
    if spec["kind"] == "none":
        return [n for n in base_names if n not in RUN_RATE_FEATURES]
    return list(base_names)
