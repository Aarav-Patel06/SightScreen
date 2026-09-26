import { describe, expect, it } from "vitest";

import { battingTeamName, winSubject } from "./batting-team";

const sides = [
  { id: 29, name: "England" },
  { id: 20, name: "India" },
];

describe("battingTeamName", () => {
  it("names the side on the row, not the first-listed side", () => {
    expect(battingTeamName({ batting_team_id: 20 }, sides)).toBe("India");
  });

  it("is null when the row does not say", () => {
    expect(battingTeamName({ batting_team_id: null }, sides)).toBeNull();
    expect(battingTeamName(null, sides)).toBeNull();
  });

  it("is null for an id that is neither of this match's sides", () => {
    // Match 3 means different matches on the two databases. A name borrowed
    // from the other one would be worse than no name.
    expect(battingTeamName({ batting_team_id: 4 }, sides)).toBeNull();
  });
});

describe("winSubject", () => {
  it("says 'batting side' rather than guessing", () => {
    expect(winSubject(null)).toBe("batting side");
    expect(winSubject("India")).toBe("India");
  });
});
