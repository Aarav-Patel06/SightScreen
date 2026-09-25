/**
 * The /players search and table (UI-PHASE.md §4.4).
 *
 * NOT VIRTUALISED, which deviates from §4.4's "the table must be
 * virtualised". That instruction assumed 18,468 rows rendered at once. The
 * index holds 8,575 - the players who have actually batted or bowled - and
 * the table never renders all of them: it shows the top 50 by career runs
 * until someone types, and then the matches. Fifty rows needs no window, and
 * a windowing dependency for fifty rows is the speculative flexibility
 * CLAUDE.md §2 rules out.
 *
 * Search is client-side over the whole index, which is fetched once per
 * revalidate. A round trip per keystroke would be slower and would make the
 * page dynamic, and 8,575 rows of small columns is not enough data to be
 * worth paging.
 */

"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { searchPlayers, type PlayerRow } from "@/lib/players";

/** Shown before anyone types. Enough to look alive, few enough to scan. */
const PREVIEW = 50;

export function PlayerSearch({ players }: { players: PlayerRow[] }) {
  const [query, setQuery] = useState("");

  const { rows, searching } = useMemo(() => {
    if (query.trim().length === 0) {
      const top = [...players].sort((a, b) => b.batRuns - a.batRuns).slice(0, PREVIEW);
      return { rows: top, searching: false };
    }
    return { rows: searchPlayers(players, query).slice(0, 200), searching: true };
  }, [players, query]);

  return (
    <>
      <div className="player-search">
        <label htmlFor="player-q">Search by name</label>
        <input
          id="player-q"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Kohli, or Virat Kohli"
          autoComplete="off"
        />
        <p className="soft tnum" aria-live="polite">
          {searching
            ? `${rows.length} match${rows.length === 1 ? "" : "es"}`
            : `top ${rows.length} by runs, of ${players.length.toLocaleString()}`}
        </p>
      </div>

      {/* Level 1. The search field above stays unboxed - it is a control,
          not an object. */}
      <div className="level-1 table-panel">
      <table className="grid players-table">
        <thead>
          <tr>
            <th scope="col">Player</th>
            <th scope="col">Formats</th>
            <th scope="col" className="num">Matches</th>
            <th scope="col" className="num">Runs</th>
            <th scope="col" className="num">Wickets</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((player) => (
            <tr key={player.playerId}>
              <td>
                <Link href={`/player/${player.playerId}`}>{player.name}</Link>
              </td>
              <td className="soft">{player.formats.join(", ")}</td>
              <td className="num tnum">{player.matches.toLocaleString()}</td>
              <td className="num tnum">{player.batRuns.toLocaleString()}</td>
              <td className="num tnum">{player.bowlWickets.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      {searching && rows.length === 0 ? (
        <p className="soft notice">
          No player of that name has batted or bowled in the corpus. Players
          who only ever appeared on a team sheet are not listed — see above.
        </p>
      ) : null}
    </>
  );
}
