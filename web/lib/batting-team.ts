/**
 * Whose win probability is this?
 *
 * `payload.p` is the BATTING side's chance - the model is trained on
 * match_states.batting_team_won - and since migration 20260925000001 each
 * prediction row records which side that was. This is the one place a team
 * name is attached to a probability; every surface goes through it.
 *
 * It never falls back to a guess. The match page used to label p with
 * `matches.team_a`, and Cricsheet lists the side that batted FIRST as team_a,
 * so on 340 of 342 matches the page named the side bowling at the chase:
 * match 8429 read "England to win 1%" while India needed 32 off 1. When the
 * batting side cannot be determined the answer is "batting side", which is
 * true, rather than a name, which would be a coin toss presented as a fact.
 */

import type { WinProbPrediction } from "./prediction";

export interface Side {
  id: number | null;
  name: string | null;
}

/**
 * The batting side's name for this prediction, or null if it is unknown.
 *
 * Looked up against the match's own two sides only: an id that is neither
 * of them (a match id that means different matches on the two databases,
 * say) is unknown, not a name from somewhere else.
 */
export function battingTeamName(
  prediction: Pick<WinProbPrediction, "batting_team_id"> | null,
  sides: readonly Side[]
): string | null {
  const id = prediction?.batting_team_id ?? null;
  if (id === null) return null;
  return sides.find((side) => side.id === id)?.name ?? null;
}

/** What to call the side whose probability is shown. */
export function winSubject(name: string | null): string {
  return name ?? "batting side";
}
