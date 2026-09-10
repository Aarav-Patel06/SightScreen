# Phase 1 close-out

Win probability model, offline. Closed. 3 sessions. This is the reference for
what got decided and why — read the "things a future session would get
wrong" section before touching `eval/`, `models/`, or Supabase's
`model_versions`.

## Final numbers

| Metric | Value |
|---|---|
| Test Brier, overall (2nd innings) | **0.1232** (target ≤0.125) |
| Test Brier, final 3 overs | **0.0652** (target ≤0.085) |
| Test log loss, overall / final 3 overs | 0.3804 / 0.2093 |
| vs. logistic baseline (paired, match-clustered) | +0.0110 [0.0076, 0.0145] — **significant** |
| vs. historical base-rate baseline (paired) | +0.0266 [0.0219, 0.0314] — **significant** |
| Calibration winner | **Identity — no calibration layer** |
| Calibration verdict (Decision 4's match-clustered check) | 4/10 test deciles still fail — disclosed, not resolved |
| Model version | `winprob2-20260910`, `is_active=true`, local `model_versions` only (see below) |
| Feature-importance leak check | Passed — investigated one nominal exception, explained, not a leak |
| Canary (shuffled-split leak detector) | Loud for LightGBM (gap 0.0917), was near-zero for the linear baseline (0.0005) — same instrument, different verdict per model class |

Every number above is touched-once-at-the-end test data. Full reports:
`api/data/eval_reports/baselines_*.json` (session 1),
`win_prob_2nd_1787830785.json` (session 2, the underlying model — unchanged
since), `calibration_selection_1789001840.json` (session 3, this one).
Session-level detail lives in `docs/phase1-session1-baselines.md`,
`docs/phase1-session2-win-prob.md`, and `docs/phase1-session3-calibration.md`
(the comparison table, full reliability diagram, and monotonicity/
distribution-shift findings) — this document is the cross-session summary.

## Decision log by session

**Session 1 — temporal splits and baselines.** `eval/splits.py` built as the
single gate for second-innings training data — a function returning finished
numpy arrays, not a date constant, enforcing the section 9.1 predicate plus a
4th clause found by querying the real corpus (`required_run_rate IS NOT
NULL` — 992 rows pass the documented three-clause predicate but have no
usable feature). Both section 9.2 baselines built and measured: logistic
0.1342 overall / 0.0925 final-3-overs, historical base rate 0.1498 / 0.0811.
The leakage test suite (a shuffled-ball-level-split canary among others) is
the actual deliverable, not the baselines themselves — "tests that fail on a
leak, not tests that pass on a correct split."

**Session 2 — the LightGBM model.** Trained without player-ability features
(deferred to Phase 5, logged in SPEC.md §15) and without `dls_resources_pct`
(NULL for every row, Phase 0's own deferral). `venue_chase_win_rate_as_of`/
`venue_avg_first_innings_as_of` built (`features/venue_stats.py`), mirroring
`elo.py`'s strict-`<`, never-a-global-average-substitute as-of contract, with
a 10-prior-match floor for the venue features specifically. The canary from
session 1 got re-run against this model and came back loud (0.0917 gap vs.
0.0005 for the linear baseline) — confirmed the leak detector is
model-capacity-aware, not a fixed number. Three-variant ablation showed venue
and Elo each add a small, not-individually-significant lift. Calibration
(global isotonic, fit on all of validation) made test Brier very slightly
*worse* and missed the old ball-counted "<3pp, n>200" target in 5–6 of 10
deciles — reported honestly, not hidden, and became this session's starting
point.

**Session 3 — fix calibration properly, closes Phase 1.** Diagnosed the
session-2 miscalibration more precisely: a single global monotone map
assumes miscalibration is a function of predicted probability alone, when
the same raw score arises from genuinely-different regimes (early
near-coin-flip vs. late near-determined states). Built a proper method
*selection* protocol instead of picking one map on faith: validation split
temporally (fit chunk ≤2024-07-01, select chunk after — chosen from a real
647/649-match split, not assumed), five candidates evaluated on equal
footing (identity, global isotonic, phase-stratified isotonic, global Platt,
phase-stratified Platt), and calibration itself measured with a
match-clustered bootstrap CI per decile instead of a raw percentage-point
gap (session 1/2's Brier bootstrap machinery extended one more time).
**Identity won** — every one of the four active calibration methods scored
*worse* on the held-out selection chunk than doing nothing. This wasn't the
pre-registered expectation (phase-stratified Platt was), and is reported as
such: the deeper finding isn't "Platt beats isotonic," it's that with the
data actually available, no calibration method reliably improves on the raw
model, and the model's own raw calibration is closer to acceptable than
session 2's less rigorous metric suggested. `models/registry.py` persists
the final artifact and writes one real `model_versions` row — **local
database only**, not Supabase (SPEC.md §2.1: "training never touches
Supabase" — a correction to this session's own plan, which hadn't checked
that before proposing a `model_versions` write).

## Things a future session would get wrong

**A shuffled-split canary's "pass" is only as meaningful as the model class
it's testing.** Session 1's 3-feature logistic regression showed a 0.0005
gap under the shuffled-vs-honest construction; session 2's LightGBM showed a
0.0917 gap under the *identical* construction, on the *same* corpus. A small
gap does not mean "this corpus/pipeline is leak-free" — it means the model
being tested doesn't have the capacity to exploit the leak. Re-run the
canary against every new model class (a different tree config, a neural
net, anything with more capacity than the last thing tested), and expect the
"convincing" magnitude to scale with capacity, not stay fixed.

**`required_run_rate IS NOT NULL` is a real, silent exclusion clause, not a
paranoia check.** 992 rows in the real corpus pass the three documented
predicate clauses but have no computable `required_run_rate` (a DLS-decided
match with no explicit target). `eval/splits.py` enforces this as a fourth
clause; if you ever bypass `get_second_innings_split` and query
`match_states` directly, you will silently train on rows with a NULL
feature unless you rediscover this yourself.

**An as-of aggregate outranking a raw state feature in gain importance is
not automatically a leak.** Session 2 found `elo_diff` outranking
`balls_remaining` and investigated rather than either panicking or
shrugging: `balls_remaining` is one of the inputs used to *derive*
`required_run_rate`, and once `required_run_rate`/`runs_required`/`target`
are already available to the tree, `balls_remaining`'s own marginal
information is small — ordinary redundancy among correlated features, not
future information. The actual trigger that would have been hard to explain
away was an as-of feature outranking `runs_required` itself, which didn't
happen. Check the *specific* feature named in a leak-investigation trigger,
not just "some aggregate ranked high."

**Calibration methods need their own held-out selection step, or you're
just moving the overfitting problem, not fixing it.** Session 2 fit global
isotonic on *all* of validation and only found the problem by accident, on
test. Session 3 built a genuine fit/select split within validation
specifically to catch this *before* touching test, and the result was
decisive: every real calibration method (isotonic and Platt, global and
phase-stratified) scored worse than no calibration at all on the held-out
select chunk. If a future session adds calibration back (more validation
data, a coarser stratification, a different method entirely), it must be
re-evaluated through this same fit/select protocol, not fit once and shipped
on faith.

**A raw percentage-point calibration check ("<3pp, n>200 balls") is not
measuring what it looks like it's measuring.** Balls within a match are
correlated; 200 balls can be a handful of effective independent
observations. Session 3's `reliability_match_clustered` (a match-clustered
bootstrap CI per decile) is the corrected version — use it, not a flat pp
threshold, for any future calibration work. It's also less alarmist: several
buckets that looked like clear miscalibration under the old ball-counted
check turned out to contain the predicted mean once measured with a
properly-clustered CI.

**`model_versions` is a Supabase table, not a local-training-DB table, per
SPEC.md §2.1 — even though the identical schema exists locally too (Phase 0
migrations apply to both).** Writing a row locally is for this
training/evaluation environment's own bookkeeping ("loadable by version"
within `api/`), not a serving registry. Actually deploying a model (pushing
the artifact to GitHub Releases/Supabase Storage and writing Supabase's own
`model_versions` row) is Phase 2 deploy-time work. Don't let a future
session's `registry.py` grow into quietly writing to Supabase from a
training script — that's the exact "training never touches Supabase" rule
this session caught itself almost breaking.

**Phase 1 closes with real, disclosed calibration debt, not a clean
pass.** Even the winning candidate (identity) fails Decision 4's
match-clustered check in 4 of 10 test deciles, and the winning candidate
differs in *which* deciles fail between the selection chunk and test — some
evidence the residual miscalibration is itself somewhat sample-dependent,
not one fixed, chaseable bias. The quarter-bucketed distribution-shift check
(Decision 6) shows real Brier variability across 2025–2026 test quarters
(0.1059–0.1417) without a clean monotonic decay trend — supports keeping
§8.1/§8.2's ongoing monitoring jobs, but doesn't hand you a single "recalibrate
every N days" number. Phase 2/3 inherit an unsolved, honestly-measured
problem, not a solved one dressed up as solved.

**Docker Desktop isn't always running when a session starts, and isn't
where you'd expect.** In this environment it's a per-user install
(`C:\Users\<user>\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe`,
not `C:\Program Files\Docker`). If `docker ps` can't reach the daemon, start
it and wait for the compose container's healthcheck to pass before any
DB-touching code will work — a real, several-minutes-long blocker this
session hit before any of the above could even be verified against real
data.

**Supabase's free tier auto-pauses a project after a period of inactivity**
— `tests/db/test_schema_parity.py` will fail with a "tenant/user not found"
connection error that has nothing to do with a code regression. That's the
hosted project needing to be woken from the Supabase dashboard, not
something to debug in `config.py` or the test itself.
