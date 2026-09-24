/**
 * Is anything live? (UI-PHASE.md §3.1)
 *
 * The header sits in the root layout, so a server-rendered live slot would be
 * as stale as whatever cache the page it sits on uses — an hour, on the ISR
 * landing page. So the slot fetches this on mount instead, and the shell
 * itself stays static.
 *
 * That trades one problem for another: a query per visitor rather than a
 * query per render. `unstable_cache` closes it. The database sees at most one
 * query per REVALIDATE_SECONDS no matter how many people are looking, and the
 * Cache-Control header lets the CDN and the browser absorb the rest.
 *
 * Failure is not an error here. If the query throws, or Supabase is paused,
 * or the environment is missing, this answers `{ live: null }` — which is the
 * same answer as "nothing is live", and §3.1 already specifies what the
 * header does with it: show an empty slot, not a placeholder. A front door
 * that 500s because a background lookup failed is a worse outcome than a
 * header that is briefly quiet.
 */

import { unstable_cache } from "next/cache";
import { NextResponse } from "next/server";

import { LIVE_WINDOW_MS, isRecent, shortName } from "@/lib/live-match";
import { parsePrediction, type WinProbPrediction } from "@/lib/prediction";
import { supabaseServer } from "@/lib/supabase-server";

export const runtime = "nodejs";

/** One database read per this many seconds, across all visitors. */
const REVALIDATE_SECONDS = 30;

export interface LivePayload {
  matchId: number;
  battingShort: string;
  bowlingShort: string;
  prediction: WinProbPrediction;
}

async function readLive(): Promise<LivePayload | null> {
  const supabase = supabaseServer();

  // source='live' is written by the producer and never inferred - the
  // migration rejects deriving it from timestamps, because a replay of a
  // match played today would be misclassified. Recency on top of it is what
  // makes this self-clearing: when the worker stops, rows stop arriving.
  const { data, error } = await supabase
    .from("predictions")
    .select("prediction_id, created_at, model_version, payload, match_id")
    .eq("source", "live")
    .eq("prediction_type", "win_prob")
    .not("innings", "is", null)
    .gte("created_at", new Date(Date.now() - LIVE_WINDOW_MS).toISOString())
    .order("prediction_id", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (error || !data) return null;
  if (!isRecent(data.created_at, Date.now())) return null;

  const prediction = parsePrediction(data as never);
  if (prediction === null) return null;

  const { data: match } = await supabase
    .from("matches")
    .select("team_a, team_b")
    .eq("match_id", data.match_id)
    .maybeSingle();

  const ids = [match?.team_a, match?.team_b].filter((id): id is number => typeof id === "number");
  const { data: teams } = await supabase.from("teams").select("team_id, name").in("team_id", ids);

  const nameOf = (id: number | null | undefined) =>
    teams?.find((team) => team.team_id === id)?.name ?? null;

  // The second-innings batting side is chasing, and which of team_a/team_b
  // that is cannot be read here - `deliveries` is empty on Supabase. So the
  // labels are the two sides in table order, not batting order, and the slot
  // does not claim otherwise.
  const a = nameOf(match?.team_a);
  const b = nameOf(match?.team_b);

  return {
    matchId: data.match_id,
    battingShort: a ? shortName(a) : "",
    bowlingShort: b ? shortName(b) : "",
    prediction,
  };
}

const cachedLive = unstable_cache(readLive, ["live-match-slot"], {
  revalidate: REVALIDATE_SECONDS,
});

export async function GET() {
  let live: LivePayload | null = null;
  try {
    live = await cachedLive();
  } catch {
    // Deliberately swallowed. See the header comment: no live match and a
    // failed lookup produce the same header, so distinguishing them here
    // would only let one of them take down the page.
    live = null;
  }

  return NextResponse.json(
    { live },
    {
      headers: {
        "Cache-Control": `public, max-age=${REVALIDATE_SECONDS}, s-maxage=${REVALIDATE_SECONDS}`,
      },
    }
  );
}
