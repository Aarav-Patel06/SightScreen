# Column census

**Check this before building on a Supabase column.** If the column is not in
this table, regenerate it: `cd web && node scripts/make-column-census.mjs`.

Counted 2026-09-24. Regenerate after any migration or mirror change — a census
is only worth reading if it is newer than the pipeline it describes.

## Why

Three features have been built on columns that looked populated because their
neighbours were, and all three were backed out:

| Column | Was assumed | Actually |
|---|---|---|
| `players.batting_hand`, `bowling_style`, `dob` | populated | 0 of 18,468 |
| `teams.short_name` | populated | 0 of 350 |
| `matches.winner`, `target_runs`, `result_method` | populated | 0 of 107 until session 3 mirrored them |

Nothing in a schema listing separates "synced and full" from "synced and
empty". `\d matches` shows the column either way, and the generated types say
`winner: number | null` either way. Only a count tells you, and nobody runs a
count before writing a SELECT.

**0 column(s) currently diverge** — populated in the local corpus
and empty on Supabase. That is the shape of all three bugs above, and a column
marked DIVERGES is one somebody will build on and have to back out.

An **EMPTY** cell on one side alone is not necessarily a problem:
`deliveries` and `match_states` are empty on Supabase by design (SPEC.md
§2.1 — 3.78M rows do not fit the free tier), and `player_state` is empty
everywhere until Phase 5.

## The census

"full" means no NULLs. A percentage is the populated fraction.

| Column | Local rows | Local | Supabase rows | Supabase | |
|---|---|---|---|---|---|
| `agent_query_log.created_at` | 0 | — | 9 | full |  |
| `agent_query_log.duration_ms` | 0 | — | 9 | full |  |
| `agent_query_log.log_id` | 0 | — | 9 | full |  |
| `agent_query_log.query_ref` | 0 | — | 9 | full |  |
| `agent_query_log.rejected_by` | 0 | — | 9 | 11.1% |  |
| `agent_query_log.row_count` | 0 | — | 9 | 88.9% |  |
| `agent_query_log.sql_text` | 0 | — | 9 | full |  |
| `agent_query_log.verdict` | 0 | — | 9 | full |  |
| `agent_usage.cache_read_tokens` | 0 | — | 1 | full |  |
| `agent_usage.cache_write_tokens` | 0 | — | 1 | full |  |
| `agent_usage.conversations` | 0 | — | 1 | full |  |
| `agent_usage.cost_usd` | 0 | — | 1 | full |  |
| `agent_usage.day` | 0 | — | 1 | full |  |
| `agent_usage.input_tokens` | 0 | — | 1 | full |  |
| `agent_usage.output_tokens` | 0 | — | 1 | full |  |
| `agent_usage.updated_at` | 0 | — | 1 | full |  |
| `calibration_runs.computed_at` | 0 | — | 8 | full |  |
| `calibration_runs.model_version` | 0 | — | 8 | full |  |
| `calibration_runs.report` | 0 | — | 8 | full |  |
| `calibration_runs.run_id` | 0 | — | 8 | full |  |
| `deliveries.ball_in_over` | 3,780,368 | full | 0 | — |  |
| `deliveries.batter_id` | 3,780,368 | full | 0 | — |  |
| `deliveries.batting_team_id` | 3,780,368 | full | 0 | — |  |
| `deliveries.bowler_id` | 3,780,368 | full | 0 | — |  |
| `deliveries.bowling_team_id` | 3,780,368 | full | 0 | — |  |
| `deliveries.delivery_id` | 3,780,368 | full | 0 | — |  |
| `deliveries.extra_type` | 3,780,368 | 5.0% | 0 | — |  |
| `deliveries.innings` | 3,780,368 | full | 0 | — |  |
| `deliveries.is_super_over` | 3,780,368 | full | 0 | — |  |
| `deliveries.legal_ball_num` | 3,780,368 | full | 0 | — |  |
| `deliveries.match_date` | 3,780,368 | full | 0 | — |  |
| `deliveries.match_id` | 3,780,368 | full | 0 | — |  |
| `deliveries.non_striker_id` | 3,780,368 | full | 0 | — |  |
| `deliveries.over_num` | 3,780,368 | full | 0 | — |  |
| `deliveries.player_out_id` | 3,780,368 | 4.5% | 0 | — |  |
| `deliveries.runs_batter` | 3,780,368 | full | 0 | — |  |
| `deliveries.runs_extras` | 3,780,368 | full | 0 | — |  |
| `deliveries.wicket_count` | 3,780,368 | full | 0 | — |  |
| `deliveries.wicket_type` | 3,780,368 | 4.5% | 0 | — |  |
| `elo_asof_summary.effective_date` | 25,290 | full | 25,290 | full |  |
| `elo_asof_summary.format` | 25,290 | full | 25,290 | full |  |
| `elo_asof_summary.rating` | 25,290 | full | 25,290 | full |  |
| `elo_asof_summary.team_id` | 25,290 | full | 25,290 | full |  |
| `elo_ratings.as_of` | 25,662 | full | 0 | — |  |
| `elo_ratings.elo_id` | 25,662 | full | 0 | — |  |
| `elo_ratings.format` | 25,662 | full | 0 | — |  |
| `elo_ratings.match_id` | 25,662 | full | 0 | — |  |
| `elo_ratings.rating` | 25,662 | full | 0 | — |  |
| `elo_ratings.team_id` | 25,662 | full | 0 | — |  |
| `match_states.balls_bowled` | 3,779,439 | full | 0 | — |  |
| `match_states.balls_remaining` | 3,779,439 | full | 0 | — |  |
| `match_states.balls_since_wicket` | 3,779,439 | full | 0 | — |  |
| `match_states.batter_balls_faced` | 3,779,439 | full | 0 | — |  |
| `match_states.batter_runs_so_far` | 3,779,439 | full | 0 | — |  |
| `match_states.batting_team_won` | 3,779,439 | 97.8% | 0 | — |  |
| `match_states.current_run_rate` | 3,779,439 | 99.3% | 0 | — |  |
| `match_states.delivery_id` | 3,779,439 | full | 0 | — |  |
| `match_states.dls_resources_pct` | 3,779,439 | **EMPTY** | 0 | — |  |
| `match_states.has_reconciliation_anomaly` | 3,779,439 | full | 0 | — |  |
| `match_states.innings` | 3,779,439 | full | 0 | — |  |
| `match_states.is_dls_decided` | 3,779,439 | full | 0 | — |  |
| `match_states.match_date` | 3,779,439 | full | 0 | — |  |
| `match_states.match_id` | 3,779,439 | full | 0 | — |  |
| `match_states.partnership_balls` | 3,779,439 | full | 0 | — |  |
| `match_states.partnership_runs` | 3,779,439 | full | 0 | — |  |
| `match_states.phase` | 3,779,439 | full | 0 | — |  |
| `match_states.required_run_rate` | 3,779,439 | 46.5% | 0 | — |  |
| `match_states.rrr_minus_crr` | 3,779,439 | 46.2% | 0 | — |  |
| `match_states.runs_required` | 3,779,439 | 46.5% | 0 | — |  |
| `match_states.score` | 3,779,439 | full | 0 | — |  |
| `match_states.target` | 3,779,439 | 46.5% | 0 | — |  |
| `match_states.wickets` | 3,779,439 | full | 0 | — |  |
| `matches.competition` | 13,143 | full | 107 | full |  |
| `matches.external_ids` | 13,143 | full | 107 | full |  |
| `matches.format` | 13,143 | full | 107 | full |  |
| `matches.has_reconciliation_anomaly` | 13,143 | full | 107 | full |  |
| `matches.match_id` | 13,143 | full | 107 | full |  |
| `matches.result_method` | 13,143 | full | 107 | 96.3% |  |
| `matches.start_time` | 13,143 | full | 107 | full |  |
| `matches.status` | 13,143 | full | 107 | full |  |
| `matches.target_overs` | 13,143 | 98.2% | 107 | 93.5% |  |
| `matches.target_runs` | 13,143 | 98.2% | 107 | 96.3% |  |
| `matches.team_a` | 13,143 | full | 107 | 97.2% |  |
| `matches.team_b` | 13,143 | full | 107 | 97.2% |  |
| `matches.toss_decision` | 13,143 | full | 107 | 96.3% |  |
| `matches.toss_winner` | 13,143 | full | 107 | 96.3% |  |
| `matches.venue_id` | 13,143 | 92.0% | 107 | 86.9% |  |
| `matches.winner` | 13,143 | 96.5% | 107 | 96.3% |  |
| `model_versions.artifact_path` | 1 | full | 1 | full |  |
| `model_versions.is_active` | 1 | full | 1 | full |  |
| `model_versions.is_shadow` | 1 | full | 1 | full |  |
| `model_versions.model_type` | 1 | full | 1 | full |  |
| `model_versions.model_version` | 1 | full | 1 | full |  |
| `model_versions.notes` | 1 | **EMPTY** | 1 | full |  |
| `model_versions.test_brier` | 1 | full | 1 | full |  |
| `model_versions.test_log_loss` | 1 | full | 1 | full |  |
| `model_versions.train_end_date` | 1 | full | 1 | full |  |
| `model_versions.trained_at` | 1 | full | 1 | full |  |
| `player_aliases.alias_id` | 18,468 | full | 18,468 | full |  |
| `player_aliases.player_id` | 18,468 | full | 18,468 | full |  |
| `player_aliases.source` | 18,468 | full | 18,468 | full |  |
| `player_aliases.source_id` | 18,468 | full | 18,468 | full |  |
| `player_aliases.source_name` | 18,468 | full | 18,468 | full |  |
| `player_state.as_of` | 0 | — | 0 | — |  |
| `player_state.bat_ability_mean` | 0 | — | 0 | — |  |
| `player_state.bat_ability_sd` | 0 | — | 0 | — |  |
| `player_state.bowl_ability_mean` | 0 | — | 0 | — |  |
| `player_state.bowl_ability_sd` | 0 | — | 0 | — |  |
| `player_state.format` | 0 | — | 0 | — |  |
| `player_state.innings_observed` | 0 | — | 0 | — |  |
| `player_state.player_id` | 0 | — | 0 | — |  |
| `players.batting_hand` | 18,468 | **EMPTY** | 18,468 | **EMPTY** |  |
| `players.bowling_style` | 18,468 | **EMPTY** | 18,468 | **EMPTY** |  |
| `players.canonical_name` | 18,468 | full | 18,468 | full |  |
| `players.dob` | 18,468 | **EMPTY** | 18,468 | **EMPTY** |  |
| `players.player_id` | 18,468 | full | 18,468 | full |  |
| `prediction_outcomes.actual` | 0 | — | 12,081 | full |  |
| `prediction_outcomes.brier` | 0 | — | 12,081 | full |  |
| `prediction_outcomes.log_loss` | 0 | — | 12,081 | full |  |
| `prediction_outcomes.prediction_id` | 0 | — | 12,081 | full |  |
| `prediction_outcomes.resolved_at` | 0 | — | 12,081 | full |  |
| `predictions.ball_in_over` | 0 | — | 12,495 | 97.0% |  |
| `predictions.created_at` | 0 | — | 12,495 | full |  |
| `predictions.delivery_id` | 0 | — | 12,495 | **EMPTY** |  |
| `predictions.innings` | 0 | — | 12,495 | 97.0% |  |
| `predictions.match_id` | 0 | — | 12,495 | full |  |
| `predictions.match_phase` | 0 | — | 12,495 | full |  |
| `predictions.model_version` | 0 | — | 12,495 | full |  |
| `predictions.over_num` | 0 | — | 12,495 | 97.0% |  |
| `predictions.payload` | 0 | — | 12,495 | full |  |
| `predictions.prediction_id` | 0 | — | 12,495 | full |  |
| `predictions.prediction_type` | 0 | — | 12,495 | full |  |
| `predictions.source` | 0 | — | 12,495 | full |  |
| `predictions.subject_id` | 0 | — | 12,495 | **EMPTY** |  |
| `reference_sync_state.content_hash` | 2 | full | 2 | full |  |
| `reference_sync_state.rebuilt_at` | 2 | full | 2 | full |  |
| `reference_sync_state.row_count` | 2 | full | 2 | full |  |
| `reference_sync_state.synced_at` | 2 | **EMPTY** | 2 | full |  |
| `reference_sync_state.table_name` | 2 | full | 2 | full |  |
| `team_aliases.alias_id` | 354 | full | 358 | full |  |
| `team_aliases.source` | 354 | full | 358 | full |  |
| `team_aliases.source_id` | 354 | **EMPTY** | 358 | **EMPTY** |  |
| `team_aliases.source_name` | 354 | full | 358 | full |  |
| `team_aliases.team_id` | 354 | full | 358 | full |  |
| `teams.name` | 347 | full | 347 | full |  |
| `teams.short_name` | 347 | **EMPTY** | 347 | **EMPTY** |  |
| `teams.team_id` | 347 | full | 347 | full |  |
| `unresolved_entities.candidates` | 46 | full | 2 | full |  |
| `unresolved_entities.created_at` | 46 | full | 2 | full |  |
| `unresolved_entities.entity_kind` | 46 | full | 2 | full |  |
| `unresolved_entities.first_seen_match_id` | 46 | **EMPTY** | 2 | **EMPTY** |  |
| `unresolved_entities.reason` | 46 | full | 2 | full |  |
| `unresolved_entities.source` | 46 | full | 2 | full |  |
| `unresolved_entities.source_id` | 46 | **EMPTY** | 2 | **EMPTY** |  |
| `unresolved_entities.source_name` | 46 | full | 2 | full |  |
| `unresolved_entities.status` | 46 | full | 2 | full |  |
| `unresolved_entities.unresolved_id` | 46 | full | 2 | full |  |
| `venue_aliases.alias_id` | 542 | full | 543 | full |  |
| `venue_aliases.source` | 542 | full | 543 | full |  |
| `venue_aliases.source_id` | 542 | **EMPTY** | 543 | **EMPTY** |  |
| `venue_aliases.source_name` | 542 | full | 543 | full |  |
| `venue_aliases.venue_id` | 542 | full | 543 | full |  |
| `venue_asof_summary.chase_n` | 10,508 | full | 10,508 | full |  |
| `venue_asof_summary.chase_wins` | 10,508 | full | 10,508 | full |  |
| `venue_asof_summary.effective_date` | 10,508 | full | 10,508 | full |  |
| `venue_asof_summary.first_inns_n` | 10,508 | full | 10,508 | full |  |
| `venue_asof_summary.first_inns_runs` | 10,508 | full | 10,508 | full |  |
| `venue_asof_summary.venue_id` | 10,508 | full | 10,508 | full |  |
| `venues.city` | 349 | 84.8% | 349 | 84.8% |  |
| `venues.country` | 349 | **EMPTY** | 349 | **EMPTY** |  |
| `venues.name` | 349 | full | 349 | full |  |
| `venues.venue_id` | 349 | full | 349 | full |  |
