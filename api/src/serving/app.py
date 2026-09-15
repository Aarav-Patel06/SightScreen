"""FastAPI prediction service (SPEC.md sections 2.2 and 3, Phase 2 session 4).

Deliberately two endpoints. This session's job is to prove a container can be
deployed, reach Supabase through the pooler, load a verified model, and write
a prediction that can be read back out - not to build the Phase 3 surface.
Everything else waits until there is a frontend asking for it.

  GET  /health            what this process actually resolved and loaded
  POST /predict/win-prob  one ball in, one calibrated probability out, one
                          row in predictions

The prediction itself reuses ingest/replay.py's `predict_win_prob` rather
than reimplementing it. That function already reads the feature order out of
the artifact instead of hardcoding one, and Phase 2 session 1 built it to be
the single scoring path; a second copy here is exactly the divergence session
3 spent itself eliminating.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from features.as_of import compute_as_of_features
from features.match_state import MatchStateRow
from ingest.replay import predict_win_prob
from serving import startup

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup checks run here, not at import, so a failure is a clean fatal
    # rather than an import-time traceback with no context.
    _state.update(startup.run_checks(startup.service_role()))
    yield
    conn = _state.get("conn")
    if conn is not None:
        conn.close()


app = FastAPI(title="SightScreen prediction service", lifespan=lifespan)


class WinProbRequest(BaseModel):
    """One innings-2 ball. Field names match match_states' columns."""

    match_id: int = Field(description="matches.match_id as it exists on Supabase")
    innings: int = 2
    score: int
    wickets: int
    balls_bowled: int
    balls_remaining: int
    target: int
    runs_required: int
    current_run_rate: float | None = None
    required_run_rate: float | None = None
    rrr_minus_crr: float | None = None
    partnership_runs: int = 0
    partnership_balls: int = 0
    balls_since_wicket: int = 0
    phase: str
    batting_team_id: int
    bowling_team_id: int
    venue_id: int | None = None
    format: str = "T20"
    match_date: str = Field(description="ISO date; the as-of key (SPEC.md section 6.2)")


class WinProbResponse(BaseModel):
    prediction_id: int
    model_version: str
    win_probability: float
    features_used: dict


@app.get("/health")
def health() -> dict:
    if not _state:
        raise HTTPException(status_code=503, detail="startup checks have not completed")
    facts = dict(_state["facts"])
    facts["uptime_seconds"] = round(startup.uptime_seconds(), 1)
    facts["status"] = "ok"
    return facts


@app.post("/predict/win-prob", response_model=WinProbResponse)
def predict(request: WinProbRequest) -> WinProbResponse:
    if not _state:
        raise HTTPException(status_code=503, detail="startup checks have not completed")
    if request.innings != 2:
        raise HTTPException(
            status_code=400,
            detail="only innings 2 is modelled; the first-innings score projection is Phase 4",
        )

    conn = _state["conn"]
    model = _state["model"]

    # As-of features come from Supabase's summaries via the SAME helper
    # training calls (session 3, Decision 3). The only difference between
    # this call and the training one is which connection it receives.
    as_of = compute_as_of_features(
        conn,
        request.venue_id,
        request.batting_team_id,
        request.bowling_team_id,
        request.format,
        datetime.fromisoformat(request.match_date).date(),
    )

    row = MatchStateRow(
        match_id=request.match_id,
        innings=request.innings,
        score=request.score,
        wickets=request.wickets,
        balls_bowled=request.balls_bowled,
        balls_remaining=request.balls_remaining,
        target=request.target,
        runs_required=request.runs_required,
        current_run_rate=request.current_run_rate,
        required_run_rate=request.required_run_rate,
        rrr_minus_crr=request.rrr_minus_crr,
        partnership_runs=request.partnership_runs,
        partnership_balls=request.partnership_balls,
        balls_since_wicket=request.balls_since_wicket,
        phase=request.phase,
        batter_runs_so_far=0,
        batter_balls_faced=0,
    )

    probability = predict_win_prob(model["artifact"], row, as_of)
    if probability is None:
        raise HTTPException(status_code=400, detail="no prediction produced for this state")

    payload = {
        "p": probability,
        "innings": request.innings,
        "balls_bowled": request.balls_bowled,
        "runs_required": request.runs_required,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO predictions
                (match_id, delivery_id, model_version, prediction_type, payload,
                 match_phase, created_at)
            VALUES (%s, NULL, %s, 'win_prob', %s, 'innings2', %s)
            RETURNING prediction_id
            """,
            (
                request.match_id,
                model["model_version"],
                json.dumps(payload),
                datetime.now(timezone.utc),
            ),
        )
        prediction_id = cur.fetchone()[0]

    return WinProbResponse(
        prediction_id=prediction_id,
        model_version=model["model_version"],
        win_probability=probability,
        features_used=as_of,
    )
