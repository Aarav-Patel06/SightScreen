/**
 * The result line on /matches.
 *
 * The same result the match page states (lib/match-result.ts) - "England
 * won by 27 runs" - and never the chase's last state. The final prediction
 * payload is the state BEFORE the last ball, so "chase needed 32 off 1"
 * describes a moment that never finished, and it used to follow every
 * winner on this page.
 *
 * Degrades one way: the full result; else, for a match whose winner Supabase
 * does not have (a live-worker row), which side won from prediction_outcomes;
 * else nothing.
 */

import { describe, expect, it } from "vitest";

import { resultLine, type MatchResult } from "./match-index";

function result(overrides: Partial<MatchResult> = {}): MatchResult {
  return {
    resultMethod: "normal",
    winner: null,
    winByRuns: null,
    winByWickets: null,
    outcomeMethod: null,
    tieWinner: null,
    tieDecidedBy: null,
    chaseWon: null,
    ...overrides,
  };
}

describe("resultLine", () => {
  it("states the real result, margin included", () => {
    expect(resultLine(result({ winner: "England", winByRuns: 27 }))).toBe("England won by 27 runs");
    expect(resultLine(result({ winner: "Sharjah Warriorz", winByWickets: 1 }))).toBe(
      "Sharjah Warriorz won by 1 wicket"
    );
    expect(resultLine(result({ resultMethod: "tie", tieWinner: "Punjab", tieDecidedBy: "super_over" }))).toBe(
      "Match tied · Punjab won the super over"
    );
    expect(resultLine(result({ resultMethod: "no_result" }))).toBe("No result");
  });

  it("never states the chase's last state", () => {
    const line = resultLine(result({ winner: "England", winByRuns: 27 }));
    expect(line).not.toMatch(/needed|required|off \d/);
  });

  it("says only who won when the margin is not recorded", () => {
    expect(resultLine(result({ winner: "Mumbai" }))).toBe("Mumbai won");
  });

  it("names the outcome by the target when the team is not known", () => {
    // matches.winner is NULL for a live-worker row, whose match never came
    // from the corpus. prediction_outcomes still knows which side won.
    expect(resultLine(result({ resultMethod: null, chaseWon: true }))).toBe("Target reached");
    expect(resultLine(result({ resultMethod: null, chaseWon: false }))).toBe("Target defended");
  });

  it("says nothing rather than guessing when nothing is known", () => {
    expect(resultLine(result({ resultMethod: null }))).toBeNull();
    expect(resultLine(null)).toBeNull();
  });
});
