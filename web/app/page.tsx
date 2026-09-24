/**
 * The front door (UI-PHASE.md §4.1).
 *
 * ISR rather than force-dynamic, and that is the load-bearing decision on
 * this page. Free-tier Supabase pauses after about a week idle - this project
 * has recorded pauses of 12 minutes, 12 minutes and 3.3 hours - and a
 * dynamically rendered landing page is a 500 for that entire window, on the
 * one URL a stranger is most likely to open. With `revalidate`, Next serves
 * the last good HTML and regenerates in the background, so a paused database
 * is invisible. lib/landing-figures.ts then handles the cold case, where
 * there is no last good render, by falling back to committed counts that
 * carry the date they were taken.
 *
 * The hero never queries anything. It is the one element that must render, so
 * it reads a committed fixture: match 8532, in which Sharjah Warriorz chased
 * 135 and won off the final delivery. A live match replaces it when one
 * exists, but the floor is a file on disk.
 */

import Link from "next/link";

import { BallStrip } from "@/components/ball-strip";
import { toMarks } from "@/lib/ball-strip";
import { CORPUS_FACTS } from "@/lib/corpus-facts";
import { loadLandingFigures } from "@/lib/landing-figures";
import { parsePrediction, type WinProbPrediction } from "@/lib/prediction";

import fixture from "@/lib/fixtures/innings-8532.json";

/** One regeneration an hour. These figures change daily at most. */
export const revalidate = 3600;

const heroPredictions: WinProbPrediction[] = fixture.rows
  .map((row) => parsePrediction({ ...row, created_at: "" } as never))
  .filter((p): p is WinProbPrediction => p !== null);

const heroMarks = toMarks(heroPredictions);
const heroFinal = heroPredictions[heroPredictions.length - 1];

export default async function Home() {
  const figures = await loadLandingFigures();

  return (
    <main className="landing">
      <section className="hero">
        <p className="soft hero-identity">
          {fixture.competition} · {fixture.match_date}
        </p>
        <h1 className="hero-teams">
          {fixture.batting_first} v {fixture.chasing}
        </h1>
        <p className="hero-state">
          <span className="fig">{fixture.chasing}</span>{" "}
          <span className="soft">chased</span> <span className="fig">{fixture.target}</span>{" "}
          <span className="soft">and won off the final ball ·</span>{" "}
          <span className="fig">{heroFinal.score}</span>
          <span className="soft">/</span>
          <span className="fig">{heroFinal.wickets}</span>
        </p>

        <BallStrip marks={heroMarks} height={96} defaultWidth={1108} draw className="hero-strip" />

        <p className="prose hero-note">
          Every mark is one delivery. Its height is how much that ball changed
          who was going to win, above the line for the batting side and below
          it for the bowling side.
        </p>
      </section>

      <section className="band">
        <div className="prose">
          <p>
            SightScreen predicts who wins a Twenty20 or one-day chase, updated
            after every ball, from the match situation and nothing else. It
            covers the second innings only — see below for why.
          </p>
          <p>
            What makes it different is that it publishes its own{" "}
            <Link href="/accuracy">track record</Link>, including the
            probability bands where it is miscalibrated and the matches it got
            most wrong. The <Link href="/model-card">model card</Link> says what
            it was trained on and where it should not be trusted.
          </p>
          {/* A fact, at reading size. It was set as small print, which made
              the two most concrete numbers on the page look like a
              disclaimer. The staleness note moves into a title attribute:
              still true, still available, no longer competing with the
              figure it qualifies. */}
          <p
            className="corpus-line"
            title={
              figures.anyStale
                ? `Both counted ${CORPUS_FACTS.countedAt}. The serving database is not answering right now, so these may have moved since.`
                : undefined
            }
          >
            Scored on{" "}
            <span className="tnum">{figures.matchesWithPredictions.value.toLocaleString()}</span>{" "}
            matches, across a corpus of{" "}
            <span className="tnum">{figures.players.value.toLocaleString()}</span> players.
            {figures.anyStale ? <span className="soft"> As of {CORPUS_FACTS.countedAt}.</span> : null}
          </p>
        </div>
      </section>

      <section className="band">
        <h2>Ask it something</h2>
        <div className="entry">
          <p className="entry-figure fig">{CORPUS_FACTS.deliveries.toLocaleString()}</p>
          <div className="prose">
            <p>
              <Link href="/ask">Query the corpus in plain language</Link>. Every
              answer shows the SQL it ran, so you can see it came from the data
              rather than from the model&rsquo;s memory.
            </p>
            {/* The distinction is real - this number is counted in the
                corpus the Ask tools reach, not in the database serving this
                page - but as running text it was three lines of caveat under
                a one-line claim. Short clause, full explanation on hover and
                to a screen reader via the abbr title. */}
            <p className="soft entry-provenance">
              <abbr
                title={`Counted ${CORPUS_FACTS.countedAt} in the corpus the Ask tools query. The database serving this page holds only recent matches, so the figure cannot come from it.`}
              >
                Deliveries in the corpus Ask reaches
              </abbr>
              , not in this page&rsquo;s database.
            </p>
          </div>
        </div>
      </section>

      {/* The list of what is not built moved to /accuracy.
          On the landing page it read as four caveats standing between a
          visitor and the product. Beside the track record it reads as the
          same discipline applied twice: here is what it gets wrong, and here
          is what it does not attempt. One sentence stays, because a front
          door that never mentions its limits is the thing the list existed
          to avoid. */}
      <section className="band">
        <p className="prose">
          There is a good deal this does not do — no first-innings
          predictions, no estimate of a player&rsquo;s current form, nothing
          about matches that have not started.{" "}
          <Link href="/accuracy">The accuracy page</Link> lists each one and
          why.
        </p>
      </section>
    </main>
  );
}
