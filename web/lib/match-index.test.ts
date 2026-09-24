/**
 * The result line on /matches.
 *
 * Worth testing because it is the one place on the index that makes a claim
 * about what happened, and because the temptation it resists - computing a
 * margin - produces a number that looks authoritative and cannot be checked.
 *
 * The final prediction payload is the state BEFORE the last ball, the same
 * off-by-one lib/ball-strip.ts documents. So the wickets and balls it carries
 * are the wickets and balls at the second-to-last moment of the match, and
 * "won by 4 wickets with 1 ball to spare" is not derivable from them.
 * "1 needed off 1" is, and is exactly true.
 */

import { describe, expect, it } from "vitest";

import { resultLine, type MatchResult } from "./match-index";

function result(overrides: Partial<MatchResult> = {}): MatchResult {
  return {
    winner: null,
    chaseWon: null,
    runsRequired: null,
    ballsRemaining: null,
    ...overrides,
  };
}

describe("resultLine", () => {
  it("names the winning team when matches.winner is mirrored", () => {
    expect(
      resultLine(result({ winner: "Sharjah Warriorz", runsRequired: 1, ballsRemaining: 1 }))
    ).toBe("Sharjah Warriorz won · chase needed 1 off 1");
  });

  it("names the outcome by the target when the team is not known", () => {
    // matches.winner is NULL for a live-worker row, whose match never came
    // from the corpus. prediction_outcomes still knows which side won.
    expect(resultLine(result({ chaseWon: true, runsRequired: 4, ballsRemaining: 3 }))).toBe(
      "Target reached · chase needed 4 off 3"
    );
    expect(resultLine(result({ chaseWon: false, runsRequired: 23, ballsRemaining: 6 }))).toBe(
      "Target defended · chase needed 23 off 6"
    );
  });

  it("prefers the named team over the derived side", () => {
    expect(resultLine(result({ winner: "Mumbai", chaseWon: false }))).toBe("Mumbai won");
  });

  it("never states a margin", () => {
    // The guard that matters. A margin would be read as "won by 4 wickets"
    // and is not derivable: the last payload predates the final ball, so a
    // run out on it would make the wicket count wrong by one and nothing
    // would say so.
    const line = resultLine(
      result({ winner: "Sharjah Warriorz", runsRequired: 1, ballsRemaining: 1 })
    );
    expect(line).not.toMatch(/wicket/i);
    expect(line).not.toMatch(/\bby\b/);
  });

  it("says only who won when the situation is unknown", () => {
    expect(resultLine(result({ winner: "Mumbai" }))).toBe("Mumbai won");
  });

  it("says nothing rather than guessing when nothing is known", () => {
    // The five matches with no predictions and no mirrored winner. An
    // em dash in the cell beats a confident blank claim.
    expect(resultLine(result())).toBeNull();
    expect(resultLine(null)).toBeNull();
  });

  it("does not treat a chase won off the last ball as a missing situation", () => {
    // 0 is a real value here and `??` would keep it; `||` would not. The
    // narrowest possible win - scores level, no balls left - must still
    // render its numbers.
    expect(resultLine(result({ chaseWon: true, runsRequired: 0, ballsRemaining: 0 }))).toBe(
      "Target reached · chase needed 0 off 0"
    );
  });
});
