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

/** CSS comments, removed. They sit between rules and break naive rule matching. */
function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "");
}

/**
 * Widths a border is allowed to be.
 *
 * 0 and `none` for removing one; 1px hairlines; 2px for a focus ring; 3px for
 * the accent bars on the landing bands. Adding a value here should mean
 * someone decided a new kind of line exists.
 */

/**
 * Top-level rules from the theme block, with at-rule bodies excluded.
 *
 * `@media` and `@supports` exist precisely to redeclare a property under
 * different conditions, so a rule inside one is not drift. Everything else
 * shares the same conditions, which means two declarations of one property
 * are resolved by file position - and file position is not a decision anyone
 * makes deliberately.
 */
export interface Rule {
  selectors: string[];
  properties: string[];
}

function topLevelRules(text: string): Rule[] {
  const css = stripComments(text);
  const rules: Rule[] = [];
  let depth = 0;
  let buffer = "";
  for (let i = 0; i < css.length; i += 1) {
    const ch = css[i];
    if (ch === "{") {
      depth += 1;
      if (depth === 1) {
        // Selector for a rule that is not nested inside an at-rule.
        const prelude = buffer.trim();
        buffer = "";
        if (prelude.startsWith("@")) {
          // An at-rule: skip its whole body, brace-balanced.
          let inner = 1;
          i += 1;
          while (i < css.length && inner > 0) {
            if (css[i] === "{") inner += 1;
            else if (css[i] === "}") inner -= 1;
            i += 1;
          }
          depth = 0;
          i -= 1;
          continue;
        }
        const close = css.indexOf("}", i);
        const body = css.slice(i + 1, close);
        rules.push({
          selectors: prelude.split(",").map((s) => s.split(/\s+/).filter(Boolean).join(" ")),
          properties: body
            .split(";")
            .filter((d) => d.includes(":"))
            .map((d) => d.split(":", 1)[0].trim().toLowerCase())
            .filter((prop) => prop.length > 0 && !prop.startsWith("--")),
        });
        i = close;
        depth = 0;
      }
      continue;
    }
    if (ch === "}") {
      depth = Math.max(0, depth - 1);
      buffer = "";
      continue;
    }
    buffer += ch;
  }
  return rules;
}

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

// --- the radius scale (step 6 part two) ----------------------------------
//
// WHY THIS EXISTS. The hero rendered with 4px corners while the scorecard
// beside it had 12px, and both are `.level-2`. The cause was a pre-elevation
// `.theme-paper .hero` rule that declared its own 4px radius and happened to
// sit later in the file at equal specificity, so source order won silently.
//
// That is the second time a value with an implied standard turned out to have
// no scale behind it - spacing was the first - and the symptom was identical:
// a handful of nearly-equal raw numbers nobody had chosen together. 2px, 3px,
// 4px, 10px, 12px and 999px were all in use.

const RADIUS_TOKENS = ["--radius-panel", "--radius-control", "--radius-sm", "--radius-pill"];

describe("border radius", () => {
  const declarations = [...css.matchAll(/(?<![-\w])border-radius\s*:\s*([^;{}]+)/g)].map(
    (m) => m[1].trim()
  );

  it("finds the radii at all", () => {
    expect(declarations.length).toBeGreaterThan(5);
  });

  it("declares the whole scale", () => {
    for (const token of RADIUS_TOKENS) {
      expect(css, `${token} is missing from .theme-paper`).toContain(`${token}:`);
    }
  });

  it("uses a token everywhere, or an explicit zero", () => {
    // `0` is allowed and meaningful: level 0 is a real level, and .panel
    // says so by having no radius at all.
    const offenders = declarations.filter(
      (value) => value !== "0" && !/var\(--radius-/.test(value)
    );
    expect(
      offenders,
      `raw border-radius values - add them to the scale in .theme-paper or use ` +
        `an existing token: ${offenders.join(", ")}`
    ).toEqual([]);
  });

  it("gives every panel level the same radius", () => {
    // The user-visible rule this bug produced: the scorecard was right and
    // the hero was not. Two panel radii differing by 2px is drift, not a
    // scale - so there is one.
    const block = css.slice(css.indexOf(".theme-paper {"));
    for (const level of [".theme-paper .level-1 {", ".theme-paper .level-2 {"]) {
      const i = block.indexOf(level);
      expect(i, `${level} not found`).toBeGreaterThan(-1);
      const body = block.slice(i, block.indexOf("}", i));
      expect(body, `${level} must use --radius-panel`).toContain("var(--radius-panel)");
    }
  });
});

describe("no selector sets a radius twice", () => {
  // THE BUG CLASS, caught twice in one session and both times by looking at
  // the rendered page rather than the stylesheet.
  //
  //   .theme-paper .hero        - declared at 563 via .level-2 and again at
  //                               1147 with its own 4px, later wins
  //   .theme-paper .ask-button  - declared in the shared control rule and
  //                               again below it with 2px, later wins
  //
  // A second block adding DIFFERENT properties is normal and useful - the
  // chrome section legitimately re-colours the footer. A second block
  // redeclaring border-radius has no legitimate use here, and equal
  // specificity means the winner is decided by file position, which is not a
  // decision anyone makes on purpose.
  it("declares border-radius at most once per selector", () => {
    // COMMENTS ARE STRIPPED FIRST, and the first version of this did not do
    // it. The matcher required each rule to be preceded by `}`, so any rule
    // introduced by a comment - which in this stylesheet is most of them -
    // produced a "selector" beginning `*/`, failed the .theme-paper prefix
    // test, and was skipped. It saw 4 of the 14 radius declarations and
    // passed on a file containing the exact duplicate it was written to
    // catch. Found by probing it rather than by trusting a green run.
    const block = stripComments(css.slice(css.indexOf(".theme-paper {")));
    const seen = new Map<string, number>();
    for (const m of block.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (!/border-radius\s*:/.test(m[2])) continue;
      for (const raw of m[1].split(",")) {
        const sel = raw.split(/\s+/).filter(Boolean).join(" ");
        if (!sel.startsWith(".theme-paper")) continue;
        seen.set(sel, (seen.get(sel) ?? 0) + 1);
      }
    }
    const twice = [...seen.entries()].filter(([, n]) => n > 1).map(([s, n]) => `${s} (${n}x)`);
    expect(
      twice,
      "these set border-radius in more than one block, so file position decides " +
        "which wins - fold them into one rule"
    ).toEqual([]);
  });
});

// --- the same property declared twice, anywhere ---------------------------
//
// GENERALISED FROM THE RADIUS CASE, because three defects in one session all
// had this shape and three is a property of the stylesheet rather than a
// coincidence:
//
//   .theme-paper .hero        - .level-2 gave it 12px; a session-2 block
//                               later in the file gave it 4px and won
//   .theme-paper .ask-button  - the shared control rule set its geometry; a
//                               block below redeclared it and won
//   the radius duplicates     - the same thing a third time
//
// Every one was invisible in review and obvious in the render. Equal
// specificity means file position decides, and nobody chooses file position.
//
// @media and @supports bodies are excluded: redeclaring under different
// conditions is what they are for. Everything counted here shares one set of
// conditions.

describe("no property is declared twice for the same selector", () => {
  const rules = topLevelRules(css.slice(css.indexOf(".theme-paper {")));

  it("parses a realistic number of rules", () => {
    // NON-VACUITY, and this file has earned the paranoia: the first version
    // of the radius guard saw 4 of 14 declarations because it skipped every
    // rule introduced by a comment, and passed on the file containing the
    // duplicate it existed to catch.
    expect(rules.length).toBeGreaterThan(80);
    expect(rules.some((r) => r.properties.includes("border-radius"))).toBe(true);
    expect(rules.some((r) => r.selectors.includes(".theme-paper .level-2"))).toBe(true);
  });

  it("excludes at-rule bodies, which are allowed to redeclare", () => {
    // .corner-graphic is `display: none` at the top level and `display: block`
    // inside a media query. That is the intended pattern, not drift.
    const topLevelDisplays = rules.filter(
      (r) => r.selectors.includes(".theme-paper .corner-graphic") && r.properties.includes("display")
    );
    expect(topLevelDisplays).toHaveLength(1);
  });

  it("declares each property once per selector", () => {
    const seen = new Map<string, number>();
    for (const rule of rules) {
      for (const selector of rule.selectors) {
        if (!selector.startsWith(".theme-paper")) continue;
        for (const property of rule.properties) {
          const key = `${selector} { ${property} }`;
          seen.set(key, (seen.get(key) ?? 0) + 1);
        }
      }
    }
    const twice = [...seen.entries()]
      .filter(([, n]) => n > 1)
      .map(([key, n]) => `${key} ${n}x`)
      .sort();
    expect(
      twice,
      "declared in more than one block under the same conditions, so file " +
        "position decides the winner - fold them into one rule"
    ).toEqual([]);
  });
});
