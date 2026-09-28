/**
 * The data behind /matches (UI-PHASE.md §4.2).
 *
 * WHAT THIS INDEXES, AND WHAT IT DOES NOT. The serving database holds 107
 * matches, not the 13,143 in the corpus, and every one of them is there
 * because something wrote predictions for it - `predictions.match_id` is a
 * foreign key, so the match row has to exist first (see
 * `replay_log.mirror_match_rows`). The index is therefore a list of matches
 * the model has been run on. It is not a fixture list and must not read like
 * one; the page says so above the table.
 *
 * UNNAMEABLE MATCHES ARE EXCLUDED. Three live-worker rows (ids 1-3) lost
 * `team_a`, `team_b` and `venue_id` to a backfill incident; the provider
 * UUIDs survive in `external_ids`, but recovering the names would buy three
 * rows that still have no strip and can never be scored, because there is no
 * corpus counterpart to resolve outcomes against. So the rule is stated as a
 * property rather than as an id list: a match whose two sides cannot be named
 * is not listed. An id list would silently stop matching the day the ids
 * changed; this keeps working.
 *
 * The three pre-Phase-3 demo replays that used to appear here as "Not
 * replayed" were repaired in UI Phase 2 step 1 rather than relabelled. Their
 * rows carried `innings IS NULL` - written before migration 20260918000003
 * added the ball key - which put them outside the partial unique index and
 * made them invisible to resolve_outcomes, calibration_monitor and the filter
 * below alike. They were deleted and replayed properly.
 *
 * THE SWEEP. Each row shows a 120px sparkline, so the loader needs every
 * prediction: 12,121 rows, paged 1000 at a time because that is PostgREST's
 * ceiling. Pages are fetched in parallel after one count query. This runs
 * once per revalidate window, not per visitor.
 *
 * Failure is a first-class path, following lib/landing-figures.ts: nothing
 * here throws. A failed sweep yields a table with no sparklines and a line
 * saying why, because a list of 107 matches with no strips is still useful
 * and a 500 is not.
 */

import { toMarks, type Mark } from "./ball-strip";
import { resultText, type MatchOutcome } from "./match-result";
import { loadModelVersions } from "./load-model-versions";
import { oneSourcePerMatch, oneVersionPerMatch } from "./model-version";
import { parsePrediction, type WinProbPrediction } from "./prediction";
import { supabaseServer } from "./supabase-server";

/** PostgREST caps a response at 1000 rows regardless of the limit asked for. */
const PAGE = 1000;
/** prediction_outcomes ids per request - see loadChaseOutcomes. */
const OUTCOME_CHUNK = 500;

export interface MatchRow {
  matchId: number;
  competition: string;
  format: string;
  date: string;
  teamA: string | null;
  teamB: string | null;
  venue: string | null;
  /** Null when the match has no usable predictions. */
  marks: Mark[] | null;
  /** A result line, or null when nothing about the result is known. */
  result: MatchResult | null;
}

/**
 * The match's outcome (lib/match-result.ts's fields), plus which side won as
 * prediction_outcomes records it - the only result a live-worker row, whose
 * match never came from the corpus, has.
 */
export interface MatchResult extends MatchOutcome {
  /** True if the chasing side won, from prediction_outcomes. */
  chaseWon: boolean | null;
}

export interface MatchIndex {
  matches: MatchRow[];
  /** True when the prediction sweep failed; sparklines are absent, not empty. */
  sparklinesUnavailable: boolean;
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
 * Every win_prob prediction, keyed by match.
 *
 * `innings=not.is.null` drops the pre-Phase-3 rows. They are not merely old:
 * match 13143's are a twelve-ball smoke test run about ten times, and letting
 * them through would draw a sparkline out of a deploy check.
 */
async function loadPredictionsByMatch(): Promise<Map<number, WinProbPrediction[]> | null> {
  const supabase = supabaseServer();

  const base = () =>
    supabase
      .from("predictions")
      .select("prediction_id, created_at, model_version, payload, match_id, source")
      .eq("prediction_type", "win_prob")
      .not("innings", "is", null)
      .order("prediction_id", { ascending: true });

  const { count, error: countError } = await supabase
    .from("predictions")
    .select("*", { count: "exact", head: true })
    .eq("prediction_type", "win_prob")
    .not("innings", "is", null);

  if (countError || count === null) {
    console.warn(`[matches] sparkline sweep skipped - count failed: ${describe(countError)}`);
    return null;
  }

  // Parallel, not sequential: the page count is known up front, so thirteen
  // round trips can overlap rather than stack into thirteen latencies.
  const pages = Math.ceil(count / PAGE);
  const [versions, ...results] = await Promise.all([
    loadModelVersions(),
    ...Array.from({ length: pages }, (_, i) => base().range(i * PAGE, i * PAGE + PAGE - 1)),
  ]);

  const swept = [];
  for (const { data, error } of results) {
    if (error || !data) {
      console.warn(`[matches] sparkline sweep failed mid-page: ${describe(error)}`);
      return null;
    }
    swept.push(...data);
  }

  // One model version per match (lib/model-version.ts), decided over the
  // whole sweep: a match's two versions can straddle a page boundary.
  const byMatch = new Map<number, WinProbPrediction[]>();
  for (const row of oneSourcePerMatch(oneVersionPerMatch(swept, versions))) {
    const parsed = parsePrediction(row as never);
    if (parsed === null) continue;
    const matchId = (row as { match_id: number }).match_id;
    const list = byMatch.get(matchId);
    if (list) list.push(parsed);
    else byMatch.set(matchId, [parsed]);
  }
  return byMatch;
}

/**
 * Did the chasing side win, per match.
 *
 * `prediction_outcomes` is one row per prediction with no `match_id`, so this
 * asks only about the last prediction of each match and maps back through the
 * id. Every prediction of a match carries the same `batting_team_won`, so one
 * is as good as 12,081.
 */
async function loadChaseOutcomes(
  lastPredictionIdByMatch: Map<number, number>
): Promise<Map<number, boolean>> {
  const outcomes = new Map<number, boolean>();
  const ids = [...lastPredictionIdByMatch.values()];
  if (ids.length === 0) return outcomes;

  try {
    // One id per match, so this passed PostgREST's 1,000-row cap once the
    // index did - silently, blanking every result line past the thousandth.
    // Chunked well under it (which also keeps the URL short).
    const chunks: number[][] = [];
    for (let i = 0; i < ids.length; i += OUTCOME_CHUNK) chunks.push(ids.slice(i, i + OUTCOME_CHUNK));
    const results = await Promise.all(
      chunks.map((chunk) =>
        supabaseServer()
          .from("prediction_outcomes")
          .select("prediction_id, actual")
          .in("prediction_id", chunk)
      )
    );

    const data = [];
    for (const { data: rows, error } of results) {
      if (error || !rows) {
        console.warn(`[matches] chase outcomes unavailable: ${describe(error)}`);
        return outcomes;
      }
      data.push(...rows);
    }

    const byPrediction = new Map<number, boolean>();
    for (const row of data) {
      const actual = row.actual as { batting_team_won?: unknown } | null;
      if (actual && typeof actual.batting_team_won === "boolean") {
        byPrediction.set(row.prediction_id, actual.batting_team_won);
      }
    }
    for (const [matchId, predictionId] of lastPredictionIdByMatch) {
      const won = byPrediction.get(predictionId);
      if (won !== undefined) outcomes.set(matchId, won);
    }
  } catch (error) {
    console.warn(`[matches] chase outcomes threw: ${describe(error)}`);
  }
  return outcomes;
}

/**
 * Every listable match, newest first, paged.
 *
 * Unbounded, this read stopped at PostgREST's 1,000-row cap without an error,
 * and the daily Cricsheet job takes the table past that within months. Paged
 * exactly as loadPredictionsByMatch is: count first, then the pages in
 * parallel.
 */
async function loadMatchRows() {
  const supabase = supabaseServer();
  const base = () =>
    supabase
      .from("matches")
      .select(
        "match_id, competition, format, start_time, team_a, team_b, venue_id, winner, target_runs, result_method, win_by_runs, win_by_wickets, outcome_method, tie_winner, tie_decided_by"
      )
      // See UNNAMEABLE MATCHES above. Filtered in the query rather than after
      // the fetch so the count the page reports is the count it renders.
      .not("team_a", "is", null)
      .not("team_b", "is", null)
      // match_id breaks start_time ties, so pages cannot overlap or skip.
      .order("start_time", { ascending: false })
      .order("match_id", { ascending: false });

  const { count, error: countError } = await supabase
    .from("matches")
    .select("*", { count: "exact", head: true })
    .not("team_a", "is", null)
    .not("team_b", "is", null);
  if (countError || count === null) {
    console.warn(`[matches] index unavailable - count failed: ${describe(countError)}`);
    return null;
  }

  const pages = Math.ceil(count / PAGE);
  const results = await Promise.all(
    Array.from({ length: pages }, (_, i) => base().range(i * PAGE, i * PAGE + PAGE - 1))
  );
  const rows = [];
  for (const { data, error } of results) {
    if (error || !data) {
      console.warn(`[matches] index unavailable: ${describe(error)}`);
      return null;
    }
    rows.push(...data);
  }
  return rows;
}

export async function loadMatchIndex(): Promise<MatchIndex> {
  const supabase = supabaseServer();

  const [rows, byMatch] = await Promise.all([loadMatchRows(), loadPredictionsByMatch()]);
  if (rows === null) {
    return { matches: [], sparklinesUnavailable: byMatch === null };
  }

  const teamIds = new Set<number>();
  const venueIds = new Set<number>();
  for (const row of rows) {
    for (const id of [row.team_a, row.team_b, row.winner, row.tie_winner]) {
      if (typeof id === "number") teamIds.add(id);
    }
    if (typeof row.venue_id === "number") venueIds.add(row.venue_id);
  }

  const [teams, venues] = await Promise.all([
    teamIds.size
      ? supabase.from("teams").select("team_id, name").in("team_id", [...teamIds])
      : Promise.resolve({ data: [], error: null }),
    venueIds.size
      ? supabase.from("venues").select("venue_id, name").in("venue_id", [...venueIds])
      : Promise.resolve({ data: [], error: null }),
  ]);

  const teamName = new Map((teams.data ?? []).map((t) => [t.team_id, t.name]));
  const venueName = new Map((venues.data ?? []).map((v) => [v.venue_id, v.name]));

  const lastPredictionIdByMatch = new Map<number, number>();
  if (byMatch) {
    for (const [matchId, predictions] of byMatch) {
      if (predictions.length > 0) {
        lastPredictionIdByMatch.set(matchId, predictions[predictions.length - 1].prediction_id);
      }
    }
  }
  const chaseWon = await loadChaseOutcomes(lastPredictionIdByMatch);

  const matches: MatchRow[] = rows.map((row) => {
    const predictions = byMatch?.get(row.match_id) ?? null;

    return {
      matchId: row.match_id,
      competition: row.competition,
      format: row.format,
      date: row.start_time.slice(0, 10),
      teamA: typeof row.team_a === "number" ? (teamName.get(row.team_a) ?? null) : null,
      teamB: typeof row.team_b === "number" ? (teamName.get(row.team_b) ?? null) : null,
      venue: typeof row.venue_id === "number" ? (venueName.get(row.venue_id) ?? null) : null,
      marks: predictions && predictions.length > 0 ? toMarks(predictions) : null,
      result: {
        resultMethod: row.result_method,
        winner: typeof row.winner === "number" ? (teamName.get(row.winner) ?? null) : null,
        winByRuns: row.win_by_runs,
        winByWickets: row.win_by_wickets,
        outcomeMethod: row.outcome_method,
        tieWinner: typeof row.tie_winner === "number" ? (teamName.get(row.tie_winner) ?? null) : null,
        tieDecidedBy: row.tie_decided_by,
        chaseWon: chaseWon.get(row.match_id) ?? null,
      },
    };
  });

  return { matches, sparklinesUnavailable: byMatch === null };
}

/**
 * The result as one line - the same words as the match page.
 *
 * NEVER THE CHASE'S LAST STATE. This used to read "England won · chase
 * needed 32 off 1": the final payload is the state BEFORE the last ball, so
 * that describes a moment that never finished. The result's own columns
 * (migration 20260927000001) say what happened.
 *
 * Degrades one way: the full result; else, with no winner on the row (a
 * live-worker match), which side won from prediction_outcomes - named by the
 * target, because "Chase won" would need to know who chased; else nothing.
 */
export function resultLine(result: MatchResult | null): string | null {
  if (result === null) return null;
  const stated = resultText(result);
  if (stated !== null) return stated;
  if (result.chaseWon === null) return null;
  return result.chaseWon ? "Target reached" : "Target defended";
}
