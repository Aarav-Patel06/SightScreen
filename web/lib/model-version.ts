/**
 * One model version per match (SPEC.md section 12.2).
 *
 * `predictions` is keyed by (match, model_version, ball), so a match can hold
 * a full chase from two models: the served one and a shadow, or an old model
 * and its regenerated backfill. Drawn together they interleave into one
 * curve that no model produced. Every page therefore shows exactly one
 * version per match:
 *
 *   the ACTIVE version, if the match has rows from it;
 *   otherwise the NEWEST version (latest trained_at) it has rows from;
 *   and if model_versions could not be read, the version of the newest row.
 *
 * `model_versions` has no anon policy, so this is decided on the server
 * (lib/load-model-versions.ts reads it); the live match page is handed the
 * answer rather than working it out. This module is pure and has no server
 * imports, so that client component can use `isShownVersion`.
 */

export interface ModelVersionInfo {
  model_version: string;
  is_active: boolean;
  trained_at: string;
}

interface VersionedRow {
  model_version: string;
  prediction_id: number;
}

export function activeVersion(versions: readonly ModelVersionInfo[]): string | null {
  return versions.find((v) => v.is_active)?.model_version ?? null;
}

/** The one version to show for a single match's rows, or null if it has none. */
export function chooseVersion(rows: readonly VersionedRow[], versions: readonly ModelVersionInfo[]): string | null {
  const newestRow = new Map<string, number>();
  for (const row of rows) {
    newestRow.set(row.model_version, Math.max(newestRow.get(row.model_version) ?? 0, row.prediction_id));
  }
  if (newestRow.size === 0) return null;

  const info = new Map(versions.map((v) => [v.model_version, v]));
  const rank = (version: string): [number, string, number] => [
    info.get(version)?.is_active ? 1 : 0,
    info.get(version)?.trained_at ?? "",
    newestRow.get(version) ?? 0,
  ];
  return [...newestRow.keys()].sort((a, b) => {
    const [ra, rb] = [rank(a), rank(b)];
    if (ra[0] !== rb[0]) return rb[0] - ra[0];
    if (ra[1] !== rb[1]) return ra[1] < rb[1] ? 1 : -1;
    return rb[2] - ra[2];
  })[0];
}

/** A single match's rows, reduced to its one version. */
export function oneVersion<T extends VersionedRow>(rows: readonly T[], versions: readonly ModelVersionInfo[]): T[] {
  const chosen = chooseVersion(rows, versions);
  return rows.filter((row) => row.model_version === chosen);
}

/** Rows from many matches, each reduced to its own one version. Order is kept. */
export function oneVersionPerMatch<T extends VersionedRow & { match_id: number }>(
  rows: readonly T[],
  versions: readonly ModelVersionInfo[]
): T[] {
  const byMatch = new Map<number, T[]>();
  for (const row of rows) {
    const list = byMatch.get(row.match_id);
    if (list) list.push(row);
    else byMatch.set(row.match_id, [row]);
  }
  const chosen = new Map([...byMatch].map(([matchId, list]) => [matchId, chooseVersion(list, versions)]));
  return rows.filter((row) => row.model_version === chosen.get(row.match_id));
}

interface SourcedRow {
  source?: string | null;
}

/**
 * One source within a match's version (session 3). Once the daily Cricsheet
 * job merges a match the live worker predicted, the match holds the live
 * chase and the complete Cricsheet version under the SAME model version. The
 * Cricsheet version (source 'backfill') is shown: complete and exact. The
 * live rows are the live cohort for /accuracy and are never drawn beside it.
 * Apply after `oneVersion`.
 */
export function oneSource<T extends SourcedRow>(rows: readonly T[]): T[] {
  const backfill = rows.filter((row) => row.source === "backfill");
  return backfill.length > 0 ? backfill : [...rows];
}

/** `oneSource` for rows from many matches, each decided on its own. Order is kept. */
export function oneSourcePerMatch<T extends SourcedRow & { match_id: number }>(rows: readonly T[]): T[] {
  const hasBackfill = new Set(rows.filter((row) => row.source === "backfill").map((row) => row.match_id));
  return rows.filter((row) => !hasBackfill.has(row.match_id) || row.source === "backfill");
}

/**
 * Does a row belong on a page showing `shown`? The live match page's stream
 * and resync use this to keep to the version the server chose; `null` means
 * the server had no version at all, so the first rows to arrive decide.
 */
export function isShownVersion(rowVersion: string, shown: string | null): boolean {
  return shown === null || rowVersion === shown;
}
