/**
 * A win probability is never shown as certain. The model's output is never
 * exactly 0 or 1, and "100%" on a chase that still needs a run claims
 * something no model can know, so the ends read ">99%" and "<1%".
 */

import { describe, expect, it } from "vitest";

import { formatProbability } from "./probability";

describe("formatProbability", () => {
  it("rounds to whole percent in between", () => {
    expect(formatProbability(0.4549)).toBe("45%");
    expect(formatProbability(0.36)).toBe("36%");
    expect(formatProbability(0.994)).toBe("99%");
    expect(formatProbability(0.006)).toBe("1%");
  });

  it("never says 100%", () => {
    expect(formatProbability(0.995)).toBe(">99%");
    expect(formatProbability(0.99785)).toBe(">99%");
    expect(formatProbability(1)).toBe(">99%");
  });

  it("never says 0%", () => {
    expect(formatProbability(0.0049)).toBe("<1%");
    expect(formatProbability(0)).toBe("<1%");
  });
});
