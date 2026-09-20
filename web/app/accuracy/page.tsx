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
  baselineVerdict,
  parseReport,
  populatedDeciles,
  score,
  withInterval,
  type PopulationReport,
} from "@/lib/accuracy";
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
            <td>{d.contains_predicted ? "" : <span className="chip chip-low">off</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Scored({ population }: { population: PopulationReport }) {
  if (population.n === 0 || population.brier === undefined) return null;
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
      <div className="panel">
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
      </div>

      <div className="panel">
        <h2>Replayed — matches the model was tuned on</h2>
        <p className="small muted">
          We re-ran {backfill?.n_matches ?? 0} finished matches through the live
          path. <strong>The model was selected using these games</strong>, so this
          is a demonstration that the pipeline works end to end, not a measure of
          how it performs on cricket it has never seen. Read it as a demo.
        </p>
        {backfill ? <Scored population={backfill} /> : null}
      </div>

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

      <p className="tiny muted">
        <Link href="/model-card">Model card — the plain-language version</Link> ·{" "}
        <Link href="/about/model">What the model can&apos;t do</Link> ·{" "}
        <Link href="/">Back</Link> · analytics, not betting advice.
      </p>
    </main>
  );
}
