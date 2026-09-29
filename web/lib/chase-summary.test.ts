/**
 * The completed match page's summary of a chase, from stored predictions:
 * the chasing side's peak win probability and when, and the biggest
 * single-ball swing. Both describe moments IN the chase, so none of them may
 * come from the chase's first over: the model is still settling there (see
 * SPEC.md section 12.2, "Summary facts skip the first over").
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

  it("finds the peak and the low among predictions made after the first over", () => {
    const afterOver1 = predictions.filter((p) => p.balls_bowled >= 6);
    const peak = Math.max(...afterOver1.map((p) => p.p));
    const low = Math.min(...afterOver1.map((p) => p.p));
    expect(summary.peak?.p).toBe(peak);
    expect(summary.low?.p).toBe(low);
    expect(peak).toBeLessThan(predictions[0].p); // on 8429 the pre-ball estimate is higher
    const at = afterOver1.find((p) => p.p === peak)!;
    expect(summary.peak?.afterOvers).toBe(formatOvers(at.balls_bowled));
  });

  it("never reports a moment from the first over", () => {
    for (const figure of [summary.peak?.afterOvers, summary.low?.afterOvers, summary.biggest?.at]) {
      expect(figure).toMatch(/^\d+\.\d$/);
      expect(Number(figure!.split(".")[0])).toBeGreaterThanOrEqual(1);
    }
  });

  it("finds the biggest swing among deliveries bowled after the first over", () => {
    const swings = predictions
      .slice(0, -1)
      .map((p, i) => ({ from: p, swing: predictions[i + 1].p - p.p }))
      .filter(({ from }) => from.balls_bowled >= 6);
    const biggest = Math.max(...swings.map(({ swing }) => Math.abs(swing)));
    expect(summary.biggest?.pp).toBe(Math.round(biggest * 100));
    expect(summary.biggest?.pp).not.toBe(20); // the start-of-chase 45% -> 26%
    expect(["up", "down"]).toContain(summary.biggest?.direction);
  });

  it("says where on the curve each fact is, so the page can mark it", () => {
    expect(predictions[summary.peak!.index].p).toBe(summary.peak!.p);
    expect(predictions[summary.low!.index].p).toBe(summary.low!.p);
    // The swing is marked at the state AFTER the ball, which is where its
    // over is read from and where the curve shows the move.
    const after = predictions[summary.biggest!.index];
    const before = predictions[summary.biggest!.index - 1];
    expect(Math.round(Math.abs(after.p - before.p) * 100)).toBe(summary.biggest!.pp);
    expect(formatOvers(after.balls_bowled)).toBe(summary.biggest!.at);
  });
});

describe("chaseSummary on a chase whose extremes fall in the first over", () => {
  // Synthetic: a 25-point jump and the chase's highest and lowest figures
  // all sit in over 1; from over 2 on it drifts between 50% and 60%.
  const base = predictions[0];
  const ps = [0.5, 0.9, 0.1, 0.65, 0.4, 0.55, 0.5, 0.52, 0.6, 0.58, 0.5, 0.55];
  const synthetic = ps.map((p, i) => ({ ...base, prediction_id: i + 1, balls_bowled: i, p }));
  const summary = chaseSummary(synthetic);

  it("takes the peak, low and biggest swing from over 2 onwards only", () => {
    expect(summary.peak?.p).toBe(0.6);
    expect(summary.peak?.afterOvers).toBe("1.2");
    expect(summary.low?.p).toBe(0.5);
    expect(summary.biggest?.pp).toBe(8); // 0.52 -> 0.60, bowled at 1.1 balls in
    expect(summary.biggest?.at).toBe("1.2");
  });
});

describe("chaseSummary on a chase over inside its first over", () => {
  it("returns nothing rather than a figure from the settling-in over", () => {
    expect(chaseSummary(predictions.filter((p) => p.balls_bowled < 6))).toEqual({
      peak: null,
      low: null,
      biggest: null,
    });
  });
});

describe("chaseSummary on a chase too short to summarise", () => {
  it("returns nothing rather than a peak of one point", () => {
    expect(chaseSummary(predictions.slice(0, 1))).toEqual({ peak: null, low: null, biggest: null });
  });
});
