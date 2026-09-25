/**
 * Recharts cannot read CSS variables, so the palette is duplicated in two
 * chart files. This is the test that notices when a copy drifts.
 *
 * Both files carried a comment saying "lib/tokens.test.ts ... cannot see
 * these copies" - an accurate description of a gap, left open for five
 * sessions. The 2026-09-25 repalette is exactly the event that breaks them:
 * a token moves in globals.css, the charts keep the old hex, and nothing
 * fails. The charts would simply have been drawn in the previous palette.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { parseThemeTokens } from "./tokens";

const read = (p: string) => readFileSync(resolve(process.cwd(), p), "utf8");
const tokens = parseThemeTokens(read("app/globals.css"));

/** Which chart constant must equal which stylesheet token. */
const MAPPING: Record<string, string> = {
  paper: "--paper",
  ink: "--ink",
  soft: "--ink-soft",
  rule: "--rule",
  bat: "--bat",
};

const FILES = ["app/accuracy/charts.tsx", "app/match/[matchId]/live-match.tsx"];

function chartColours(file: string): Record<string, string> {
  const source = read(file);
  const block = source.match(/const CHART = \{([\s\S]*?)\} as const;/);
  expect(block, `${file} has no CHART block`).not.toBeNull();
  const out: Record<string, string> = {};
  for (const m of block![1].matchAll(/(\w+):\s*"(#[0-9A-Fa-f]{6})"/g)) {
    out[m[1]] = m[2].toUpperCase();
  }
  return out;
}

describe.each(FILES)("%s chart colours", (file) => {
  const colours = chartColours(file);

  it("was parsed at all", () => {
    // Guards the guard: an empty map passes every comparison below.
    expect(Object.keys(colours).length).toBeGreaterThan(0);
  });

  it.each(Object.keys(MAPPING))("%s matches the stylesheet token", (key) => {
    const token = MAPPING[key];
    if (!(key in colours)) return; // not every chart uses every colour
    expect(
      colours[key],
      `${file}'s CHART.${key} is ${colours[key]} but ${token} is ${tokens[token]} - ` +
        `Recharts takes JS props, so this copy has to be updated by hand`
    ).toBe(tokens[token]);
  });

  it("introduces no colour that is not a token", () => {
    const unknown = Object.entries(colours).filter(
      ([key, hex]) => !(key in MAPPING) || !Object.values(tokens).includes(hex)
    );
    expect(unknown.map(([k, v]) => `${k}=${v}`)).toEqual([]);
  });
});
