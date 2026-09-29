/**
 * The strip's readout and caption, walked across every ball of a real chase
 * (match 8429) with the keyboard - the path a reader without a pointer takes.
 *
 * Two promises: the readout never prints a bare event code (the "score ·"
 * that shipped before 47bf970), and every probability change is said in
 * percentage points - "up 2 pp", "down 3 pp", "no change" - except at the
 * start of the chase, whose change is the model's run-rate artifact and is
 * not reported as a change at all.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import FIXTURE from "@/lib/fixtures/match-8429-predictions.json";
import FIXTURE_1000068 from "@/lib/fixtures/match-1000068-predictions.json";
import { toMarks } from "@/lib/ball-strip";
import { parsePrediction, type WinProbPrediction } from "@/lib/prediction";

import { BallStrip } from "./ball-strip";

afterEach(cleanup);

const marks = toMarks(
  FIXTURE.rows
    .map((row) => parsePrediction(row as never))
    .filter((p): p is WinProbPrediction => p !== null)
);

function readoutLines(): string[] {
  const readout = document.querySelector(".strip-readout");
  return Array.from(readout?.querySelectorAll(":scope > span") ?? []).map((s) => s.textContent ?? "");
}

// 305 key presses, each re-rendering the strip under jsdom: 4.6s for this whole
// file in CI on 2026-09-27, against Vitest's 5s default per test, and about 8s
// on the owner's machine, where it timed out. The time is jsdom rendering, not
// a hang, so the limit is raised for this one test rather than globally.
const READ_EVERY_BALL_TIMEOUT_MS = 30_000;

describe("the strip on 8429", () => {
  it("captions the biggest swing in percentage points, without the start-of-chase drop", () => {
    render(<BallStrip marks={marks} defaultWidth={1108} battingTeam="India" />);
    const rest = Math.round(Math.max(...marks.slice(1).map((m) => Math.abs(m.swing))) * 100);
    expect(readoutLines()[0]).toBe(`Biggest swing: ${rest}% points`);
  });

  it("reads every ball in words and percentage points", () => {
    render(<BallStrip marks={marks} defaultWidth={1108} battingTeam="India" />);
    const strip = screen.getByRole("img");
    const seen: string[][] = [];
    for (let i = 0; i < marks.length; i++) {
      fireEvent.keyDown(strip, { key: "ArrowRight" });
      seen.push(readoutLines());
    }
    expect(seen).toHaveLength(305);

    for (const [index, [state, model]] of seen.entries()) {
      // Line one: never a bare event code.
      expect(state, `ball ${index + 1}`).not.toMatch(/\bscore\b/);
      expect(state, `ball ${index + 1}`).not.toMatch(/·\s*$/);
    }
    // The start of the chase: its change is not reported.
    expect(seen[0][1]).toMatch(/^India \d+% to win · start of the chase$/);
    // Every other ball with a successor: up / down / no change, in pp.
    for (const [, model] of seen.slice(1, -1)) {
      expect(model).toMatch(/^India \d+% to win, (up \d+ pp|down \d+ pp|no change)$/);
    }
    expect(seen.map(([, m]) => m).join("\n")).not.toMatch(/\bpts?\b|points(?! )/);
  }, READ_EVERY_BALL_TIMEOUT_MS);

  it("names no team when it has none to name", () => {
    render(<BallStrip marks={marks} defaultWidth={1108} />);
    fireEvent.keyDown(screen.getByRole("img"), { key: "ArrowRight" });
    fireEvent.keyDown(screen.getByRole("img"), { key: "ArrowRight" });
    expect(readoutLines()[1]).toMatch(/^\d+% to win, (up \d+ pp|down \d+ pp|no change)$/);
  });
});

describe("the strip on 1000068, a chase that ended near certainty", () => {
  const won = toMarks(
    FIXTURE_1000068.rows
      .map((row) => parsePrediction(row as never))
      .filter((p): p is WinProbPrediction => p !== null)
  );

  it("never reads 100% in the readout", () => {
    render(<BallStrip marks={won} defaultWidth={1108} battingTeam="England" />);
    const strip = screen.getByRole("img");
    const lines: string[] = [];
    for (let i = 0; i < won.length; i++) {
      fireEvent.keyDown(strip, { key: "ArrowRight" });
      lines.push(readoutLines()[1]);
    }
    expect(lines.join("\n")).not.toMatch(/\b100%|\b0%/);
    expect(lines.some((l) => l.startsWith("England >99% to win"))).toBe(true);
  });

  it("never reads 100% in the description", () => {
    render(<BallStrip marks={won} defaultWidth={1108} battingTeam="England" />);
    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).toContain("ending at >99%");
    expect(label).not.toMatch(/\b100%/);
  });
});
