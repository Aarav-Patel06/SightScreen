/**
 * The palette's accessibility floors, checked against the stylesheet itself.
 *
 * This test exists because --bowl shipped at 3.46:1 and nothing caught it
 * until a contrast column was added to /design by hand. The failure mode it
 * guards is not "someone picks a bad colour" - it is "someone adjusts a hue
 * for aesthetic reasons and does not think about text".
 *
 * So it reads globals.css from disk rather than importing constants. A test
 * holding its own copy of the hexes would pass indefinitely while the
 * stylesheet drifted away from it, which is the specific way a contrast test
 * becomes decoration.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  AA_TEXT,
  CHROME_SAFE_TEXT,
  CHROME_TOKENS,
  EXEMPT_TOKENS,
  FILL_FLOOR,
  FILL_TOKENS,
  NON_COLOUR_PREFIXES,
  PAPER,
  SPACE_STEP_PX,
  SPACE_TOKENS,
  TEXT_TOKENS,
  contrast,
  luminance,
  parseLengthTokens,
  parseThemeTokens,
} from "./tokens";

// Resolved from the working directory, not from import.meta.url: the test
// environment is jsdom, where import.meta.url is the document's http:// URL
// and fileURLToPath rejects it. Vitest runs with cwd at the web root.
const css = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");
const tokens = parseThemeTokens(css);
const paper = tokens[PAPER];

describe("the stylesheet is readable at all", () => {
  it("finds the theme block and a base colour", () => {
    // If this fails, every assertion below is vacuously true, so it is
    // checked first and explicitly.
    expect(Object.keys(tokens).length).toBeGreaterThan(5);
    expect(paper).toMatch(/^#[0-9A-F]{6}$/);
  });
});

describe("text tokens clear WCAG AA", () => {
  for (const token of TEXT_TOKENS) {
    it(`${token} is at least ${AA_TEXT}:1 against ${PAPER}`, () => {
      const hex = tokens[token];
      expect(hex, `${token} is not defined in .theme-paper`).toBeDefined();
      expect(contrast(hex, paper)).toBeGreaterThanOrEqual(AA_TEXT);
    });
  }
});

describe("fill tokens clear the floor that actually binds", () => {
  // FILL_FLOOR, not AA_LARGE. 3.0 passes WCAG 1.4.11 and collapses the
  // texture ladder - see the constant's own comment for the measurements.
  for (const token of FILL_TOKENS) {
    it(`${token} is at least ${FILL_FLOOR}:1 against ${PAPER}`, () => {
      const hex = tokens[token];
      expect(hex, `${token} is not defined in .theme-paper`).toBeDefined();
      expect(contrast(hex, paper)).toBeGreaterThanOrEqual(FILL_FLOOR);
    });
  }
});

describe("chrome surfaces carry only what is legible on them", () => {
  // Contrast is a property of a PAIR, and this palette has four
  // backgrounds. --ink-soft is 5.58:1 on paper and 3.52:1 on chrome; the
  // single-background era had no way to express that.
  for (const surface of CHROME_TOKENS) {
    for (const token of CHROME_SAFE_TEXT) {
      it(`${token} is at least ${AA_TEXT}:1 on ${surface}`, () => {
        expect(contrast(tokens[token], tokens[surface])).toBeGreaterThanOrEqual(AA_TEXT);
      });
    }

    it(`nothing else is claimed to be safe on ${surface}`, () => {
      // The guard on the list above: if a token is added to CHROME_SAFE_TEXT
      // that does not clear the floor, the loop catches it. This catches the
      // reverse - a token that WOULD pass and is missing from the list is
      // fine, but the list must not silently grow to include one that fails.
      for (const token of CHROME_SAFE_TEXT) {
        const ratio = contrast(tokens[token], tokens[surface]);
        expect(ratio, `${token} on ${surface} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(
          AA_TEXT
        );
      }
    });
  }
});

describe("every colour is classified", () => {
  it("has no token that nobody decided about", () => {
    // The check that actually catches the next mistake. Adding a colour to
    // globals.css without putting it in a registry means nobody decided
    // whether it may carry text, and this fails until someone does.
    const classified = new Set<string>([...TEXT_TOKENS, ...FILL_TOKENS, ...EXEMPT_TOKENS]);
    const unclassified = Object.keys(tokens).filter(
      (token) =>
        !classified.has(token) &&
        !NON_COLOUR_PREFIXES.some((prefix) => token.startsWith(prefix))
    );

    expect(unclassified, `classify these in lib/tokens.ts: ${unclassified.join(", ")}`).toEqual([]);
  });

  it("puts no token in two registries at once", () => {
    const all = [...TEXT_TOKENS, ...FILL_TOKENS, ...EXEMPT_TOKENS];
    expect(all.length).toBe(new Set(all).size);
  });
});

describe("the two sides are balanced in prose", () => {
  it("gives --bat-text and --bowl-text within 10% of the same contrast", () => {
    // Not an accessibility floor - a design one. At the 4.5 minimum the
    // bowling side reads visibly lighter than the batting side, which encodes
    // a preference the palette should not have.
    const bat = contrast(tokens["--bat-text"], paper);
    const bowl = contrast(tokens["--bowl-text"], paper);
    expect(Math.abs(bat - bowl) / Math.max(bat, bowl)).toBeLessThan(0.1);
  });

  it("keeps the fill and text variants of a side the same colour or darker", () => {
    // The text variant is the fill, darkened if it needs to be - never a
    // different hue, or the two stop reading as one side.
    //
    // Was `toBeLessThan`, asserting the text variant is strictly darker.
    // That held while fills were allowed down to 3:1 and --bowl needed a
    // separate darker variant to carry words. Since FILL_FLOOR rose to 4.5
    // the fill already clears the text floor, so the two are the SAME hex
    // and strict inequality is the wrong assertion - it would force an
    // arbitrary darkening with no reason behind it.
    for (const side of ["bat", "bowl"] as const) {
      const fill = tokens[`--${side}`];
      const text = tokens[`--${side}-text`];
      expect(luminance(text)).toBeLessThanOrEqual(luminance(fill));
      // Same colour, or a darkening of it. Compared channel by channel
      // rather than by luminance, because two unrelated hues can share a
      // luminance - teal and brown here differ by 1.01:1.
      const ratio = contrast(fill, text);
      expect(ratio, `--${side}-text is not a darkening of --${side}`).toBeLessThan(2);
    }
  });
});

describe("contrast", () => {
  it("is symmetric and bounded", () => {
    expect(contrast("#000000", "#FFFFFF")).toBeCloseTo(21, 1);
    expect(contrast("#FFFFFF", "#000000")).toBeCloseTo(21, 1);
    expect(contrast("#777777", "#777777")).toBeCloseTo(1, 5);
  });
});

// --- the spacing scale (UI-PHASE-2 step 2) --------------------------------
//
// There was no spacing scale before 2026-09-25 - a seven-step type scale and
// nothing for space - so 3, 5, 6, 7, 10, 14 and 18px accumulated across five
// sessions. These assert the scale exists, is a scale, and stays one.

describe("the spacing scale", () => {
  const lengths = parseLengthTokens(css);
  const px = (token: string) => {
    const value = lengths[token];
    expect(value, `${token} is missing from the stylesheet`).toBeDefined();
    return value;
  };

  it.each(SPACE_TOKENS)("%s is declared and on the 4px rhythm", (token) => {
    const value = px(token);
    expect(Number.isFinite(value)).toBe(true);
    expect(value % SPACE_STEP_PX).toBe(0);
  });

  it("increases monotonically, so the steps are ordered", () => {
    const values = SPACE_TOKENS.map(px);
    for (let i = 1; i < values.length; i += 1) {
      expect(values[i]).toBeGreaterThan(values[i - 1]);
    }
  });

  it("has no two steps with the same value", () => {
    const values = SPACE_TOKENS.map(px);
    expect(new Set(values).size).toBe(values.length);
  });
});
