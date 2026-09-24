/**
 * The ball strip's data model (UI-PHASE.md §1.2).
 *
 * An innings renders as one mark per delivery, left to right. Each mark
 * encodes the win-probability swing that ball caused, which direction it
 * moved, and what happened. This module turns prediction rows into marks and
 * decides which of those encodings a given width can actually carry. It is
 * pure and has no JSX, for the same reason merge-predictions.ts is: the
 * interesting failures here are off-by-ones and ordering, and those are worth
 * testing without a browser.
 *
 * WHY THE EVENT IS DERIVED RATHER THAN READ. §1.2 describes fill as "solid for
 * a scoring shot, hollow for a dot, a distinct notch for a wicket". Those are
 * columns on `deliveries` - but `deliveries` is empty on Supabase by design
 * (SPEC.md §2.1; the free tier cannot hold 3.78M rows) and the browser only
 * ever reads Supabase. So ball events are not available to the client at all,
 * and must be reconstructed from consecutive prediction payloads. Three things
 * make that reconstruction easy to get silently wrong, and all three are
 * handled below.
 */

import type { Phase, WinProbPrediction } from "./prediction";

/** What happened on a ball, reconstructed from the state before the next one. */
export type BallEvent = "dot" | "score" | "wicket" | "unknown";

export interface Mark {
  /** Position in the strip, 0-based. Not a ball number - extras repeat those. */
  index: number;
  /** Change in win probability this ball caused, signed toward the batting side. */
  swing: number;
  event: BallEvent;
  /** Win probability BEFORE this ball, which is what the model was asked. */
  p: number;
  phase: Phase;
  ballsBowled: number;
  score: number;
  wickets: number;
  runsRequired: number;
  /** Carried so a tooltip can address the underlying row. */
  predictionId: number;
}

/**
 * Turn an innings' predictions into marks.
 *
 * Expects rows already ordered by prediction_id - which is what
 * mergePredictions and every loader in the app produce - and does not re-sort,
 * so that a caller who has broken that ordering sees a wrong strip rather than
 * having it quietly corrected here.
 *
 * The three traps:
 *
 * 1. OFF BY ONE. `match_states` holds the state BEFORE its delivery (migration
 *    20260826180003), so prediction[i] describes the situation the bowler ran
 *    in to. The ball's own effect is therefore visible only in prediction
 *    [i+1]: swing[i] = p[i+1] - p[i], and the event comes from comparing the
 *    two score/wicket counts. Attributing prediction[i]'s own score to mark[i]
 *    would shift the whole strip one ball right, which looks entirely
 *    plausible and is wrong everywhere.
 *
 * 2. EXTRAS. `balls_bowled` does not advance on a wide or a no-ball, so it
 *    repeats - the fixture match has five such repeats across 125 deliveries.
 *    Marks are therefore indexed by position, never by ball number, and the
 *    strip is as long as the delivery count, not the legal-ball count.
 *
 * 3. THE LAST BALL IS UNKNOWABLE. Nothing follows the final prediction, so its
 *    delivery's outcome is not in the data - not late, not missing, simply
 *    never recorded. It is marked `unknown` and rendered outline-only with a
 *    stated reason rather than being dropped or guessed. In the fixture that
 *    final ball is the winning run, so the honest gap lands on the most
 *    dramatic mark in the innings. That is the correct outcome: the strip
 *    declines to state something it does not know, in the one place a viewer
 *    is most likely to notice.
 */
export function toMarks(predictions: readonly WinProbPrediction[]): Mark[] {
  return predictions.map((prediction, index) => {
    const next = predictions[index + 1];

    return {
      index,
      predictionId: prediction.prediction_id,
      p: prediction.p,
      phase: prediction.phase,
      ballsBowled: prediction.balls_bowled,
      score: prediction.score,
      wickets: prediction.wickets,
      runsRequired: prediction.runs_required,
      swing: next ? next.p - prediction.p : 0,
      event: next ? eventBetween(prediction, next) : "unknown",
    };
  });
}

/**
 * What happened on the ball that took the innings from `before` to `after`.
 *
 * A wicket is detected from the wicket COUNT rising, not from a dismissal
 * type - `deliveries.wicket_type` is unavailable here, and it would be the
 * wrong test anyway, since it includes `retired hurt` and `retired not out`,
 * which are not dismissals that cost a wicket.
 *
 * Wicket wins over runs when both happen, because a run out that concedes a
 * single is a wicket ball in every sense a reader cares about.
 */
function eventBetween(before: WinProbPrediction, after: WinProbPrediction): BallEvent {
  if (after.wickets > before.wickets) return "wicket";
  return after.score > before.score ? "score" : "dot";
}

/**
 * How much horizontal room each mark gets, in CSS pixels.
 *
 * Kept separate from tierFor so a caller can show the measured number - the
 * /design page does, because "2.52px per mark" is the single fact that
 * explains every tier decision below it.
 */
export function pitchFor(widthPx: number, markCount: number): number {
  return markCount > 0 ? widthPx / markCount : 0;
}

/**
 * Which encodings a pitch can carry.
 *
 * UI-PHASE.md §1.2 asks for one component at four sizes carrying four
 * encodings each. Measured against the corpus, that is not possible. A T20
 * second innings runs to 121 legal balls at maximum, and 340px less the 16px
 * page gutters leaves 308px - a 2.55px pitch. The encodings do not all
 * survive that:
 *
 *   - Height and direction do. Vertical resolution is untouched by horizontal
 *     crowding, and these two carry the strip's actual narrative.
 *   - Fill does not. Hollow is a 1px stroke with no fill, so it needs 1px of
 *     stroke on each side plus at least 1px of interior to read as hollow at
 *     all: a 3px bar, plus a 1px gap to stay discrete, is a 4px floor. At
 *     2.55px the two strokes overlap and every hollow mark renders solid -
 *     the encoding is not merely degraded, it silently inverts its meaning.
 *   - Hatch does not, and by a wider margin. A 10px-pitch diagonal inside a
 *     2.5px shape shows one stripe or none depending where the mark falls,
 *     which is noise, not texture. Hatch is a large-area treatment; see
 *     components/textures.tsx.
 *
 * So the tier is a declaration of what is legible, and the component derives
 * it from its own measured width rather than taking it as a prop - a size
 * prop can be passed wrongly, a measurement cannot.
 *
 * `area` exists because below roughly 2px a mark is sub-pixel: the 120px
 * sparkline in §1.2 would give 121 marks 0.99px each. At that point discrete
 * marks are a lie about the rendering, and the honest object is a filled
 * area - a shape, not a strip.
 */
export type Tier = "full" | "reduced" | "minimal" | "area";

export const HOVER_FLOOR_PX = 6;
export const FILL_FLOOR_PX = 4;
export const DISCRETE_FLOOR_PX = 2;

/**
 * The texture floors live here too, rather than in components/textures.tsx
 * where they are used, and that is not an arbitrary choice.
 *
 * textures.tsx is a "use client" module. A Server Component that imports a
 * plain value from one does not get the value - it gets an opaque client
 * reference. React resolves that reference when rendering it as a child, so
 * `{HATCH_MIN_PX}` prints 24 and looks fine, but any expression evaluated on
 * the server sees an object: `12 < HATCH_MIN_PX` becomes `12 < NaN`, which is
 * false, with no error and no warning. That cost an afternoon on /design,
 * where the caption marking the sizes below the floor silently vanished while
 * the sentence quoting the same constant rendered correctly two paragraphs
 * above.
 *
 * So every shared constant in this design system lives in a module with no
 * "use client" directive. Put one in a client module and the failure will be
 * invisible.
 */
export const HATCH_PITCH_PX = 10;
export const HATCH_MIN_PX = 24;
export const HOLLOW_MIN_PX = 4;

export function tierFor(pitchPx: number): Tier {
  if (pitchPx >= HOVER_FLOOR_PX) return "full";
  if (pitchPx >= FILL_FLOOR_PX) return "reduced";
  if (pitchPx >= DISCRETE_FLOOR_PX) return "minimal";
  return "area";
}

/** Does this tier draw fill, i.e. distinguish a dot from a scoring shot? */
export function tierHasFill(tier: Tier): boolean {
  return tier === "full" || tier === "reduced";
}

/**
 * Can a viewer point at one specific mark?
 *
 * Only at `full`. §1.2 says "hovering a mark reveals that delivery", but the
 * phase is mobile-first and touch has no hover - and a 2.55px target sits
 * against a 44px minimum touch target, which is off by a factor of seventeen.
 * Every tier below `full` uses a scrubber across the whole strip instead,
 * resolving to the nearest mark. That is a different interaction, not a
 * smaller one.
 */
export function tierHasPerMarkHit(tier: Tier): boolean {
  return tier === "full";
}

/**
 * The largest absolute swing in the innings, used to scale mark heights.
 *
 * Scaled per innings rather than to a fixed domain because the median swing is
 * about 2.5pp against a maximum near 35pp: a fixed domain would render the
 * whole innings as a flat line with two spikes, losing the texture of the
 * chase. The cost is that heights are not comparable between matches, which is
 * why the strip always renders its own scale label.
 */
export function peakSwing(marks: readonly Mark[]): number {
  return marks.reduce((max, mark) => Math.max(max, Math.abs(mark.swing)), 0);
}

/**
 * A text alternative describing the innings (UI-PHASE.md §5, session 5).
 *
 * Written now rather than in the accessibility session because the strip is
 * meaningless to a screen reader without it, and a component that ships
 * inaccessible gets retrofitted rather than fixed.
 */
export function describeStrip(marks: readonly Mark[]): string {
  if (marks.length === 0) return "No deliveries recorded.";

  const wickets = marks.filter((mark) => mark.event === "wicket").length;
  const dots = marks.filter((mark) => mark.event === "dot").length;
  const first = marks[0];
  const last = marks[marks.length - 1];
  const peak = Math.round(peakSwing(marks) * 100);

  return (
    `Win probability across ${marks.length} deliveries, ` +
    `starting at ${asPercent(first.p)} and ending at ${asPercent(last.p)}. ` +
    `${wickets} wicket${wickets === 1 ? "" : "s"}, ${dots} dot balls. ` +
    `The largest single-ball swing was ${peak} percentage points. ` +
    `The final delivery's outcome is not recorded, because no prediction ` +
    `follows it.`
  );
}

function asPercent(p: number): string {
  return `${Math.round(p * 100)}%`;
}
