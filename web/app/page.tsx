/**
 * The landing page (UI-PHASE-2 §6). Closes the UI phase.
 *
 * ORDER, AND WHY: wordmark, hero, what this is, scorecard, four previews,
 * gaps. The scorecard is third rather than last because it is the claim the
 * rest of the page has to earn — and it is the one panel on this site that
 * leads with a null result.
 *
 * LEVELS. Prose stays level 0; the panels are the hero, the scorecard and the
 * four modules. The paragraphs are not boxed, which is the rule from step 2:
 * if it is prose, it is a panel; if it is a thing, it is a level.
 *
 * NO MODEL SPEND ON PAGE LOAD. The Ask module renders a committed exchange
 * recorded during a paid eval run, not a live call. lib/ask-example.test.ts
 * holds it to its source.
 *
 * EVERY LOADER DEGRADES. The hero falls back to a committed fixture, the
 * figures to committed counts, and each preview module renders its own
 * absence. Free-tier Supabase pauses after about a week idle — this project
 * has recorded pauses of 12 minutes, 12 minutes and 3.3 hours — and the front
 * door must not be a 500 for that window.
 */

import Link from "next/link";

import { BallStrip } from "@/components/ball-strip";
import {
  parseReport,
  score,
  withInterval,
  withSignedInterval,
  type PopulationReport,
} from "@/lib/accuracy";
import { CORPUS_FACTS } from "@/lib/corpus-facts";
import { loadHeroMatch } from "@/lib/hero-match";
import { loadLandingFigures } from "@/lib/landing-figures";
import { loadLandingPreviews } from "@/lib/landing-previews";
import { supabaseServer } from "@/lib/supabase-server";

import ASK_EXAMPLE from "@/lib/fixtures/ask-example.json";

export const revalidate = 3600;

/** The calibration report, or null. Its own failure path; the scorecard says so. */
async function loadScorecard(): Promise<PopulationReport[] | null> {
  try {
    const { data, error } = await supabaseServer()
      .from("calibration_runs")
      .select("report")
      .order("computed_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (error || !data) return null;
    const report = parseReport(data.report);
    return report ? [report.populations.backfill, report.populations.live] : null;
  } catch {
    return null;
  }
}

export default async function Home() {
  const [hero, figures, previews, populations] = await Promise.all([
    loadHeroMatch(),
    loadLandingFigures(),
    loadLandingPreviews(),
    loadScorecard(),
  ]);
  const [backfill, live] = populations ?? [undefined, undefined];
  const logistic = backfill?.vs_baselines?.logistic;
  const baseRate = backfill?.vs_baselines?.historical_base_rate;
  const hasBaselines =
    logistic && !("unavailable" in logistic) && baseRate && !("unavailable" in baseRate);

  return (
    <main className="landing">
      {/*
        THE SPLIT WORDMARK, and this is the only place it appears.
        §4.2 reserves it for the landing page because the header sits on
        --chrome where teal is 2.94:1 and crimson 3.49:1. Here it sits on
        --paper. Measured on every paper surface before shipping: teal 5.44 /
        5.71 / 5.08, crimson 6.47 / 6.79 / 6.05 — all clear 4.5:1, including
        the foot of the page wash.
      */}
      <h1 className="landing-wordmark">
        <span className="wm-sight">Sight</span>
        <span className="wm-screen">Screen</span>
      </h1>

      {/* ---------------------------------------------------------------- */}
      {/* HERO — level 2                                                    */}
      {/* ---------------------------------------------------------------- */}
      <section className="level-2 hero" aria-labelledby="hero-teams">
        <p className="hero-kicker">
          {hero.match.competition ?? "Match"}
          {hero.match.date ? ` · ${hero.match.date}` : null}
          {hero.stale ? (
            <span className="soft"> · last known good, {hero.capturedAt}</span>
          ) : null}
          {hero.match.isLive ? (
            /*
              A FACT, NOT A BADGE (§6). No pill, no border, no background —
              a crimson dot and ink text, set at the same size as the rest of
              the kicker. The dot's pulse is slow and is switched off entirely
              under prefers-reduced-motion; see globals.css.
            */
            <span className="live-now">
              <span className="live-now-dot" aria-hidden="true" />
              LIVE NOW
            </span>
          ) : null}
        </p>

        <p className="hero-teams" id="hero-teams">
          {hero.match.teamA} <span className="soft">v</span> {hero.match.teamB}
        </p>

        {hero.match.winner ? (
          <p className="hero-result">
            <span className="fig">{hero.match.winner}</span> <span className="soft">won</span>
          </p>
        ) : null}

        {hero.marks ? (
          <>
            <BallStrip
              marks={hero.marks}
              height={96}
              defaultWidth={1108}
              draw
              className="hero-strip"
            />
            <p className="prose hero-note">
              Every mark is one delivery. Its height is how much that ball
              changed who was going to win, above the line for the batting side
              and below it for the bowling side.
            </p>
          </>
        ) : (
          <p className="prose hero-note soft">
            The ball-by-ball strip for this match is not available right now.
          </p>
        )}
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* SCORECARD — level 2, and the most carefully worded thing here     */}
      {/* ---------------------------------------------------------------- */}
      <section className="level-2 scorecard" aria-labelledby="scorecard-heading">
        <h2 id="scorecard-heading">How accurate is it?</h2>

        {backfill && backfill.brier !== undefined ? (
          <>
            <p className="scorecard-headline">
              <span className="scorecard-number tnum">{score(backfill.brier)}</span>
              <span className="scorecard-unit">
                Brier score · 95% CI{" "}
                <span className="tnum">
                  [{score(backfill.brier_ci_low!)}, {score(backfill.brier_ci_high!)}]
                </span>{" "}
                · <span className="tnum">{backfill.n_matches}</span> replayed matches
              </span>
            </p>

            <p className="prose scorecard-gloss">
              A Brier score is the average squared gap between a forecast and
              what happened. Zero is perfect. Always saying &ldquo;50&#8209;50&rdquo;
              scores 0.25.
            </p>

            <p className="prose">
              <strong>Backfilled, not live.</strong> These {backfill.n_matches} matches
              were replayed through the model after the fact. Nothing on this
              line was predicted before the result was known. The live
              population has <strong className="tnum">{live?.n ?? 0} scored predictions</strong>
              {live?.unresolved ? (
                <>
                  {" "}
                  — <span className="tnum">{live.unresolved.predictions}</span> are logged
                  across <span className="tnum">{live.unresolved.matches}</span> matches and
                  cannot be scored until the match archive catches up
                </>
              ) : null}
              .
            </p>

            {hasBaselines ? (
              <div className="scorecard-baselines">
                <p className="prose">
                  <strong>Better than guessing the base rate:</strong>{" "}
                  <span className="tnum">
                    {withSignedInterval(baseRate.improvement, baseRate.ci_low, baseRate.ci_high)}
                  </span>
                  . A baseline that always predicts how often chases succeed
                  historically. The model beats it, and the interval clears zero.
                </p>
                <p className="prose">
                  <strong>Not established against a logistic baseline:</strong>{" "}
                  <span className="tnum">
                    {withSignedInterval(logistic.improvement, logistic.ci_low, logistic.ci_high)}
                  </span>
                  . Three features — required run rate, wickets in hand, balls
                  remaining. The interval includes zero, so this model is not
                  measurably better than it.
                </p>
                <p className="prose scorecard-turn">
                  That second line used to be positive. On 100 matches the margin
                  was <span className="tnum">+0.0149 [+0.0014, +0.0284]</span> — it
                  cleared zero by 0.0014.{" "}
                  <strong>
                    The model hasn&rsquo;t changed. The evidence base tripled, and a
                    marginal result didn&rsquo;t survive it.
                  </strong>
                </p>
              </div>
            ) : null}

            {backfill.n_deciles_failed !== undefined ? (
              <p className="prose scorecard-miscalibrated">
                <strong className="flag">
                  <span className="tnum">{backfill.n_deciles_failed}</span> of{" "}
                  <span className="tnum">{backfill.n_deciles_populated ?? 10}</span> probability
                  bands are miscalibrated.
                </strong>{" "}
                When the model says 70%, those bands don&rsquo;t happen 70% of the
                time, by more than sampling noise explains.
              </p>
            ) : null}

            <p className="scorecard-link">
              <Link href="/accuracy">See the full reliability diagram →</Link>
            </p>
          </>
        ) : (
          <p className="prose soft">
            The accuracy figures are not available right now — the calibration
            report did not load. <Link href="/accuracy">The accuracy page</Link>{" "}
            has the detail when it does.
          </p>
        )}
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* PREVIEW MODULES — four, each level 1, each real data              */}
      {/* ---------------------------------------------------------------- */}
      <div className="previews">
        <section className="level-1 preview" aria-labelledby="preview-matches">
          <h2 id="preview-matches">
            <Link href="/matches">Matches</Link>
          </h2>
          {previews.matches ? (
            <ul className="preview-list">
              {previews.matches.map((m) => (
                <li key={m.matchId}>
                  <Link href={`/match/${m.matchId}`}>{m.teams}</Link>
                  <span className="soft tnum preview-date">{m.date}</span>
                  {m.marks ? (
                    <BallStrip marks={m.marks} height={18} defaultWidth={120} decorative />
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="soft small">Recent fixtures are not available right now.</p>
          )}
        </section>

        <section className="level-1 preview" aria-labelledby="preview-players">
          <h2 id="preview-players">
            <Link href="/players">Players</Link>
          </h2>
          {previews.players ? (
            <ul className="preview-list">
              {previews.players.map((p) => (
                <li key={p.playerId}>
                  <Link href={`/player/${p.playerId}`}>{p.name}</Link>
                  <span className="soft tnum preview-date">
                    {p.runs.toLocaleString()} runs
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="soft small">The player index is not available right now.</p>
          )}
        </section>

        <section className="level-1 preview" aria-labelledby="preview-ask">
          <h2 id="preview-ask">
            <Link href="/ask">Ask</Link>
          </h2>
          <p className="preview-question">{ASK_EXAMPLE.question}</p>
          <p className="preview-answer small">{ASK_EXAMPLE.excerpt.replace(/\*\*/g, "")}</p>
          <p className="soft tiny">
            Asked {ASK_EXAMPLE.askedAt}. Every answer shows the SQL it ran.
          </p>
        </section>

        <section className="level-1 preview" aria-labelledby="preview-accuracy">
          <h2 id="preview-accuracy">
            <Link href="/accuracy">Accuracy</Link>
          </h2>
          <p className="preview-answer small">
            {backfill?.n_deciles_populated ?? 10} probability bands, each with a
            match-clustered interval. The page names the {backfill?.n_deciles_failed ?? 0}{" "}
            that miss, and the five matches the model got most wrong.
          </p>
          <p
            className="soft tiny"
            title={`Counted ${CORPUS_FACTS.countedAt} in the corpus the Ask tools query. The database serving this page holds only recent matches, so the figure cannot come from it.`}
          >
            <span className="tnum">{CORPUS_FACTS.deliveries.toLocaleString()}</span>{" "}
            deliveries in the corpus behind it
            {figures.anyStale ? <> · as of {CORPUS_FACTS.countedAt}</> : null}
          </p>
        </section>
      </div>

      {/* ---------------------------------------------------------------- */}
      {/* WHAT THIS IS — level 0, prose                                     */}
      {/* ---------------------------------------------------------------- */}
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
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* GAPS — level 0                                                    */}
      {/* ---------------------------------------------------------------- */}
      <section className="band">
        <p className="prose">
          There is a good deal this does not do — no first-innings predictions,
          no estimate of a player&rsquo;s current form, nothing about matches
          that have not started. <Link href="/accuracy">The accuracy page</Link>{" "}
          lists each one and why.
        </p>
      </section>
    </main>
  );
}
