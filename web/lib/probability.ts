/**
 * A win probability as it may be shown - the one formatter every surface uses.
 *
 * Whole percent (§12.2: no precision the model does not have), and never the
 * ends. The model never outputs exactly 0 or 1, and "100%" on a chase that
 * still needs a run claims a certainty no model has; so anything that would
 * round to 100% reads ">99%", and anything that would round to 0% reads "<1%".
 */
export function formatProbability(p: number): string {
  const whole = Math.round(p * 100);
  if (whole >= 100) return ">99%";
  if (whole <= 0) return "<1%";
  return `${whole}%`;
}

/** The same bounds, for a sentence read aloud: "over 99 percent". */
export function spokenProbability(p: number): string {
  const whole = Math.round(p * 100);
  if (whole >= 100) return "over 99 percent";
  if (whole <= 0) return "under 1 percent";
  return `${whole} percent`;
}
