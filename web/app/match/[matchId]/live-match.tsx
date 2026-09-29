"use client";

/**
 * The subscription and the curve (SPEC.md 7.4, 12.1 items 1, 2, 6; 12.2).
 *
 * The subscribe-then-reconcile sequence is Decision 4. §7.4's pattern stops
 * at "subscribe for deltas", which leaves a real gap: a row inserted between
 * the server component's query and the channel reaching SUBSCRIBED belongs
 * to neither, and on a win-probability curve that is a missing ball with
 * nothing visibly wrong. So the reconcile fires ON 'SUBSCRIBED', not before,
 * and deliberately overlaps the stream - mergePredictions is keyed on
 * prediction_id precisely so the overlap is free.
 */

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { battingTeamName, winSubject } from "@/lib/batting-team";
import { isLiveMatch } from "@/lib/live-match";
import { chaseSummary, swingSentence, turnSentence, type ChaseSummary } from "@/lib/chase-summary";
import { resultText } from "@/lib/match-result";
import { formatProbability } from "@/lib/probability";
import { highWaterMark, mergePredictions } from "@/lib/merge-predictions";
import { isShownVersion, oneSource } from "@/lib/model-version";
import { parsePrediction, type Phase, type WinProbPrediction } from "@/lib/prediction";
import { HATCH_PITCH_PX, toMarks } from "@/lib/ball-strip";
import { BallStrip } from "@/components/ball-strip";
import { WinProbChart } from "@/components/win-prob-chart";
import { supabaseBrowser } from "@/lib/supabase-browser";

interface Header {
  matchId: number;
  competition: string;
  format: string;
  teamA: string | null;
  teamAId: number | null;
  teamB: string | null;
  teamBId: number | null;
  venue: string | null;
  startDate: string;
  status: string;
  winner: string | null;
  resultMethod: string | null;
  winByRuns: number | null;
  winByWickets: number | null;
  outcomeMethod: string | null;
  tieWinner: string | null;
  tieDecidedBy: string | null;
}

// SPEC.md 12.2: "Pre-toss predictions must be visibly marked low-confidence.
// A bot that knows when it doesn't know is more trustworthy than one that
// prints 62% in the same font at ball one and ball 119."
// How often to re-read the match and heal anything the stream lost. Long
// enough to stay a safety net rather than the mechanism - Realtime delivers
// in ~250 ms when it is working - and well inside the 60s the page is held to.
const RESYNC_MS = 30_000;

const CONFIDENCE: Record<Phase, { label: string; low: boolean }> = {
  powerplay: { label: "powerplay · low confidence", low: true },
  middle: { label: "middle overs · medium confidence", low: false },
  death: { label: "death overs · higher confidence", low: false },
};

/**
 * The phase-confidence mark (§1.3).
 *
 * Hatched for a low-confidence phase, solid otherwise, at 14px - above the
 * 4px hollow floor and below the 24px hatch floor lib/ball-strip.ts
 * documents, so the hatch here reads as "not solid" rather than as a
 * countable texture. That is the whole job at this size: the words beside it
 * carry the detail, and the mark only has to differ.
 *
 * aria-hidden because the label repeats it in words. A screen reader hearing
 * "diagonal hatch, powerplay, low confidence" learns nothing from the first
 * two syllables.
 */
function ConfidenceSwatch({ low }: { low: boolean }) {
  const id = `phase-hatch-${low ? "low" : "solid"}`;
  return (
    <svg width={14} height={14} aria-hidden="true" className="phase-swatch">
      {low ? (
        <>
          <defs>
            <pattern
              id={id}
              width={HATCH_PITCH_PX}
              height={HATCH_PITCH_PX}
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <rect width={HATCH_PITCH_PX / 2} height={HATCH_PITCH_PX} fill="var(--ink-soft)" />
            </pattern>
          </defs>
          <rect
            x={0.5}
            y={0.5}
            width={13}
            height={13}
            fill={`url(#${id})`}
            stroke="var(--ink-soft)"
          />
        </>
      ) : (
        <rect x={0.5} y={0.5} width={13} height={13} fill="var(--ink-soft)" />
      )}
    </svg>
  );
}

export function LiveMatch({
  matchId,
  header,
  initialPredictions,
  modelVersion = null,
}: {
  matchId: number;
  header: Header;
  initialPredictions: WinProbPrediction[];
  /**
   * The one model version this page shows (lib/model-version.ts), chosen on
   * the server. Null: the server had none, so any version is accepted.
   */
  modelVersion?: string | null;
}) {
  const [predictions, setPredictions] = useState(initialPredictions);
  const [connection, setConnection] = useState<"connecting" | "live" | "error">(
    "connecting"
  );
  const [recovered, setRecovered] = useState(0);
  // The clock isLiveMatch reads. Advanced on each resync tick and on each
  // streamed row, so a match that stops updating drops its live treatment
  // within one tick of crossing the window, without a reload.
  const [now, setNow] = useState(() => Date.now());
  // Refs, not state: the effect reads these inside its callbacks, and
  // re-running it on every new ball would tear down the channel.
  const latest = useRef(highWaterMark(initialPredictions));
  // Ids the stream itself delivered, and the mark it started from. Together
  // they say whether a row the resync found is one the stream should have
  // brought - which is the difference between "caught up" and "dropped".
  const streamed = useRef<Set<number>>(new Set());
  const watermarkAtSubscribe = useRef(highWaterMark(initialPredictions));

  useEffect(() => {
    const supabase = supabaseBrowser();
    let cancelled = false;

    // Refetch the whole match and merge. Deliberately NOT "everything newer
    // than the high-water mark": a row the stream drops in the middle is
    // below the mark forever after, so a tail-only refetch would leave a
    // permanent hole in the curve and then never look at it again. The merge
    // is keyed on prediction_id, so re-reading rows already held costs one
    // indexed query and changes nothing.
    async function reconcile(): Promise<WinProbPrediction[]> {
      const { data, error } = await supabase
        .from("predictions")
        .select("prediction_id, created_at, model_version, payload, match_id, batting_team_id, source")
        .eq("match_id", matchId)
        .eq("prediction_type", "win_prob")
        // Same filter as the server loader, for the same reason - see
        // page.tsx. If the two disagreed, the resync would pull in rows the
        // initial render excluded and the curve would change shape thirty
        // seconds after it appeared.
        .not("innings", "is", null)
        .order("prediction_id", { ascending: true });
      if (error || data === null) return [];
      return oneSource(
        data
          .filter((row) => isShownVersion(row.model_version, modelVersion))
          .map((row) => parsePrediction(row as never))
          .filter((x): x is WinProbPrediction => x !== null)
      );
    }

    const channel = supabase
      .channel(`match:${matchId}`)
      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "predictions",
          filter: `match_id=eq.${matchId}`,
        },
        (message) => {
          // Realtime takes ONE filter, and it is spent on match_id - so
          // unkeyed rows arrive here even though both queries above exclude
          // them, and would stream onto the curve live. Dropped on arrival so
          // the three paths agree. `measure-realtime-coldstart.mjs` inserts
          // `p: 0.5` probe rows with no ball key and does not clean them up,
          // which is exactly what this stops appearing mid-match.
          if ((message.new as { innings?: number | null }).innings == null) return;
          // One model version per match: a shadow model's rows for this match
          // must not stream onto the curve the page is showing.
          if (!isShownVersion((message.new as { model_version: string }).model_version, modelVersion)) return;

          const parsed = parsePrediction(message.new as never);
          if (parsed === null) return;
          streamed.current.add(parsed.prediction_id);
          latest.current = Math.max(latest.current, parsed.prediction_id);
          setNow(Date.now());
          // One source: once the Cricsheet version arrives it replaces the
          // live rows, and a live row never joins a Cricsheet curve.
          setPredictions((current) => oneSource(mergePredictions(current, [parsed])));
        }
      )
      .subscribe(async (status) => {
        if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
          setConnection("error");
          return;
        }
        if (status !== "SUBSCRIBED" || cancelled) return;

        // Close the gap. Anything written between the server render and this
        // moment is invisible to both the initial query and the stream.
        const rows = await reconcile();
        if (cancelled) return;
        if (rows.length > 0) {
          watermarkAtSubscribe.current = highWaterMark(rows);
          latest.current = Math.max(latest.current, watermarkAtSubscribe.current);
          setPredictions((current) => oneSource(mergePredictions(current, rows)));
        }
        setConnection("live");
      });

    // The safety net, and it is not belt-and-braces (Phase 2 session 5).
    // Measured: on the first channel opened after the project has been
    // idle, postgres_changes reports SUBSCRIBED and then drops inserts
    // intermittently for the first several seconds - 3 of 5 probes lost on
    // a cold channel, 0 of 10 on two warm ones immediately after. Nothing
    // about that is visible to the client: the channel is open, the query
    // worked, and the curve is simply missing balls. A page that trusts the
    // stream alone is broken for every user arriving after a quiet period,
    // which on a project that idles is most of them.
    const timer = setInterval(async () => {
      setNow(Date.now());
      const rows = await reconcile();
      if (cancelled || rows.length === 0) return;
      // Rows written after we were listening, that the stream never brought.
      const missed = rows.filter(
        (row) =>
          row.prediction_id > watermarkAtSubscribe.current &&
          !streamed.current.has(row.prediction_id)
      );
      for (const row of missed) streamed.current.add(row.prediction_id);
      if (missed.length > 0) setRecovered((count) => count + missed.length);
      setPredictions((current) => oneSource(mergePredictions(current, rows)));
    }, RESYNC_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
      void supabase.removeChannel(channel);
    };
  }, [matchId, modelVersion]);

  const current = predictions.at(-1) ?? null;
  const previousOver = useMemo(() => {
    if (current === null) return null;
    // Only states after the first legal ball: before it the run rate is
    // missing, and the model reads that differently from the 0.0 after a
    // dot, so comparing across it measures the artifact (lib/ball-strip.ts
    // isStartOfChase), not the over.
    const sixAgo = predictions.find(
      (p) => p.balls_bowled > 0 && p.balls_bowled >= current.balls_bowled - 6
    );
    return sixAgo && sixAgo !== current ? current.p - sixAgo.p : null;
  }, [predictions, current]);

  // Positional, like the strip below it: components/win-prob-chart.tsx says
  // why the curve and the strip share one index rather than balls_bowled.
  const chartPoints = useMemo(
    () => predictions.map((p) => ({ ball: p.balls_bowled, p: p.p })),
    [predictions]
  );

  const marks = useMemo(() => toMarks(predictions), [predictions]);
  const summary = useMemo(() => chaseSummary(predictions), [predictions]);

  // Whose probability p is: the batting side recorded on the row, never
  // team_a (the side that batted first) and never any other guess.
  const battingName = battingTeamName(current, [
    { id: header.teamAId, name: header.teamA },
    { id: header.teamBId, name: header.teamB },
  ]);
  const subject = winSubject(battingName);
  // Did the side these probabilities belong to win? Only when both names are
  // known and agree - a tie, a no-result or an unknown batting side is not a
  // won chase.
  const chaseWon = battingName !== null && header.winner === battingName;
  const live = isLiveMatch(header.status, current?.created_at ?? null, now);
  const percent = current ? formatProbability(current.p) : "--";
  const confidence = current ? CONFIDENCE[current.phase] : null;

  // Live: what is still needed. Complete: the result. Neither - a row that
  // says live but stopped updating - is said plainly, not dressed as live.
  let situation = "No predictions yet for this match.";
  if (current && live) {
    situation = `Need ${current.runs_required} off ${current.balls_remaining}, ${
      10 - current.wickets
    } wickets left`;
  } else if (header.status === "complete") {
    // The real result, never the final chase state: the last prediction is
    // the state BEFORE the last ball. Unknown result: say only that it ended.
    situation = resultText(header) ?? "Match complete";
  } else if (current) {
    situation = `Stopped updating ${current.created_at.slice(0, 16).replace("T", " ")} UTC · last state: needed ${
      current.runs_required
    } off ${current.balls_remaining}`;
  }

  return (
    <>
      {/* 12.1 item 1 - header, situation line, latency indicator */}
      <div className="panel">
        <h1>
          {header.teamA ?? "Team A"} v {header.teamB ?? "Team B"}
        </h1>
        <div className="small muted">
          {header.competition} · {header.format} · {header.venue ?? "venue unknown"} ·{" "}
          {header.startDate}
        </div>
        <div className="small" style={{ marginTop: 8 }}>
          {situation}
        </div>
        {live && (
          <div className="tiny muted" style={{ marginTop: 8 }}>
            {/* Per SPEC.md 4.3's 2026-09-14 edit: never "42s behind live".
                CricketData publishes no per-ball timestamp, so the provider leg
                is unmeasurable and claiming a figure would be inventing one. */}
            updates every 15s · provider lag not published ·{" "}
            {connection === "live"
              ? "subscribed"
              : connection === "error"
                ? "subscription error"
                : "connecting"}
            {recovered > 0 &&
              ` · ${recovered} update${recovered === 1 ? "" : "s"} the stream dropped, recovered by resync`}
          </div>
        )}
      </div>

      {/* 12.1 item 2 - WP bar, history strip, last-over delta */}
      {/* Level 2 (UI-PHASE-2 section 5): the hero. Of everything on this page
          this is the thing it exists to show, and levels are assigned by
          hierarchy rather than applied uniformly. The heading above stays
          level 0 - a page title is not an object. */}
      {header.status === "complete" ? (
        // A finished match gets the chase in two facts rather than the live
        // furniture: a big number for the last ball, a bar, a confidence line
        // and three "not produced" phase boxes all describe a match in
        // progress. The curve and the strip below stay.
        <div className="level-2 match-hero">
          <CompletedSummary summary={summary} subject={subject} chaseWon={chaseWon} />
        </div>
      ) : (
      <div className="level-2 match-hero">
        <div className="row">
          <div>
            <div className="wp-number">{percent}</div>
            <div className="small muted">{subject} to win</div>
          </div>
          <div style={{ textAlign: "right" }}>
            {current && (
              <div className="small">
                {current.score}/{current.wickets}
                <span className="muted"> chasing {current.target}</span>
              </div>
            )}
            {previousOver !== null && (
              <div className="tiny muted">
                {`${previousOver >= 0 ? "+" : ""}${Math.round(previousOver * 100)} percentage points last over`}
              </div>
            )}
          </div>
        </div>

        <div className="wp-bar" role="img" aria-label={`${subject} win probability ${percent}`}>
          <span style={{ width: `${current ? current.p * 100 : 0}%` }} />
        </div>

        {/* §1.3: confidence is texture, not a colour. The chip this replaces
            carried its meaning in --warn, which fails as a signal for anyone
            who cannot separate it from the surrounding text and disappears
            entirely in greyscale. The swatch below is hatched when the phase
            is low-confidence and solid when it is not, and the word says the
            same thing - colour is never the only cue. */}
        {confidence && current && (
          <span className="phase-mark" data-low={confidence.low ? "true" : "false"}>
            <ConfidenceSwatch low={confidence.low} />
            {confidence.label}
          </span>
        )}{" "}
        <Link className="tiny" href="/about/model">
          how good is this number?
        </Link>

        {/* The strip §12.1 asks for is pre-toss -> post-toss -> innings break
            -> now. Only innings-2 predictions exist: match_phase is the
            literal 'innings2' for every row and the worker produces no
            pre-match prediction at all. Showing the absent phases as absent
            beats inventing them. */}
        <div className="strip">
          {["pre-toss", "post-toss", "innings break"].map((label) => (
            <span key={label} className="cell absent">
              {label}: not produced
            </span>
          ))}
          <span className="cell">
            now: {percent}
          </span>
        </div>
      </div>
      )}

      {/* ONE level-1 around the curve AND the strip, per section 5. They
          share an x-axis and are indexed by the same delivery position, so
          two panels would draw a line between two halves of one figure. */}
      <div className="level-1 match-figure">

      {/* 12.1 item 6 - the curve */}
      <div>
        <h2>Win probability, {subject}</h2>
        {chartPoints.length === 0 ? (
          <p className="small muted">
            Nothing to plot yet. Drive a replay with{" "}
            <code>python -m ingest.drive_replay --match-id {matchId}</code>.
          </p>
        ) : (
          <WinProbChart points={chartPoints} subject={subject} />
        )}
        <div className="tiny muted">
          {predictions.length} predictions · x axis is one mark per delivery;
          the tick labels are legal balls bowled, so an extra repeats the
          label of the delivery before it
        </div>
      </div>

      {/* §4.3: the strip beneath the curve, sharing its x-axis.
          Both are now indexed by delivery position, so mark n sits under
          point n. The curve says where the probability was; the strip says
          what each ball did to it. */}
      {marks.length > 0 && (
        <div>
          <h2>Every delivery</h2>
          <BallStrip marks={marks} height={72} defaultWidth={640} battingTeam={subject} />
          <div className="tiny muted" style={{ marginTop: 8 }}>
            Height is the swing that ball caused, above the line for the
            batting side. The baseline under it is dotted through the
            powerplay, dashed in the middle overs and solid at the death —
            the same confidence the mark above reports.
          </div>
        </div>
      )}

      </div>

      {/* §12.1 items 3, 4 and 5. They are not built, and the honest-gap
          principle (§0.2) says to say so where they would have been rather
          than to leave the page looking complete. Scope, not apology. */}
      <div className="panel">
        <h2>Not built yet</h2>
        <dl className="absent-items">
          <div>
            <dt>Current batter&rsquo;s projection</dt>
            <dd>
              Needs a per-player model. <code>player_state</code>, the table
              that would hold one, has no rows until Phase 5.
            </dd>
          </div>
          <div>
            <dt>Current bowler&rsquo;s projection</dt>
            <dd>The same model and the same empty table.</dd>
          </div>
          <div>
            <dt>This batter against this bowler</dt>
            <dd>
              The corpus has every ball between them. Turning that into a
              prediction needs both player models above, and a sample large
              enough to beat the prior.
            </dd>
          </div>
          <div>
            <dt>Who moved the match</dt>
            <dd>
              The per-ball swings are drawn above. Attributing each one to the
              batter or the bowler is a separate model, and it is Phase 5.
            </dd>
          </div>
        </dl>
      </div>
    </>
  );
}

/**
 * The completed chase in two facts (lib/chase-summary.ts), neither of which
 * is taken from the chase's first over.
 */
function CompletedSummary({
  summary,
  subject,
  chaseWon,
}: {
  summary: ChaseSummary;
  subject: string;
  chaseWon: boolean;
}) {
  const turn = turnSentence(summary, subject, chaseWon);
  if (turn === null) {
    return <p className="small muted">Too few predictions to summarise this chase.</p>;
  }
  return (
    <>
      <p className="small">{turn}</p>
      {summary.biggest ? <p className="small">{swingSentence(summary.biggest, subject)}</p> : null}
    </>
  );
}
