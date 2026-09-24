/**
 * The landing page's three figures (UI-PHASE.md §4.1 item 3).
 *
 * Each entry point carries a number that proves it is real. §4.1 asks for
 * "real numbers from the database, not hardcoded" — two of the three can be,
 * and the third cannot, for a structural reason worth stating rather than
 * papering over.
 *
 * WHERE EACH NUMBER COMES FROM
 *
 * Matches with predictions: from the calibration report, not from counting
 * `predictions`. PostgREST has no `count(DISTINCT)`, so counting distinct
 * match ids over the wire would mean pulling ~12.5k rows on every
 * regeneration. The daily monitor already computes exactly this figure and
 * writes it to `calibration_runs.report`, and /accuracy already reads that
 * row — so taking it from the same place means the two pages cannot disagree
 * about how much evidence exists, which is a stronger property than freshness.
 *
 * Players: a head count on `players`, which is populated on Supabase. Note
 * that the row count is the only thing populated — `batting_hand`,
 * `bowling_style` and `dob` are NULL for all 18,468.
 *
 * Deliveries: NOT from the serving database, and it cannot be. `deliveries`
 * is empty on Supabase by design (3.78M rows do not fit the free tier) and is
 * additionally denied to the anonymous role as a negative control. The real
 * corpus lives in local Postgres and in the Railway replica the /ask tools
 * query. It is committed by scripts/make-corpus-facts.mjs and the page says
 * where it was counted.
 *
 * FAILURE IS A FIRST-CLASS PATH. Free-tier Supabase pauses after ~7 days idle
 * — this project has recorded pauses of 12 minutes, 12 minutes and 3.3 hours.
 * Every loader here returns a figure with a `stale` flag rather than throwing,
 * following the convention in app/accuracy/page.tsx, so a paused database
 * produces a complete page with dated numbers instead of a 500.
 */

import { CORPUS_FACTS } from "./corpus-facts";
import { parseReport } from "./accuracy";
import { supabaseServer } from "./supabase-server";

export interface Figure {
  value: number;
  /** True when the live query failed and the committed count was used. */
  stale: boolean;
  /** Set only when stale: when the committed count was taken. */
  countedAt?: string;
}

export interface LandingFigures {
  matchesWithPredictions: Figure;
  players: Figure;
  /** Always "stale" in the sense of committed — see the header comment. */
  deliveries: Figure;
  /** True if anything fell back, so the page can say so once rather than thrice. */
  anyStale: boolean;
}

/**
 * Describe a failure in a way that is worth logging.
 *
 * Supabase's error objects are not always Errors and do not always carry a
 * message: the first run of this logging produced `query failed: ` with
 * nothing after it, which is precisely the useless line standing rule 14 is
 * about — it proves something went wrong and tells you nothing about what.
 * So every field is tried, and an object with no usable field is serialised
 * rather than coerced to "[object Object]".
 */
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
 * Fall back to the committed count, and say so in the build log.
 *
 * Standing rule 14: a tolerated error must be fixed or silenced deliberately,
 * never left to become furniture. This path is genuinely tolerable - it is
 * the whole reason the page survives a paused database - but tolerating it
 * silently means a permanent outage and a two-second blip render the same
 * way, and nobody finds out which they had.
 *
 * Observed on this page's first production build: the figures rendered from
 * the fallback while both queries succeeded when run by hand moments later.
 * The captured reason was `JWT issued at future`, and the local clock leads
 * Supabase by about two seconds - so it is intermittent rather than broken,
 * and it would have been invisible without this line.
 */
function fallback(figure: string, reason: string, value: number): Figure {
  console.warn(
    `[landing] ${figure}: using the committed count from ${CORPUS_FACTS.countedAt} - ${reason}`
  );
  return { value, stale: true, countedAt: CORPUS_FACTS.countedAt };
}

/**
 * Matches the model has been scored on, from the latest calibration run.
 *
 * Sums the two populations because a visitor asking "how much has this been
 * tested on" means both — the distinction between replayed and live matters
 * enormously on /accuracy and not at all in a one-line figure, and splitting
 * it here would imply a precision the sentence does not carry.
 */
async function loadMatchesWithPredictions(): Promise<Figure> {
  try {
    const { data, error } = await supabaseServer()
      .from("calibration_runs")
      .select("report")
      .order("computed_at", { ascending: false })
      .limit(1)
      .maybeSingle();

    if (error || !data) {
      return fallback(
        "matches",
        error ? `query failed: ${describe(error)}` : "no calibration run yet",
        CORPUS_FACTS.matchesWithPredictions
      );
    }

    const report = parseReport(data.report);
    if (report === null) {
      return fallback("matches", "report did not parse", CORPUS_FACTS.matchesWithPredictions);
    }

    const total =
      (report.populations.backfill?.n_matches ?? 0) + (report.populations.live?.n_matches ?? 0);

    // A report that parses but counts nothing means the monitor ran before
    // anything was scored. The committed figure is more informative than a
    // confident zero on the front door.
    if (total <= 0) {
      return fallback("matches", "report scored nothing", CORPUS_FACTS.matchesWithPredictions);
    }

    return { value: total, stale: false };
  } catch (error) {
    return fallback("matches", `threw: ${describe(error)}`, CORPUS_FACTS.matchesWithPredictions);
  }
}

async function loadPlayers(): Promise<Figure> {
  try {
    const { count, error } = await supabaseServer()
      .from("players")
      .select("*", { count: "exact", head: true });

    if (error || count === null || count <= 0) {
      return fallback(
        "players",
        error ? `query failed: ${describe(error)}` : `count was ${count}`,
        CORPUS_FACTS.players
      );
    }
    return { value: count, stale: false };
  } catch (error) {
    return fallback("players", `threw: ${describe(error)}`, CORPUS_FACTS.players);
  }
}

export async function loadLandingFigures(): Promise<LandingFigures> {
  const [matchesWithPredictions, players] = await Promise.all([
    loadMatchesWithPredictions(),
    loadPlayers(),
  ]);

  // Deliveries is always the committed count, but it is not "stale" in the
  // sense the page warns about - it is the only place that number exists.
  // The page labels its provenance separately.
  const deliveries: Figure = { value: CORPUS_FACTS.deliveries, stale: false };

  return {
    matchesWithPredictions,
    players,
    deliveries,
    anyStale: matchesWithPredictions.stale || players.stale,
  };
}
