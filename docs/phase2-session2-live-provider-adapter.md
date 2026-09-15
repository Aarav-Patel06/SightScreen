# Phase 2 session 2 — the real live provider adapter

The session was meant to wrap CricketData's ball-by-ball feed behind
`LiveClient`. A timeboxed spike found there is no such feed to wrap, on any
provider we're willing to pay for, so the adapter reconstructs a delivery
stream from scorecard snapshots instead — and the session's real output is
an honest measurement of what that costs.

## The provider spike

One question per provider: **does a single response contain per-delivery
striker_id, bowler_id, runs, and wicket?**

| Provider | Verdict | Evidence |
|---|---|---|
| **CricketData** (key already in hand) | **No** | `match_bbb` *is* entitled on our key — `bbbEnabled` is true for 12–18 of 25 rows at 2023/2025 offsets and false across the 2026 window, so it's coverage/recency, not tier-gating. But the payload is not a delivery stream: **11, 20, 18 and 20** balls returned for matches with **~190, ~213, ~574 and ~229** actual deliveries, and *every* returned ball has `penalty` set. It is an extras log. Ball keys are `n, inning, over, ball, batsman{id,name}, bowler{id,name}, runs, penalty, extras` — striker and bowler identity are present, but **there is no wicket or dismissal field anywhere in the schema**. |
| **Big Balls Sports Data** | **No** | Settled from their public OpenAPI spec, no signup needed: the whole cricket surface is six endpoints (`series`, `matches`, `matches/{id}`, `/scorecard`, `/state`, `players/{id}`) with **zero** ball/delivery/commentary schema. Their "full ball-by-ball scorecards" copy means per-player aggregates (Balls Faced, Dismissal Type, Strike Rate). |
| **Sportmonks** | **Yes** | Documented `balls` include on fixtures/livescores: per-ball `ball` (e.g. 34.2), `batsman_id`, `bowler_id`, `score.runs`, `score.is_wicket`, plus `batting.batsman`/`batting.bowler`/`batting.runoutby`. Ball-by-ball is in **all** plans; Major €29/mo, World €75, Enterprise €125; 14-day trial. |

Total spike cost: **14 of 2,000 daily hits.** Sportmonks was rejected on
price, so CricketData stays and the adapter reconstructs.

**Recorded in SPEC.md §15**: the provider decision, and separately that
**Phase 5 (WPA, player predictions, impact leaderboard) is now blocked on a
ball-by-ball-capable provider** — snapshot reconstruction carries no
per-ball player identity, and §6.5/§6.6/§12.1 all need it. Phases 2–4 are
unaffected because the Phase 1 model uses no player features.

## What reconstruction actually costs — measured, not asserted

`python -m eval.measure_reconstruction` synthesises the snapshots a poller
would have seen from 25 completed matches already in local Postgres, runs
them through the real adapter, rebuilds state with the same
`IncrementalMatchStateBuilder` the live path uses, and compares against the
bulk-built `match_states` row by row, keyed by (innings, balls_bowled).

**The timing model matters more than it looks.** A first run used a uniform
30 s/ball and reported **0.00%** partnership error at every interval ≤30 s —
which is an artifact, not a result: with uniform gaps, at most one ball can
ever land between polls at those intervals. Real ball gaps are bursty (quick
single, then a wicket and a new batter walking in), so gaps are drawn
lognormally (σ=0.55, seeded) around the same mean. That changed the answer:

| Poll interval | Inferred balls | Wrong score | Wrong wickets | Wrong partnership_runs | Wrong partnership_balls | Max p_balls error |
|---|---|---|---|---|---|---|
| 10 s | 1.9% | 0.63% | 0.05% | 1.81% | **1.19%** | 14 |
| **15 s** (shipped) | 7.2% | 2.60% | 0.22% | 3.47% | **2.66%** | 39 |
| 30 s | 35.1% | 12.57% | 1.12% | 21.05% | **17.11%** | 75 |
| 60 s | 75.0% | 31.18% | 3.00% | 47.27% | **37.76%** | 85 |
| 120 s | 96.7% | 53.37% | 6.38% | 73.43% | **56.36%** | 85 |

5,530 states compared per interval. Degradation is steep and non-linear
past 15 s — worth knowing, because Decision 3's quota-exhaustion path backs
off to 60 s, where better than a third of partnership values are wrong.

## The retrain decision, pre-registered

Before any number existed, the plan committed to: *if more than 2% of
states at the shipped interval carry a wrong `partnership_balls`, train a
variant without the three partnership features and report the Brier cost.*

At 15 s the figure is **2.66%**, so the rule fired. This is exactly why the
threshold was fixed in advance — the uniform-timing run would have said
0.00% and waved the retrain away.

`python -m eval.measure_degraded_variant` trains both variants from one
feature bundle with identical hyperparameters and seed, so the feature set
is the only difference:

| Variant | Features | Test Brier |
|---|---|---|
| `state_venue_elo` (Phase 1's shipped model) | 14 | **0.12320** |
| `state_venue_elo_no_partnership` | 11 | **0.12396** |

**Cost of dropping the three partnership features: +0.00076 Brier, paired
match-clustered 95% CI [-0.00034, +0.00184] — not significant.** The
interval straddles zero, so on this test set the honest variant is
indistinguishable from the full one.

That settles the serving question cleanly: **the live path serves
`state_venue_elo_no_partnership`**, because a model should never be handed
a feature its feed cannot actually deliver — and here that discipline costs
nothing measurable. The full 14-feature model remains correct for offline
work against `match_states`, where partnership values are exact.

In the final 3 overs the two are indistinguishable to five decimal places
(0.06525 vs 0.06525) — the partnership features contribute essentially
nothing precisely where the prediction matters most and the model is
sharpest. Whatever they add sits in the middle overs.

(The full variant reproducing Phase 1 session 2's recorded 0.12320 to five
decimal places, from the same seed, is also a free reproducibility check on
the training path.)

## Decision 1 — the conformance suite, unmodified

`CricketDataClient` is the third implementation to pass
`tests/ingest/test_live_client.py`'s `_assert_conforms`, which was **not
touched**. Two things made that possible rather than requiring a relaxed
assertion:

- **The catch-up block.** The binding assertion is
  `len(get_deliveries_since(id, 0)) == state.ball_count`. A worker joining a
  match in progress knows the provider's ball count but has reconstructed
  nothing. Instead of weakening the invariant, the adapter emits every
  already-bowled ball as a single span flagged `INFERRED`. It also answers
  session 1's open restart-recovery question.
- **One interface change, reported rather than slipped in.**
  `Delivery.batter_id`/`non_striker_id`/`bowler_id` were non-optional `int`;
  they are now `int | None`. That is the interface being corrected, not bent
  around a provider: `deliveries.batter_id` and friends are **already
  nullable in the schema**, and `ingest/cricsheet.py` already writes `None`
  when a player resolution queues. The suite never asserted non-null player
  IDs, so it ran unchanged.

`Delivery` also gained `confidence: ReconstructionConfidence`
(`CONFIRMED`/`INFERRED`) — a real ball-by-ball provider would always emit
`CONFIRMED`, so the flag is provider-neutral rather than CricketData-shaped.

## Decision 2 — entity resolution, and why no new table

**`provider_aliases` was not built, deliberately.** `player_aliases`,
`team_aliases` and `venue_aliases` already map
`(source, source_name, source_id) → entity_id`, and the `source` column's
own schema comment reads `-- 'cricsheet' | 'cricketdata' | ...`. §4.4
designed this for exactly this moment; a resolution written with
`source='cricketdata'` is already permanent. A fourth table would have
duplicated working infrastructure.

Two live-path rules, both learned by reading Phase 0's resolver rather than
from a failure:

1. **`source_id` is always `None`.** In `_resolve`, a *present but unknown*
   `source_id` short-circuits straight to auto-create with no fuzzy scoring,
   no collision check and no queue. Passing CricketData's UUIDs would have
   minted a brand-new duplicate team and venue **for every match on first
   sight** — silently duplicating the entity universe.
2. **`allow_create=False`** (new, backwards-compatible parameter on
   `_resolve` and the three wrappers; the default preserves Phase 0
   behaviour exactly). Minting a canonical entity with no human in the loop
   is precisely the unsupervised guess a serving path must not make. The
   suppressed path queues with reason `create_suppressed`. It also stops
   Supabase minting IDs that would collide with the local→Supabase sync's
   PK-keyed upsert.

Degradation, by severity:

| Outcome | Worker behaviour |
|---|---|
| Team unresolved | **Refuse to track the match** — `elo_diff` is unobtainable and `batting_team_id` is `NOT NULL`. Mirrors `cricsheet.py`'s `RejectMatch`, recorded with the `unresolved_id`, and does not abort a poll covering other live matches. |
| Venue unresolved | **Track anyway**, `venue_id=None` → both venue features `NaN`. The identical cold-start path 24–33% of training matches already took, so calibrated accuracy is unaffected. |
| Player unresolved | Not applicable — this feed has no per-ball players at all. |

## Decision 3 — budget, enforced from the provider's own counter

Every successful response carries `info.hitsToday`/`hitsLimit`, so
`LiveBudget` is authoritative rather than a local tally that drifts after a
restart (and it confirmed the $5.99 tier: `hitsLimit: 2000`).

- Refuses a match it cannot afford to finish, before spending anything.
- No-op polls short-circuit before any reconstruction or write (§7.1 step 2).
- Adaptive interval: 15 s in play, 45 s between overs.
- Quota exhaustion **degrades rather than stops**: 60 s at 90% consumed,
  120 s at 98%.
- One `currentMatches` call covers every live match, so cost is per-poll,
  not per-match.

## Decision 4 — the validation gate

`validate_transition(prev, next)` sits at the boundary; nothing reaches
`IncrementalMatchStateBuilder` without passing it, so bad provider data
cannot corrupt `match_states`.

| Failure | Detection | Response |
|---|---|---|
| Duplicate / repeated snapshot | `ball_count` unchanged | No-op, never re-emit |
| Backwards state | balls decrease | Reject, retain last-good |
| Corrected score | runs or wickets decrease while balls hold | **Correction**: re-baseline, never emit a negative delivery, never patch already-emitted history |
| Missing balls | Δballs > 1 | Inferred span, every ball flagged |
| Extras | Δballs == 0 with Δruns > 0 | Non-legal delivery; legal ball count correctly does not advance |
| Different match | provider id mismatch | Reject |
| Disappearing innings | innings count shrinks | Reject |

## Decision 5 — latency: what is measurable, and what isn't

**The §4.3 claim cannot be validated on this feed.** No CricketData
endpoint carries a per-ball timestamp — `currentMatches`, `cricScore` and
`matches` give only `dateTimeGMT` (match start). So "ball bowled →
prediction written" is unmeasurable end to end, and nothing here estimates
the missing piece.

What the harness does measure: `provider_rtt`, `reconstruction`,
`prediction`, their sum `poll_to_write`, and a `detection_bound`
(= `poll_to_write` + poll interval) that explicitly **excludes** the
provider's own unmeasurable lag.

**Result: pending.** No match was live during the session — `cricScore`
reported 27 matches, zero with `ms == "live"`, at both the start and end of
the run. The harness was executed against the real feed and correctly
reported `status: pending` rather than inventing a figure. Next fixtures
were a county match on 2026-09-15 and CPL on 2026-09-18.

Per the reporting rules agreed before the run: **§4.3's 30–60 s claim and
the UI latency figure are untouched**, and a T20 measurement remains an
open item for the Phase 2 close-out. Budget for the eventual run is
hard-capped at **200 hits** (10% of daily) inside the tool.

## Decision 6 — fixtures

`python -m ingest.record_fixtures` captured six **real** bodies into
`tests/fixtures/cricketdata/` with the API key redacted (10-hit cap, 6
used): `currentMatches`, `cricScore`, `matches`, a successful `match_bbb`
(the extras-log proof), and both genuine failure bodies
(`ERR: Not able to get BBB…`, `Scorecard … not found`).

Decision 4's malformed cases are **synthesised by mutating those real
bodies** — the provider will misbehave, but not on demand, and waiting for
a bug we can't schedule is not a test strategy. **No test touches the
network or burns quota.**

## Files

| File | Change |
|---|---|
| `api/src/ingest/cricketdata.py` | New — `CricketDataClient`, snapshot parsing, reconstruction, `LiveBudget`, `validate_transition`, transports |
| `api/src/ingest/live_client.py` | Player IDs widened to `int \| None`; `ReconstructionConfidence` added |
| `api/src/ingest/entity_resolution.py` | `allow_create` (default `True`, unchanged behaviour); `create_suppressed` reason |
| `api/src/ingest/record_fixtures.py`, `measure_latency.py` | New tools |
| `api/src/eval/measure_reconstruction.py`, `measure_degraded_variant.py` | New measurement tools |
| `api/src/models/win_prob_2nd.py` | `state_venue_elo_no_partnership` variant; categorical index resolved by name, not a hardcoded 10 |
| `supabase/migrations/20260914000001_cricketdata_match_key.sql` | New — partial unique index on `external_ids->>'cricketdata'` |
| `tests/ingest/test_cricketdata.py` | 34 tests, all offline |
| `tests/ingest/test_live_client.py` | Third implementation added; `_assert_conforms` **unchanged** |
| `SPEC.md` §15 | Provider decision; Phase 5 blocked-on-BBB; sync item marked as landing in Session 3 |

## Known limitations

- **No per-ball player identity**, so no WPA/player predictions (Phase 5
  blocked — logged in §15, not left to be discovered later).
- **Latency distribution pending** — no live match during the session.
- **Reduced-overs detection is free-text parsing.** A reduced match only
  states its new length inside `status`; when the regex fails, the adapter
  falls back to nominal length and flags the match `reduced_unknown` rather
  than silently serving a wrong `balls_remaining`.
- **The reconstruction measurement uses a synthetic timing model**, not real
  provider update timing (which is unobservable without a live match). The
  bursty model is a defensible stand-in, not ground truth.
- **Session 3** carries the §15 Supabase sync (elo_ratings + venue as-of
  summary), which Railway deployment is blocked on.
