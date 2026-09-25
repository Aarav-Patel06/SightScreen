/**
 * The accuracy page (SPEC.md §8.5, §9.3, §12.2).
 *
 * "No cricket app does this. Ship it." - §8.5.
 *
 * The page renders the daily monitor's report rather than computing
 * anything. The reliability figures are match-clustered bootstraps and
 * eval/metrics.py already implements them; a second implementation in
 * TypeScript would be both slow per request and the kind of divergence this
 * project keeps getting bitten by.
 *
 * The thing this page has to get right is not the chart. It is that a reader
 * understands, without being told twice, that the big sample is the one that
 * means least. Every section separates:
 *
 *   REPLAYED - finished matches re-run after the fact. These are the §9.1
 *              TEST split, which Phase 1 used to SELECT the shipped
 *              calibrator, so there is no held-out data behind them at all.
 *   LIVE     - predicted before the result existed. The only population
 *              whose accuracy is an honest estimate of anything.
 */

import Link from "next/link";

import {
  LOGISTIC_PRIOR,
  baselineVerdict,
  compareSegments,
  parseReport,
  populatedDeciles,
  score,
  withInterval,
  type BaselineComparison,
  type PopulationReport,
  type SegmentComparison,
} from "@/lib/accuracy";
import { HATCH_PITCH_PX } from "@/lib/ball-strip";
import { supabaseServer } from "@/lib/supabase-server";

import { ReliabilityDiagram } from "./charts";

export const dynamic = "force-dynamic";

async function loadReport() {
  const supabase = supabaseServer();
  const { data, error } = await supabase
    .from("calibration_runs")
    .select("run_id, computed_at, model_version, report")
    .order("computed_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error || !data) return null;
  const report = parseReport(data.report);
  return report === null ? null : { report, computedAt: data.computed_at };
}

/**
 * The mark for a decile whose 95% interval does not contain its own predicted
 * mean - i.e. the model is measurably miscalibrated in that band.
 *
 * Hatched rather than solid, per §1.3's evidence-class vocabulary, and in
 * --flag because this is the page's one genuinely alarming cell. aria-hidden
 * because the word "off" beside it says the same thing; a screen reader
 * gains nothing from "diagonal hatch, off".
 */
/**
 * What the system does not attempt (UI-PHASE.md §4.1 item 4, relocated).
 *
 * This lived on the landing page, where four absences stood between a
 * visitor and the product and read as caveats. Here it reads as the same
 * discipline applied twice: the sections above say where the model is
 * measurably wrong, and this says what it does not try to do at all. Both
 * are the same refusal to let silence imply capability.
 *
 * AFTER the measurements, deliberately. A page whose first content is a list
 * of absences has buried the thing it exists to show.
 */
const NOT_ATTEMPTED = [
  {
    title: "First-innings predictions",
    body: "The model reads a chase. It needs a target to work backwards from, so the first half of every match is blank.",
  },
  {
    title: "Player form",
    body: "What a player has done is recorded here. What they can do now is not — those are different questions, and only the first has an answer.",
  },
  {
    title: "Upcoming matches",
    body: "No fixtures are loaded and no pre-match model exists. A page that resembled predictions without being them would be worse than nothing.",
  },
  {
    title: "Live player impact",
    body: "Which batter or bowler is actually swinging the match, ball by ball. The win-probability model supports it; the attribution layer on top isn't written.",
  },
];

function MissSwatch() {
  return (
    <svg width={12} height={12} aria-hidden="true" className="band-swatch">
      <defs>
        <pattern
          id="band-off-hatch"
          width={HATCH_PITCH_PX}
          height={HATCH_PITCH_PX}
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <rect width={HATCH_PITCH_PX / 2} height={HATCH_PITCH_PX} fill="var(--flag)" />
        </pattern>
      </defs>
      <rect
        x={0.5}
        y={0.5}
        width={11}
        height={11}
        fill="url(#band-off-hatch)"
        stroke="var(--flag)"
      />
    </svg>
  );
}

function Deciles({ population }: { population: PopulationReport }) {
  const deciles = populatedDeciles(population.reliability);
  if (deciles.length === 0) return null;
  return (
    <table className="grid">
      <thead>
        <tr>
          <th>predicted</th>
          <th>balls</th>
          <th>matches</th>
          <th>observed</th>
          <th>95% CI</th>
          <th> </th>
        </tr>
      </thead>
      <tbody>
        {deciles.map((d) => (
          <tr key={d.bin_low} className={d.n_matches < 30 ? "thin" : undefined}>
            <td>{d.mean_predicted!.toFixed(3)}</td>
            <td>{d.n.toLocaleString()}</td>
            {/* The number that matters. §9.3: balls within a match are
                correlated, so 2,377 balls can be 47 observations. */}
            <td>{d.n_matches}</td>
            <td>{d.observed_rate!.toFixed(3)}</td>
            <td className="tiny">
              [{d.ci_low!.toFixed(3)}, {d.ci_high!.toFixed(3)}]
            </td>
            <td>
              {d.contains_predicted ? (
                <span className="visually-hidden">calibrated</span>
              ) : (
                // §4.5 and §1.3: the band whose clustered interval excludes
                // its own predicted mean. --flag, and a hatched swatch beside
                // the word - the colour alone would say nothing in greyscale
                // and nothing to a reader who cannot separate it from the
                // text, and this is the one cell on the page that reports a
                // failure.
                <span className="band-off">
                  <MissSwatch />
                  off
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Narrow away the `{ unavailable }` arm so the JSX can read the fields. */
function isLogistic(
  value: BaselineComparison | { unavailable: string } | undefined
): value is BaselineComparison {
  return value !== undefined && !("unavailable" in value);
}

const SEGMENT_LABELS: Record<string, string> = {
  full_member: "Full Member internationals",
  other: "Franchise and associate",
};

function Scored({ population }: { population: PopulationReport }) {
  if (population.n === 0 || population.brier === undefined) return null;

  // The segment split is reported because it is a NULL result. See
  // eval/calibration_monitor.vs_baselines_by_segment for the evidence: the
  // obvious reading of the pooled fall - that the model's edge is a franchise
  // effect - was tested and does not hold, and the premise behind it (narrower
  // Elo spreads between Full Members) is contradicted by the corpus.
  const raw = population.vs_baselines_by_segment;
  const segments =
    raw && !("unavailable" in raw)
      ? (Object.entries(raw).filter(
          (entry): entry is [string, SegmentComparison] => !("unavailable" in entry[1])
        ).map(([key, value]) => [SEGMENT_LABELS[key] ?? key, value] as const))
      : null;

  const pair = segments?.length === 2 ? compareSegments(segments[0][1].logistic, segments[1][1].logistic) : null;
  const segmentNote = !pair
    ? "Segments are scored on the same model and the same baselines."
    : pair.overlap && !pair.eitherSignificant
      ? "No detectable difference between the segments: the two intervals overlap and neither excludes zero. " +
        "This is reported to answer the obvious question rather than to claim a split — the model's edge over " +
        "the logistic baseline is not established in either kind of cricket, not weak in one and strong in the other."
      : pair.overlap
        ? "The intervals overlap, so any difference between the segments is smaller than this much data can resolve."
        : "The intervals do not overlap, which does indicate a real difference between the segments.";

  return (
    <>
      <p className="small">
        Brier score{" "}
        <strong>{withInterval(population.brier, population.brier_ci_low!, population.brier_ci_high!)}</strong>
        <span className="muted"> over {population.n_matches} matches</span>
      </p>
      <p className="tiny muted">
        Lower is better. The interval is a 95% bootstrap over <em>matches</em>, not
        balls — balls in one match are not independent evidence, so the effective
        sample here is {population.n_matches}, not {population.n.toLocaleString()}.
      </p>

      <h3>Is a stated 70% chance really 70%?</h3>
      <p className="tiny muted">
        {population.n_deciles_failed} of {population.n_deciles_populated} bands are
        off — the observed rate&apos;s interval does not contain what was predicted.
        That is disclosed debt, not a rendering bug.
      </p>
      <ReliabilityDiagram deciles={population.reliability} />
      <Deciles population={population} />

      {population.by_phase && (
        <>
          <h3>By phase</h3>
          <table className="grid">
            <tbody>
              {["powerplay", "middle", "death"].map((phase) => {
                const bucket = population.by_phase?.[phase];
                if (!bucket) return null;
                return (
                  <tr key={phase}>
                    <td>{phase}</td>
                    <td>{score(bucket.brier)}</td>
                    <td className="tiny">
                      [{score(bucket.ci_low)}, {score(bucket.ci_high)}]
                    </td>
                    <td className="tiny muted">{bucket.n_matches} matches</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}

      {population.vs_baselines && (
        <>
          <h3>Against the baselines</h3>
          <ul className="small">
            {Object.entries(population.vs_baselines).map(([name, comparison]) => (
              <li key={name}>
                <strong>{name.replace(/_/g, " ")}</strong>: {baselineVerdict(comparison)}
              </li>
            ))}
          </ul>
          <p className="tiny muted">
            Both baselines are scored on exactly the same matches as the model, so
            this is a like-for-like comparison rather than two numbers from
            different samples placed side by side.
          </p>
          {isLogistic(population.vs_baselines.logistic) &&
            !population.vs_baselines.logistic.model_is_better && (
              <p className="tiny muted">
                This was {withInterval(
                  LOGISTIC_PRIOR.improvement,
                  LOGISTIC_PRIOR.ciLow,
                  LOGISTIC_PRIOR.ciHigh
                )}{" "}
                over {LOGISTIC_PRIOR.nMatches} matches on {LOGISTIC_PRIOR.computedAt}.
                It cleared zero by {score(LOGISTIC_PRIOR.ciLow)} and did not survive a
                larger sample. That is what a marginal result looks like when more
                data arrives — not the model getting worse, which is unchanged.
              </p>
            )}
          {segments && (
            <>
              <h4>By segment</h4>
              <table className="small">
                <thead>
                  <tr>
                    <th scope="col">Segment</th>
                    <th scope="col" className="tnum">Matches</th>
                    <th scope="col">vs the logistic baseline</th>
                  </tr>
                </thead>
                <tbody>
                  {segments.map(([label, segment]) => (
                    <tr key={label}>
                      <th scope="row">{label}</th>
                      <td className="tnum">{segment.n_matches}</td>
                      <td>{baselineVerdict(segment.logistic ?? { unavailable: "not computed" })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="tiny muted">
                {segmentNote}
              </p>
            </>
          )}
        </>
      )}

      {population.biggest_misses && population.biggest_misses.length > 0 && (
        <>
          <h3>Biggest misses</h3>
          <ul className="small">
            {population.biggest_misses.map((miss) => (
              <li key={`${miss.match_id}-${miss.over}`}>
                <Link href={`/match/${miss.match_id}`}>
                  {miss.competition}, {miss.match_date}
                </Link>{" "}
                <span className="muted">
                  over {miss.over}: said {Math.round(miss.predicted * 100)}%, the
                  chase {miss.actual_won ? "won" : "lost"}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

export default async function AccuracyPage() {
  const loaded = await loadReport();

  if (loaded === null) {
    return (
      <main>
        <div className="panel">
          <h1>How accurate is this model?</h1>
          <p className="small muted">
            No calibration run has been recorded yet. The daily monitor writes one
            at 03:00 UTC; until then there is nothing to show, which is better than
            showing something computed on the spot.
          </p>
        </div>
      </main>
    );
  }

  const { report, computedAt } = loaded;
  const backfill = report.populations.backfill;
  const live = report.populations.live;

  return (
    <main>
      <div className="panel">
        <h1>How accurate is this model?</h1>
        <p className="small muted">
          Every number here is computed from predictions this system actually
          logged — not from a spreadsheet, and not from the numbers we hoped for.
          Model <code>{report.model_version}</code>, measured{" "}
          {new Date(computedAt).toISOString().slice(0, 16).replace("T", " ")} UTC.
        </p>
      </div>

      {/* LIVE FIRST, deliberately. It is the smaller number and the honest
          one, and putting the flattering sample at the top would be the whole
          problem this page exists to avoid. */}
      {/* §4.5: the distinction becomes structural. These are not two panels
          of equal weight - one is the measurement and the other is a
          demonstration that the pipeline runs, and the layout now says so
          before the words do. */}
      <section className="level-1 accuracy-live">
        <p className="section-kicker">The measurement</p>
        <h2>Live — predicted before anyone knew the result</h2>
        {live && live.n > 0 ? (
          <Scored population={live} />
        ) : (
          <>
            <p className="small">
              <strong>
                {live?.unresolved.predictions ?? 0} predictions
              </strong>{" "}
              <span className="muted">
                across {live?.unresolved.matches ?? 0} live{" "}
                {live?.unresolved.matches === 1 ? "match" : "matches"}, none scored
                yet
              </span>
            </p>
            <p className="small muted">
              {live?.reason_unscored ??
                "Nothing has been scored yet."}{" "}
              Until then there is no accuracy figure here, and inventing one from
              the replayed sample below would be measuring a different thing.
            </p>
            <p className="tiny muted">
              This is the only section that will ever be a real estimate of how
              good the model is. It fills in on its own.
            </p>
          </>
        )}
      </section>

      <section className="level-1 accuracy-replayed">
        <p className="section-kicker">Not a measurement</p>
        <h2>Replayed — matches the model was tuned on</h2>
        <p className="small muted">
          We re-ran {backfill?.n_matches ?? 0} finished matches through the live
          path. <strong>The model was selected using these games</strong>, so this
          is a demonstration that the pipeline works end to end, not a measure of
          how it performs on cricket it has never seen. Read it as a demo.
        </p>
        {backfill ? <Scored population={backfill} /> : null}
      </section>

      <div className="panel">
        <h2>What the monitor decided</h2>
        <p className="small">
          {backfill?.refit?.reason ?? "No refit decision recorded."}
        </p>
        <p className="tiny muted">
          A recalibration has to earn its place: it is promoted only if it beats
          doing nothing by a margin whose confidence interval excludes zero. A run
          that reports honestly and changes nothing is the job working.
        </p>
      </div>

      {/* After the measurements, not before them. */}
      <section className="panel not-attempted">
        <p className="section-kicker">Not measured, because not attempted</p>
        <h2>What it doesn&rsquo;t do</h2>
        <p className="small muted">
          The sections above are where the model is measurably wrong. This is
          what it does not try to do at all — the same refusal to let silence
          imply capability.
        </p>
        <dl className="not-built">
          {NOT_ATTEMPTED.map((item) => (
            <div key={item.title}>
              <dt>{item.title}</dt>
              <dd>{item.body}</dd>
            </div>
          ))}
        </dl>
      </section>

      <p className="page-links">
        <Link href="/model-card">Model card — the plain-language version</Link> ·{" "}
        <Link href="/about/model">What the model can&apos;t do</Link> ·{" "}
        <Link href="/">Back</Link> · analytics, not betting advice.
      </p>
    </main>
  );
}
