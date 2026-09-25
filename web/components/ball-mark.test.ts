/**
 * The header mark and the favicon are the same drawing.
 *
 * components/ball-mark.tsx duplicates the seam geometry from app/icon.svg
 * because importing SVG as a component would mean adding @svgr/webpack for
 * one string. Duplication is fine; SILENT duplication is not, and this
 * project's recurring failure is exactly a generated thing drifting from its
 * source while both look fine in isolation.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { BALL_FILL, BALL_SEAM, BALL_SEAM_PATH } from "./ball-mark";

const svg = readFileSync(resolve(process.cwd(), "app/icon.svg"), "utf8");

describe("the header mark matches the favicon", () => {
  it("draws the identical seam path", () => {
    const match = svg.match(/d="(M [^"]+)"/);
    expect(match, "app/icon.svg has no seam path").not.toBeNull();
    expect(match![1]).toBe(BALL_SEAM_PATH);
  });

  it("uses the identical colours", () => {
    expect(svg).toContain(BALL_FILL);
    expect(svg).toContain(BALL_SEAM);
  });

  it("uses the colours sampled from the artwork, not the CSS tokens", () => {
    // If someone "tidies" these into var(--flag) the mark stops matching
    // public/logo.png, which is the thing it is derived from.
    expect(BALL_FILL).toBe("#98374B");
    expect(BALL_SEAM).toBe("#F7F6E9");
  });
});
