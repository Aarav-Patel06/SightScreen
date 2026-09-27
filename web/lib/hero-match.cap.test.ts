/**
 * The landing hero against PostgREST's row cap.
 *
 * 2026-09-26: after the daily Cricsheet catch-up the hero showed India v
 * Zimbabwe (2026-07-25) instead of Sri Lanka v England (2026-09-17). The
 * existence check fetched EVERY prediction row of the 20 newest Full Member
 * matches - 3,642 rows - and PostgREST returned the first 1,000, which by
 * insertion order all belonged to older matches. The newest match had rows;
 * they were simply past the cut.
 *
 * Rows here are laid out the way they were: older matches' predictions were
 * inserted first, so an unordered read reaches them first.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_ROWS, fakeSupabase } from "./fake-postgrest";

let db: ReturnType<typeof fakeSupabase>;
vi.mock("./supabase-server", () => ({ supabaseServer: () => db }));

const { loadHeroMatch } = await import("./hero-match");

const ENGLAND = 1;
const SRI_LANKA = 2;
const INDIA = 3;
const ZIMBABWE = 4;
const MUMBAI = 50; // a franchise: not a Full Member, never a hero

function predictions(matchId: number, batting: number, n: number, firstId: number) {
  return Array.from({ length: n }, (_, i) => ({
    prediction_id: firstId + i,
    match_id: matchId,
    created_at: "2026-09-17T18:00:00+00:00",
    model_version: "winprob2-20260910",
    prediction_type: "win_prob",
    innings: 2,
    batting_team_id: batting,
    payload: {
      p: 0.5,
      innings: 2,
      balls_bowled: i,
      balls_remaining: 120 - i,
      runs_required: 100 - i,
      score: i,
      wickets: 0,
      target: 101,
      phase: "powerplay",
    },
  }));
}

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const matches = [
    // Newest first by date; ids are Supabase's, the new one far above.
    { match_id: 1000068, competition: "Sri Lanka tour of England", format: "T20",
      start_time: "2026-09-17T00:00:00+00:00", team_a: SRI_LANKA, team_b: ENGLAND,
      winner: ENGLAND, status: "complete" },
    { match_id: 9300, competition: "IPL", format: "T20",
      start_time: "2026-09-18T00:00:00+00:00", team_a: MUMBAI, team_b: INDIA,
      winner: INDIA, status: "complete" },
    ...Array.from({ length: 8 }, (_, i) => ({
      match_id: 9233 - i, competition: "India tour of Zimbabwe", format: "ODI",
      start_time: `2026-07-${String(25 - i).padStart(2, "0")}T00:00:00+00:00`,
      team_a: INDIA, team_b: ZIMBABWE, winner: INDIA, status: "complete" })),
  ];
  // Eight older ODIs x 150 rows = 1,200 rows inserted BEFORE the new match's.
  let next = 1;
  const rows = [];
  for (const m of matches.slice(2).reverse()) {
    rows.push(...predictions(m.match_id, ZIMBABWE, 150, next));
    next += 150;
  }
  rows.push(...predictions(9300, INDIA, 120, next));
  next += 120;
  rows.push(...predictions(1000068, ENGLAND, 120, next));
  expect(rows.length).toBeGreaterThan(MAX_ROWS);

  db = fakeSupabase({
    teams: [
      { team_id: ENGLAND, name: "England", full_member: true },
      { team_id: SRI_LANKA, name: "Sri Lanka", full_member: true },
      { team_id: INDIA, name: "India", full_member: true },
      { team_id: ZIMBABWE, name: "Zimbabwe", full_member: true },
      { team_id: MUMBAI, name: "Mumbai Indians", full_member: false },
    ],
    matches,
    predictions: rows,
  });
});

describe("the hero when candidates' rows exceed the row cap", () => {
  it("still picks the newest Full Member match, whose rows are past row 1,000", async () => {
    const result = await loadHeroMatch();
    expect(result.stale).toBe(false);
    expect(result.match.matchId).toBe(1000068);
    expect(`${result.match.teamA} v ${result.match.teamB}`).toBe("Sri Lanka v England");
    expect(result.match.date).toBe("2026-09-17");
    expect(result.match.battingTeam).toBe("England");
    expect(result.marks?.length).toBeGreaterThan(1);
  });

  it("never picks a match with a side that is not a Full Member, however recent", async () => {
    // The IPL fixture is the newest row of all and has predictions.
    const result = await loadHeroMatch();
    expect(result.match.matchId).not.toBe(9300);
    expect([result.match.teamA, result.match.teamB]).not.toContain("Mumbai Indians");
  });
});
