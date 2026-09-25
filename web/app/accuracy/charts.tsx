"use client";

/**
 * The reliability diagram (SPEC.md §8.5, §9.3).
 *
 * Predicted probability against what actually happened, with a 95%
 * match-clustered interval on each point and the diagonal a perfectly
 * calibrated model would sit on. A point whose interval misses the diagonal
 * is a band where the stated probability is wrong by more than sampling
 * noise explains.
 *
 * The interval is the whole point, so it is drawn rather than described.
 * §12.2: every number gets an uncertainty specific to it, and on this chart
 * an unadorned dot would be exactly the bare point estimate that rule
 * forbids.
 *
 * Colours are hex literals duplicated from globals.css because Recharts
 * props cannot read `var()` - the same compromise live-match.tsx makes.
 */

import {
  CartesianGrid,
  ErrorBar,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { reliabilityPoints, type Decile } from "@/lib/accuracy";

/**
 * The light palette's values, duplicated as literals.
 *
 * Recharts takes colours as JS props and renders them into inline SVG
 * attributes, so `var(--rule)` reaches the DOM uninterpreted and resolves to
 * nothing. live-match.tsx makes the same compromise and says so; the
 * hand-rolled ball strip does not need to, which is one of the reasons it is
 * hand-rolled.
 *
 * Keep in step with .theme-paper in globals.css. lib/tokens.test.ts holds
 * that block to its contrast floors and cannot see these copies -
 * lib/chart-colours.test.ts now does, by parsing both this file and the
 * stylesheet and comparing. The comment above used to end at "cannot see
 * these copies", which described the gap accurately and left it open.
 */
const CHART = {
  paper: "#F7F6E9",
  ink: "#2A2419",
  soft: "#6B6152",
  rule: "#DFD6BD",
  bat: "#366C73",
} as const;

export function ReliabilityDiagram({ deciles }: { deciles: Decile[] | undefined }) {
  const points = reliabilityPoints(deciles);
  if (points.length === 0) {
    return <p className="small muted">Nothing scored yet, so there is nothing to plot.</p>;
  }

  return (
    <>
      <div style={{ height: 260 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: -18 }}>
            <CartesianGrid stroke={CHART.rule} />
            <XAxis
              type="number"
              dataKey="predicted"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tick={{ fill: CHART.soft, fontSize: 11 }}
              stroke={CHART.rule}
            />
            <YAxis
              type="number"
              dataKey="observed"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tick={{ fill: CHART.soft, fontSize: 11 }}
              stroke={CHART.rule}
            />
            {/* Perfect calibration. Drawn as a segment rather than a
                gridline so it reads as the thing being compared against. */}
            <ReferenceLine
              segment={[
                { x: 0, y: 0 },
                { x: 1, y: 1 },
              ]}
              stroke={CHART.rule}
              strokeDasharray="3 3"
            />
            <Tooltip
              cursor={{ stroke: CHART.rule }}
              contentStyle={{
                background: CHART.paper,
                border: `1px solid ${CHART.rule}`,
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={(value: number, name: string) => [value.toFixed(3), name]}
              labelFormatter={() => ""}
            />
            <Scatter data={points} fill={CHART.bat} isAnimationActive={false}>
              <ErrorBar dataKey="error" width={4} strokeWidth={1.5} stroke={CHART.bat} />
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <div className="tiny muted">
        Each point is one band of predicted probability. The bar is a 95% interval
        on what actually happened, bootstrapped over matches. On the dashed line
        means the stated probability was right.
      </div>
    </>
  );
}
