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
import { parsePrediction, type WinProbPrediction } from "./prediction";
import { supabaseServer } from "./supabase-server";

/** PostgREST caps a response at 1000 rows regardless of the limit asked for. */
const PAGE = 1000;

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

export interface MatchResult {
  /** The winning team's name, when `matches.winner` is populated. */
  winner: string | null;
  /** True if the chasing side won, from prediction_outcomes. */
  chaseWon: boolean | null;
  /** Runs still needed at the last ball the model saw. */
  runsRequired: number | null;
  /** Balls left at that same point. */
  ballsRemaining: number | null;
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
      .select("prediction_id, created_at, model_version, payload, match_id")
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
  const results = await Promise.all(
    Array.from({ length: pages }, (_, i) => base().range(i * PAGE, i * PAGE + PAGE - 1))
  );

  const byMatch = new Map<number, WinProbPrediction[]>();
  for (const { data, error } of results) {
    if (error || !data) {
      console.warn(`[matches] sparkline sweep failed mid-page: ${describe(error)}`);
      return null;
    }
    for (const row of data) {
      const parsed = parsePrediction(row as never);
      if (parsed === null) continue;
      const matchId = (row as { match_id: number }).match_id;
      const list = byMatch.get(matchId);
      if (list) list.push(parsed);
      else byMatch.set(matchId, [parsed]);
    }
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
    const { data, error } = await supabaseServer()
      .from("prediction_outcomes")
      .select("prediction_id, actual")
      .in("prediction_id", ids);

    if (error || !data) {
      console.warn(`[matches] chase outcomes unavailable: ${describe(error)}`);
      return outcomes;
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

export async function loadMatchIndex(): Promise<MatchIndex> {
  const supabase = supabaseServer();

  const [matchesResult, byMatch] = await Promise.all([
    supabase
      .from("matches")
      .select(
        "match_id, competition, format, start_time, team_a, team_b, venue_id, winner, target_runs, result_method"
      )
      // See UNNAMEABLE MATCHES above. Filtered in the query rather than after
      // the fetch so the count the page reports is the count it renders.
      .not("team_a", "is", null)
      .not("team_b", "is", null)
      .order("start_time", { ascending: false }),
    loadPredictionsByMatch(),
  ]);

  if (matchesResult.error || !matchesResult.data) {
    console.warn(`[matches] index unavailable: ${describe(matchesResult.error)}`);
    return { matches: [], sparklinesUnavailable: byMatch === null };
  }
  const rows = matchesResult.data;

  const teamIds = new Set<number>();
  const venueIds = new Set<number>();
  for (const row of rows) {
    for (const id of [row.team_a, row.team_b, row.winner]) {
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
    const last = predictions?.[predictions.length - 1] ?? null;

    return {
      matchId: row.match_id,
      competition: row.competition,
      format: row.format,
      date: row.start_time.slice(0, 10),
      teamA: typeof row.team_a === "number" ? (teamName.get(row.team_a) ?? null) : null,
      teamB: typeof row.team_b === "number" ? (teamName.get(row.team_b) ?? null) : null,
      venue: typeof row.venue_id === "number" ? (venueName.get(row.venue_id) ?? null) : null,
      marks: predictions && predictions.length > 0 ? toMarks(predictions) : null,
      result: buildResult(
        typeof row.winner === "number" ? (teamName.get(row.winner) ?? null) : null,
        chaseWon.get(row.match_id) ?? null,
        last
      ),
    };
  });

  return { matches, sparklinesUnavailable: byMatch === null };
}

function buildResult(
  winner: string | null,
  chaseWon: boolean | null,
  last: WinProbPrediction | null
): MatchResult | null {
  if (winner === null && chaseWon === null) return null;
  return {
    winner,
    chaseWon,
    runsRequired: last?.runs_required ?? null,
    ballsRemaining: last?.balls_remaining ?? null,
  };
}

/**
 * The result as one line.
 *
 * THE MARGIN IS DELIBERATELY NOT COMPUTED. The final payload is the state
 * BEFORE the last ball - the same off-by-one lib/ball-strip.ts documents - so
 * "won by 4 wickets" is not derivable from it, while "1 needed off 1" is
 * exactly true. A margin here would be a number nobody could check and most
 * people would believe.
 *
 * Degrades in one direction only: with `matches.winner` it names the team,
 * without it says which side won, and with neither it says nothing at all.
 */
export function resultLine(result: MatchResult | null): string | null {
  if (result === null) return null;

  // When the team is named the subject is the team; when it is not, the
  // subject has to be the target, because "Chase defended" is nonsense and
  // "Chase won · chase needed 4 off 3" says chase twice.
  const who =
    result.winner !== null
      ? `${result.winner} won`
      : result.chaseWon === null
        ? null
        : result.chaseWon
          ? "Target reached"
          : "Target defended";

  if (who === null) return null;

  if (result.runsRequired !== null && result.ballsRemaining !== null) {
    // "chase needed", not bare "needed". The requirement belongs to the side
    // batting second, which is NOT always the winner: match 4465 has Finland
    // winning while Sweden chased, and "Finland won · 74 needed off 17" reads
    // as though Finland needed 74. Naming the chase makes the sentence true
    // in both directions without needing to know which team was batting -
    // which Supabase cannot tell us anyway, since batting order lives in
    // `deliveries` and that table is empty there.
    return `${who} · chase needed ${result.runsRequired} off ${result.ballsRemaining}`;
  }
  return who;
}
