/**
 * The player pages' data (UI-PHASE.md §4.4).
 *
 * DESCRIPTIVE RECORD ONLY. Everything here is what a player has done. None
 * of it is an estimate of what they can do now - that is `player_state`,
 * empty until Phase 5 - and the two are different questions. The page says
 * so where a form curve would go, which is the same refusal
 * `get_player_form` makes in the agent.
 *
 * Read from `player_index` and `player_career_summary`, built by
 * `features.player_summary` and synced with a verified content hash. They
 * exist because the aggregates are computed from `deliveries`, which is
 * empty on Supabase by §2.1's design - so the page cannot compute them at
 * request time and nothing else could supply them.
 */

import { supabaseServer } from "./supabase-server";

/**
 * Below this many innings, a number is shown hollow rather than as a figure.
 *
 * §12.2: "Grey out small samples rather than displaying a misleading precise
 * number", and §1.3 renders that as hollow rather than as a lighter grey -
 * a lighter grey still reads as a number you can use. Twenty innings is the
 * threshold §5's acceptance names.
 */
export const THIN_INNINGS = 20;

export interface PlayerRow {
  playerId: number;
  name: string;
  /** The resolver's normalize_name output, precomputed. See searchPlayers. */
  normalized: string;
  /** The resolver's surname_key output, precomputed. */
  surname: string;
  matches: number;
  formats: string[];
  batInnings: number;
  batRuns: number;
  bowlInnings: number;
  bowlWickets: number;
}

export interface CareerLine {
  format: string;
  phase: string;
  batInnings: number;
  batBalls: number;
  batRuns: number;
  batOuts: number;
  batFours: number;
  batSixes: number;
  bowlBalls: number;
  bowlRuns: number;
  bowlWickets: number;
}

export interface PlayerDetail {
  playerId: number;
  name: string;
  matches: number;
  lines: CareerLine[];
}

function describe(error: unknown): string {
  if (error === null || error === undefined) return "no reason given";
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "object") {
    const parts = ["message", "code", "details", "hint"]
      .map((key) => (error as Record<string, unknown>)[key])
      .filter((value): value is string => typeof value === "string" && value.length > 0);
    if (parts.length > 0) return parts.join(" · ");
    try {
      return JSON.stringify(error);
    } catch {
      return "unserialisable error object";
    }
  }
  return String(error) || "no reason given";
}

/**
 * Every player with a record, for the index.
 *
 * 8,575 rows - the players who have batted or bowled at least one delivery.
 * The other ~9,900 of the corpus's 18,468 appear only because a team sheet
 * named them, and an index where more than half the rows go nowhere is the
 * problem /matches already had to solve.
 *
 * All of them at once, deliberately. 8,575 rows of six small columns is
 * about 600KB, fetched once per revalidate rather than per keystroke, and it
 * makes search instant and typo-tolerant on the client. A server round trip
 * per keystroke would be slower and would make the page dynamic.
 */
export async function loadPlayerIndex(): Promise<PlayerRow[]> {
  const PAGE = 1000;
  const supabase = supabaseServer();

  try {
    const { count, error: countError } = await supabase
      .from("player_index")
      .select("*", { count: "exact", head: true });

    if (countError || count === null) {
      console.warn(`[players] index unavailable: ${describe(countError)}`);
      return [];
    }

    const pages = Math.ceil(count / PAGE);
    const results = await Promise.all(
      Array.from({ length: pages }, (_, i) =>
        supabase
          .from("player_index")
          // One string literal, not a concatenation: supabase-js infers the
          // row type from the literal, and a `+` turns it into `string`,
          // which degrades every column to GenericStringError.
          .select(
            "player_id, canonical_name, normalized_name, surname_key, matches, formats, bat_innings, bat_runs, bowl_innings, bowl_wickets"
          )
          .order("player_id", { ascending: true })
          .range(i * PAGE, i * PAGE + PAGE - 1)
      )
    );

    const rows: PlayerRow[] = [];
    for (const { data, error } of results) {
      if (error || !data) {
        console.warn(`[players] index failed mid-page: ${describe(error)}`);
        return [];
      }
      for (const row of data) {
        rows.push({
          playerId: row.player_id,
          name: row.canonical_name,
          normalized: row.normalized_name,
          surname: row.surname_key,
          matches: row.matches,
          formats: row.formats ? row.formats.split(",") : [],
          batInnings: row.bat_innings,
          batRuns: row.bat_runs,
          bowlInnings: row.bowl_innings,
          bowlWickets: row.bowl_wickets,
        });
      }
    }
    return rows;
  } catch (error) {
    console.warn(`[players] index threw: ${describe(error)}`);
    return [];
  }
}

export async function loadPlayer(playerId: number): Promise<PlayerDetail | null> {
  try {
    const supabase = supabaseServer();
    const [indexResult, careerResult] = await Promise.all([
      supabase
        .from("player_index")
        .select("player_id, canonical_name, matches")
        .eq("player_id", playerId)
        .maybeSingle(),
      supabase
        .from("player_career_summary")
        .select("*")
        .eq("player_id", playerId)
        .order("format", { ascending: true }),
    ]);

    if (indexResult.error || !indexResult.data) return null;

    const lines: CareerLine[] = (careerResult.data ?? []).map((row) => ({
      format: row.format,
      phase: row.phase,
      batInnings: row.bat_innings,
      batBalls: row.bat_balls,
      batRuns: row.bat_runs,
      batOuts: row.bat_outs,
      batFours: row.bat_fours,
      batSixes: row.bat_sixes,
      bowlBalls: row.bowl_balls,
      bowlRuns: row.bowl_runs,
      bowlWickets: row.bowl_wickets,
    }));

    return {
      playerId: indexResult.data.player_id,
      name: indexResult.data.canonical_name,
      matches: indexResult.data.matches,
      lines,
    };
  } catch (error) {
    console.warn(`[players] player ${playerId} threw: ${describe(error)}`);
    return null;
  }
}

// --- derived figures -----------------------------------------------------
//
// Each returns null rather than a number when the denominator is zero. A
// batting average with no dismissals is not infinity and not the run total;
// it is undefined, and the page renders that as "not out in N innings"
// rather than inventing a figure.

export function battingAverage(line: CareerLine): number | null {
  return line.batOuts > 0 ? line.batRuns / line.batOuts : null;
}

export function strikeRate(line: CareerLine): number | null {
  return line.batBalls > 0 ? (100 * line.batRuns) / line.batBalls : null;
}

export function economy(line: CareerLine): number | null {
  return line.bowlBalls > 0 ? (6 * line.bowlRuns) / line.bowlBalls : null;
}

export function bowlingAverage(line: CareerLine): number | null {
  return line.bowlWickets > 0 ? line.bowlRuns / line.bowlWickets : null;
}

/**
 * Is this line too thin to quote a rate from?
 *
 * Judged on INNINGS, not balls. A player with 300 balls across 4 innings has
 * a strike rate you can compute and an average you cannot trust, and it is
 * the average people read. lib/accuracy.ts makes the same choice for the
 * same reason - `isThin` there is on matches, not rows.
 */
export function isThin(line: CareerLine): boolean {
  return line.batInnings < THIN_INNINGS;
}


/**
 * Search, using the resolver's canonicalisation rather than a second one.
 *
 * §4.4's acceptance: "Kohli" and "Virat Kohli" must both find the same
 * player, and the instruction is to reuse the resolver's canonical-form
 * generation rather than writing a second matcher. The resolver is Python
 * and this is TypeScript, so what crosses is its OUTPUT:
 * `ingest/entity_resolution.py`'s `normalize_name` and `surname_key` are
 * applied during the rebuild and stored on `player_index`. There is one
 * implementation of what a name means, and it is not this file.
 *
 * What is left here is only the matching policy, which the resolver does not
 * own: a prefix match on the normalized name, the surname key, or any single
 * word of the name. The corpus stores "V Kohli", so "virat kohli" cannot
 * match the full string and has to reach it through the surname.
 *
 * Ranked so that an exact name beats a surname beats a word, and within a
 * tier by career runs - because someone typing "Kohli" almost certainly
 * means the one with 28,134 of them, not the one with 59.
 */
export function searchPlayers(rows: readonly PlayerRow[], query: string): PlayerRow[] {
  const q = query.trim().toLowerCase();
  if (q.length === 0) return [];

  const scored: Array<{ row: PlayerRow; rank: number }> = [];
  for (const row of rows) {
    let rank = -1;
    if (row.normalized === q) rank = 0;
    else if (row.normalized.startsWith(q)) rank = 1;
    else if (row.surname === q) rank = 2;
    else if (row.surname.startsWith(q)) rank = 3;
    else if (row.normalized.split(" ").some((word) => word.startsWith(q))) rank = 4;
    // The "Virat Kohli" case: a multi-word query whose own surname key
    // matches this row's. The corpus spells the name "V Kohli", so nothing
    // above can reach it.
    else if (q.includes(" ") && row.surname === q.split(" ").filter((w) => w.length > 1).pop())
      rank = 5;

    if (rank >= 0) scored.push({ row, rank });
  }

  scored.sort((a, b) => a.rank - b.rank || b.row.batRuns - a.row.batRuns);
  return scored.map((s) => s.row);
}
