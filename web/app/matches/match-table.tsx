/**
 * The matches table and its filters (UI-PHASE.md §4.2).
 *
 * A table, not cards - these are rows of the same shape, and eleven
 * attributes of one match are not eleven objects.
 *
 * NEITHER VIRTUALISED NOR PAGINATED. §4.2 says "paginated or virtualised at
 * 50", which assumed a corpus-sized list. The serving database holds 107
 * matches. One `<table>` renders that without help, and adding a windowing
 * dependency for 107 rows would be the speculative flexibility CLAUDE.md §2
 * rules out. The measured render time is recorded in docs/ui-session3.md.
 *
 * FILTERING IS CLIENT-SIDE for the same reason: 107 rows are already here, so
 * a round trip per filter change would be slower and would make the page
 * dynamic for no gain.
 *
 * COMPETITION IS A COLUMN, NOT A FILTER, which deviates from §4.2. There are
 * 53 competitions across 107 matches - about two each - so the dropdown would
 * filter to a single row almost every time and would be longer than the
 * result. Format and "has predictions" are the two that actually partition
 * this set.
 */

"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { BallStrip } from "@/components/ball-strip";
import { resultLine, type MatchRow } from "@/lib/match-index";

type FormatFilter = "all" | "T20" | "ODI";

export function MatchTable({
  matches,
  sparklinesUnavailable,
}: {
  matches: MatchRow[];
  sparklinesUnavailable: boolean;
}) {
  const [format, setFormat] = useState<FormatFilter>("all");
  const [predictedOnly, setPredictedOnly] = useState(false);

  const visible = useMemo(
    () =>
      matches.filter(
        (match) =>
          (format === "all" || match.format === format) &&
          (!predictedOnly || match.marks !== null)
      ),
    [matches, format, predictedOnly]
  );

  const withPredictions = matches.filter((m) => m.marks !== null).length;

  return (
    <>
      <div className="filters">
        <fieldset>
          <legend className="visually-hidden">Format</legend>
          {(["all", "T20", "ODI"] as const).map((option) => (
            <label key={option}>
              <input
                type="radio"
                name="format"
                checked={format === option}
                onChange={() => setFormat(option)}
              />
              {option === "all" ? "All formats" : option}
            </label>
          ))}
        </fieldset>

        <label>
          <input
            type="checkbox"
            checked={predictedOnly}
            onChange={(event) => setPredictedOnly(event.target.checked)}
          />
          Replayed only
        </label>

        <p className="soft tnum filter-count">
          {visible.length} of {matches.length}
        </p>
      </div>

      {sparklinesUnavailable ? (
        <p className="soft notice">
          Win-probability strips are unavailable right now — the prediction
          query did not answer. The matches below are still correct.
        </p>
      ) : null}

      <table className="grid matches-table">
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">Competition</th>
            <th scope="col">Match</th>
            <th scope="col">Result</th>
            <th scope="col">Win probability</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((match) => (
            <tr key={match.matchId}>
              <td className="tnum nowrap">{match.date}</td>
              <td className="soft">{match.format} · {match.competition}</td>
              <td>
                <Link href={`/match/${match.matchId}`}>
                  {match.teamA ?? "Unknown"} v {match.teamB ?? "Unknown"}
                </Link>
              </td>
              <td className="tnum">{resultLine(match.result) ?? <span className="soft">—</span>}</td>
              <td className="spark-cell">
                {match.marks ? (
                  <BallStrip
                    marks={match.marks}
                    height={22}
                    defaultWidth={120}
                    interactive={false}
                    // The row states the date, the teams and the result in
                    // text. Describing the strip too would add a 235-character
                    // paragraph per row to a 107-row table.
                    decorative
                  />
                ) : (
                  // §0.2, and the one case §4.2 asked for a scorecard in.
                  // A scorecard is not buildable here: the serving database
                  // has no score, no innings total and no batting order for
                  // a match nothing has replayed.
                  <span className="soft not-replayed">Not replayed</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {visible.length === 0 ? (
        <p className="soft notice">No matches match those filters.</p>
      ) : null}

      <p className="soft footnote">
        {withPredictions} of {matches.length} have ball-by-ball predictions. The
        rest are on the serving database because something wrote a row for
        them, but were never replayed through the model.
      </p>
    </>
  );
}
