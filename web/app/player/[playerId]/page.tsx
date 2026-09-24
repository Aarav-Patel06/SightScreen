/**
 * /player/[playerId] — the descriptive record (UI-PHASE.md §4.4).
 *
 * THE REFUSAL AT THE TOP IS THE POINT OF THIS PAGE. Where a form curve would
 * go, it says that the system records what a player has done and does not
 * estimate what they can do now, and that those are different questions.
 * That is the same refusal `get_player_form` makes in the agent, rendered in
 * the interface — and §4.4 calls it the single clearest demonstration of the
 * product's stance a visitor will see. It is not a placeholder for a chart.
 *
 * Every rate carries its sample size, and a line thinner than
 * THIN_INNINGS renders hollow rather than as a precise-looking figure
 * (§12.2, §1.3).
 *
 * `batting_hand`, `bowling_style` and `dob` are omitted entirely rather than
 * rendered empty: they are NULL for all 18,468 players and Cricsheet cannot
 * supply them, so a row saying "Batting hand: —" would imply the data is
 * merely missing for this player rather than absent everywhere.
 */

import Link from "next/link";
import { notFound } from "next/navigation";

import {
  THIN_INNINGS,
  battingAverage,
  bowlingAverage,
  economy,
  isThin,
  loadPlayer,
  strikeRate,
  type CareerLine,
} from "@/lib/players";

export const revalidate = 3600;

const PHASE_ORDER = ["powerplay", "middle", "death"];

function fmt(value: number | null, digits = 2): string {
  return value === null ? "—" : value.toFixed(digits);
}

/** A rate, or a hollow cell when the sample is too thin to quote one. */
function Rate({ value, line, digits = 2 }: { value: number | null; line: CareerLine; digits?: number }) {
  if (value === null) return <span className="soft">—</span>;
  if (isThin(line)) {
    return (
      <span
        className="hollow-figure"
        title={`${line.batInnings} innings — below the ${THIN_INNINGS}-innings threshold`}
      >
        {value.toFixed(digits)}
      </span>
    );
  }
  return <span className="tnum">{value.toFixed(digits)}</span>;
}

export default async function PlayerPage({
  params,
}: {
  params: Promise<{ playerId: string }>;
}) {
  const { playerId } = await params;
  const id = Number(playerId);
  if (!Number.isFinite(id)) notFound();

  const player = await loadPlayer(id);
  if (player === null) notFound();

  const careers = player.lines
    .filter((line) => line.phase === "all")
    .sort((a, b) => b.batRuns - a.batRuns);

  return (
    <main className="landing">
      <section className="band">
        <h1 className="page-title">{player.name}</h1>
        <p className="soft tnum">
          {player.matches.toLocaleString()} matches in the corpus
        </p>

        {/* §4.4: where a form curve would go. */}
        <div className="refusal prose">
          <p>
            <strong>This is a record, not a form estimate.</strong> Everything
            below is what {player.name} has done. Nothing here estimates what
            they can do now — the system does not model current ability, and
            the two are different questions.
          </p>
          <p className="soft">
            An average over a career and an estimate of the next innings are
            not the same number, and presenting the first as the second is the
            most common way cricket statistics mislead.{" "}
            <Link href="/model-card">The model card</Link> says what is
            modelled and what is not.
          </p>
        </div>
      </section>

      {careers.map((career) => {
        const splits = player.lines
          .filter((line) => line.format === career.format && line.phase !== "all")
          .sort((a, b) => PHASE_ORDER.indexOf(a.phase) - PHASE_ORDER.indexOf(b.phase));

        return (
          <section className="band" key={career.format}>
            <h2>{career.format}</h2>

            {career.batInnings > 0 ? (
              <>
                <h3>Batting</h3>
                <table className="grid player-table">
                  <thead>
                    <tr>
                      <th scope="col">Phase</th>
                      <th scope="col" className="num">Innings</th>
                      <th scope="col" className="num">Runs</th>
                      <th scope="col" className="num">Balls</th>
                      <th scope="col" className="num">Average</th>
                      <th scope="col" className="num">Strike rate</th>
                      <th scope="col" className="num">4s / 6s</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[career, ...splits]
                      .filter((line) => line.batInnings > 0)
                      .map((line) => (
                        <tr key={line.phase} className={line.phase === "all" ? "career-row" : undefined}>
                          <td>{line.phase === "all" ? "Career" : line.phase}</td>
                          <td className="num tnum">{line.batInnings.toLocaleString()}</td>
                          <td className="num tnum">{line.batRuns.toLocaleString()}</td>
                          <td className="num tnum">{line.batBalls.toLocaleString()}</td>
                          <td className="num">
                            <Rate value={battingAverage(line)} line={line} digits={1} />
                          </td>
                          <td className="num">
                            <Rate value={strikeRate(line)} line={line} digits={1} />
                          </td>
                          <td className="num tnum">
                            {line.batFours} / {line.batSixes}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                {/* An average with no dismissals is undefined, not infinite.
                    Said once per table rather than in every cell. */}
                {career.batOuts === 0 ? (
                  <p className="soft footnote">
                    Never dismissed in {career.batInnings} innings, so no average
                    can be computed.
                  </p>
                ) : null}
              </>
            ) : null}

            {career.bowlBalls > 0 ? (
              <>
                <h3>Bowling</h3>
                <table className="grid player-table">
                  <thead>
                    <tr>
                      <th scope="col">Phase</th>
                      <th scope="col" className="num">Balls</th>
                      <th scope="col" className="num">Runs</th>
                      <th scope="col" className="num">Wickets</th>
                      <th scope="col" className="num">Average</th>
                      <th scope="col" className="num">Economy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[career, ...splits]
                      .filter((line) => line.bowlBalls > 0)
                      .map((line) => (
                        <tr key={line.phase} className={line.phase === "all" ? "career-row" : undefined}>
                          <td>{line.phase === "all" ? "Career" : line.phase}</td>
                          <td className="num tnum">{line.bowlBalls.toLocaleString()}</td>
                          <td className="num tnum">{line.bowlRuns.toLocaleString()}</td>
                          <td className="num tnum">{line.bowlWickets.toLocaleString()}</td>
                          <td className="num tnum">{fmt(bowlingAverage(line), 1)}</td>
                          <td className="num tnum">{fmt(economy(line), 2)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                <p className="soft footnote">
                  Wickets are bowler-credited only — a run out is not the
                  bowler&rsquo;s. Runs conceded exclude byes and leg-byes.
                </p>
              </>
            ) : null}
          </section>
        );
      })}

      <section className="band">
        <h2>Not built yet</h2>
        <dl className="absent-items">
          <div>
            <dt>Current form</dt>
            <dd>
              An estimate of present ability, as distinct from the record
              above. Needs <code>player_state</code>, which has no rows until
              Phase 5.
            </dd>
          </div>
          <div>
            <dt>By venue, and recent innings</dt>
            <dd>
              Both are in the corpus and neither is in the summary this page
              reads. They are a rebuild away, not a model away.
            </dd>
          </div>
          <div>
            <dt>Batting hand, bowling style, date of birth</dt>
            <dd>
              Omitted rather than shown empty. They are NULL for all 18,468
              players and Cricsheet does not publish them, so this is an
              absence in the source rather than a gap for this player.
            </dd>
          </div>
        </dl>
      </section>
    </main>
  );
}
