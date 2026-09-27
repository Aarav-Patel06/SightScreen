/**
 * A completed chase in two facts, from stored predictions: when the chasing
 * side's chance peaked, and the single ball that moved it most.
 *
 * Both describe moments IN the chase, so neither uses the prediction made
 * before ball 1 (the model's pre-chase estimate, not a moment) or the
 * start-of-chase transition, whose swing is the run-rate feature switching
 * from missing to zero rather than anything the ball did (isStartOfChase).
 */

import { isStartOfChase, toMarks, type BallEvent } from "./ball-strip";
import type { WinProbPrediction } from "./prediction";

export interface ChaseSummary {
  /** The highest win probability after a ball, and after how many overs. */
  peak: { p: number; afterOvers: string } | null;
  /** The lowest, likewise - the telling figure for a chase that was WON. */
  low: { p: number; afterOvers: string } | null;
  /** The largest single-ball swing, in whole percentage points. */
  biggest: {
    pp: number;
    direction: "up" | "down";
    event: BallEvent;
    runs: number;
    legal: boolean;
    /** Overs completed once the ball was bowled, e.g. "23.4". */
    at: string;
  } | null;
}

/** Legal balls as overs.balls: 87 -> "14.3". */
export function formatOvers(legalBalls: number): string {
  return `${Math.floor(legalBalls / 6)}.${legalBalls % 6}`;
}

export function chaseSummary(predictions: readonly WinProbPrediction[]): ChaseSummary {
  const afterBalls = predictions.slice(1);
  if (afterBalls.length === 0) return { peak: null, low: null, biggest: null };

  const top = afterBalls.reduce((best, p) => (p.p > best.p ? p : best));
  const peak = { p: top.p, afterOvers: formatOvers(top.balls_bowled) };
  const bottom = afterBalls.reduce((worst, p) => (p.p < worst.p ? p : worst));
  const low = { p: bottom.p, afterOvers: formatOvers(bottom.balls_bowled) };

  const marks = toMarks(predictions);
  let biggest: ChaseSummary["biggest"] = null;
  let largest = -1;
  for (const mark of marks) {
    if (mark.event === "unknown" || isStartOfChase(mark)) continue;
    if (Math.abs(mark.swing) > largest) {
      largest = Math.abs(mark.swing);
      biggest = {
        pp: Math.round(largest * 100),
        direction: mark.swing >= 0 ? "up" : "down",
        event: mark.event,
        runs: mark.runs,
        legal: mark.legal,
        at: formatOvers(predictions[mark.index + 1].balls_bowled),
      };
    }
  }
  return { peak, low, biggest };
}
