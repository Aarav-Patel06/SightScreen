/**
 * The landing page's hero match (UI-PHASE-2.md §2.4).
 *
 * §2.4 replaces the committed hero fixture with a query, and is explicit that
 * the reason the fixture existed still holds: the hero must never fail to
 * render. Free-tier Supabase pauses after ~7 days idle and this project has
 * recorded pauses of 12 minutes, 12 minutes and 3.3 hours. So the fixture
 * stays in the repo as the floor, and this module returns it — flagged — when
 * the query fails or finds nothing.
 *
 * WHY THREE ROUND TRIPS AND NO JOIN. The obvious query is "most recent match
 * where both teams are Full Members and predictions exist", which is two
 * joins. PostgREST can embed a foreign table and filter on it, but only via
 * the FK constraint name, and it cannot express "has at least one related
 * row" without pulling the related rows. Naming FK constraints in a query
 * string makes the page break on a migration that renames one, silently, at
 * request time. So instead:
 *
 *   1. the eleven Full Member team ids          (teams.full_member)
 *   2. recent matches with both sides in that set
 *   3. which of those candidates actually have keyed predictions
 *
 * Each is a bounded query on an indexed column, and step 3 is the one that
 * matters: `innings IS NOT NULL` is the same filter lib/match-index.ts,
 * models/resolve_outcomes.py and eval/calibration_monitor.py use. A row
 * without it is invisible to the strip and unscoreable, so a hero built on
 * one would render an empty chart.
 *
 * ELEVEN, NOT TWELVE. Afghanistan is an ICC Full Member with no row in this
 * corpus. See the comment on teams.full_member in
 * supabase/migrations/20260924000003_teams_full_member.sql.
 */

import HERO_FIXTURE from "./fixtures/hero-match.json";
import { supabaseServer } from "./supabase-server";

/**
 * How many recent Full Member matches to consider before giving up.
 *
 * Bounded because step 3 filters them against `predictions`, and an unbounded
 * candidate list would put an unbounded `.in()` in that query. Twenty is
 * comfortably more than the number of Full Member fixtures that could lack
 * predictions at once, and one PostgREST page holds their rows either way.
 */
const CANDIDATES = 20;

export interface HeroMatch {
  matchId: number;
  competition: string | null;
  format: string | null;
  /** ISO date, or null if the match row has no start_time. */
  date: string | null;
  teamA: string;
  teamB: string;
  /** The winning side's name, or null for a match with no mirrored result. */
  winner: string | null;
}

export interface HeroResult {
  match: HeroMatch;
  /** True when the query failed or found nothing and the fixture was used. */
  stale: boolean;
  /** Set only when stale: when the fixture was taken. */
  capturedAt?: string;
}

/** Shared with lib/landing-figures.ts's rationale: an unusable log line is no log line. */
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
  return String(error);
}

function fallback(reason: string): HeroResult {
  console.warn(`[hero] using the committed fixture: ${reason}`);
  return {
    match: HERO_FIXTURE.match,
    stale: true,
    capturedAt: HERO_FIXTURE.capturedAt,
  };
}

export async function loadHeroMatch(): Promise<HeroResult> {
  const supabase = supabaseServer();

  const teamsResult = await supabase
    .from("teams")
    .select("team_id, name")
    .eq("full_member", true);
  if (teamsResult.error || !teamsResult.data?.length) {
    return fallback(`no Full Member teams: ${describe(teamsResult.error)}`);
  }
  const names = new Map<number, string>(
    teamsResult.data.map((row) => [row.team_id, row.name])
  );
  const ids = [...names.keys()];

  const matchesResult = await supabase
    .from("matches")
    .select("match_id, competition, format, start_time, team_a, team_b, winner")
    .in("team_a", ids)
    .in("team_b", ids)
    .order("start_time", { ascending: false })
    .limit(CANDIDATES);
  if (matchesResult.error || !matchesResult.data?.length) {
    return fallback(`no Full Member fixtures: ${describe(matchesResult.error)}`);
  }
  const candidates = matchesResult.data;

  const predictionsResult = await supabase
    .from("predictions")
    .select("match_id")
    .in(
      "match_id",
      candidates.map((row) => row.match_id)
    )
    .not("innings", "is", null);
  if (predictionsResult.error) {
    return fallback(`prediction check failed: ${describe(predictionsResult.error)}`);
  }
  const withPredictions = new Set(predictionsResult.data.map((row) => row.match_id));

  // candidates is already newest-first, so the first hit is the answer.
  const hit = candidates.find((row) => withPredictions.has(row.match_id));
  if (!hit) {
    return fallback(`none of the ${candidates.length} most recent have keyed predictions`);
  }

  const teamA = hit.team_a === null ? undefined : names.get(hit.team_a);
  const teamB = hit.team_b === null ? undefined : names.get(hit.team_b);
  if (!teamA || !teamB) {
    // Both sides came from the Full Member id list, so this is unreachable
    // unless teams changed between the two queries. Fall back rather than
    // render "undefined v undefined" — the exact shape ids 1-3 produced.
    return fallback(`match ${hit.match_id} has a side that cannot be named`);
  }

  return {
    match: {
      matchId: hit.match_id,
      competition: hit.competition,
      format: hit.format,
      date: hit.start_time ? hit.start_time.slice(0, 10) : null,
      teamA,
      teamB,
      winner: hit.winner === null ? null : (names.get(hit.winner) ?? null),
    },
    stale: false,
  };
}
