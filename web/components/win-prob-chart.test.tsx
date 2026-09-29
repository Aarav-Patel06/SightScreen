/**
 * The win probability curve renders on the server.
 *
 * It is the landing hero now, and the hero must never be an empty box - not
 * before hydration, and not without JavaScript (components/win-prob-chart.tsx).
 * Recharts' ResponsiveContainer draws nothing until it has measured its
 * parent, which a server render never does, so this holds the fix to its
 * purpose: the server HTML already contains the line.
 */

import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { WinProbChart } from "./win-prob-chart";

const points = Array.from({ length: 30 }, (_, i) => ({ ball: i, p: 0.5 + i / 100 }));

describe("WinProbChart on the server", () => {
  it("renders the curve into the HTML", () => {
    const html = renderToString(<WinProbChart points={points} subject="India" defaultWidth={1108} />);
    expect(html).toContain("<svg");
    expect(html).toMatch(/class="recharts-curve recharts-line-curve"[^>]* d="M/);
  });

  it("draws a marker where it is asked to, and skips one off the end", () => {
    const html = renderToString(
      <WinProbChart
        points={points}
        subject="India"
        markers={[
          { index: 12, kind: "swing" },
          { index: 99, kind: "turn" },
        ]}
      />
    );
    expect(html.match(/recharts-reference-dot-dot/g)?.length).toBe(1);
  });
});
