/**
 * /matches against PostgREST's row cap.
 *
 * Latent on 2026-09-26 (414 matches), certain within months: the daily
 * Cricsheet job adds a few matches a day. Two reads would then truncate at
 * 1,000 rows with no error - the `matches` list itself, so the index would
 * stop listing matches, and the chase-outcome lookup (one row per match), so
 * result lines would go blank for everything past the thousandth.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_ROWS, fakeSupabase } from "./fake-postgrest";

let db: ReturnType<typeof fakeSupabase>;
vi.mock("./supabase-server", () => ({ supabaseServer: () => db }));

const { loadMatchIndex } = await import("./match-index");

const MATCHES = 1100;

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const matches = Array.from({ length: MATCHES }, (_, i) => ({
    match_id: i + 1,
    competition: "Test League",
    format: "T20",
    start_time: new Date(Date.UTC(2023, 0, 1) + i * 86_400_000).toISOString(),
    team_a: 1,
    team_b: 2,
    venue_id: null,
    winner: null, // so the result line depends on prediction_outcomes alone
    target_runs: 101,
    result_method: "normal",
  }));
  const predictions = matches.map((m) => ({
    prediction_id: m.match_id,
    match_id: m.match_id,
    created_at: "2026-01-01T00:00:00Z",
    model_version: "winprob2-20260910",
    prediction_type: "win_prob",
    innings: 2,
    payload: {
      p: 0.5, innings: 2, balls_bowled: 10, balls_remaining: 110,
      runs_required: 90, score: 10, wickets: 0, target: 101, phase: "powerplay",
    },
  }));
  const outcomes = predictions.map((p) => ({
    prediction_id: p.prediction_id,
    actual: { batting_team_won: true },
  }));
  expect(MATCHES).toBeGreaterThan(MAX_ROWS);
  db = fakeSupabase({
    matches,
    predictions,
    prediction_outcomes: outcomes,
    teams: [
      { team_id: 1, name: "Alpha" },
      { team_id: 2, name: "Beta" },
    ],
    venues: [],
  });
});

describe("the match index past 1,000 matches", () => {
  it("lists every match, not the first 1,000", async () => {
    const { matches } = await loadMatchIndex();
    expect(matches).toHaveLength(MATCHES);
  });

  it("gives every match its result line, including those past the thousandth", async () => {
    const { matches } = await loadMatchIndex();
    const missing = matches.filter((m) => m.result?.chaseWon !== true);
    expect(missing.map((m) => m.matchId).slice(0, 5)).toEqual([]);
  });
});
