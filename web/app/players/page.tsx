/**
 * /players — the index (UI-PHASE.md §4.4).
 *
 * Lists the 8,575 players with a record, not the 18,468 named in the corpus.
 * More than half of those 18,468 have never batted or bowled a delivery:
 * they appear because a team sheet named them, and an index where most rows
 * go nowhere is the problem /matches already had to solve. The heading says
 * which number is which.
 */

import { CORPUS_FACTS } from "@/lib/corpus-facts";
import { loadPlayerIndex } from "@/lib/players";

import { PlayerSearch } from "./player-search";

export const revalidate = 3600;

export const metadata = {
  title: "Players — SightScreen",
  description: "Every player who has batted or bowled in the corpus, and their record.",
};

export default async function PlayersPage() {
  const players = await loadPlayerIndex();

  return (
    <main className="landing">
      <section className="band">
        <h1 className="page-title">Players</h1>
        <p className="prose soft">
          The <span className="tnum">{players.length.toLocaleString()}</span> players who have
          batted or bowled at least one delivery, of{" "}
          <span className="tnum">{CORPUS_FACTS.players.toLocaleString()}</span> named in the
          corpus. The rest appear only on a team sheet, with nothing recorded
          against them.
        </p>

        {players.length === 0 ? (
          <p className="soft notice">
            The player index is not answering right now. It is a derived table
            on the serving database, so this page has nothing to show until it
            does.
          </p>
        ) : (
          <PlayerSearch players={players} />
        )}
      </section>
    </main>
  );
}
