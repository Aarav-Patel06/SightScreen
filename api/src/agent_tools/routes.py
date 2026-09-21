"""The agent's tool endpoints (SPEC.md §10.2), behind the guard.

Four tools are buildable against the corpus as it exists:

    query_ball_data      arbitrary SELECT over the five views, guarded
    get_matchup          one batter against one bowler
    resolve_entity       a name to ranked candidates, read-only
    get_live_prediction  the current win probability for a live match

`get_player_form` is the fifth §10.2 names and it is NOT buildable. It
returns a structured unavailability instead - see player_form() below for
why that is not the same thing as a refusal to be useful.

THE SESSION 2 BOUNDARY. This file is the tool layer only. The agent loop,
the Anthropic SDK, the system prompt and §10.4's citation enforcement are
session 2. That split is deliberate: security-critical code gets its own
session with its own gate.

Connections are opened per request rather than held. FastAPI runs sync
endpoints in a threadpool, so a single shared psycopg connection would be
used concurrently from several threads, and the guard's SET LOCAL needs its
own transaction - two threads sharing one connection would have one's timeout
silently apply to the other's query. A connect costs tens of milliseconds
against analytical queries budgeted at five seconds.
"""

from __future__ import annotations

import hmac
import sys
import time
import uuid

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from agent_tools import name_forms
from agent_tools.sql_guard import MAX_ROWS, GuardRejection, check
from config import _looks_like_placeholder, settings
from ingest.entity_resolution import (
    _score_candidates,
    normalize_name,
    surname_blocks,
    surname_key,
)

router = APIRouter(prefix="/agent", tags=["agent"])

STATEMENT_TIMEOUT = "5s"

# How many rows the MODEL is shown, as against the MAX_ROWS the guard allows
# the database to return. These are different limits for different reasons and
# both are reported.
#
# The cost argument is real - a 1,000-row JSON payload measured 24,446 tokens
# and is re-sent on every subsequent turn - but it is the weaker one. The
# stronger one: handing a model a thousand rows invites it to compute an
# aggregate by READING them instead of asking SQL for the number. That is
# precisely the behaviour §10.4's citation rule exists to prevent, so a tool
# that makes it convenient is working against the system prompt. A count
# derived by eyeballing rows also has no sample size to quote, because the
# model never asked for one.
#
# So the rows are a SAMPLE, labelled as such, and the true count is a
# separate field. Aggregate in SQL, where the answer comes back with its own
# denominator.
MODEL_ROWS = 50
MAX_CANDIDATES = 5

# A floor, not a marker list. config._looks_like_placeholder catches the
# three strings anyone thought to enumerate - "changeme" is the one actually
# committed in .env.example - but an enumerated list only ever catches the
# placeholders someone already imagined. Anything this short is not a shared
# secret whatever it spells, and 32 is what secrets.token_urlsafe(24) yields,
# so the rule cannot reject a correctly generated value.
MIN_SECRET_LENGTH = 32


# --- authentication, failing closed ---------------------------------------


def require_agent_secret(x_agent_secret: str | None = Header(default=None)) -> None:
    """Fail closed on absent, placeholder, and wrong.

    The placeholder case is the one worth stating: AGENT_TOOL_SHARED_SECRET
    is `str | None` and the literal "changeme" loads fine today - tests/
    test_config.py:81-92 asserts that it does, because the field belongs to a
    phase that had not started. A deploy that forgot to set it would
    otherwise authenticate anyone who read the committed .env.example.
    """
    configured = settings.agent_tool_shared_secret
    if (
        not configured
        or _looks_like_placeholder(configured)
        or len(configured) < MIN_SECRET_LENGTH
    ):
        # 503, not 401: the caller did nothing wrong, the service is
        # misconfigured, and saying so to the operator is not a leak.
        raise HTTPException(status_code=503, detail="agent tools are not configured")
    if x_agent_secret is None or not hmac.compare_digest(x_agent_secret, configured):
        raise HTTPException(status_code=401, detail="unauthorized")


def _replica_connection() -> psycopg.Connection:
    url = settings.agent_sql_role_db_url
    if not url:
        raise HTTPException(status_code=503, detail="agent corpus is not configured")
    try:
        # autocommit off: SET LOCAL is a no-op outside a transaction, and
        # layer 3 is a SET LOCAL.
        return psycopg.connect(url, row_factory=dict_row)
    except psycopg.Error as exc:
        # The URL is never interpolated into a message. Same rule the
        # calibration monitor got after the fifth failure surface.
        raise HTTPException(status_code=503, detail="agent corpus unreachable") from exc


# --- the query log --------------------------------------------------------


def _log_query(ref: str, sql: str, verdict: str, layer: str | None,
               rows: int | None, duration_ms: int) -> None:
    """Server-side record of every query, on Supabase.

    Deliberately the other database: the read-only role cannot write
    anywhere, which is the point of it, so the log cannot live beside the
    corpus. The api already holds a privileged Supabase connection.

    Logs the SQL and the verdict. Never a connection string, never the
    shared secret. A failure to log must not fail the request - the log is
    for us, and losing a row is better than turning a served answer into a
    500.
    """
    url = settings.supabase_session_pooler_url
    if not url:
        return
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO agent_query_log "
                "(query_ref, sql_text, verdict, rejected_by, row_count, duration_ms) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (ref, sql, verdict, layer, rows, duration_ms),
            )
    except Exception as exc:  # noqa: BLE001 - logging must never break the request
        # But it must not fail SILENTLY either. A swallowed failure here
        # means every query looks logged and none are, which is the shape
        # the calibration monitor was caught in: a check that reports
        # nothing is indistinguishable from a check that passes. Print the
        # exception TYPE and the ref - never the message, which for a
        # connection error contains the host, and never the SQL.
        print(
            f"agent_query_log write failed: {type(exc).__name__} (ref {ref})",
            file=sys.stderr,
            flush=True,
        )


def _rejected(ref: str) -> HTTPException:
    """One shape, whatever the cause (Decision 5).

    No table name, no column name, no layer identity. An error saying
    `relation "users" does not exist` is free reconnaissance, and a message
    that varies by cause is an oracle telling the caller which layer they
    tripped. The ref is a random uuid that means nothing to the caller and
    joins to the server-side log for us.
    """
    return HTTPException(
        status_code=400,
        detail={"error": GuardRejection.PUBLIC_MESSAGE, "ref": ref},
    )


# --- 1. query_ball_data ---------------------------------------------------


class SqlRequest(BaseModel):
    sql: str = Field(description="A single SELECT over the agent_* views")


@router.post("/query_ball_data", dependencies=[Depends(require_agent_secret)])
def query_ball_data(request: SqlRequest) -> dict:
    ref = str(uuid.uuid4())
    started = time.perf_counter()

    try:
        # One row past the ceiling, so truncation can be REPORTED rather than
        # inferred from a row count that looks identical either way.
        guarded = check(request.sql, probe_extra_row=True)
    except GuardRejection as rejection:
        _log_query(
            ref, request.sql, "rejected", rejection.layer, None,
            int((time.perf_counter() - started) * 1000),
        )
        raise _rejected(ref) from None

    try:
        with _replica_connection() as conn, conn.transaction():
            conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            rows = conn.execute(guarded).fetchall()
    except psycopg.Error:
        # Layers 1 and 3 live here. A privilege denial or a cancelled query
        # gets the SAME payload as a guard rejection, so the caller cannot
        # tell "the guard stopped me" from "the database stopped me" - which
        # would otherwise map out exactly what the views expose.
        _log_query(
            ref, guarded, "database_error", "readonly_role_or_timeout", None,
            int((time.perf_counter() - started) * 1000),
        )
        raise _rejected(ref) from None

    duration_ms = int((time.perf_counter() - started) * 1000)
    # Log the RAW count, before the probe row is dropped. A logged 1001 then
    # means "truncated" unambiguously, where a logged 1000 would not.
    _log_query(ref, guarded, "ok", None, len(rows), duration_ms)

    # Two independent reductions, reported separately because they mean
    # different things. `truncated` says the DATABASE had more rows than
    # layer 4 permitted - so the result is not the whole answer.
    # `rows_shown` says how many of the rows we did get are in this payload.
    # Collapsing them would make a complete 200-row result look like a
    # truncated one, which is the ambiguity probe_extra_row exists to remove.
    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]
    row_count = len(rows)
    shown = rows[:MODEL_ROWS]
    return {
        "ref": ref,
        # The true count of what the query returned, whether or not every row
        # is below. Aggregates should come from SQL, but if a count is going
        # to be read off this payload it should at least be the right one.
        "row_count": row_count,
        "rows": shown,
        "rows_shown": len(shown),
        "rows_shown_limit": MODEL_ROWS,
        # §10.3 layer 4 caps the result; these two say so out loud. Without
        # them a truncated aggregate is indistinguishable from a complete
        # one and the agent reports a partial count as a total - confidently,
        # because nothing in the payload suggested otherwise.
        "truncated": truncated,
        "row_limit": MAX_ROWS,
        "sql": guarded,
    }


# --- 2. get_matchup -------------------------------------------------------


class MatchupRequest(BaseModel):
    batter: str
    bowler: str
    format: str | None = None


@router.post("/get_matchup", dependencies=[Depends(require_agent_secret)])
def get_matchup(request: MatchupRequest) -> dict:
    """One batter against one bowler, over the whole corpus.

    Goes through the same guard as query_ball_data rather than around it.
    A tool that builds its own SQL and executes it directly would be a sixth
    path to the database with none of the five layers on it.
    """
    filters = ["batter = %(batter)s", "bowler = %(bowler)s"]
    if request.format:
        filters.append("format = %(format)s")
    sql = (
        "SELECT count(*) AS balls, "
        "sum(runs_batter) AS runs, "
        "count(*) FILTER (WHERE wicket_type IS NOT NULL) AS dismissals "
        "FROM agent_deliveries WHERE " + " AND ".join(filters)
    )

    ref = str(uuid.uuid4())
    started = time.perf_counter()
    try:
        guarded = check(sql)
    except GuardRejection as rejection:
        _log_query(ref, sql, "rejected", rejection.layer, None, 0)
        raise _rejected(ref) from None

    params = {"batter": request.batter, "bowler": request.bowler, "format": request.format}
    try:
        with _replica_connection() as conn, conn.transaction():
            conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            row = conn.execute(guarded, params).fetchone()
    except psycopg.Error:
        _log_query(ref, guarded, "database_error", "readonly_role_or_timeout", None, 0)
        raise _rejected(ref) from None

    duration_ms = int((time.perf_counter() - started) * 1000)
    _log_query(ref, guarded, "ok", None, 1, duration_ms)

    balls = row["balls"] or 0
    runs = row["runs"] or 0
    dismissals = row["dismissals"] or 0
    return {
        "ref": ref,
        "batter": request.batter,
        "bowler": request.bowler,
        "balls": balls,
        "runs": runs,
        "dismissals": dismissals,
        # §10.4 wants a sample size quoted with every number. Returning
        # balls alongside the rate is what makes that possible without the
        # model having to infer it.
        "strike_rate": round(100.0 * runs / balls, 2) if balls else None,
        "average": round(runs / dismissals, 2) if dismissals else None,
    }


# --- 3. resolve_entity ----------------------------------------------------


class ResolveRequest(BaseModel):
    name: str
    kind: str = "player"


@router.post("/resolve_entity", dependencies=[Depends(require_agent_secret)])
def resolve_entity(request: ResolveRequest) -> dict:
    """A name to RANKED CANDIDATES. Read-only, and it does not commit to one.

    It deliberately does NOT call ingest.entity_resolution.resolve_player.
    That function writes even when asked not to: with allow_create=False it
    still INSERTs an alias row on a fuzzy match (entity_resolution.py:402-409)
    and INSERTs into unresolved_entities on a miss (:428). Wired to a tool,
    an agent asking about "Viraat Kolhi" would mint alias rows from
    model-generated strings - permanent corpus state created by a typo in a
    chat message.

    So this reuses the PURE helpers - normalize_name, surname_key and
    _score_candidates, none of which take a connection - and reads the
    views. Returning candidates rather than a winner is also the better tool
    contract: the agent can ask which one you meant, and §10.4's citation
    rule gets something to quote.
    """
    view, id_column = {
        "player": ("agent_players", "player_id"),
        "team": ("agent_teams", "team_id"),
        "venue": ("agent_venues", "venue_id"),
    }.get(request.kind, (None, None))
    if view is None:
        raise HTTPException(status_code=400, detail="kind must be player, team or venue")

    normalized = normalize_name(request.name)
    if not normalized:
        return {"query": request.name, "candidates": []}

    sql = f"SELECT {id_column} AS entity_id, name FROM {view}"
    with _replica_connection() as conn, conn.transaction():
        conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        rows = conn.execute(sql).fetchall()

    # Block on the surname bucket for players, through the SAME predicate
    # _player_candidates uses. The first version of this line compared
    # surname keys for equality and fell back to the full table when the
    # bucket came out empty; "Viraat Kolhi" then scored against all 18,468
    # players and came back with "Rahat Ali" at 66.7. Two separate mistakes,
    # and the fallback is the worse one - it turned "I found nothing" into a
    # confident wrong answer, which is the failure mode §10.4 exists to
    # prevent. No fallback now: an empty bucket returns no candidates.
    matched_deterministically = False
    if request.kind == "player":
        bucket = surname_key(normalized)
        rows = [r for r in rows if surname_blocks(bucket, r["name"])]

        # Deterministic narrowing BEFORE any similarity score. Measured
        # 2026-09-21: fuzz.ratio put A/S/T/V Kohli in a four-way tie at 83.3
        # for "Virat Kohli", and ranked E and T Malinga ABOVE SL Malinga for
        # "Lasith Malinga". Similarity cannot separate names that differ by
        # one character in a short token and share a surname, so the fix is
        # not to weight the initial - it is to stop asking.
        #
        # `narrow` keeps only candidates whose stored initials contain, in
        # order, the initials of what was actually said. It returns [] when
        # nothing is compatible and the fuzzy path below then runs unchanged,
        # so an unusual spelling still resolves.
        compatible = set(name_forms.narrow(request.name, [r["name"] for r in rows]))
        if compatible:
            rows = [r for r in rows if r["name"] in compatible]
            matched_deterministically = True

    scored = _score_candidates(
        normalized, [(r["entity_id"], r["name"]) for r in rows], request.kind
    )
    return {
        "query": request.name,
        "kind": request.kind,
        # Told to the model, because it changes what a score MEANS. After a
        # deterministic narrowing, two remaining candidates are genuinely
        # ambiguous rather than merely close - "Lasith Malinga" leaves SL and
        # LN Malinga, both of whom really do have an L - and that is a case
        # for asking the user, not for taking the top score.
        "matched_deterministically": matched_deterministically,
        "candidates": [
            {"id": c.entity_id, "name": c.canonical_name, "score": round(c.score, 1)}
            for c in scored[:MAX_CANDIDATES]
        ],
    }


# --- 4. get_live_prediction -----------------------------------------------


class LivePredictionRequest(BaseModel):
    # A BARE INT, and only a bare int (Gap 1). The views expose
    # 'corpus:<id>' AS match_ref and no bare match_id, because the corpus and
    # Supabase number matches in different SERIAL spaces over the same
    # fixtures. Passing a corpus id here used to be a silently wrong answer;
    # now it is a validation error, which is the same "make the wrong thing
    # impossible" move as the ball key.
    match_id: int = Field(description="matches.match_id ON SUPABASE, not a corpus ref")


@router.post("/get_live_prediction", dependencies=[Depends(require_agent_secret)])
def get_live_prediction(request: LivePredictionRequest) -> dict:
    url = settings.supabase_session_pooler_url
    if not url:
        raise HTTPException(status_code=503, detail="serving database is not configured")
    with psycopg.connect(url, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT prediction_id, match_id, model_version, win_probability, "
            "       innings, over_num, ball_in_over, created_at "
            "FROM predictions WHERE match_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (request.match_id,),
        ).fetchone()
    if row is None:
        return {
            "available": False,
            "reason": "no prediction has been logged for this match",
            "match_id": request.match_id,
        }
    return {"available": True, **row}


# --- 5. get_player_form, which cannot be built -----------------------------


@router.post("/get_player_form", dependencies=[Depends(require_agent_secret)])
def player_form(request: ResolveRequest) -> dict:
    """Structured unavailability. Never a rolling average dressed as form.

    This looks like theatre next to query_ball_data, which can compute a
    recent average perfectly well. It is not, because the two answer
    different questions:

      "How many runs has X scored recently?" is descriptive, and
      query_ball_data should answer it. Refusing would make the SQL tool
      useless.

      "Is X in form?" is inferential. It needs a posterior with uncertainty -
      that is player_state, and it is empty until Phase 5.

    Moving the refusal down to the data layer does not work, because the data
    layer cannot tell the two apart: the same SELECT serves both. What
    differs is the CLAIM made about the number, not the rows returned. Hiding
    the rows would break the descriptive case to prevent a framing error in
    the inferential one.

    So the refusal stays here, and the real safeguard against presenting an
    average as an ability estimate is §10.4's citation requirement, enforced
    in session 2's evals. Session 2 inherits an obligation, not a solved
    problem.
    """
    return {
        "available": False,
        "player": request.name,
        "reason": (
            "A form estimate requires a calibrated ability posterior with "
            "uncertainty. The player_state table that would hold it is empty; "
            "the hierarchical model that fills it is Phase 5."
        ),
        "what_is_available": (
            "query_ball_data can compute descriptive recent scoring - runs, "
            "balls, strike rate, dismissals over a date range - which is a "
            "record of what happened, not an estimate of ability. Quote the "
            "sample size with it."
        ),
        "not_in_the_dataset": ["batting_hand", "bowling_style", "dob"],
    }
