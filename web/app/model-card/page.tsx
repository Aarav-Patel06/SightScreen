/**
 * The model card (SPEC.md §14).
 *
 * "Write a one-page model card in plain language - what it predicts, what it
 * explicitly cannot (see §9.4), how confident it is at each phase, and how it
 * corrects itself. Link it from the accuracy page. Bring it to interviews."
 *
 * Written for someone who has never heard of a Brier score. That is the
 * constraint, and it rules out most of the vocabulary the rest of this
 * project uses. Where a number is genuinely needed it is explained in the
 * same sentence, and anything that could drift is left to /accuracy rather
 * than restated here - a card that disagrees with the measurements is worse
 * than a card without numbers.
 *
 * Deliberately static. /accuracy is the live numbers; this is the standing
 * explanation of what the system is and is not, and it should not change
 * when a nightly job runs.
 */

import Link from "next/link";

export const metadata = {
  title: "Model card — SightScreen",
  description: "What this model predicts, what it cannot, and how it checks itself.",
};

export default function ModelCardPage() {
  return (
    <main>
      <div className="panel">
        <h1>Model card</h1>
        <p className="small muted">
          One page on what this system predicts, what it deliberately does not,
          and how it checks its own work. No background needed.
        </p>
      </div>

      <div className="panel">
        <h2>What it does</h2>
        <p className="small">
          It watches a run chase — the second half of a limited-overs cricket
          match, where one team has a target and a fixed number of balls to reach
          it — and after every ball says how likely the chasing team is to win.
        </p>
        <p className="small">
          It learned from about 3.8 million deliveries of recorded cricket. It
          knows the match situation (runs needed, balls left, wickets in hand),
          how the two teams have performed historically, and how the ground itself
          usually plays — some grounds favour chasing, some do not.
        </p>
        <p className="small muted">
          It does not watch video, know about injuries, read team news, or have
          any idea what the weather is doing.
        </p>
      </div>

      <div className="panel">
        <h2>What it cannot do</h2>
        <p className="small">
          <strong>It cannot tell you how many runs a particular batter will
          score.</strong> Not because it is not good enough yet — because that
          number is close to unpredictable in principle. A good batter who
          averages 40 still gets out for 3 regularly. If you tried to predict
          individual scores you would be right almost none of the time, and any
          system claiming otherwise is describing the past, not the future.
        </p>
        <p className="small">
          What <em>is</em> predictable is the distribution: of all the innings
          where we say &ldquo;30% chance of passing fifty&rdquo;, about 30% should
          pass fifty. That is a different and more honest question, and it is the
          one a later version will answer.
        </p>
        <p className="small">
          <strong>It only covers the chase.</strong> It says nothing during the
          first innings, because predicting a final score is a separate problem
          that has not been built yet. And it has no opinion before the match
          starts.
        </p>
      </div>

      <div className="panel">
        <h2>How sure it is, and when</h2>
        <p className="small">
          Confidence is not constant through an innings, so the display says which
          phase you are looking at:
        </p>
        <ul className="small">
          <li>
            <strong>Early (first six overs)</strong> — marked low confidence. Almost
            anything can still happen, and a number here is closer to a starting
            guess than a read on the game.
          </li>
          <li>
            <strong>Middle</strong> — moderate. The shape of the chase is forming.
          </li>
          <li>
            <strong>Late (closing overs)</strong> — most reliable. With few balls
            left the arithmetic does most of the work, and this is where the model
            is measurably at its best.
          </li>
        </ul>
        <p className="small muted">
          A system that prints 62% in the same font at ball one and ball 119 is
          hiding something. This one labels the difference.
        </p>
      </div>

      <div className="panel">
        <h2>What it does not claim</h2>
        <p className="small">
          There is no &ldquo;plus or minus&rdquo; on any single prediction, and one
          is not invented. Nothing in the system can currently produce a
          trustworthy margin of error for an individual number, and a band made up
          from the overall average would look scientific while meaning nothing — it
          would be identical at the first ball and the last.
        </p>
        <p className="small">
          So instead of a fake margin you get the phase label above, and a page of
          real measurements.
        </p>
      </div>

      <div className="panel">
        <h2>How it checks itself</h2>
        <p className="small">
          Every prediction is written down at the moment it is made, before the
          result is known. When the match finishes, each one is scored against what
          actually happened. Nothing is graded after the fact by a human deciding
          what it meant.
        </p>
        <p className="small">
          The basic test: <strong>of all the times it said 70%, did about 70% of
          them happen?</strong> Run that over every band from 0% to 100% and you
          find out whether the numbers mean what they say.
        </p>
        <p className="small">
          <Link href="/accuracy">The results are published</Link>, including the
          bands where it is currently wrong. It is off in four of ten bands today.
          That is disclosed on purpose — a system that only showed its good results
          would not be worth checking.
        </p>
      </div>

      <div className="panel">
        <h2>How it corrects itself — and why it usually does nothing</h2>
        <p className="small">
          A job runs every night. It re-scores everything, publishes the results,
          and then asks whether adjusting the model&apos;s numbers would make them
          more truthful.
        </p>
        <p className="small">
          <strong>Almost always, the answer is no, and it changes nothing.</strong>{" "}
          That is the job working, not failing. An adjustment is only applied if it
          beats leaving things alone by a margin large enough that it cannot be
          explained by luck — and it is tested against matches it was not tuned on,
          because anything can be made to look good on the data used to build it.
        </p>
        <p className="small">
          Four different correction methods were tried during development. None
          beat doing nothing. So nothing was applied, and that result was written
          down rather than quietly retried until something won.
        </p>
        <p className="small muted">
          The nightly job also refuses to attempt a correction when there is too
          little recent data to tell a real improvement from noise, and says so
          plainly instead of producing a confident-looking adjustment.
        </p>
      </div>

      <div className="panel">
        <h2>The honest summary</h2>
        <ul className="small">
          <li>Better than the simple benchmarks it is measured against — by a margin that holds up statistically.</li>
          <li>Most reliable late in a chase; treat early numbers loosely.</li>
          <li>Currently miscalibrated in four of ten probability bands, and says so.</li>
          <li>Cannot predict individual player scores, and will not pretend to.</li>
          <li>Checked nightly against its own record, in public.</li>
        </ul>
      </div>

      <p className="tiny muted">
        <Link href="/accuracy">The measurements</Link> ·{" "}
        <Link href="/about/model">Technical detail</Link> ·{" "}
        <Link href="/">Back</Link> · analytics, not betting advice.
      </p>
    </main>
  );
}
