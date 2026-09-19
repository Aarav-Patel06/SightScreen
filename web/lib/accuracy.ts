/**
 * The calibration report's shape, narrowed (SPEC.md §8.1, §8.5).
 *
 * `calibration_runs.report` is JSONB, so the generated types call it `Json`
 * and the application's knowledge of its interior lives here - the same
 * split `lib/prediction.ts` makes for `predictions.payload`. Written by
 * `api/src/eval/calibration_monitor.py`; keep the two in step, because a
 * mismatch is silent.
 *
 * Everything here is pure, so the honesty rules the page depends on can be
 * tested without rendering anything. That is the `merge-predictions.ts`
 * precedent: put the logic somewhere a test can reach it.
 */

/** Which population a number describes. The distinction the page is built on. */
export type Population = "backfill" | "live";

export interface Decile {
  bin_low: number;
  bin_high: number;
  n: number;
  n_matches: number;
  mean_predicted: number | null;
  observed_rate: number | null;
  ci_low: number | null;
  ci_high: number | null;
  contains_predicted: boolean | null;
}

export interface PhaseBucket {
  n: number;
  n_matches: number;
  brier: number;
  ci_low: number;
  ci_high: number;
}

export interface BaselineComparison {
  baseline_brier: number;
  model_brier: number;
  improvement: number;
  ci_low: number;
  ci_high: number;
  n_matches: number;
  model_is_better: boolean;
}

export interface Miss {
  match_id: number;
  over: string;
  predicted: number;
  actual_won: boolean;
  brier: number;
  phase: string;
  competition: string;
  match_date: string;
}

export interface PopulationReport {
  n: number;
  n_matches: number;
  unresolved: { predictions: number; matches: number };
  /** Absent when nothing in this population has been scored yet. */
  brier?: number;
  brier_ci_low?: number;
  brier_ci_high?: number;
  log_loss?: number;
  n_deciles_populated?: number;
  n_deciles_failed?: number;
  reliability?: Decile[];
  by_phase?: Record<string, PhaseBucket>;
  vs_baselines?: Record<string, BaselineComparison | { unavailable: string }>;
  biggest_misses?: Miss[];
  refit?: { ran: boolean; winner: string; reason: string };
  reason_unscored?: string;
}

export interface CalibrationReport {
  model_version: string;
  populations: Record<Population, PopulationReport>;
  n_resamples: number;
  floors: { window_matches: number; select_matches: number };
  finished_at: string;
}

/**
 * Narrow a report row, or return null.
 *
 * Null rather than throwing, for the same reason `parsePrediction` does: a
 * report written by an older monitor must not blank the page. The caller
 * renders "no run yet" instead.
 */
export function parseReport(raw: unknown): CalibrationReport | null {
  if (raw === null || typeof raw !== "object") return null;
  const report = raw as Partial<CalibrationReport>;
  if (typeof report.model_version !== "string") return null;
  if (report.populations === null || typeof report.populations !== "object") return null;
  return report as CalibrationReport;
}

/** Populated deciles only. An empty bin has no observed rate to plot. */
export function populatedDeciles(deciles: Decile[] | undefined): Decile[] {
  return (deciles ?? []).filter(
    (d): d is Decile => d.n > 0 && d.mean_predicted !== null && d.observed_rate !== null
  );
}

/**
 * How thin is this decile really?
 *
 * §9.3: "n counts balls, and balls within a match are correlated, so 200
 * balls may be 5 effective observations." The page shows both numbers side
 * by side, and this is what decides whether to grey the row - §12.2's "grey
 * out small samples rather than displaying a misleading precise number".
 * The threshold is on MATCHES, because that is the effective sample.
 */
export const THIN_MATCH_COUNT = 30;

export function isThin(decile: Pick<Decile, "n_matches">): boolean {
  return decile.n_matches < THIN_MATCH_COUNT;
}

/** Whole percent. §12.2 forbids implying precision the model does not have. */
export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/** Four decimals is the convention every Brier in this project is quoted at. */
export function score(value: number): string {
  return value.toFixed(4);
}

/**
 * A Brier with its own interval, never a bare point estimate (§12.2).
 */
export function withInterval(point: number, low: number, high: number): string {
  return `${score(point)}  [${score(low)}, ${score(high)}]`;
}

/**
 * The one-line verdict for a baseline comparison.
 *
 * Deliberately refuses to say "better" when the interval includes zero. The
 * paired match-clustered CI is the test §9.3 mandates; a point estimate on
 * the right side of zero is not a result.
 */
export function baselineVerdict(
  comparison: BaselineComparison | { unavailable: string }
): string {
  if ("unavailable" in comparison) return comparison.unavailable;
  const delta = score(Math.abs(comparison.improvement));
  const interval = `[${score(comparison.ci_low)}, ${score(comparison.ci_high)}]`;
  if (comparison.model_is_better) {
    return `model is better by ${delta} Brier, 95% CI ${interval} over ${comparison.n_matches} matches`;
  }
  return `no significant difference - the 95% CI ${interval} includes zero over ${comparison.n_matches} matches`;
}

/**
 * Chart rows for the reliability diagram.
 *
 * Recharts cannot read CSS variables, and its ErrorBar wants offsets from
 * the point rather than absolute bounds - so both conversions happen here,
 * where a test can check them, instead of inside the JSX.
 */
export function reliabilityPoints(deciles: Decile[] | undefined) {
  return populatedDeciles(deciles).map((d) => ({
    predicted: d.mean_predicted as number,
    observed: d.observed_rate as number,
    error: [
      (d.observed_rate as number) - (d.ci_low as number),
      (d.ci_high as number) - (d.observed_rate as number),
    ] as [number, number],
    n: d.n,
    matches: d.n_matches,
    ok: d.contains_predicted === true,
    thin: isThin(d),
    label: `${percent(d.bin_low)}-${percent(d.bin_high)}`,
  }));
}
