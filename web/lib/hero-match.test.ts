/**
 * The landing hero's fallback behaviour (UI-PHASE-2.md §2.4).
 *
 * §2.4 turns the hero into a query and keeps the fixture as the floor, then
 * says: "Test the fallback by pointing at an unreachable database, as Session
 * 2 did." That is what these do. The happy path is a lookup and barely worth
 * asserting; the interesting paths are the four ways this can find nothing,
 * because each one is a different empty and only one of them is an outage.
 *
 * Supabase is stubbed, not reached — a test that needs the network cannot
 * assert what happens when the network is gone.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HERO_FIXTURE from "./fixtures/hero-match.json";

const teamsQuery = vi.fn();
const matchesQuery = vi.fn();
const predictionsQuery = vi.fn();

vi.mock("./supabase-server", () => ({
  supabaseServer: () => ({
    from: (table: string) => {
      if (table === "teams") {
        return { select: () => ({ eq: () => teamsQuery() }) };
      }
      if (table === "matches") {
        return {
          select: () => ({
            in: () => ({
              in: () => ({ order: () => ({ limit: () => matchesQuery() }) }),
            }),
          }),
        };
      }
      return { select: () => ({ in: () => ({ not: () => predictionsQuery() }) }) };
    },
  }),
}));

const { loadHeroMatch } = await import("./hero-match");

const TEAMS = [
  { team_id: 3, name: "India" },
  { team_id: 7, name: "Australia" },
];
const MATCHES = [
  {
    match_id: 9500,
    competition: "Border-Gavaskar Trophy",
    format: "T20",
    start_time: "2026-07-26T14:00:00+00:00",
    team_a: 3,
    team_b: 7,
    winner: 3,
  },
  {
    match_id: 9400,
    competition: "Border-Gavaskar Trophy",
    format: "ODI",
    start_time: "2026-07-21T14:00:00+00:00",
    team_a: 7,
    team_b: 3,
    winner: null,
  },
];

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  teamsQuery.mockReset();
  matchesQuery.mockReset();
  predictionsQuery.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function healthy() {
  teamsQuery.mockResolvedValue({ data: TEAMS, error: null });
  matchesQuery.mockResolvedValue({ data: MATCHES, error: null });
  predictionsQuery.mockResolvedValue({ data: [{ match_id: 9500 }], error: null });
}

describe("when the database answers", () => {
  it("returns the most recent Full Member match that has predictions", async () => {
    healthy();
    const result = await loadHeroMatch();

    expect(result.stale).toBe(false);
    expect(result.capturedAt).toBeUndefined();
    expect(result.match).toEqual({
      matchId: 9500,
      competition: "Border-Gavaskar Trophy",
      format: "T20",
      date: "2026-07-26",
      teamA: "India",
      teamB: "Australia",
      winner: "India",
    });
  });

  it("skips a more recent match that has no keyed predictions", async () => {
    // The whole reason step 3 exists. 9500 is newer, so a query that only
    // ordered by date would pick it and render an empty chart.
    healthy();
    predictionsQuery.mockResolvedValue({ data: [{ match_id: 9400 }], error: null });

    const result = await loadHeroMatch();
    expect(result.stale).toBe(false);
    expect(result.match.matchId).toBe(9400);
    expect(result.match.winner).toBeNull();
  });
});

describe("when the database is unreachable", () => {
  const OUTAGE = { message: "fetch failed", code: "ECONNREFUSED" };

  it.each([
    ["the teams query fails", () => teamsQuery.mockResolvedValue({ data: null, error: OUTAGE })],
    ["the matches query fails", () => matchesQuery.mockResolvedValue({ data: null, error: OUTAGE })],
    [
      "the predictions query fails",
      () => predictionsQuery.mockResolvedValue({ data: null, error: OUTAGE }),
    ],
  ])("falls back to the committed fixture when %s", async (_label, breakIt) => {
    healthy();
    breakIt();

    const result = await loadHeroMatch();
    expect(result.stale).toBe(true);
    expect(result.capturedAt).toBe(HERO_FIXTURE.capturedAt);
    expect(result.match).toEqual(HERO_FIXTURE.match);
  });

  it("says why, so a paused database is distinguishable from an empty one", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    healthy();
    teamsQuery.mockResolvedValue({ data: null, error: OUTAGE });

    await loadHeroMatch();
    expect(warn).toHaveBeenCalledOnce();
    const line = warn.mock.calls[0][0] as string;
    expect(line).toContain("fetch failed");
    expect(line).toContain("ECONNREFUSED");
  });
});

describe("when the database answers but has nothing to show", () => {
  it("falls back when no team is flagged full_member", async () => {
    // The shape of a migration that was applied to one database and not the
    // other: the query succeeds and returns zero rows.
    healthy();
    teamsQuery.mockResolvedValue({ data: [], error: null });

    const result = await loadHeroMatch();
    expect(result.stale).toBe(true);
    expect(result.match).toEqual(HERO_FIXTURE.match);
  });

  it("falls back when no candidate has keyed predictions", async () => {
    healthy();
    predictionsQuery.mockResolvedValue({ data: [], error: null });

    const result = await loadHeroMatch();
    expect(result.stale).toBe(true);
  });

  it("never renders a side it cannot name", async () => {
    // What ids 1-3 produced: a match row whose team ids resolve to nothing.
    // Rendering "undefined v undefined" is worse than rendering the fixture.
    healthy();
    matchesQuery.mockResolvedValue({
      data: [{ ...MATCHES[0], team_a: 999 }],
      error: null,
    });
    predictionsQuery.mockResolvedValue({ data: [{ match_id: 9500 }], error: null });

    const result = await loadHeroMatch();
    expect(result.stale).toBe(true);
    expect(result.match).toEqual(HERO_FIXTURE.match);
  });
});

describe("the committed fixture itself", () => {
  it("is a complete, nameable match", async () => {
    // It is rendered verbatim when Supabase is down, so a fixture with a null
    // team would be a 500 discovered during an outage. Generated by
    // scripts/make-hero-fixture.mjs from a real row.
    expect(HERO_FIXTURE.match.teamA).toBeTruthy();
    expect(HERO_FIXTURE.match.teamB).toBeTruthy();
    expect(HERO_FIXTURE.match.matchId).toBeGreaterThan(0);
    expect(HERO_FIXTURE.capturedAt).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
