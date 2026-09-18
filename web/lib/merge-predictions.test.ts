/**
 * The gap between the server render and the subscription (Decision 4).
 *
 * These are the timing cases nobody reproduces by hand, which is exactly why
 * the merge is a pure function rather than logic inside a component.
 */

import { describe, expect, it } from "vitest";

import { highWaterMark, mergePredictions } from "./merge-predictions";
import type { WinProbPrediction } from "./prediction";

function p(prediction_id: number, balls_bowled: number, prob = 0.5): WinProbPrediction {
  return {
    prediction_id,
    created_at: new Date(1_700_000_000_000 + prediction_id * 1000).toISOString(),
    model_version: "winprob2-20260910",
    p: prob,
    innings: 2,
    balls_bowled,
    balls_remaining: 120 - balls_bowled,
    runs_required: 100,
    score: 50,
    wickets: 2,
    target: 150,
    phase: "middle",
  };
}

describe("mergePredictions", () => {
  it("keeps server-rendered history when nothing has streamed yet", () => {
    const initial = [p(1, 0), p(2, 1)];
    expect(mergePredictions(initial, []).map((x) => x.prediction_id)).toEqual([1, 2]);
  });

  it("recovers a row inserted during the gap before SUBSCRIBED", () => {
    // The whole reason this function exists. Row 3 landed after the server
    // query and before the channel opened, so the stream never carried it;
    // the reconcile refetch is what brings it back.
    const fromServer = [p(1, 0), p(2, 1)];
    const missedDuringGap = [p(3, 2)];
    const fromStream = [p(4, 3)];

    const merged = mergePredictions(
      mergePredictions(fromServer, missedDuringGap),
      fromStream
    );
    expect(merged.map((x) => x.prediction_id)).toEqual([1, 2, 3, 4]);
  });

  it("does not double-count a row delivered by BOTH the refetch and the stream", () => {
    // The reconcile window overlaps the stream deliberately, so this is the
    // normal case, not an edge case.
    const fromServer = [p(1, 0)];
    const reconcile = [p(2, 1), p(3, 2)];
    const stream = [p(3, 2), p(4, 3)];

    const merged = mergePredictions(mergePredictions(fromServer, reconcile), stream);
    expect(merged.map((x) => x.prediction_id)).toEqual([1, 2, 3, 4]);
    expect(merged).toHaveLength(4);
  });

  it("orders by prediction_id, so extras sharing a ball number keep their order", () => {
    // Match 9339: 125 deliveries across 111 legal balls. A wide does not
    // advance balls_bowled, so sorting by ball would make the order of those
    // two deliveries arbitrary.
    const wideThenLegal = [p(11, 40, 0.30), p(12, 40, 0.28), p(13, 41, 0.31)];
    const merged = mergePredictions([], wideThenLegal.slice().reverse());
    expect(merged.map((x) => x.prediction_id)).toEqual([11, 12, 13]);
    expect(merged.map((x) => x.balls_bowled)).toEqual([40, 40, 41]);
  });

  it("lets a later row for the same id win, so a corrected value is not ignored", () => {
    const merged = mergePredictions([p(1, 0, 0.2)], [p(1, 0, 0.9)]);
    expect(merged).toHaveLength(1);
    expect(merged[0].p).toBe(0.9);
  });

  it("high water mark is 0 for an empty history, so the reconcile fetches everything", () => {
    expect(highWaterMark([])).toBe(0);
    expect(highWaterMark([p(7, 3), p(2, 1)])).toBe(7);
  });
});
