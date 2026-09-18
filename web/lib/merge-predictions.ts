/**
 * Merging the server-rendered history with the Realtime stream
 * (SPEC.md section 7.4, Phase 2 session 5, Decision 4).
 *
 * §7.4 says to fetch current state in a server component and then subscribe
 * for deltas. Between those two things there is a gap: a row inserted after
 * the server's query but before the channel reaches SUBSCRIBED belongs to
 * neither, and is silently lost. On a win-probability curve that is a
 * missing ball, and nothing about the page looks wrong.
 *
 * The page closes it by refetching everything newer than the last row it has
 * once the channel is SUBSCRIBED. That refetch deliberately OVERLAPS with
 * the stream, so rows arrive twice - which is why merging is keyed on
 * prediction_id rather than appended.
 *
 * Pure, so it is testable without a browser or a database. That is the point:
 * the gap is the kind of thing that only shows up under timing nobody
 * reproduces by hand.
 */

import type { WinProbPrediction } from "./prediction";

export function mergePredictions(
  existing: readonly WinProbPrediction[],
  incoming: readonly WinProbPrediction[]
): WinProbPrediction[] {
  const byId = new Map<number, WinProbPrediction>();
  for (const prediction of existing) byId.set(prediction.prediction_id, prediction);
  for (const prediction of incoming) byId.set(prediction.prediction_id, prediction);

  // Ordered by prediction_id, NOT by balls_bowled. Extras legitimately repeat
  // a ball number - match 9339 has 125 deliveries across 111 legal balls - so
  // sorting by ball would make the order of those deliveries arbitrary.
  // prediction_id is the arrival order, which is the order they were bowled.
  return [...byId.values()].sort((a, b) => a.prediction_id - b.prediction_id);
}

/** The highest id seen, so the reconcile query knows where to resume. */
export function highWaterMark(predictions: readonly WinProbPrediction[]): number {
  return predictions.reduce((max, p) => (p.prediction_id > max ? p.prediction_id : max), 0);
}
