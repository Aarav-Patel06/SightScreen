/**
 * A completed match's result, in words - never the final chase state.
 *
 * One case per outcome shape Cricsheet records (migration 20260927000001
 * lists their counts). A margin that is not recorded is left out, never
 * guessed; a result that is not recorded is null, so the page says nothing
 * rather than something invented.
 */

import { describe, expect, it } from "vitest";

import { resultText, type MatchOutcome } from "./match-result";

function outcome(overrides: Partial<MatchOutcome> = {}): MatchOutcome {
  return {
    resultMethod: "normal",
    winner: null,
    winByRuns: null,
    winByWickets: null,
    outcomeMethod: null,
    tieWinner: null,
    tieDecidedBy: null,
    ...overrides,
  };
}

describe("resultText", () => {
  it("says runs and wickets, singular and plural", () => {
    expect(resultText(outcome({ winner: "England", winByRuns: 27 }))).toBe("England won by 27 runs");
    expect(resultText(outcome({ winner: "England", winByRuns: 1 }))).toBe("England won by 1 run");
    expect(resultText(outcome({ winner: "India", winByWickets: 4 }))).toBe("India won by 4 wickets");
    expect(resultText(outcome({ winner: "India", winByWickets: 1 }))).toBe("India won by 1 wicket");
  });

  it("names the method when one decided it", () => {
    expect(
      resultText(outcome({ winner: "India", winByRuns: 18, outcomeMethod: "D/L", resultMethod: "dls" }))
    ).toBe("India won by 18 runs (DLS)");
    expect(
      resultText(outcome({ winner: "Mumbai", winByWickets: 8, outcomeMethod: "VJD", resultMethod: "dls" }))
    ).toBe("Mumbai won by 8 wickets (VJD)");
    expect(resultText(outcome({ winner: "Malta", outcomeMethod: "Awarded", resultMethod: "dls" }))).toBe(
      "Malta were awarded the match"
    );
    expect(
      resultText(outcome({ winner: "Lancashire", outcomeMethod: "Lost fewer wickets", resultMethod: "dls" }))
    ).toBe("Lancashire won (lost fewer wickets)");
  });

  it("says only who won when the margin is not recorded", () => {
    // A live-worker match: the provider feed has a winner and no margin.
    expect(resultText(outcome({ winner: "England" }))).toBe("England won");
  });

  it("says a tie, and who won what decided it", () => {
    expect(resultText(outcome({ resultMethod: "tie" }))).toBe("Match tied");
    expect(resultText(outcome({ resultMethod: "tie", outcomeMethod: "D/L" }))).toBe("Match tied (DLS)");
    expect(
      resultText(outcome({ resultMethod: "tie", tieWinner: "Punjab", tieDecidedBy: "super_over" }))
    ).toBe("Match tied · Punjab won the super over");
    expect(
      resultText(outcome({ resultMethod: "tie", tieWinner: "New Zealand", tieDecidedBy: "bowl_out" }))
    ).toBe("Match tied · New Zealand won the bowl-out");
  });

  it("says no result", () => {
    expect(resultText(outcome({ resultMethod: "no_result" }))).toBe("No result");
  });

  it("says nothing when nothing is recorded", () => {
    expect(resultText(outcome({ resultMethod: null }))).toBeNull();
  });

  it("never states the chase's last state", () => {
    const text = resultText(outcome({ winner: "England", winByRuns: 27 }));
    expect(text).not.toMatch(/needed|required|off \d/);
  });
});
