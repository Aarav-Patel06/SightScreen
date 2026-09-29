/**
 * The start-of-chase artifact, on real data (match 8429, India's chase of 388).
 *
 * The prediction made before ball 1 has no current run rate - no balls, 0/0 -
 * and the model reads that missing value differently from the 0.0 it sees
 * after a dot ball. So the first mark's swing, 45% -> 26% on 8429, is the
 * feature switching from missing to zero, not the delivery: re-scoring ball 1
 * with a run rate of 0.0 reproduces ball 2's prediction exactly. (Diagnosed
 * 2026-09-27; the model fix is SPEC.md's next model task.)
 *
 * Until that ships, no figure that describes a moment in the chase - biggest
 * swing, peak, the height scale - may be that transition.
 */

import { describe, expect, it } from "vitest";

import FIXTURE from "./fixtures/match-8429-predictions.json";
import { describeStrip, isStartOfChase, peakSwing, toMarks } from "./ball-strip";
import { parsePrediction, type WinProbPrediction } from "./prediction";

const predictions = FIXTURE.rows
  .map((row) => parsePrediction(row as never))
  .filter((p): p is WinProbPrediction => p !== null);
const marks = toMarks(predictions);

describe("the start-of-chase transition on 8429", () => {
  it("is the first mark, and is by far its largest raw swing", () => {
    expect(marks[0].ballsBowled).toBe(0);
    expect(Math.abs(marks[0].swing)).toBeCloseTo(0.198, 3);
    const rest = Math.max(...marks.slice(1).map((m) => Math.abs(m.swing)));
    expect(Math.abs(marks[0].swing)).toBeGreaterThan(rest);
  });

  it("is recognised as the start of the chase, and nothing else is", () => {
    expect(isStartOfChase(marks[0])).toBe(true);
    expect(marks.slice(1).filter(isStartOfChase)).toEqual([]);
  });

  it("is not the biggest swing", () => {
    const rest = Math.max(...marks.slice(1).map((m) => Math.abs(m.swing)));
    expect(peakSwing(marks)).toBe(rest);
  });

  it("is not what the screen-reader description calls the biggest swing", () => {
    const rest = Math.round(Math.max(...marks.slice(1).map((m) => Math.abs(m.swing))) * 100);
    expect(describeStrip(marks, "India")).toContain(`Biggest swing: ${rest}% points`);
  });
});

describe("a chase that opens with a wide", () => {
  it("keeps the wide's swing: both sides of it lack a run rate, so nothing switched", () => {
    const base = predictions.slice(0, 3).map((p) => ({ ...p }));
    // before ball 1, after a wide (still 0 legal balls), after the first legal ball
    base[1] = { ...base[1], balls_bowled: 0, score: 1, runs_required: 387 };
    const opened = toMarks(base);
    expect(isStartOfChase(opened[0])).toBe(false); // the wide
    expect(isStartOfChase(opened[1])).toBe(true); // the first legal ball
  });
});
