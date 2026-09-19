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
            <CartesianGrid stroke="#262b30" />
            <XAxis
              type="number"
              dataKey="predicted"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tick={{ fill: "#9aa3ab", fontSize: 11 }}
              stroke="#262b30"
            />
            <YAxis
              type="number"
              dataKey="observed"
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tick={{ fill: "#9aa3ab", fontSize: 11 }}
              stroke="#262b30"
            />
            {/* Perfect calibration. Drawn as a segment rather than a
                gridline so it reads as the thing being compared against. */}
            <ReferenceLine
              segment={[
                { x: 0, y: 0 },
                { x: 1, y: 1 },
              ]}
              stroke="#3a4148"
              strokeDasharray="3 3"
            />
            <Tooltip
              cursor={{ stroke: "#3a4148" }}
              contentStyle={{
                background: "#171a1d",
                border: "1px solid #262b30",
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={(value: number, name: string) => [value.toFixed(3), name]}
              labelFormatter={() => ""}
            />
            <Scatter data={points} fill="#4ea1ff" isAnimationActive={false}>
              <ErrorBar dataKey="error" width={4} strokeWidth={1.5} stroke="#4ea1ff" />
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
