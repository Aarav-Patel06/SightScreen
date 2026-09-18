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
const CONFIDENCE: Record<Phase, { label: string; low: boolean }> = {
  powerplay: { label: "powerplay · low confidence", low: true },
  middle: { label: "middle overs · medium confidence", low: false },
  death: { label: "death overs · higher confidence", low: false },
};

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
  // A ref, not state: the reconcile reads it inside the subscribe callback,
  // and re-running that effect on every new ball would tear down the channel.
  const latest = useRef(highWaterMark(initialPredictions));

  useEffect(() => {
    const supabase = supabaseBrowser();
    let cancelled = false;

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
          const parsed = parsePrediction(message.new as never);
          if (parsed === null) return;
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
        const { data } = await supabase
          .from("predictions")
          .select("prediction_id, created_at, model_version, payload, match_id")
          .eq("match_id", matchId)
          .eq("prediction_type", "win_prob")
          .gt("prediction_id", latest.current)
          .order("prediction_id", { ascending: true });
        if (cancelled) return;

        const missed = (data ?? [])
          .map((row) => parsePrediction(row as never))
          .filter((x): x is WinProbPrediction => x !== null);
        if (missed.length > 0) {
          latest.current = Math.max(latest.current, highWaterMark(missed));
          setPredictions((current) => mergePredictions(current, missed));
        }
        setConnection("live");
      });

    return () => {
      cancelled = true;
      void supabase.removeChannel(channel);
    };
  }, [matchId]);

  const current = predictions.at(-1) ?? null;
  const previousOver = useMemo(() => {
    if (current === null) return null;
    const sixAgo = predictions.find((p) => p.balls_bowled >= current.balls_bowled - 6);
    return sixAgo && sixAgo !== current ? current.p - sixAgo.p : null;
  }, [predictions, current]);

  const chartData = useMemo(
    () =>
      predictions.map((p, index) => ({
        index,
        ball: p.balls_bowled,
        wp: Math.round(p.p * 1000) / 10,
      })),
    [predictions]
  );

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
        </div>
      </div>

      {/* 12.1 item 2 - WP bar, history strip, last-over delta */}
      <div className="panel">
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

        {confidence && (
          <span className={confidence.low ? "chip chip-low" : "chip"}>
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

      {/* 12.1 item 6 - the curve */}
      <div className="panel">
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
                <CartesianGrid stroke="#262b30" vertical={false} />
                <XAxis
                  dataKey="ball"
                  tick={{ fill: "#9aa3ab", fontSize: 11 }}
                  stroke="#262b30"
                />
                <YAxis
                  domain={[0, 100]}
                  ticks={[0, 25, 50, 75, 100]}
                  tick={{ fill: "#9aa3ab", fontSize: 11 }}
                  stroke="#262b30"
                />
                <ReferenceLine y={50} stroke="#3a4148" strokeDasharray="3 3" />
                <Tooltip
                  contentStyle={{
                    background: "#171a1d",
                    border: "1px solid #262b30",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelFormatter={(ball) => `after ${ball} balls`}
                  formatter={(value: number) => [`${value}%`, "win probability"]}
                />
                <Line
                  type="monotone"
                  dataKey="wp"
                  stroke="#4ea1ff"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        <div className="tiny muted">
          {predictions.length} predictions · x axis is legal balls bowled, so an
          extra shares a ball with the delivery before it
        </div>
      </div>
    </>
  );
}
