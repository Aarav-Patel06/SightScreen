/**
 * The four preview modules on the landing page (UI-PHASE-2 §6).
 *
 * §6 asks that each module be "a real preview rendered from real data, not an
 * icon and a description". So each loader below returns either real rows or
 * null, and the page renders the module's own absence rather than a
 * placeholder — the same rule the rest of the site follows about honest gaps.
 *
 * WHY NOT REUSE THE PAGE LOADERS. `loadMatchIndex` pages the entire
 * prediction set — 52 requests and ~12MB — to draw 341 sparklines.
 * `loadPlayerIndex` pulls 8,575 rows. Both are correct for the pages they
 * serve and absurd for a three-row preview, and the landing page is the one
 * URL a stranger is most likely to open. These are narrow queries with hard
 * limits instead.
 *
 * Every loader here degrades rather than throws, following
 * lib/landing-figures.ts: a preview that cannot load costs a module, not the
 * front door.
 */

import { toMarks, type Mark } from "./ball-strip";
import { parsePrediction } from "./prediction";
import { supabaseServer } from "./supabase-server";

/** Shared with lib/landing-figures.ts's reasoning: an unusable log line is no log line. */
function describe(error: unknown): string {
  if (error === null || error === undefined) return "no reason given";
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "object") {
    const parts = ["message", "code", "details", "hint"]
      .map((key) => (error as Record<string, unknown>)[key])
      .filter((value): value is string => typeof value === "string" && value.length > 0);
    if (parts.length > 0) return parts.join(" · ");
  }
  return String(error);
}

export interface PreviewMatch {
  matchId: number;
  date: string | null;
  teams: string;
  marks: Mark[] | null;
}

export interface PreviewPlayer {
  playerId: number;
  name: string;
  runs: number;
  innings: number;
}

export interface LandingPreviews {
  matches: PreviewMatch[] | null;
  players: PreviewPlayer[] | null;
}

/** How many rows each module shows. §6 says three or four; three fits the column. */
const ROWS = 3;

/**
 * The three most recent Full Member matches, with strips.
 *
 * Full Member rather than most-recent-anything, because the hero above is a
 * Full Member fixture and a preview that disagreed with it would look like a
 * bug. `teams.full_member` is the flag from 20260924000003.
 */
async function loadPreviewMatches(): Promise<PreviewMatch[] | null> {
  const supabase = supabaseServer();

  const teams = await supabase.from("teams").select("team_id, name").eq("full_member", true);
  if (teams.error || !teams.data?.length) {
    console.warn(`[previews] no Full Member teams: ${describe(teams.error)}`);
    return null;
  }
  const names = new Map(teams.data.map((r) => [r.team_id, r.name]));
  const ids = [...names.keys()];

  const matches = await supabase
    .from("matches")
    .select("match_id, start_time, team_a, team_b")
    .in("team_a", ids)
    .in("team_b", ids)
    .order("start_time", { ascending: false })
    .limit(ROWS * 3); // a margin, so rows without strips do not empty the module

  if (matches.error || !matches.data?.length) {
    console.warn(`[previews] no recent fixtures: ${describe(matches.error)}`);
    return null;
  }

  const predictions = await supabase
    .from("predictions")
    .select("prediction_id, created_at, model_version, payload, match_id")
    .in("match_id", matches.data.map((r) => r.match_id))
    .eq("prediction_type", "win_prob")
    .not("innings", "is", null)
    .order("prediction_id", { ascending: true });

  const byMatch = new Map<number, ReturnType<typeof parsePrediction>[]>();
  if (predictions.error) {
    console.warn(`[previews] strips unavailable: ${describe(predictions.error)}`);
  } else {
    for (const row of predictions.data ?? []) {
      const parsed = parsePrediction(row as never);
      if (parsed === null) continue;
      const id = (row as { match_id: number }).match_id;
      byMatch.set(id, [...(byMatch.get(id) ?? []), parsed]);
    }
  }

  const out: PreviewMatch[] = [];
  for (const row of matches.data) {
    if (out.length === ROWS) break;
    const teamA = row.team_a === null ? undefined : names.get(row.team_a);
    const teamB = row.team_b === null ? undefined : names.get(row.team_b);
    if (!teamA || !teamB) continue; // never render a side it cannot name
    const rows = byMatch.get(row.match_id) ?? [];
    out.push({
      matchId: row.match_id,
      date: row.start_time ? row.start_time.slice(0, 10) : null,
      teams: `${teamA} v ${teamB}`,
      marks: rows.length > 1 ? toMarks(rows as never) : null,
    });
  }
  return out.length > 0 ? out : null;
}

/**
 * The top three players by career runs.
 *
 * `player_career_summary` holds one row per player per format, so runs are
 * summed across formats on the server. PostgREST cannot GROUP BY, so this
 * takes the top rows by `bat_runs` from `player_index`, which already carries
 * the cross-format total.
 */
async function loadPreviewPlayers(): Promise<PreviewPlayer[] | null> {
  const supabase = supabaseServer();
  const { data, error } = await supabase
    .from("player_index")
    .select("player_id, canonical_name, bat_runs, bat_innings")
    .order("bat_runs", { ascending: false, nullsFirst: false })
    .limit(ROWS);

  if (error || !data?.length) {
    console.warn(`[previews] no players: ${describe(error)}`);
    return null;
  }
  return data.map((row) => ({
    playerId: row.player_id,
    name: row.canonical_name,
    runs: row.bat_runs ?? 0,
    innings: row.bat_innings ?? 0,
  }));
}

export async function loadLandingPreviews(): Promise<LandingPreviews> {
  const [matches, players] = await Promise.all([loadPreviewMatches(), loadPreviewPlayers()]);
  return { matches, players };
}
