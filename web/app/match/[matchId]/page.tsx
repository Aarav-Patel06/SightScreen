/**
 * Live match page (SPEC.md 12.1 items 1, 2 and 6).
 *
 * Items 3, 4, 5 and 7 are deliberately absent: the batter/bowler cards, the
 * matchup callout and the WPA leaderboard all need per-ball player identity,
 * which SPEC.md section 15 records as blocked on a ball-by-ball-capable
 * provider, and the action buttons are Phase 6's agent.
 *
 * This is a server component. Per section 7.4 it fetches current state with
 * a normal query and hands it to the client component, which subscribes for
 * deltas - rather than reconstructing history from the stream.
 */

import Link from "next/link";

import { parsePrediction, type WinProbPrediction } from "@/lib/prediction";
import { supabaseServer } from "@/lib/supabase-server";

import { LiveMatch } from "./live-match";

export const dynamic = "force-dynamic";

interface MatchHeader {
  matchId: number;
  competition: string;
  format: string;
  teamA: string | null;
  teamB: string | null;
  venue: string | null;
  startDate: string;
}

async function loadMatch(matchId: number): Promise<MatchHeader | null> {
  const supabase = supabaseServer();
  const { data, error } = await supabase
    .from("matches")
    .select("match_id, competition, format, start_time, team_a, team_b, venue_id")
    .eq("match_id", matchId)
    .maybeSingle();
  if (error || !data) return null;

  // teams and venues are tiny reference tables; two extra round trips on a
  // server render is cheaper than denormalising names into every prediction.
  const [teams, venue] = await Promise.all([
    supabase
      .from("teams")
      .select("team_id, name")
      .in("team_id", [data.team_a, data.team_b].filter((x): x is number => x !== null)),
    data.venue_id === null
      ? Promise.resolve({ data: null })
      : supabase.from("venues").select("name").eq("venue_id", data.venue_id).maybeSingle(),
  ]);

  const nameOf = (id: number | null) =>
    teams.data?.find((t) => t.team_id === id)?.name ?? null;

  return {
    matchId: data.match_id,
    competition: data.competition,
    format: data.format,
    teamA: nameOf(data.team_a),
    teamB: nameOf(data.team_b),
    venue: (venue.data as { name: string } | null)?.name ?? null,
    startDate: data.start_time.slice(0, 10),
  };
}

async function loadPredictions(matchId: number): Promise<WinProbPrediction[]> {
  const supabase = supabaseServer();
  const { data, error } = await supabase
    .from("predictions")
    .select("prediction_id, created_at, model_version, payload, match_id")
    .eq("match_id", matchId)
    .eq("prediction_type", "win_prob")
    // Migration 20260918000003's stated contract: "every Phase 3 reader
    // filters on innings IS NOT NULL". This page did not, and was the only
    // user-facing surface rendering unkeyed rows - so for match 13143 it drew
    // a curve out of ten interleaved runs of a twelve-ball deploy smoke test,
    // while /matches listed the same match as never replayed. Two surfaces
    // disagreeing about what counts as a prediction meant one of them was
    // wrong, and it was this one.
    //
    // The unkeyed rows are not merely old. They sit outside the partial
    // unique index, so nothing dedupes them: match 13143 holds 121 such rows
    // carrying 13 distinct payloads.
    .not("innings", "is", null)
    .order("prediction_id", { ascending: true });
  if (error || !data) return [];
  // A malformed row - one written before the payload was enriched, say -
  // is dropped rather than throwing. A single bad row must not blank a page.
  return data
    .map((row) => parsePrediction(row as never))
    .filter((x): x is WinProbPrediction => x !== null);
}

export default async function MatchPage({
  params,
}: {
  params: Promise<{ matchId: string }>;
}) {
  const { matchId: raw } = await params;
  const matchId = Number.parseInt(raw, 10);
  if (!Number.isFinite(matchId)) {
    return (
      <main>
        <div className="panel">Not a match id.</div>
      </main>
    );
  }

  const [match, initial] = await Promise.all([
    loadMatch(matchId),
    loadPredictions(matchId),
  ]);

  if (match === null) {
    return (
      <main>
        <div className="panel">
          <h1>Match {matchId}</h1>
          <p className="muted small">
            No such match on the serving database. Only live and recent matches live
            there (SPEC.md section 2.1); the historical corpus stays local.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main>
      <LiveMatch
        matchId={matchId}
        header={match}
        initialPredictions={initial}
      />
      <p className="page-links">
        <Link href="/about/model">How good is this model?</Link> · predictions are
        analytics, not betting advice.
      </p>
    </main>
  );
}
