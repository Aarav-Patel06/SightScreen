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
import { toMarks, type Mark } from "./ball-strip";
import { battingTeamName } from "./batting-team";
import { chaseSummary, type ChaseSummary } from "./chase-summary";
import { isLiveMatch } from "./live-match";
import { resultText } from "./match-result";
import { loadModelVersions } from "./load-model-versions";
import { oneSource, oneVersion } from "./model-version";
import { parsePrediction, type WinProbPrediction } from "./prediction";
import { supabaseServer } from "./supabase-server";

/**
 * How many recent Full Member matches to consider before giving up.
 *
 * Bounded because step 3 checks each against `predictions`, newest first,
 * until one has keyed rows. Twenty is comfortably more than the number of
 * Full Member fixtures that could lack predictions at once; in practice the
 * first candidate answers and the loop makes one request.
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
  /**
   * The side whose win probability the strip shows - from the prediction
   * row, never from team order. Null when unknown, and on the fixture path.
   */
  battingTeam: string | null;
  /**
   * Is this match in progress right now?
   *
   * lib/live-match.ts's isLiveMatch: `matches.status` says 'live' AND the
   * latest prediction is recent. Status alone stayed 'live' on finished
   * matches for months, and a LIVE NOW tag on one of those is the worst thing
   * this page could get wrong.
   */
  isLive: boolean;
  /** "Live" or "Latest match" - what the hero is showing, said plainly. */
  label: "Live" | "Latest match";
  /** The result in words (lib/match-result.ts), or null while live/unknown. */
  result: string | null;
  /**
   * What the strip covers: the chase only - "India's chase of 388". Null when
   * the batting side or target is unknown, and on the fixture path.
   */
  chase: string | null;
}

export interface HeroResult {
  match: HeroMatch;
  /**
   * The strip for this match, or null when it could not be loaded.
   *
   * Separate from `stale`: the identity can be live while the strip is not,
   * and a hero with real teams and no chart is better than a fixture.
   */
  marks: Mark[] | null;
  /**
   * The chase's summary facts (lib/chase-summary.ts), for the caption under
   * the curve. Null exactly when `marks` is.
   */
  summary: ChaseSummary | null;
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
    // The fixture has no strip, so there is no probability to attribute.
    match: {
      ...HERO_FIXTURE.match,
      battingTeam: null,
      label: "Latest match",
      result: HERO_FIXTURE.match.winner ? `${HERO_FIXTURE.match.winner} won` : null,
      chase: null,
    },
    marks: null,
    summary: null,
    stale: true,
    capturedAt: HERO_FIXTURE.capturedAt,
  };
}

/**
 * The chosen match's predictions.
 *
 * Its own failure path: returns [] rather than throwing, and the caller
 * renders the hero without a chart. PostgREST caps a response at 1000 rows
 * and the longest ODI chase logged is 305 predictions, so one request is
 * always enough - but the limit is stated rather than assumed.
 */
async function loadPredictions(matchId: number): Promise<WinProbPrediction[]> {
  const supabase = supabaseServer();
  const { data, error } = await supabase
    .from("predictions")
    .select("prediction_id, created_at, model_version, payload, batting_team_id, source")
    .eq("match_id", matchId)
    .eq("prediction_type", "win_prob")
    .not("innings", "is", null)
    .order("prediction_id", { ascending: true })
    .limit(1000);

  if (error || !data?.length) {
    console.warn(`[hero] no strip for match ${matchId}: ${describe(error)}`);
    return [];
  }
  // One model version per match, then one source (lib/model-version.ts).
  return oneSource(oneVersion(data, await loadModelVersions()))
    .map((row) => parsePrediction(row as never))
    .filter((p) => p !== null);
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
    .select(
      "match_id, competition, format, start_time, team_a, team_b, winner, status, result_method, win_by_runs, win_by_wickets, outcome_method, tie_winner, tie_decided_by"
    )
    .in("team_a", ids)
    .in("team_b", ids)
    .order("start_time", { ascending: false })
    .limit(CANDIDATES);
  if (matchesResult.error || !matchesResult.data?.length) {
    return fallback(`no Full Member fixtures: ${describe(matchesResult.error)}`);
  }
  const candidates = matchesResult.data;

  // THE SELECTION RULE, newest candidate first:
  //   - LIVE, IN ITS CHASE, with a keyed prediction under five minutes old
  //     (isLiveMatch) takes the hero. Keyed predictions are innings 2 only -
  //     the worker writes none in a first innings - so "has a fresh keyed
  //     row" is "the chase has started". A live match still in its first
  //     innings, or one gone quiet, is passed over: the previous match stays.
  //   - otherwise the newest COMPLETED one with keyed predictions.
  //
  // ONE ROW PER CANDIDATE, never all of them: an unbounded fetch of twenty
  // matches' predictions hit PostgREST's silent 1,000-row cap on 2026-09-26.
  const now = Date.now();
  let hit: (typeof candidates)[number] | undefined;
  for (const row of candidates) {
    if (row.status !== "live" && row.status !== "complete") continue;
    const check = await supabase
      .from("predictions")
      .select("match_id, created_at")
      .eq("match_id", row.match_id)
      .not("innings", "is", null)
      .order("prediction_id", { ascending: false })
      .limit(1);
    if (check.error) {
      return fallback(`prediction check failed: ${describe(check.error)}`);
    }
    const newest = check.data?.[0];
    if (!newest) continue;
    if (row.status === "live" && !isLiveMatch(row.status, newest.created_at, now)) continue;
    hit = row;
    break;
  }
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

  const predictions = await loadPredictions(hit.match_id);
  const latest = predictions.at(-1) ?? null;
  const battingTeam = battingTeamName(latest, [
    { id: hit.team_a, name: teamA },
    { id: hit.team_b, name: teamB },
  ]);
  const isLive = isLiveMatch(hit.status, latest?.created_at ?? null, now);
  const tieWinner = hit.tie_winner === null ? null : (names.get(hit.tie_winner) ?? null);

  return {
    marks: predictions.length > 1 ? toMarks(predictions) : null,
    summary: predictions.length > 1 ? chaseSummary(predictions) : null,
    match: {
      matchId: hit.match_id,
      competition: hit.competition,
      format: hit.format,
      date: hit.start_time ? hit.start_time.slice(0, 10) : null,
      teamA,
      teamB,
      winner: hit.winner === null ? null : (names.get(hit.winner) ?? null),
      battingTeam,
      isLive,
      label: isLive ? "Live" : "Latest match",
      result: isLive
        ? null
        : resultText({
            resultMethod: hit.result_method,
            winner: hit.winner === null ? null : (names.get(hit.winner) ?? null),
            winByRuns: hit.win_by_runs,
            winByWickets: hit.win_by_wickets,
            outcomeMethod: hit.outcome_method,
            tieWinner,
            tieDecidedBy: hit.tie_decided_by,
          }),
      chase: battingTeam && latest ? `${battingTeam}'s chase of ${latest.target}` : null,
    },
    stale: false,
  };
}
