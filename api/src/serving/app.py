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

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from features.as_of import compute_as_of_features
from features.match_state import MatchStateRow
from ingest.replay import predict_win_prob
from serving import startup
from models.artifact import ModelVersionMismatch
from serving.db import ConnectionCoolingDown, describe

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
    # The ball key (Phase 3 session 1). Optional so a caller predating the
    # prediction log still works, but a request without it writes a row the
    # unique index cannot protect - the replay logger always sends it.
    over_num: int | None = None
    ball_in_over: int | None = None
    venue_id: int | None = None
    format: str = "T20"
    match_date: str = Field(description="ISO date; the as-of key (SPEC.md section 6.2)")


class WinProbResponse(BaseModel):
    prediction_id: int
    model_version: str
    win_probability: float
    features_used: dict
    # False when the ball key already existed, i.e. this POST was a retry of
    # one that had already committed. The caller needs to be able to tell the
    # difference; the old endpoint could not, which is how 125 posts became
    # 128 rows.
    logged: bool = True


@app.get("/health")
def health(response: Response) -> dict:
    """Ask the database, do not report a fact cached at boot.

    The first version returned `status: ok` throughout a real Supabase pause,
    because every value in it was collected at startup and nothing here
    touched the connection. A monitor would have seen green while the database
    was gone - which is worse than no health check, because it is trusted.
    """
    if not _state:
        raise HTTPException(status_code=503, detail="startup checks have not completed")

    # Refresh the SHARED facts, then copy for the response. Refreshing a copy
    # would mean the periodic re-check wrote into a dict that is thrown away,
    # so every cached call would keep serving the startup values - the exact
    # staleness this re-check exists to remove.
    shared = _state["facts"]
    facts = dict(shared)
    facts["uptime_seconds"] = round(startup.uptime_seconds(), 1)

    reachable, kind, detail = _state["database"].probe(allow_reconnect=True)
    facts["db_reachable"] = reachable
    facts["db_status"] = kind or ("ok" if reachable else "unknown")
    if not reachable:
        kind = kind or _state["database"].last_kind
        facts["db_status"] = kind or "unknown"
        facts["db_detail"] = describe(kind) if kind else detail
        facts["status"] = "degraded"
        response.status_code = 503
        return facts

    # Reference freshness re-checked here, not trusted from boot. See
    # serving/startup.py's REFERENCE_RECHECK_SECONDS.
    startup.refresh_endpoint_state(shared)
    stale = startup.refresh_reference_state(_state["conn"], shared)
    facts.update(
        {
            k: v
            for k, v in shared.items()
            if k.startswith(("reference_", "db_resolved", "db_address", "facts_measured"))
        }
    )
    if stale:
        facts["status"] = "degraded"
        facts["db_detail"] = stale
        response.status_code = 503
        return facts

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

    # Establish that the connection is LIVE before using it, then reconnect if
    # it is not. A Supabase pause terminates the connection server-side, and
    # psycopg only discovers that on the next query - so `closed` is still
    # False and a plain get() would hand back a corpse. Without this the
    # service returned 500s until someone redeployed.
    database = _state["database"]
    reachable, _, _ = database.probe()
    if not reachable:
        database.discard()
    try:
        # max_attempts=1: fail fast rather than sleeping inside a request. The
        # holder's own cooldown, not a retry here, is what keeps a paused
        # project from being hammered.
        conn = database.get(max_attempts=1)
    except ConnectionCoolingDown as exc:
        # Nothing was attempted; report the last real cause.
        raise HTTPException(
            status_code=503, detail=describe(database.last_kind or "other")
        ) from exc
    except Exception as exc:  # noqa: BLE001 - reported to the caller below
        kind = database.discard(exc)
        raise HTTPException(status_code=503, detail=describe(kind or "other")) from exc
    _state["conn"] = conn
    model = _state["model"]

    # Confirm the pin is still the active version BEFORE producing a number.
    # A promotion (SPEC.md section 8.4) while this container is alive would
    # otherwise have it write rows tagged with a model that did not produce
    # them, which is what Phase 3's accuracy page groups calibration by.
    # Refuse rather than guess: the probability came from the old artifact, so
    # neither label would be true.
    try:
        _state["version_guard"].confirm(conn)
    except ModelVersionMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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

    try:
        probability = predict_win_prob(model["artifact"], row, as_of)
    except Exception as exc:  # noqa: BLE001 - a dead connection must not 500
        kind = _state["database"].discard(exc)
        raise HTTPException(status_code=503, detail=describe(kind or "other")) from exc
    if probability is None:
        raise HTTPException(status_code=400, detail="no prediction produced for this state")

    # Enriched so the match page needs nothing but `predictions`, `matches`
    # and the reference tables (Phase 2 session 5, Decision on match_states).
    #
    # The browser cannot read `match_states` at all - it has no anon grant and,
    # since 20260918000001, RLS enabled with no policy. Carrying the state the
    # header needs in the payload avoids writing corpus tables to Supabase,
    # which would in turn force `match_states`' four REAL columns to NUMERIC
    # (see docs/phase2-session4b-railway.md's open items). `phase` matters
    # doubly: `match_phase` is the literal 'innings2' for every row, so the
    # real powerplay/middle/death phase is otherwise never persisted, and
    # SPEC.md 12.2's confidence labelling needs it.
    payload = {
        "p": probability,
        "innings": request.innings,
        "balls_bowled": request.balls_bowled,
        "balls_remaining": request.balls_remaining,
        "runs_required": request.runs_required,
        "score": request.score,
        "wickets": request.wickets,
        "target": request.target,
        "phase": request.phase,
    }
    # All three key columns or none of them. A partial key would sit inside
    # the partial index with a NULL in it, and NULLs never conflict, so it
    # would be silently unprotected - worse than having no key at all,
    # because the column list would suggest otherwise.
    has_ball_key = request.over_num is not None and request.ball_in_over is not None
    key = (
        (request.innings, request.over_num, request.ball_in_over)
        if has_ball_key
        else (None, None, None)
    )

    # ON CONFLICT DO NOTHING against the ball key (Phase 3 session 1,
    # migration 20260918000003). The endpoint is retried by the transport -
    # measured: 125 posts, 128 rows - and a duplicate is harmless on the
    # curve but double-counts in a calibration bin. DO NOTHING rather than
    # DO UPDATE: the first row for a ball is the one that was served, and
    # rewriting it would quietly change a prediction that has already been
    # shown to someone.
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO predictions
                (match_id, delivery_id, model_version, prediction_type, payload,
                 match_phase, created_at, innings, over_num, ball_in_over)
            VALUES (%s, NULL, %s, 'win_prob', %s, 'innings2', %s, %s, %s, %s)
            ON CONFLICT (match_id, model_version, prediction_type, innings, over_num, ball_in_over)
                WHERE innings IS NOT NULL
                DO NOTHING
            RETURNING prediction_id
            """,
            (
                request.match_id,
                model["model_version"],
                json.dumps(payload),
                datetime.now(timezone.utc),
                *key,
            ),
        )
        row = cur.fetchone()

    logged = row is not None
    if logged:
        prediction_id = row[0]
    else:
        # The key already existed, so this POST is a retry of one that
        # committed. Return the existing row rather than a 409: the caller
        # asked for this ball to be scored and logged, and it is.
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT prediction_id FROM predictions
                WHERE match_id = %s AND model_version = %s AND prediction_type = 'win_prob'
                  AND innings = %s AND over_num = %s AND ball_in_over = %s
                """,
                (
                    request.match_id,
                    model["model_version"],
                    request.innings,
                    request.over_num,
                    request.ball_in_over,
                ),
            )
            prediction_id = cur.fetchone()[0]

    return WinProbResponse(
        prediction_id=prediction_id,
        model_version=model["model_version"],
        win_probability=probability,
        features_used=as_of,
        logged=logged,
    )
