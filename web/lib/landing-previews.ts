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
import { loadModelVersions } from "./load-model-versions";
import { oneSource, oneVersion } from "./model-version";
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

  // The ROWS matches to show, chosen before any strip is read.
  const shown: { matchId: number; date: string | null; teams: string }[] = [];
  for (const row of matches.data) {
    if (shown.length === ROWS) break;
    const teamA = row.team_a === null ? undefined : names.get(row.team_a);
    const teamB = row.team_b === null ? undefined : names.get(row.team_b);
    if (!teamA || !teamB) continue; // never render a side it cannot name
    shown.push({
      matchId: row.match_id,
      date: row.start_time ? row.start_time.slice(0, 10) : null,
      teams: `${teamA} v ${teamB}`,
    });
  }

  // ONE REQUEST PER STRIP. These were one fetch of every prediction for all
  // candidates, oldest first, and PostgREST returns at most 1,000 rows per
  // request - so once the candidates passed that, the NEWEST previews, the
  // ones shown first, silently lost their strips. One match is at most ~305
  // keyed rows, which a single request always holds.
  const [versions, ...strips] = await Promise.all([
    loadModelVersions(),
    ...shown.map((match) =>
      supabase
        .from("predictions")
        .select("prediction_id, created_at, model_version, payload, match_id, source")
        .eq("match_id", match.matchId)
        .eq("prediction_type", "win_prob")
        .not("innings", "is", null)
        .order("prediction_id", { ascending: true })
    ),
  ]);

  const out: PreviewMatch[] = shown.map((match, i) => {
    const { data, error } = strips[i];
    if (error) console.warn(`[previews] strip unavailable for ${match.matchId}: ${describe(error)}`);
    // One model version per match, then one source (lib/model-version.ts).
    const parsed = oneSource(oneVersion(data ?? [], versions))
      .map((row) => parsePrediction(row as never))
      .filter((p) => p !== null);
    return { ...match, marks: parsed.length > 1 ? toMarks(parsed) : null };
  });
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
