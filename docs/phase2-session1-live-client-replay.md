# Phase 2 session 1 — LiveClient interface and replay mode

Built backwards on purpose: replay first, real provider later (still not
chosen — SPEC.md §15). Nothing here is shaped around any one provider's
schema; `LiveClient`'s domain objects are the only contract, and the
interchangeability suite proves that structurally, not conventionally.

## What got built

- `ingest/live_client.py` — `LiveClient` protocol, `Delivery`/`MatchState`/
  `MatchSummary` domain objects (provider-neutral, resolved canonical IDs
  only), `PhaseTransitionEvent`, and `_StaticLiveClient` (a minimal,
  explicitly test-only second implementation).
- `ingest/replay.py` — `ReplayClient` (reads an already-loaded historical
  match from local Postgres, paces ball visibility by wall-clock time
  rather than sleeping inside its own methods), `detect_phase_transition`,
  `predict_win_prob`, `compute_as_of_features`, `write_with_retry`, and a
  CLI (`python -m ingest.replay <match_id> --speed=... [--with-predictions]`).
- `features/match_state.py` gains `IncrementalMatchStateBuilder` — the
  same `REBUILD_SQL` logic, maintained as running Python state across a
  `Delivery` stream instead of window functions over a complete table.

## The real result: the parity test found a genuine bug

The every-ball, three-fixture parity test (`FIXTURE_NORMAL`, `FIXTURE_REDUCED`,
plus a freshly-selected DLS-decided match, cricsheet 1399119 — target
revised to 65 off 5 overs) is the deliverable this session was actually
about. It caught two real discrepancies before either could reach anything
resembling production:

1. **Float wire-format, not a real bug.** Postgres's `real` type is
   transmitted as a ~6-significant-digit decimal string, not the raw 4-byte
   pattern — `float(np.float32(10.8))` and what psycopg reads back from a
   `real` column storing that same value are two *different* float64 bit
   patterns (`0x1.59999a...` vs `0x1.599999...`), confirmed by direct
   comparison. Fixed by comparing the three rate columns with a small
   tolerance instead of exact equality — correctly documented as a
   comparison-methodology fix, not a loosened test.
2. **A genuine rounding-convention mismatch, not cosmetic.** The DLS
   fixture's reduced (30-ball) chase hit `scheduled_balls * (powerplay_frac
   + middle_frac) = 22.5` exactly — a real `.5` boundary. `REBUILD_SQL`
   casts these fractions to `::numeric` before `ROUND()`, and Postgres's
   `ROUND(numeric)` rounds half **away from zero** (`ROUND(22.5::numeric) =
   23`, confirmed empirically) — but Python's built-in `round()` and
   Postgres's own `ROUND(double precision)` both round half **to even**
   (`22`). The incremental builder was calling `round()` for phase
   boundaries and got `middle` where the bulk builder said `death` for that
   exact ball. Fixed with `_round_half_up`, used only where `REBUILD_SQL`'s
   own `::numeric` cast requires it — the target-overs fractional-ball
   calculation has no such cast and correctly keeps Python's `round()`,
   which already matches Postgres's `double precision` behavior there.

This is exactly the failure mode the session's own framing warned about:
"a mismatch means the model sees a different distribution at serving time
than at training time, and nothing in the output would reveal it." Nothing
*did* reveal it except the per-ball, ground-truth comparison — a coarser
test (spot-checking a few balls, or comparing only aggregate stats) would
have missed both issues, especially the rounding one, which only manifests
at an exact `.5` boundary a reduced-overs match happens to hit.

## Interchangeability, structurally

`tests/ingest/test_live_client.py`'s shared conformance suite runs
identically against `ReplayClient` (real match, real Postgres) and
`_StaticLiveClient` (hand-built, no database) — ball-count monotonicity,
gap/duplicate-free sequential polling, and `get_match_state`'s `innings`
agreeing with the delivery stream itself. A future real-provider adapter
runs through this same function unmodified; the guarantee lives in the
test, not in the fact that two implementations happen to agree today.

## Phase transitions: robust to the exact case that breaks a heuristic

`detect_phase_transition` fires `innings_break` from the `innings` field's
own transition, never from ball/wicket counts. Verified against the real
DLS fixture, whose first innings is well short of 20 overs — a
"20-overs-bowled" heuristic would never fire; a "10-wickets-fallen"
heuristic would fire only by coincidence. The structural signal fires
exactly once, exactly on the first ball of innings 2, and `target_runs`/
`target_overs` are already the correct, DLS-revised figures at that instant
("target locked," §7.2's biggest accuracy jump).

**Scope note:** only the ball-level events (`innings1_start`,
`innings_break`, `innings2_ball`, `match_end`) are modeled. `pre_toss`/
`toss` are only meaningful for a real provider with an actual pre-match
phase where wall-clock time passes before a ball is bowled — replay works
from an already-complete delivery stream and has no such phase to observe.
Not built here; a real provider adapter will need them.

## Prediction wiring (Decision 5)

Included, bounded to innings 2 only (no first-innings model exists yet —
§6.3/Phase 4). Reuses Phase 1's persisted artifact and exact feature order
unchanged (`models/win_prob_2nd.py`'s `STATE_FEATURES + VENUE_FEATURES +
ELO_FEATURES`), via `models/registry.load_model_version`. A live run of
`python -m ingest.replay 6290 --speed=instant --with-predictions` produces
a full, sensible win-probability curve: 0.901 at the start of the chase,
climbing smoothly to 0.998 as the batting team closes it out with wickets
in hand — real end-to-end verification the parity test alone doesn't give,
though the parity test remains the actual risk-reducing deliverable (a
state-building bug could still produce a plausible-looking but wrong
probability with nothing in one demo run to reveal it).

## As-of features at serving time

`compute_as_of_features` calls the exact same `elo_as_of`/venue-as-of
functions training does — verified directly
(`tests/ingest/test_live_asof_parity.py`): for a real match, live-computed
and training-computed `elo_diff`/`venue_chase_win_rate`/
`venue_avg_first_innings` are identical, not just similar. A cold-start
venue gets the identical `None → NaN` fallback the model was trained on,
by construction.

## Write targets (Decision 6)

| Table | Real deployed worker (session 2+) | This session's CLI |
|---|---|---|
| `matches`/`deliveries`/`match_states` (live match) | Supabase | not written (replay only reads) |
| `predictions` | Supabase | local Postgres (default; printed only in this session's CLI, no write path built yet) |
| `model_versions` | local only (Phase 1 precedent) | local only |
| `elo_ratings` | not updated live — picked up by the next periodic `elo.py rebuild` | unchanged |

`write_with_retry` (a few attempts, exponential backoff, logs and gives up
rather than raising) is built and unit-tested against a mocked failure —
ready for whichever write path session 2+ wires to Supabase, given your
free-tier project pauses after a week idle.

## Open architecture question, surfaced not solved

The real, Railway-deployed worker's `elo_as_of`/venue as-of lookups need
the *full* historical corpus, which only exists in local Postgres —
directly in tension with §2.1's "serving never touches local Postgres."
Two resolutions, neither built this session: (a) a narrow, explicit
exception for these specific reads, or (b) a periodic Elo/venue-summary
sync job to Supabase. This needs a decision before Railway deployment
(session 2+), not before this session's replay-only scope. Logged in
SPEC.md §15.

Replay's own source-data read from local Postgres is a separate, already-
resolved exception (assumption 5 in the session's plan) — a deliberate
dev/test-tool design choice, not the same open question as the one above.

## Known, named limitations (not solved this session)

- **Restart recovery.** A `get_match_state()` snapshot alone can't
  reconstruct partnership/balls-since-wicket/batter-cumulative state after
  a worker crash mid-match. Continuous operation from ball one is this
  session's assumption.
- **A true mid-chase DLS re-revision** can't be exercised through replay of
  real Cricsheet data (it only records the final, settled target). Covered
  by a synthetic unit test of `set_target()` called twice instead
  (`test_incremental_builder_picks_up_a_target_revision_mid_chase`) — proves
  the code path, not a real historical scenario.

## Verification

- `pytest tests/ingest/test_live_client.py -v` — both implementations pass
  the identical conformance suite.
- `pytest tests/features/test_match_state.py -k incremental -v` — every-ball
  parity holds for all three fixtures (after the two fixes above); the
  synthetic target-revision test passes.
- `pytest tests/ingest/test_replay.py -v` — phase events fire correctly
  against the real DLS fixture; the retry wrapper behaves correctly against
  a mocked failure.
- `pytest tests/ingest/test_live_asof_parity.py -v` — live and training
  as-of features match exactly for a real match.
- `python -m ingest.replay 6290 --speed=instant --with-predictions` — full
  win-probability curve, printed, end to end.
- `pytest tests -q` (excluding known-slow idempotent-rebuild tests) — 123
  passed, 3 deselected, no regressions.
