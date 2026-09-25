/**
 * Border widths are a drawn line, not a unit of rhythm.
 *
 * WHY THIS EXISTS. Step 2's spacing migration turned
 * `border-left: 3px solid var(--bat)` into `border-left: var(--space-1) solid`
 * — a 4px border — because `border-left` ends in `left` and the regex read it
 * as a position. It was caught by reading the diff, and only because the diff
 * was short enough to read: **nothing in this project asserts on a border
 * width**, so no test could have caught it and none would have.
 *
 * Step 4's elevation pass touches borders on every page, which is the moment
 * that gap stops being theoretical. So the surface gets an assertion before
 * the pass rather than after it.
 *
 * WHAT IT ENFORCES, and why these and not a scale:
 *
 *   1. A border width is one of a small set of device-pixel values. There is
 *      no 4px rhythm here; a 1px hairline and a 3px accent are different
 *      objects, not two steps of the same ladder, and rounding either to the
 *      spacing scale changes what it is.
 *   2. No border may take a spacing token. That is the exact bug, expressed
 *      directly: `var(--space-1)` in a border position is always wrong, even
 *      when the resulting pixel count happens to look fine.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");

/**
 * Widths a border is allowed to be.
 *
 * 0 and `none` for removing one; 1px hairlines; 2px for a focus ring; 3px for
 * the accent bars on the landing bands. Adding a value here should mean
 * someone decided a new kind of line exists.
 */
const ALLOWED = new Set(["0", "1px", "2px", "3px"]);

/** `border`, `border-top`, `border-left`, `outline`, … but not `border-radius`. */
const BORDER_DECLARATION =
  /(^|[;{\s])(border(?:-(?:top|right|bottom|left))?(?:-width)?|outline(?:-width)?)\s*:\s*([^;{}]+)/g;

interface Found {
  property: string;
  value: string;
}

function borderDeclarations(): Found[] {
  const out: Found[] = [];
  for (const match of css.matchAll(BORDER_DECLARATION)) {
    out.push({ property: match[2], value: match[3].trim() });
  }
  return out;
}

describe("border widths", () => {
  const declarations = borderDeclarations();

  it("finds the borders at all", () => {
    // Guards the guard. A regex that matches nothing makes every assertion
    // below pass vacuously, which is the failure this whole file is about.
    expect(declarations.length).toBeGreaterThan(10);
  });

  it("never uses a spacing token as a border width", () => {
    // The step-2 bug, stated directly.
    const offenders = declarations.filter((d) => /var\(--space-/.test(d.value));
    expect(
      offenders.map((d) => `${d.property}: ${d.value}`),
      "a border is a drawn line, not a step on the spacing scale"
    ).toEqual([]);
  });

  it("uses only device-pixel widths", () => {
    const offenders: string[] = [];
    for (const { property, value } of declarations) {
      // First token of the shorthand is the width, when it is a length.
      const width = value.split(/\s+/)[0];
      if (width === "none" || width === "inherit" || width.startsWith("var(")) continue;
      if (!/^-?[\d.]+(px|em|rem)?$/.test(width)) continue; // a colour-only shorthand
      if (!ALLOWED.has(width)) offenders.push(`${property}: ${value}`);
    }
    expect(
      offenders,
      `border widths outside {${[...ALLOWED].join(", ")}} - if a new kind of ` +
        `line is intended, add it to ALLOWED with a reason`
    ).toEqual([]);
  });
});
