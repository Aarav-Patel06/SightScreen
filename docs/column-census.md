# Column census

**Check this before building on a Supabase column.** If the column is not in
this table, regenerate it: `cd web && node scripts/make-column-census.mjs`.

Counted 2026-09-27. Regenerate after any migration or mirror change — a census
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
| `calibration_runs.computed_at` | 0 | — | 13 | full |  |
| `calibration_runs.model_version` | 0 | — | 13 | full |  |
| `calibration_runs.report` | 0 | — | 13 | full |  |
| `calibration_runs.run_id` | 0 | — | 13 | full |  |
| `deliveries.ball_in_over` | 3,807,789 | full | 0 | — |  |
| `deliveries.batter_id` | 3,807,789 | full | 0 | — |  |
| `deliveries.batting_team_id` | 3,807,789 | full | 0 | — |  |
| `deliveries.bowler_id` | 3,807,789 | full | 0 | — |  |
| `deliveries.bowling_team_id` | 3,807,789 | full | 0 | — |  |
| `deliveries.delivery_id` | 3,807,789 | full | 0 | — |  |
| `deliveries.extra_type` | 3,807,789 | 5.0% | 0 | — |  |
| `deliveries.innings` | 3,807,789 | full | 0 | — |  |
| `deliveries.is_super_over` | 3,807,789 | full | 0 | — |  |
| `deliveries.legal_ball_num` | 3,807,789 | full | 0 | — |  |
| `deliveries.match_date` | 3,807,789 | full | 0 | — |  |
| `deliveries.match_id` | 3,807,789 | full | 0 | — |  |
| `deliveries.non_striker_id` | 3,807,789 | full | 0 | — |  |
| `deliveries.over_num` | 3,807,789 | full | 0 | — |  |
| `deliveries.player_out_id` | 3,807,789 | 4.5% | 0 | — |  |
| `deliveries.runs_batter` | 3,807,789 | full | 0 | — |  |
| `deliveries.runs_extras` | 3,807,789 | full | 0 | — |  |
| `deliveries.wicket_count` | 3,807,789 | full | 0 | — |  |
| `deliveries.wicket_type` | 3,807,789 | 4.5% | 0 | — |  |
| `elo_asof_summary.effective_date` | 25,510 | full | 25,510 | full |  |
| `elo_asof_summary.format` | 25,510 | full | 25,510 | full |  |
| `elo_asof_summary.rating` | 25,510 | full | 25,510 | full |  |
| `elo_asof_summary.team_id` | 25,510 | full | 25,510 | full |  |
| `elo_ratings.as_of` | 25,882 | full | 0 | — |  |
| `elo_ratings.elo_id` | 25,882 | full | 0 | — |  |
| `elo_ratings.format` | 25,882 | full | 0 | — |  |
| `elo_ratings.match_id` | 25,882 | full | 0 | — |  |
| `elo_ratings.rating` | 25,882 | full | 0 | — |  |
| `elo_ratings.team_id` | 25,882 | full | 0 | — |  |
| `feature_ledger.chase_batting_team_won` | 0 | — | 13,254 | 96.6% |  |
| `feature_ledger.cricsheet_id` | 0 | — | 13,254 | full |  |
| `feature_ledger.first_innings_runs` | 0 | — | 13,254 | full |  |
| `feature_ledger.format` | 0 | — | 13,254 | full |  |
| `feature_ledger.match_id` | 0 | — | 13,254 | full |  |
| `feature_ledger.result_method` | 0 | — | 13,254 | full |  |
| `feature_ledger.start_time` | 0 | — | 13,254 | full |  |
| `feature_ledger.status` | 0 | — | 13,254 | full |  |
| `feature_ledger.team_a` | 0 | — | 13,254 | full |  |
| `feature_ledger.team_b` | 0 | — | 13,254 | full |  |
| `feature_ledger.venue_id` | 0 | — | 13,254 | 91.9% |  |
| `feature_ledger.winner` | 0 | — | 13,254 | 96.6% |  |
| `match_states.balls_bowled` | 3,806,854 | full | 0 | — |  |
| `match_states.balls_remaining` | 3,806,854 | full | 0 | — |  |
| `match_states.balls_since_wicket` | 3,806,854 | full | 0 | — |  |
| `match_states.batter_balls_faced` | 3,806,854 | full | 0 | — |  |
| `match_states.batter_runs_so_far` | 3,806,854 | full | 0 | — |  |
| `match_states.batting_team_won` | 3,806,854 | 97.8% | 0 | — |  |
| `match_states.current_run_rate` | 3,806,854 | 99.3% | 0 | — |  |
| `match_states.delivery_id` | 3,806,854 | full | 0 | — |  |
| `match_states.dls_resources_pct` | 3,806,854 | **EMPTY** | 0 | — |  |
| `match_states.has_reconciliation_anomaly` | 3,806,854 | full | 0 | — |  |
| `match_states.innings` | 3,806,854 | full | 0 | — |  |
| `match_states.is_dls_decided` | 3,806,854 | full | 0 | — |  |
| `match_states.match_date` | 3,806,854 | full | 0 | — |  |
| `match_states.match_id` | 3,806,854 | full | 0 | — |  |
| `match_states.partnership_balls` | 3,806,854 | full | 0 | — |  |
| `match_states.partnership_runs` | 3,806,854 | full | 0 | — |  |
| `match_states.phase` | 3,806,854 | full | 0 | — |  |
| `match_states.required_run_rate` | 3,806,854 | 46.5% | 0 | — |  |
| `match_states.rrr_minus_crr` | 3,806,854 | 46.2% | 0 | — |  |
| `match_states.runs_required` | 3,806,854 | 46.5% | 0 | — |  |
| `match_states.score` | 3,806,854 | full | 0 | — |  |
| `match_states.target` | 3,806,854 | 46.5% | 0 | — |  |
| `match_states.wickets` | 3,806,854 | full | 0 | — |  |
| `matches.competition` | 13,254 | full | 455 | full |  |
| `matches.external_ids` | 13,254 | full | 455 | full |  |
| `matches.format` | 13,254 | full | 455 | full |  |
| `matches.has_reconciliation_anomaly` | 13,254 | full | 455 | full |  |
| `matches.match_id` | 13,254 | full | 455 | full |  |
| `matches.outcome_method` | 13,254 | 4.5% | 455 | 4.8% |  |
| `matches.result_method` | 13,254 | full | 455 | 99.1% |  |
| `matches.start_time` | 13,254 | full | 455 | full |  |
| `matches.status` | 13,254 | full | 455 | full |  |
| `matches.target_overs` | 13,254 | 98.2% | 455 | 99.1% |  |
| `matches.target_runs` | 13,254 | 98.2% | 455 | 99.1% |  |
| `matches.team_a` | 13,254 | full | 455 | 99.3% |  |
| `matches.team_b` | 13,254 | full | 455 | 99.3% |  |
| `matches.tie_decided_by` | 13,254 | 0.6% | 455 | 0.2% |  |
| `matches.tie_winner` | 13,254 | 0.6% | 455 | 0.2% |  |
| `matches.toss_decision` | 13,254 | full | 455 | 99.1% |  |
| `matches.toss_winner` | 13,254 | full | 455 | 99.1% |  |
| `matches.venue_id` | 13,254 | 91.9% | 455 | 89.2% |  |
| `matches.win_by_runs` | 13,254 | 46.7% | 455 | 47.9% |  |
| `matches.win_by_wickets` | 13,254 | 49.8% | 455 | 50.8% |  |
| `matches.winner` | 13,254 | 96.6% | 455 | 98.7% |  |
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
| `pipeline_runs.counts` | 0 | — | 2 | full |  |
| `pipeline_runs.error_class` | 0 | — | 2 | **EMPTY** |  |
| `pipeline_runs.finished_at` | 0 | — | 2 | full |  |
| `pipeline_runs.pipeline` | 0 | — | 2 | full |  |
| `pipeline_runs.run_id` | 0 | — | 2 | full |  |
| `pipeline_runs.started_at` | 0 | — | 2 | full |  |
| `pipeline_runs.status` | 0 | — | 2 | full |  |
| `player_aliases.alias_id` | 18,489 | full | 18,489 | full |  |
| `player_aliases.player_id` | 18,489 | full | 18,489 | full |  |
| `player_aliases.source` | 18,489 | full | 18,489 | full |  |
| `player_aliases.source_id` | 18,489 | full | 18,489 | full |  |
| `player_aliases.source_name` | 18,489 | full | 18,489 | full |  |
| `player_career_summary.bat_balls` | 37,512 | full | 37,512 | full |  |
| `player_career_summary.bat_fours` | 37,512 | full | 37,512 | full |  |
| `player_career_summary.bat_innings` | 37,512 | full | 37,512 | full |  |
| `player_career_summary.bat_outs` | 37,512 | full | 37,512 | full |  |
| `player_career_summary.bat_runs` | 37,512 | full | 37,512 | full |  |
| `player_career_summary.bat_sixes` | 37,512 | full | 37,512 | full |  |
| `player_career_summary.bowl_balls` | 37,512 | full | 37,512 | full |  |
| `player_career_summary.bowl_runs` | 37,512 | full | 37,512 | full |  |
| `player_career_summary.bowl_wickets` | 37,512 | full | 37,512 | full |  |
| `player_career_summary.format` | 37,512 | full | 37,512 | full |  |
| `player_career_summary.phase` | 37,512 | full | 37,512 | full |  |
| `player_career_summary.player_id` | 37,512 | full | 37,512 | full |  |
| `player_index.bat_innings` | 8,607 | full | 8,607 | full |  |
| `player_index.bat_runs` | 8,607 | full | 8,607 | full |  |
| `player_index.bowl_innings` | 8,607 | full | 8,607 | full |  |
| `player_index.bowl_wickets` | 8,607 | full | 8,607 | full |  |
| `player_index.canonical_name` | 8,607 | full | 8,607 | full |  |
| `player_index.formats` | 8,607 | full | 8,607 | full |  |
| `player_index.matches` | 8,607 | full | 8,607 | full |  |
| `player_index.normalized_name` | 8,607 | full | 8,607 | full |  |
| `player_index.player_id` | 8,607 | full | 8,607 | full |  |
| `player_index.surname_key` | 8,607 | full | 8,607 | full |  |
| `player_state.as_of` | 0 | — | 0 | — |  |
| `player_state.bat_ability_mean` | 0 | — | 0 | — |  |
| `player_state.bat_ability_sd` | 0 | — | 0 | — |  |
| `player_state.bowl_ability_mean` | 0 | — | 0 | — |  |
| `player_state.bowl_ability_sd` | 0 | — | 0 | — |  |
| `player_state.format` | 0 | — | 0 | — |  |
| `player_state.innings_observed` | 0 | — | 0 | — |  |
| `player_state.player_id` | 0 | — | 0 | — |  |
| `players.batting_hand` | 18,489 | **EMPTY** | 18,489 | **EMPTY** |  |
| `players.bowling_style` | 18,489 | **EMPTY** | 18,489 | **EMPTY** |  |
| `players.canonical_name` | 18,489 | full | 18,489 | full |  |
| `players.dob` | 18,489 | **EMPTY** | 18,489 | **EMPTY** |  |
| `players.player_id` | 18,489 | full | 18,489 | full |  |
| `prediction_outcomes.actual` | 0 | — | 63,577 | full |  |
| `prediction_outcomes.brier` | 0 | — | 63,577 | full |  |
| `prediction_outcomes.log_loss` | 0 | — | 63,577 | full |  |
| `prediction_outcomes.prediction_id` | 0 | — | 63,577 | full |  |
| `prediction_outcomes.resolved_at` | 0 | — | 63,577 | full |  |
| `predictions.ball_in_over` | 0 | — | 63,617 | full |  |
| `predictions.batting_team_id` | 0 | — | 63,617 | 99.9% |  |
| `predictions.created_at` | 0 | — | 63,617 | full |  |
| `predictions.delivery_id` | 0 | — | 63,617 | **EMPTY** |  |
| `predictions.innings` | 0 | — | 63,617 | full |  |
| `predictions.match_id` | 0 | — | 63,617 | full |  |
| `predictions.match_phase` | 0 | — | 63,617 | full |  |
| `predictions.model_version` | 0 | — | 63,617 | full |  |
| `predictions.over_num` | 0 | — | 63,617 | full |  |
| `predictions.payload` | 0 | — | 63,617 | full |  |
| `predictions.prediction_id` | 0 | — | 63,617 | full |  |
| `predictions.prediction_type` | 0 | — | 63,617 | full |  |
| `predictions.source` | 0 | — | 63,617 | full |  |
| `predictions.subject_id` | 0 | — | 63,617 | **EMPTY** |  |
| `reference_sync_state.content_hash` | 4 | full | 4 | full |  |
| `reference_sync_state.rebuilt_at` | 4 | full | 4 | full |  |
| `reference_sync_state.row_count` | 4 | full | 4 | full |  |
| `reference_sync_state.synced_at` | 4 | full | 4 | full |  |
| `reference_sync_state.table_name` | 4 | full | 4 | full |  |
| `team_aliases.alias_id` | 360 | full | 364 | full |  |
| `team_aliases.source` | 360 | full | 364 | full |  |
| `team_aliases.source_id` | 360 | **EMPTY** | 364 | **EMPTY** |  |
| `team_aliases.source_name` | 360 | full | 364 | full |  |
| `team_aliases.team_id` | 360 | full | 364 | full |  |
| `teams.full_member` | 353 | full | 353 | full |  |
| `teams.name` | 353 | full | 353 | full |  |
| `teams.short_name` | 353 | **EMPTY** | 353 | **EMPTY** |  |
| `teams.team_id` | 353 | full | 353 | full |  |
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
| `venue_aliases.alias_id` | 543 | full | 544 | full |  |
| `venue_aliases.source` | 543 | full | 544 | full |  |
| `venue_aliases.source_id` | 543 | **EMPTY** | 544 | **EMPTY** |  |
| `venue_aliases.source_name` | 543 | full | 544 | full |  |
| `venue_aliases.venue_id` | 543 | full | 544 | full |  |
| `venue_asof_summary.chase_n` | 10,573 | full | 10,573 | full |  |
| `venue_asof_summary.chase_wins` | 10,573 | full | 10,573 | full |  |
| `venue_asof_summary.effective_date` | 10,573 | full | 10,573 | full |  |
| `venue_asof_summary.first_inns_n` | 10,573 | full | 10,573 | full |  |
| `venue_asof_summary.first_inns_runs` | 10,573 | full | 10,573 | full |  |
| `venue_asof_summary.venue_id` | 10,573 | full | 10,573 | full |  |
| `venues.city` | 350 | 84.9% | 350 | 84.9% |  |
| `venues.country` | 350 | **EMPTY** | 350 | **EMPTY** |  |
| `venues.name` | 350 | full | 350 | full |  |
| `venues.venue_id` | 350 | full | 350 | full |  |
