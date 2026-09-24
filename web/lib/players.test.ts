/**
 * Player search and the derived figures.
 *
 * The search is tested against §4.4's own acceptance — "Kohli" and "Virat
 * Kohli" must reach the same player — using the real corpus spelling, which
 * is "V Kohli". That is the case that makes the test worth having: neither
 * query is a substring of the stored name, and a naive `includes` passes the
 * first and fails the second.
 *
 * The figures are tested for what they do when a denominator is zero. A
 * batting average with no dismissals is not the run total and not infinity;
 * it is undefined, and a page that prints one of the first two is lying in a
 * way nobody checks.
 */

import { describe, expect, it } from "vitest";

import {
  THIN_INNINGS,
  battingAverage,
  bowlingAverage,
  economy,
  isThin,
  searchPlayers,
  strikeRate,
  type CareerLine,
  type PlayerRow,
} from "./players";

/** Rows as `player_index` stores them: the resolver's output, precomputed. */
function player(
  playerId: number,
  name: string,
  normalized: string,
  surname: string,
  batRuns = 0
): PlayerRow {
  return {
    playerId,
    name,
    normalized,
    surname,
    matches: 10,
    formats: ["T20"],
    batInnings: 10,
    batRuns,
    bowlInnings: 0,
    bowlWickets: 0,
  };
}

const INDEX: PlayerRow[] = [
  player(1, "V Kohli", "v kohli", "kohli", 28134),
  player(2, "T Kohli", "t kohli", "kohli", 823),
  player(3, "Parth Kohli", "parth kohli", "kohli", 59),
  player(4, "RG Sharma", "rg sharma", "sharma", 19700),
  player(5, "JJ Bumrah", "jj bumrah", "bumrah", 400),
];

describe("searchPlayers", () => {
  it("finds a player by surname alone", () => {
    const found = searchPlayers(INDEX, "Kohli");
    expect(found.map((p) => p.name)).toEqual(["V Kohli", "T Kohli", "Parth Kohli"]);
  });

  it("finds the same player by full name, which is not how the corpus spells it", () => {
    // §4.4's acceptance. The corpus stores "V Kohli", so "virat kohli" is
    // not a prefix, not a substring, and not a word match - it can only be
    // reached through the surname key the resolver produced.
    const found = searchPlayers(INDEX, "Virat Kohli");
    expect(found.length).toBeGreaterThan(0);
    expect(found[0].name).toBe("V Kohli");
  });

  it("ranks the player someone probably meant first", () => {
    // Three Kohlis. Someone typing the surname means the one with 28,134
    // career runs, not the one with 59.
    expect(searchPlayers(INDEX, "kohli")[0].name).toBe("V Kohli");
  });

  it("prefers an exact name over a surname match", () => {
    const found = searchPlayers(INDEX, "parth kohli");
    expect(found[0].name).toBe("Parth Kohli");
  });

  it("is case and whitespace insensitive", () => {
    expect(searchPlayers(INDEX, "  BUMRAH ")[0].name).toBe("JJ Bumrah");
  });

  it("matches a word inside the name", () => {
    expect(searchPlayers(INDEX, "sharma")[0].name).toBe("RG Sharma");
  });

  it("returns nothing for an empty query rather than everything", () => {
    // The index is 8,575 rows. An empty query returning all of them would
    // render the whole table on the first keystroke and then again on the
    // backspace.
    expect(searchPlayers(INDEX, "")).toEqual([]);
    expect(searchPlayers(INDEX, "   ")).toEqual([]);
  });

  it("returns nothing for a name that is not there", () => {
    expect(searchPlayers(INDEX, "zzzzz")).toEqual([]);
  });
});

// --- the figures ---------------------------------------------------------

function line(overrides: Partial<CareerLine> = {}): CareerLine {
  return {
    format: "T20",
    phase: "all",
    batInnings: 100,
    batBalls: 1000,
    batRuns: 1400,
    batOuts: 40,
    batFours: 120,
    batSixes: 60,
    bowlBalls: 600,
    bowlRuns: 700,
    bowlWickets: 30,
    ...overrides,
  };
}

describe("derived figures", () => {
  it("computes the usual rates", () => {
    const l = line();
    expect(battingAverage(l)).toBeCloseTo(35, 5);
    expect(strikeRate(l)).toBeCloseTo(140, 5);
    expect(economy(l)).toBeCloseTo(7, 5);
    expect(bowlingAverage(l)).toBeCloseTo(23.333, 3);
  });

  it("returns null for an average with no dismissals, not the run total", () => {
    // A batter never out has no average. Returning batRuns would print
    // "1400" in an Average column, which is the worst available answer.
    expect(battingAverage(line({ batOuts: 0 }))).toBeNull();
  });

  it("returns null for a bowling average with no wickets", () => {
    expect(bowlingAverage(line({ bowlWickets: 0 }))).toBeNull();
  });

  it("returns null rather than dividing by zero balls", () => {
    expect(strikeRate(line({ batBalls: 0 }))).toBeNull();
    expect(economy(line({ bowlBalls: 0 }))).toBeNull();
  });

  it("does not treat a genuine zero as missing", () => {
    // A batter out twice for nothing has an average of 0, which is a fact.
    expect(battingAverage(line({ batRuns: 0, batOuts: 2 }))).toBe(0);
    expect(strikeRate(line({ batRuns: 0, batBalls: 10 }))).toBe(0);
  });
});

describe("isThin", () => {
  it("judges on innings, not balls", () => {
    // 300 balls across 4 innings is a computable strike rate and an average
    // nobody should quote. lib/accuracy.ts makes the same call on matches
    // rather than rows.
    expect(isThin(line({ batInnings: 4, batBalls: 300 }))).toBe(true);
  });

  it("is exclusive at the threshold", () => {
    expect(isThin(line({ batInnings: THIN_INNINGS }))).toBe(false);
    expect(isThin(line({ batInnings: THIN_INNINGS - 1 }))).toBe(true);
  });
});
