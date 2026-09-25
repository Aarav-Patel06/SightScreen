/**
 * /matches — the index (UI-PHASE.md §4.2).
 *
 * ISR at an hour, matching `/`. A free-tier Supabase project pauses after
 * about a week idle, and this page runs a thirteen-page sweep of the
 * prediction table; making it dynamic would mean that sweep on every visit
 * and a 500 whenever the database is asleep.
 *
 * The heading says what this is a list of, because the obvious reading of
 * "Matches" on a cricket site is "all of them" and that would be wrong by
 * two orders of magnitude.
 */

import { CORPUS_FACTS } from "@/lib/corpus-facts";
import { loadMatchIndex } from "@/lib/match-index";

import { MatchTable } from "./match-table";

export const revalidate = 3600;

export const metadata = {
  title: "Matches — SightScreen",
  description: "Matches the win-probability model has been run on, with their ball-by-ball strips.",
};

export default async function MatchesPage() {
  const { matches, sparklinesUnavailable } = await loadMatchIndex();

  return (
    <main className="landing">
      <section className="page-intro">
        <h1 className="page-title">Matches</h1>
        <p className="prose soft">
          Every match the model has been run on — {matches.length} of the{" "}
          <span className="tnum">{CORPUS_FACTS.matches.toLocaleString()}</span> in the
          corpus. The rest have never been replayed, and the ball-by-ball data
          they would need lives on the training machine rather than here.
        </p>

        {matches.length === 0 ? (
          <p className="soft notice">
            The serving database is not answering right now. This page lists
            what it holds, so it has nothing to show until it does.
          </p>
        ) : (
          <MatchTable matches={matches} sparklinesUnavailable={sparklinesUnavailable} />
        )}
      </section>
    </main>
  );
}
