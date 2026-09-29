"use client";

/**
 * The win probability curve (SPEC.md 12.1 item 6), one point per delivery.
 *
 * Shared by the match page and the landing hero, so the two draw the same
 * curve the same way.
 *
 * x IS THE POSITIONAL INDEX, NOT balls_bowled, so the curve and the match
 * page's ball strip share one domain. balls_bowled repeats on an extra, which
 * would put two deliveries on the same x; the tick formatter still prints it,
 * so the axis reads in balls.
 *
 * IT RENDERS ON THE SERVER. ResponsiveContainer measures its parent before it
 * draws anything, and there is no parent to measure in server-rendered HTML -
 * so without `initialDimension` the hero would be an empty box until
 * hydration, and permanently empty without JavaScript. The ball strip refuses
 * that failure for the hero (components/ball-strip.tsx), and this keeps the
 * same promise: the assumed width is drawn first, and the real one replaces
 * it on the first measurement. The height is fixed either way, so nothing
 * shifts.
 *
 * AND IT HYDRATES. Rendering on the server is not enough if the client then
 * draws something different, and Recharts does in two places, both measured
 * as hydration errors on the landing page:
 *   - its clip-path id comes from a module-level counter, which on a
 *     long-running server is however many charts it has drawn so far. The
 *     chart takes a `useId` instead, which React keeps equal on both sides.
 *   - its default tick interval measures label text in the DOM to decide
 *     which to drop and where the end labels go, and a server has no DOM, so
 *     it keeps every x label while the client keeps a third, and the client
 *     alone nudges "100" down to fit. A numeric interval on both axes takes
 *     the path that measures nothing: about ten x labels, whatever the width.
 */

import { useId } from "react";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatProbability } from "@/lib/probability";

/**
 * The light palette's values, duplicated as literals.
 *
 * Recharts takes colours as JS props and renders them into inline SVG
 * attributes, so `var(--rule)` reaches the DOM uninterpreted and resolves to
 * nothing. accuracy/charts.tsx makes the same compromise for the same reason
 * and says so. The hand-rolled ball strip does NOT need this - it uses CSS
 * custom properties directly, which is one of the reasons it is hand-rolled.
 *
 * Keep in step with .theme-paper in globals.css; lib/chart-colours.test.ts
 * fails when a copy here drifts from its token.
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
 * A summary fact marked where it happened (lib/chase-summary.ts).
 *
 * "turn" is the peak or the low, "swing" the biggest single-ball swing. They
 * differ by SHAPE - filled against hollow - so colour is never the only cue
 * (UI-PHASE.md §1.3). MarkerGlyph draws the same two shapes for a caption.
 */
export interface ChartMarker {
  index: number;
  kind: "turn" | "swing";
}

export function WinProbChart({
  points,
  subject,
  height = 240,
  defaultWidth = 640,
  markers = [],
}: {
  /** One per delivery, in order: legal balls bowled, and p for the batting side. */
  points: readonly { ball: number; p: number }[];
  subject: string;
  height?: number;
  /** Width assumed for the server render, before the container is measured. */
  defaultWidth?: number;
  markers?: readonly ChartMarker[];
}) {
  const data = points.map((point, index) => ({
    index,
    ball: point.ball,
    wp: Math.round(point.p * 1000) / 10,
  }));
  // Colons are legal in an id but not safe inside url(#...), where it is used.
  const chartId = `winprob-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const tickInterval = Math.max(0, Math.ceil(data.length / 10) - 1);

  return (
    <div style={{ height }}>
      <ResponsiveContainer
        width="100%"
        height="100%"
        initialDimension={{ width: defaultWidth, height }}
      >
        <LineChart id={chartId} data={data} margin={{ top: 4, right: 8, bottom: 4, left: -18 }}>
          <CartesianGrid stroke={CHART.rule} vertical={false} />
          <XAxis
            dataKey="index"
            tick={{ fill: CHART.soft, fontSize: 11 }}
            stroke={CHART.rule}
            interval={tickInterval}
            tickFormatter={(i: number) => String(data[i]?.ball ?? "")}
          />
          <YAxis
            domain={[0, 100]}
            ticks={[0, 25, 50, 75, 100]}
            interval={0}
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
            labelFormatter={(i: number) => `after ${data[i]?.ball ?? 0} balls`}
            formatter={(value: number) => [formatProbability(value / 100), `${subject} win probability`]}
          />
          <Line
            id={`${chartId}-line`}
            type="monotone"
            dataKey="wp"
            stroke={CHART.bat}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          {markers
            .filter((marker) => data[marker.index] !== undefined)
            .map((marker) => (
              <ReferenceDot
                key={`${marker.kind}-${marker.index}`}
                x={marker.index}
                y={data[marker.index].wp}
                r={5}
                fill={marker.kind === "turn" ? CHART.ink : CHART.paper}
                stroke={CHART.ink}
                strokeWidth={2}
              />
            ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** The marker's shape at text size, for the caption that names it. */
export function MarkerGlyph({ kind }: { kind: ChartMarker["kind"] }) {
  return (
    <svg width={10} height={10} aria-hidden="true" className="marker-glyph">
      <circle
        cx={5}
        cy={5}
        r={4}
        fill={kind === "turn" ? "var(--ink)" : "var(--paper)"}
        stroke="var(--ink)"
        strokeWidth={1.5}
      />
    </svg>
  );
}
