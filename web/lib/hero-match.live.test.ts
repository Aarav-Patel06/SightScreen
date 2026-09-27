/**
 * Which match the landing hero shows (standing rule: only ever an
 * international between two Full Members, live or not).
 *
 *   1. A Full Member international that is LIVE, IN ITS CHASE and has a
 *      prediction under five minutes old takes the hero.
 *   2. Otherwise the most recent COMPLETED Full Member international with
 *      predictions - including when a live row has gone quiet.
 *   3. A live match still in its first innings does not take it: the previous
 *      match stays until the chase starts. (The worker only writes innings-2
 *      predictions, so a first innings has none.)
 *
 * And what it says about itself: "Live" or "Latest match", and that the strip
 * is the chase only - "India's chase of 388".
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { fakeSupabase } from "./fake-postgrest";

let db: ReturnType<typeof fakeSupabase>;
vi.mock("./supabase-server", () => ({ supabaseServer: () => db }));

const { loadHeroMatch } = await import("./hero-match");

const ENGLAND = 29;
const INDIA = 20;
const AUSTRALIA = 7;
const NOW = Date.now();
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

const COMPLETED = {
  match_id: 8429, competition: "India tour of England", format: "ODI",
  start_time: "2026-07-19T00:00:00+00:00", team_a: ENGLAND, team_b: INDIA,
  winner: ENGLAND, status: "complete", result_method: "normal",
  win_by_runs: 27, win_by_wickets: null, outcome_method: null, tie_winner: null, tie_decided_by: null,
};
const LIVE = {
  match_id: 1000200, competition: "Australia tour of India", format: "T20",
  start_time: ago(90), team_a: AUSTRALIA, team_b: INDIA,
  winner: null, status: "live", result_method: null,
  win_by_runs: null, win_by_wickets: null, outcome_method: null, tie_winner: null, tie_decided_by: null,
};

function chase(matchId: number, batting: number, target: number, createdAt: string, n = 4, firstId = 1) {
  return Array.from({ length: n }, (_, i) => ({
    prediction_id: firstId + i, match_id: matchId, created_at: createdAt,
    model_version: "winprob2-20260910", prediction_type: "win_prob", innings: 2,
    batting_team_id: batting,
    payload: { p: 0.5, innings: 2, balls_bowled: i, balls_remaining: 120 - i,
      runs_required: target - i, score: i, wickets: 0, target, phase: "powerplay" },
  }));
}

function seed(matches: Record<string, unknown>[], predictions: Record<string, unknown>[]) {
  db = fakeSupabase({
    teams: [
      { team_id: ENGLAND, name: "England", full_member: true },
      { team_id: INDIA, name: "India", full_member: true },
      { team_id: AUSTRALIA, name: "Australia", full_member: true },
    ],
    matches,
    predictions,
  });
}

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("case 1: a live chase with a fresh prediction", () => {
  it("takes the hero, and says it is live", async () => {
    seed([COMPLETED, LIVE], [...chase(8429, INDIA, 388, ago(60 * 24 * 60)), ...chase(1000200, INDIA, 181, ago(1), 4, 100)]);
    const hero = await loadHeroMatch();
    expect(hero.match.matchId).toBe(1000200);
    expect(hero.match.isLive).toBe(true);
    expect(hero.match.label).toBe("Live");
    expect(hero.match.chase).toBe("India's chase of 181");
  });
});

describe("case 2: otherwise, the most recent completed Full Member international", () => {
  it("is the completed match when the live row's last prediction is older than five minutes", async () => {
    seed([COMPLETED, LIVE], [...chase(8429, INDIA, 388, ago(60 * 24 * 60)), ...chase(1000200, INDIA, 181, ago(6), 4, 100)]);
    const hero = await loadHeroMatch();
    expect(hero.match.matchId).toBe(8429);
    expect(hero.match.isLive).toBe(false);
    expect(hero.match.label).toBe("Latest match");
  });

  it("states the real result and that the strip is the chase", async () => {
    seed([COMPLETED], chase(8429, INDIA, 388, ago(60 * 24 * 60)));
    const hero = await loadHeroMatch();
    expect(hero.match.result).toBe("England won by 27 runs");
    expect(hero.match.chase).toBe("India's chase of 388");
  });
});

describe("case 3: a live match still in its first innings", () => {
  it("does not take the hero; the previous match stays", async () => {
    seed([COMPLETED, LIVE], chase(8429, INDIA, 388, ago(60 * 24 * 60)));
    const hero = await loadHeroMatch();
    expect(hero.match.matchId).toBe(8429);
    expect(hero.match.isLive).toBe(false);
    expect(hero.match.label).toBe("Latest match");
  });
});
