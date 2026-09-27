/**
 * The completed match page's summary of a chase, from stored predictions:
 * the chasing side's peak win probability and when, and the biggest
 * single-ball swing. Both describe moments IN the chase, so neither may be
 * the prediction made before ball 1, nor the start-of-chase transition (see
 * lib/ball-strip.ts isStartOfChase - the run-rate artifact).
 */

import { describe, expect, it } from "vitest";

import FIXTURE from "./fixtures/match-8429-predictions.json";
import { chaseSummary, formatOvers } from "./chase-summary";
import { parsePrediction, type WinProbPrediction } from "./prediction";

const predictions = FIXTURE.rows
  .map((row) => parsePrediction(row as never))
  .filter((p): p is WinProbPrediction => p !== null);

describe("formatOvers", () => {
  it("writes legal balls as overs.balls", () => {
    expect(formatOvers(0)).toBe("0.0");
    expect(formatOvers(87)).toBe("14.3");
    expect(formatOvers(300)).toBe("50.0");
  });
});

describe("chaseSummary on 8429", () => {
  const summary = chaseSummary(predictions);

  it("finds the peak among predictions made after a ball, not the pre-chase 45%", () => {
    const afterBalls = predictions.slice(1);
    const peak = Math.max(...afterBalls.map((p) => p.p));
    expect(summary.peak?.p).toBe(peak);
    expect(peak).toBeLessThan(predictions[0].p); // on 8429 the pre-ball estimate is higher
    const at = afterBalls.find((p) => p.p === peak)!;
    expect(summary.peak?.afterOvers).toBe(formatOvers(at.balls_bowled));
  });

  it("finds the biggest swing without the start-of-chase transition", () => {
    const swings = predictions.slice(1, -1).map((p, i) => predictions[i + 2].p - p.p);
    const biggest = Math.max(...swings.map(Math.abs));
    expect(summary.biggest?.pp).toBe(Math.round(biggest * 100));
    expect(summary.biggest?.pp).not.toBe(20); // the artifact's 45% -> 26%
    expect(summary.biggest?.at).toMatch(/^\d+\.\d$/);
    expect(["up", "down"]).toContain(summary.biggest?.direction);
  });
});

describe("chaseSummary on a chase too short to summarise", () => {
  it("returns nothing rather than a peak of one point", () => {
    expect(chaseSummary(predictions.slice(0, 1))).toEqual({ peak: null, biggest: null });
  });
});
