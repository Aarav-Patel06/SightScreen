/**
 * The honesty note (Phase 2 session 5, Decision 6; SPEC.md 12.2).
 *
 * §12.2's stance is that a model which shows what it does not know is more
 * trustworthy than one that does not. Phase 1 shipped uncalibrated with 4 of
 * 10 deciles failing the clustered check, and that debt is disclosed here
 * rather than waiting for Phase 3's accuracy page.
 *
 * The numbers are read from model_versions rather than hardcoded, so they
 * cannot drift from the database - which also surfaces the KNOWN TRAIN/SERVE
 * SKEW carried in `notes`. That column has no anon policy, so this is a
 * server component using the secret key.
 */

import Link from "next/link";

import { supabaseServer } from "@/lib/supabase-server";

export const dynamic = "force-dynamic";

export default async function ModelPage() {
  const supabase = supabaseServer();
  const { data } = await supabase
    .from("model_versions")
    .select("model_version, trained_at, train_end_date, test_brier, test_log_loss, notes")
    .eq("is_active", true)
    .order("trained_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  return (
    <main>
      <div className="panel">
        <h1>How good is this model?</h1>
        <p className="small muted">
          Short answer: decent at ranking, imperfectly calibrated, and honest about
          which.
        </p>
      </div>

      <div className="panel">
        <h2>Measured accuracy</h2>
        {data === null ? (
          <p className="small muted">No active model registered.</p>
        ) : (
          <ul className="small">
            <li>
              Version <code>{data.model_version}</code>, trained on matches up to{" "}
              {data.train_end_date}.
            </li>
            <li>
              Test Brier score <strong>{Number(data.test_brier).toFixed(4)}</strong> (lower
              is better; always predicting the base rate scores about 0.25).
            </li>
            <li>
              Test log loss <strong>{Number(data.test_log_loss).toFixed(4)}</strong>.
            </li>
          </ul>
        )}
      </div>

      <div className="panel">
        <h2>What it does not know</h2>
        <ul className="small">
          <li>
            <strong>It ships uncalibrated.</strong> Four calibration methods were
            tried and none beat the identity function on a held-out selection split,
            so none was applied. Fitting one anyway would have been a change with no
            evidence behind it.
          </li>
          <li>
            <strong>4 of 10 test deciles fail the calibration check.</strong> In those
            bands the stated probability and the observed frequency differ by more
            than sampling noise explains. This is disclosed debt carried into Phase 3,
            not a solved problem.
          </li>
          <li>
            <strong>There is no per-prediction uncertainty.</strong> Nothing here
            produces an interval for a single number, so none is shown. A band derived
            from the aggregate Brier would be identical at ball 1 and ball 119, which
            would look principled and mean nothing. The phase label on the win
            probability is the honest substitute until a calibration curve exists to
            derive a real one from.
          </li>
          <li>
            <strong>Second innings only.</strong> First-innings score projection is a
            later phase, so a chase is the only thing this predicts.
          </li>
        </ul>
      </div>

      {data?.notes && (
        <div className="panel">
          <h2>Known issues with this version</h2>
          <p className="small muted">{data.notes}</p>
        </div>
      )}

      <div className="panel">
        <h2>Latency</h2>
        <p className="small muted">
          The page says &ldquo;updates every 15s · provider lag not published&rdquo;
          rather than a figure like &ldquo;42s behind live&rdquo;, because the data
          provider publishes no per-ball timestamp. The delay between a ball being
          bowled and the provider publishing it cannot be measured from here, so
          stating a number would be inventing one. Only our own polling interval is
          known.
        </p>
      </div>

      <p className="tiny muted">
        <Link href="/">Back</Link> · analytics, not betting advice.
      </p>
    </main>
  );
}
