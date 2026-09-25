/**
 * The ball strip's derivation, tested without a browser.
 *
 * Every test here covers a way the strip can be wrong while looking right: an
 * off-by-one shifts the whole innings one ball and still draws a plausible
 * curve; extras collapse two deliveries into one mark and shorten the strip by
 * an amount nobody counts; a guessed final ball invents an outcome that was
 * never recorded. None of those produce an error, a blank, or anything a
 * reviewer would catch by looking at the page.
 */

import { describe, expect, it } from "vitest";

import {
  describeEvent,
  describeStrip,
  peakSwing,
  pitchFor,
  tierFor,
  tierHasFill,
  tierHasPerMarkHit,
  toMarks,
  type Mark,
  type Tier,
} from "./ball-strip";
import type { WinProbPrediction } from "./prediction";

/** A prediction row. Defaults are a mid-innings state; override what matters. */
function pred(overrides: Partial<WinProbPrediction> = {}): WinProbPrediction {
  return {
    prediction_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    model_version: "winprob2-20260910",
    p: 0.5,
    innings: 2,
    balls_bowled: 0,
    balls_remaining: 120,
    runs_required: 135,
    score: 0,
    wickets: 0,
    target: 135,
    phase: "middle",
    ...overrides,
  };
}

describe("toMarks", () => {
  it("attributes a ball's effect to the mark it was bowled on, not the next", () => {
    // match_states holds state BEFORE the delivery, so the 12-run jump between
    // these two rows was caused by ball 0, not ball 1. Getting this backwards
    // shifts every mark in the innings one place right.
    const marks = toMarks([
      pred({ prediction_id: 1, balls_bowled: 0, score: 0, p: 0.5 }),
      pred({ prediction_id: 2, balls_bowled: 1, score: 12, p: 0.62 }),
    ]);

    expect(marks[0].event).toBe("score");
    expect(marks[0].swing).toBeCloseTo(0.12, 10);
    expect(marks[0].score).toBe(0); // the state the bowler ran in to
  });

  it("calls a ball with no change in score a dot", () => {
    const marks = toMarks([
      pred({ prediction_id: 1, score: 40, p: 0.5 }),
      pred({ prediction_id: 2, score: 40, p: 0.47 }),
    ]);

    expect(marks[0].event).toBe("dot");
    expect(marks[0].swing).toBeCloseTo(-0.03, 10);
  });

  it("detects a wicket from the count rising, not from a dismissal type", () => {
    const marks = toMarks([
      pred({ prediction_id: 1, wickets: 2, score: 40 }),
      pred({ prediction_id: 2, wickets: 3, score: 40 }),
    ]);

    expect(marks[0].event).toBe("wicket");
  });

  it("calls a run-out that conceded a single a wicket, not a scoring shot", () => {
    const marks = toMarks([
      pred({ prediction_id: 1, wickets: 2, score: 40 }),
      pred({ prediction_id: 2, wickets: 3, score: 41 }),
    ]);

    expect(marks[0].event).toBe("wicket");
  });

  it("gives an extra its own mark even though balls_bowled repeats", () => {
    // A wide does not advance balls_bowled. If marks were keyed on ball number
    // these two deliveries would collapse into one and the strip would be a
    // ball short, with nothing to indicate it.
    const marks = toMarks([
      pred({ prediction_id: 1, balls_bowled: 30, score: 40 }),
      pred({ prediction_id: 2, balls_bowled: 30, score: 41 }),
      pred({ prediction_id: 3, balls_bowled: 31, score: 41 }),
    ]);

    expect(marks).toHaveLength(3);
    expect(marks.map((mark) => mark.index)).toEqual([0, 1, 2]);
    expect(marks[0].event).toBe("score"); // the wide itself conceded a run
    expect(marks[1].event).toBe("dot");
  });

  it("marks the final delivery unknown rather than guessing it", () => {
    const marks = toMarks([
      pred({ prediction_id: 1, score: 130, p: 0.8 }),
      pred({ prediction_id: 2, score: 134, p: 0.92 }),
    ]);

    const last = marks[marks.length - 1];
    expect(last.event).toBe("unknown");
    expect(last.swing).toBe(0);
  });

  it("preserves the order it was given rather than re-sorting", () => {
    // Callers order by prediction_id (merge-predictions.ts). If one does not,
    // the strip should be visibly wrong rather than quietly repaired here.
    const marks = toMarks([
      pred({ prediction_id: 9, p: 0.3 }),
      pred({ prediction_id: 4, p: 0.4 }),
    ]);

    expect(marks.map((mark) => mark.predictionId)).toEqual([9, 4]);
  });

  it("handles an empty innings", () => {
    expect(toMarks([])).toEqual([]);
  });

  it("handles a single prediction, which has no successor at all", () => {
    const marks = toMarks([pred()]);
    expect(marks).toHaveLength(1);
    expect(marks[0].event).toBe("unknown");
  });
});

describe("tierFor", () => {
  it("picks a tier from the measured pitch", () => {
    const cases: Array<[number, Tier]> = [
      [12, "full"],
      [6, "full"],
      [5.9, "reduced"],
      [4, "reduced"],
      [3.9, "minimal"],
      [2, "minimal"],
      [1.9, "area"],
      [0.99, "area"],
      [0, "area"],
    ];

    for (const [pitch, expected] of cases) {
      expect(tierFor(pitch), `pitch ${pitch}`).toBe(expected);
    }
  });

  it("drops fill below the 4px floor, where hollow would render as solid", () => {
    expect(tierHasFill(tierFor(4))).toBe(true);
    expect(tierHasFill(tierFor(3.9))).toBe(false);
  });

  it("offers per-mark hit targets only where a mark is wide enough to hit", () => {
    expect(tierHasPerMarkHit(tierFor(6))).toBe(true);
    expect(tierHasPerMarkHit(tierFor(5.9))).toBe(false);
  });

  it("puts the real measured cases in the tiers the phase expects", () => {
    // The four sizes in UI-PHASE.md §1.2, against a 125-delivery innings -
    // the fixture match, which is the densest a T20 second innings gets.
    const marks = 125;
    expect(tierFor(pitchFor(1140, marks))).toBe("full"); // hero
    expect(tierFor(pitchFor(700, marks))).toBe("reduced"); // match page
    expect(tierFor(pitchFor(308, marks))).toBe("minimal"); // 340px phone
    expect(tierFor(pitchFor(120, marks))).toBe("area"); // sparkline

    // And an ODI second innings, which the replay manifest contains eight of.
    expect(tierFor(pitchFor(308, 301))).toBe("area");
  });
});

describe("pitchFor", () => {
  it("is width divided by mark count", () => {
    expect(pitchFor(308, 121)).toBeCloseTo(2.545, 3);
  });

  it("does not divide by zero on an innings with no deliveries", () => {
    expect(pitchFor(308, 0)).toBe(0);
  });
});

describe("peakSwing", () => {
  it("takes the largest absolute swing in either direction", () => {
    const marks = toMarks([
      pred({ prediction_id: 1, p: 0.5 }),
      pred({ prediction_id: 2, p: 0.2 }), // -0.30
      pred({ prediction_id: 3, p: 0.35 }), // +0.15
    ]);

    expect(peakSwing(marks)).toBeCloseTo(0.3, 10);
  });

  it("is zero for an empty innings, so callers can divide safely", () => {
    expect(peakSwing([])).toBe(0);
  });
});

describe("describeStrip", () => {
  it("describes the innings for a screen reader, including the unknown last ball", () => {
    const marks = toMarks([
      pred({ prediction_id: 1, p: 0.5, score: 0, wickets: 0 }),
      pred({ prediction_id: 2, p: 0.3, score: 0, wickets: 1 }),
      pred({ prediction_id: 3, p: 0.42, score: 4, wickets: 1 }),
    ]);

    const text = describeStrip(marks);
    expect(text).toContain("3 deliveries");
    expect(text).toContain("50%");
    expect(text).toContain("42%");
    expect(text).toContain("1 wicket,");
    expect(text).toContain("20 percentage points");
    expect(text).toContain("not recorded");
  });

  it("says so plainly when there is nothing to describe", () => {
    expect(describeStrip([])).toBe("No deliveries recorded.");
  });
});

describe("describeEvent", () => {
  const mark = (over: Partial<Mark>): Mark => ({
    index: 0, predictionId: 1, p: 0.5, swing: 0, phase: "middle",
    ballsBowled: 10, score: 40, wickets: 1, runsRequired: 60,
    ballsRemaining: 50, runs: 0, legal: true, event: "dot", ...over,
  });

  it("names a dot", () => {
    expect(describeEvent(mark({ event: "dot", runs: 0 }))).toBe("dot");
  });

  it("names runs with the number, which is the bug it was written for", () => {
    // The readout used to print the bare string "score" here, because the
    // run delta was computed in toMarks and thrown away.
    expect(describeEvent(mark({ event: "score", runs: 1 }))).toBe("1 run");
    expect(describeEvent(mark({ event: "score", runs: 2 }))).toBe("2 runs");
    expect(describeEvent(mark({ event: "score", runs: 4 }))).toBe("4 runs");
    expect(describeEvent(mark({ event: "score", runs: 6 }))).toBe("6 runs");
  });

  it("does not call a four a boundary", () => {
    // A stroke for four and four byes both raise the score by four, and the
    // payload carries no extras breakdown. "four" would claim a shot the
    // data cannot see.
    expect(describeEvent(mark({ event: "score", runs: 4 }))).not.toMatch(/four|boundary/i);
  });

  it("names an extra, which is the one thing the ball key does reveal", () => {
    // balls_bowled does not advance on a wide or a no-ball.
    expect(describeEvent(mark({ event: "score", runs: 1, legal: false }))).toBe("1 extra");
    expect(describeEvent(mark({ event: "score", runs: 2, legal: false }))).toBe("2 extras");
  });

  it("puts a wicket above runs, as toMarks does", () => {
    expect(describeEvent(mark({ event: "wicket", runs: 1 }))).toBe("wicket");
  });

  it("says the last ball is not recorded rather than guessing", () => {
    expect(describeEvent(mark({ event: "unknown" }))).toBe("outcome not recorded");
  });
});

describe("toMarks keeps what the readout needs", () => {
  it("carries the run delta, balls remaining, and whether the ball was legal", () => {
    const rows = [
      { p: 0.5, score: 40, wickets: 1, balls_bowled: 10, balls_remaining: 50, runs_required: 60 },
      { p: 0.6, score: 44, wickets: 1, balls_bowled: 11, balls_remaining: 49, runs_required: 56 },
      { p: 0.6, score: 45, wickets: 1, balls_bowled: 11, balls_remaining: 49, runs_required: 55 },
    ].map((r, i) => ({
      prediction_id: i, created_at: "", model_version: "m", innings: 2,
      target: 101, phase: "middle" as const, ...r,
    }));

    const marks = toMarks(rows);
    expect(marks[0].runs).toBe(4);
    expect(marks[0].legal).toBe(true);
    expect(marks[0].ballsRemaining).toBe(50);
    // balls_bowled did not advance, so this one was a wide or a no-ball.
    expect(marks[1].runs).toBe(1);
    expect(marks[1].legal).toBe(false);
  });
});
