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
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { highWaterMark, mergePredictions } from "@/lib/merge-predictions";
import { parsePrediction, type Phase, type WinProbPrediction } from "@/lib/prediction";
import { HATCH_PITCH_PX, toMarks } from "@/lib/ball-strip";
import { BallStrip } from "@/components/ball-strip";
import { supabaseBrowser } from "@/lib/supabase-browser";

interface Header {
  matchId: number;
  competition: string;
  format: string;
  teamA: string | null;
  teamB: string | null;
  venue: string | null;
  startDate: string;
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
 * The light palette's values, duplicated as literals.
 *
 * Recharts takes colours as JS props and renders them into inline SVG
 * attributes, so `var(--rule)` reaches the DOM uninterpreted and resolves to
 * nothing. accuracy/charts.tsx makes the same compromise for the same reason
 * and says so. The hand-rolled ball strip below does NOT need this - it uses
 * CSS custom properties directly, which is one of the reasons it is
 * hand-rolled.
 *
 * Keep in step with .theme-paper in globals.css; lib/tokens.test.ts holds
 * that block to its contrast floors but cannot see these copies.
 *
 * Two have no light counterpart. The old tooltip background was --panel and
 * the old 50% line was a one-off #3a4148; neither exists in a palette with no
 * raised surfaces, so the tooltip sits on --paper inside a --rule border and
 * the reference line is --rule.
 */
const CHART = {
  paper: "#F7F6E9",
  ink: "#2A2419",
  soft: "#6B6152",
  rule: "#DFD6BD",
  bat: "#366C73",
} as const;

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
}: {
  matchId: number;
  header: Header;
  initialPredictions: WinProbPrediction[];
}) {
  const [predictions, setPredictions] = useState(initialPredictions);
  const [connection, setConnection] = useState<"connecting" | "live" | "error">(
    "connecting"
  );
  const [recovered, setRecovered] = useState(0);
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
        .select("prediction_id, created_at, model_version, payload, match_id")
        .eq("match_id", matchId)
        .eq("prediction_type", "win_prob")
        // Same filter as the server loader, for the same reason - see
        // page.tsx. If the two disagreed, the resync would pull in rows the
        // initial render excluded and the curve would change shape thirty
        // seconds after it appeared.
        .not("innings", "is", null)
        .order("prediction_id", { ascending: true });
      if (error || data === null) return [];
      return data
        .map((row) => parsePrediction(row as never))
        .filter((x): x is WinProbPrediction => x !== null);
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

          const parsed = parsePrediction(message.new as never);
          if (parsed === null) return;
          streamed.current.add(parsed.prediction_id);
          latest.current = Math.max(latest.current, parsed.prediction_id);
          setPredictions((current) => mergePredictions(current, [parsed]));
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
          setPredictions((current) => mergePredictions(current, rows));
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
      setPredictions((current) => mergePredictions(current, rows));
    }, RESYNC_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
      void supabase.removeChannel(channel);
    };
  }, [matchId]);

  const current = predictions.at(-1) ?? null;
  const previousOver = useMemo(() => {
    if (current === null) return null;
    const sixAgo = predictions.find((p) => p.balls_bowled >= current.balls_bowled - 6);
    return sixAgo && sixAgo !== current ? current.p - sixAgo.p : null;
  }, [predictions, current]);

  // x IS THE POSITIONAL INDEX, NOT balls_bowled, so this curve and the ball
  // strip below it share one domain. balls_bowled repeats on an extra - the
  // footnote under the chart has always said so - which means two deliveries
  // land on the same x, and the strip, which is indexed by position, could
  // never line up with it. The tick formatter still prints balls_bowled, so
  // the axis reads as it did before.
  const chartData = useMemo(
    () =>
      predictions.map((p, index) => ({
        index,
        ball: p.balls_bowled,
        wp: Math.round(p.p * 1000) / 10,
      })),
    [predictions]
  );

  const marks = useMemo(() => toMarks(predictions), [predictions]);

  const battingName = header.teamA ?? "Batting side";
  const confidence = current ? CONFIDENCE[current.phase] : null;

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
          {current
            ? `Need ${current.runs_required} off ${current.balls_remaining}, ${
                10 - current.wickets
              } wickets left`
            : "No predictions yet for this match."}
        </div>
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
      </div>

      {/* 12.1 item 2 - WP bar, history strip, last-over delta */}
      {/* Level 2 (UI-PHASE-2 section 5): the hero. Of everything on this page
          this is the thing it exists to show, and levels are assigned by
          hierarchy rather than applied uniformly. The heading above stays
          level 0 - a page title is not an object. */}
      <div className="level-2 match-hero">
        <div className="row">
          <div>
            <div className="wp-number">
              {current ? `${Math.round(current.p * 100)}%` : "--"}
            </div>
            <div className="small muted">{battingName} to win</div>
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
                {previousOver >= 0 ? "+" : ""}
                {Math.round(previousOver * 100)} pts last over
              </div>
            )}
          </div>
        </div>

        <div className="wp-bar">
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
            now: {current ? `${Math.round(current.p * 100)}%` : "--"}
          </span>
        </div>
      </div>

      {/* ONE level-1 around the curve AND the strip, per section 5. They
          share an x-axis and are indexed by the same delivery position, so
          two panels would draw a line between two halves of one figure. */}
      <div className="level-1 match-figure">

      {/* 12.1 item 6 - the curve */}
      <div>
        <h2>Win probability, {battingName}</h2>
        {chartData.length === 0 ? (
          <p className="small muted">
            Nothing to plot yet. Drive a replay with{" "}
            <code>python -m ingest.drive_replay --match-id {matchId}</code>.
          </p>
        ) : (
          <div style={{ height: 240 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: -18 }}>
                <CartesianGrid stroke={CHART.rule} vertical={false} />
                <XAxis
                  dataKey="index"
                  tick={{ fill: CHART.soft, fontSize: 11 }}
                  stroke={CHART.rule}
                  tickFormatter={(i: number) => String(chartData[i]?.ball ?? "")}
                />
                <YAxis
                  domain={[0, 100]}
                  ticks={[0, 25, 50, 75, 100]}
                  tick={{ fill: CHART.soft, fontSize: 11 }}
                  stroke={CHART.rule}
                />
                <ReferenceLine y={50} stroke={CHART.rule} strokeDasharray="3 3" />
                <Tooltip
                  contentStyle={{
                    background: "#171a1d",
                    border: "1px solid #262b30",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelFormatter={(i: number) => `after ${chartData[i]?.ball ?? 0} balls`}
                  formatter={(value: number) => [`${value}%`, "win probability"]}
                />
                <Line
                  type="monotone"
                  dataKey="wp"
                  stroke={CHART.bat}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
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
          <BallStrip marks={marks} height={72} defaultWidth={640} battingTeam={battingName} />
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
