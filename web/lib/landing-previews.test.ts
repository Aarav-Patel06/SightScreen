/**
 * The landing previews against PostgREST's row cap - the same defect as the
 * hero's (see hero-match.cap.test.ts). The strips were one unbounded fetch of
 * every prediction for nine matches, oldest first, so once those passed 1,000
 * rows the NEWEST previews - the ones shown first - lost their strips.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_ROWS, fakeSupabase } from "./fake-postgrest";

let db: ReturnType<typeof fakeSupabase>;
vi.mock("./supabase-server", () => ({ supabaseServer: () => db }));

const { loadLandingPreviews } = await import("./landing-previews");

function rows(matchId: number, n: number, firstId: number) {
  return Array.from({ length: n }, (_, i) => ({
    prediction_id: firstId + i,
    match_id: matchId,
    created_at: "2026-09-17T18:00:00+00:00",
    model_version: "winprob2-20260910",
    prediction_type: "win_prob",
    innings: 2,
    payload: {
      p: 0.5, innings: 2, balls_bowled: i, balls_remaining: 300 - i,
      runs_required: 250 - i, score: i, wickets: 0, target: 251, phase: "middle",
    },
  }));
}

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  // Nine Full Member ODIs; newest has the highest id and its rows were
  // inserted last. 9 x 150 = 1,350 rows.
  const matches = Array.from({ length: 9 }, (_, i) => ({
    match_id: 100 + i,
    start_time: `2026-09-${String(10 + i).padStart(2, "0")}T00:00:00+00:00`,
    team_a: 1,
    team_b: 2,
  }));
  const predictions = matches.flatMap((m, i) => rows(m.match_id, 150, 1 + i * 150));
  expect(predictions.length).toBeGreaterThan(MAX_ROWS);
  db = fakeSupabase({
    teams: [
      { team_id: 1, name: "England", full_member: true },
      { team_id: 2, name: "India", full_member: true },
    ],
    matches,
    predictions,
    player_index: [],
  });
});

describe("landing previews when the strips exceed the row cap", () => {
  it("gives every shown match its strip, the newest included", async () => {
    const { matches } = await loadLandingPreviews();
    expect(matches?.map((m) => m.matchId)).toEqual([108, 107, 106]);
    for (const match of matches ?? []) {
      expect(match.marks, `match ${match.matchId} lost its strip`).not.toBeNull();
    }
  });
});
