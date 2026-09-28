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

import { loadMatchPredictions } from "@/lib/match-predictions";
import { supabaseServer } from "@/lib/supabase-server";

import { LiveMatch } from "./live-match";

export const dynamic = "force-dynamic";

interface MatchHeader {
  matchId: number;
  competition: string;
  format: string;
  teamA: string | null;
  teamAId: number | null;
  teamB: string | null;
  teamBId: number | null;
  venue: string | null;
  startDate: string;
  status: string;
  winner: string | null;
  resultMethod: string | null;
  winByRuns: number | null;
  winByWickets: number | null;
  outcomeMethod: string | null;
  tieWinner: string | null;
  tieDecidedBy: string | null;
}

async function loadMatch(matchId: number): Promise<MatchHeader | null> {
  const supabase = supabaseServer();
  const { data, error } = await supabase
    .from("matches")
    .select(
      "match_id, competition, format, start_time, team_a, team_b, venue_id, status, winner, result_method, win_by_runs, win_by_wickets, outcome_method, tie_winner, tie_decided_by"
    )
    .eq("match_id", matchId)
    .maybeSingle();
  if (error || !data) return null;

  // teams and venues are tiny reference tables; two extra round trips on a
  // server render is cheaper than denormalising names into every prediction.
  const [teams, venue] = await Promise.all([
    supabase
      .from("teams")
      .select("team_id, name")
      .in(
        "team_id",
        [data.team_a, data.team_b, data.tie_winner].filter((x): x is number => x !== null)
      ),
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
    teamAId: data.team_a,
    teamB: nameOf(data.team_b),
    teamBId: data.team_b,
    venue: (venue.data as { name: string } | null)?.name ?? null,
    startDate: data.start_time.slice(0, 10),
    status: data.status,
    winner: nameOf(data.winner),
    resultMethod: data.result_method,
    winByRuns: data.win_by_runs,
    winByWickets: data.win_by_wickets,
    outcomeMethod: data.outcome_method,
    // A tie-breaker's winner is one of the two sides, so it is already named.
    tieWinner: nameOf(data.tie_winner),
    tieDecidedBy: data.tie_decided_by,
  };
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
    loadMatchPredictions(matchId),
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
        initialPredictions={initial.predictions}
        modelVersion={initial.modelVersion}
      />
      <p className="page-links">
        <Link href="/about/model">How good is this model?</Link> · predictions are
        analytics, not betting advice.
      </p>
    </main>
  );
}
