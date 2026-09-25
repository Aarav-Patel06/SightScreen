/**
 * The palette, classified by what each token is allowed to carry.
 *
 * UI-PHASE.md §1.4 gives seven hexes and a one-line role each. That was not
 * enough structure: --bowl shipped at 3.46:1 against --paper, which is a
 * correct chart fill and a WCAG AA failure for text, and nothing distinguished
 * the two uses. The contrast column on /design caught it. This module is the
 * fix - every token is classified, and lib/tokens.test.ts holds each class to
 * its own floor.
 *
 * WHY A REGISTRY RATHER THAN A LIST OF ASSERTIONS. A test that hardcodes
 * `contrast("#C4741A") >= 3.0` passes forever after someone edits the
 * stylesheet, because the test carries its own copy of the value. So the test
 * parses globals.css at runtime and checks that every token defined there
 * appears in exactly one class below. Adding a colour without deciding what it
 * may carry fails the build, which is the only way this stays true.
 */

/** WCAG 2.1 §1.4.3. Text below 18.66px bold / 24px regular needs 4.5:1. */
export const AA_TEXT = 4.5;
/** WCAG 2.1 §1.4.11's minimum for a graphical object. Kept for reference. */
export const AA_LARGE = 3.0;

/**
 * The floor fills are actually held to, and it is not AA_LARGE.
 *
 * 3.0 satisfies WCAG and still collapses the texture system. Measured on the
 * cream base with a teal at exactly 3.0: solid/hollow 2.84, solid/hatch 2.00,
 * hatch/hollow 1.42 - against 4.77 / 3.05 / 1.56 on the old palette. Every
 * accessibility check passes while the thing that makes SPEC.md §12.2 visible
 * goes quiet. At ~5:1 the ladder returns to 4.81 / 3.07 / 1.57 with the hatch
 * pitch untouched.
 *
 * So the tested floor is the one that binds, not the one the standard states.
 * Leaving it at 3.0 would leave a gap a future hue falls into with nothing
 * failing.
 */
export const FILL_FLOOR = 4.5;

/**
 * Backgrounds other than --paper, and what may be written on them.
 *
 * The old palette had one surface. This one has four, and contrast is a
 * property of a PAIR: --ink-soft is 5.58:1 on --paper and 3.52:1 on
 * --chrome. Only --ink clears 4.5:1 on the two chrome surfaces, so chrome
 * carries --ink and nothing else, and the test asserts it rather than
 * trusting whoever styles the next header.
 */
export const CHROME_TOKENS = ["--chrome", "--chrome-deep"] as const;
export const CHROME_SAFE_TEXT = ["--ink"] as const;

/** The base every ratio in this system is measured against. */
export const PAPER = "--paper";

/** Tokens that may carry words. Held to AA_TEXT. */
export const TEXT_TOKENS = ["--ink", "--ink-soft", "--bat-text", "--bowl-text", "--flag"] as const;

/** Tokens for chart marks and filled shapes. Held to AA_LARGE. Never text. */
export const FILL_TOKENS = ["--bat", "--bowl"] as const;

/**
 * Hairlines and the page base. Exempt from both floors, and the exemption is
 * the point of listing them: --rule is 1.25:1 and must be, because a 4.5:1
 * hairline is a black line. A token not in any list is a token nobody decided
 * about, and the test rejects it.
 */
export const EXEMPT_TOKENS = [
  "--paper",
  "--paper-raised",
  "--chrome",
  "--chrome-deep",
  "--rule",
] as const;

/** Type-scale, measure and layout tokens - not colours, so not classified. */
export const NON_COLOUR_PREFIXES = [
  "--t-",
  "--space-",
  "--measure",
  "--page",
  "--column",
  "--font-",
];

/**
 * The spacing scale, asserted rather than trusted.
 *
 * Added with the scale itself in UI-PHASE-2 step 2. A scale is only a scale
 * while every step is on it: the whole reason 3, 5, 6, 7, 10, 14 and 18px
 * accumulated over five sessions is that there was nothing to be off.
 */
export const SPACE_TOKENS = [
  "--space-1",
  "--space-2",
  "--space-3",
  "--space-4",
  "--space-5",
  "--space-6",
] as const;

/** The rhythm every step must land on. */
export const SPACE_STEP_PX = 4;

/**
 * Length tokens from the theme block, in pixels.
 *
 * Separate from `parseThemeTokens` rather than folded into it: that one
 * deliberately matches only six-digit hex, because every consumer of it is a
 * contrast calculation and a `68ch` in that map would become `NaN` inside a
 * luminance function rather than a failure anyone could read.
 */
export function parseLengthTokens(css: string): Record<string, number> {
  const block = css.match(/\.theme-paper\s*\{([\s\S]*?)\}/);
  if (!block) return {};

  const lengths: Record<string, number> = {};
  for (const match of block[1].matchAll(/(--[a-z0-9-]+)\s*:\s*(-?[0-9.]+)px\s*;/g)) {
    lengths[match[1]] = Number(match[2]);
  }
  return lengths;
}

/** WCAG 2.1 relative luminance of an #RRGGBB string. */
export function luminance(hex: string): number {
  const channels = [1, 3, 5].map((offset) => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

/** Contrast ratio between two #RRGGBB strings, 1:1 to 21:1. */
export function contrast(a: string, b: string): number {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
}

/**
 * Pull `--name: #HEX;` pairs out of the .theme-paper block of a stylesheet.
 *
 * Deliberately narrow: it reads only that block and only hex values, so a
 * token defined as `rgb(...)` or in another selector is not silently skipped -
 * it simply is not found, and the "every token is classified" check fails,
 * which is the behaviour we want from a gate.
 */
export function parseThemeTokens(css: string): Record<string, string> {
  const block = css.match(/\.theme-paper\s*\{([\s\S]*?)\}/);
  if (!block) return {};

  const tokens: Record<string, string> = {};
  for (const match of block[1].matchAll(/(--[a-z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{6})\s*;/g)) {
    tokens[match[1]] = match[2].toUpperCase();
  }
  return tokens;
}
