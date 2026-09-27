/**
 * A completed match's result, in words (migration 20260927000001's columns).
 *
 * The real result - "England won by 27 runs", "Match tied · Punjab won the
 * super over" - and never the final chase state: the last prediction is the
 * state BEFORE the last ball, so "needed 32 off 1" describes a moment that
 * never finished, not what happened.
 *
 * Degrades one way only. A margin that is not recorded is left out ("England
 * won"), never guessed; a result that is not recorded is null.
 */

export interface MatchOutcome {
  /** matches.result_method: 'normal' | 'dls' | 'tie' | 'no_result'. */
  resultMethod: string | null;
  winner: string | null;
  winByRuns: number | null;
  winByWickets: number | null;
  /** Cricsheet's outcome.method, verbatim: 'D/L', 'VJD', 'Awarded', ... */
  outcomeMethod: string | null;
  tieWinner: string | null;
  tieDecidedBy: string | null;
}

/** Cricsheet writes the Duckworth-Lewis(-Stern) method as 'D/L'. */
const METHOD_LABEL: Record<string, string> = { "D/L": "DLS", VJD: "VJD" };

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function resultText(outcome: MatchOutcome): string | null {
  const method = outcome.outcomeMethod ? METHOD_LABEL[outcome.outcomeMethod] : undefined;
  const suffix = method ? ` (${method})` : "";

  if (outcome.resultMethod === "no_result") return "No result";

  if (outcome.resultMethod === "tie") {
    if (outcome.tieWinner && outcome.tieDecidedBy === "super_over") {
      return `Match tied${suffix} · ${outcome.tieWinner} won the super over`;
    }
    if (outcome.tieWinner && outcome.tieDecidedBy === "bowl_out") {
      return `Match tied${suffix} · ${outcome.tieWinner} won the bowl-out`;
    }
    return `Match tied${suffix}`;
  }

  const winner = outcome.winner;
  if (winner === null) return null;
  if (outcome.outcomeMethod === "Awarded") return `${winner} were awarded the match`;
  if (outcome.outcomeMethod === "Lost fewer wickets") return `${winner} won (lost fewer wickets)`;
  if (outcome.winByRuns !== null) return `${winner} won by ${plural(outcome.winByRuns, "run")}${suffix}`;
  if (outcome.winByWickets !== null) {
    return `${winner} won by ${plural(outcome.winByWickets, "wicket")}${suffix}`;
  }
  return `${winner} won${suffix}`;
}
