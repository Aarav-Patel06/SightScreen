/**
 * The header's live detection, tested without a database or a clock.
 *
 * Both functions here guard against a specific wrong-looking-right failure:
 * an abbreviation that looks like an official team code but isn't, and a
 * recency check that answers `false` for an unparseable timestamp by
 * accident rather than by decision.
 */

import { describe, expect, it } from "vitest";

import { LIVE_WINDOW_MS, isRecent, shortName } from "./live-match";

describe("shortName", () => {
  it("takes initials of the significant words", () => {
    expect(shortName("Sharjah Warriorz")).toBe("SW");
    expect(shortName("Abu Dhabi Knight Riders")).toBe("ADK");
    expect(shortName("Chennai Super Kings")).toBe("CSK");
  });

  it("drops joining words that carry no identity", () => {
    expect(shortName("Board of Control XI")).toBe("BC");
  });

  it("falls back to a prefix when only one significant word survives", () => {
    // "Cricket" and "Club" are dropped, leaving one word - and "M" would be
    // a worse label than "MAR", so the single-word branch takes a prefix.
    expect(shortName("Marylebone Cricket Club")).toBe("MAR");
    expect(shortName("Warriorz")).toBe("WAR");
    expect(shortName("Mumbai")).toBe("MUM");
  });

  it("handles hyphenated names", () => {
    expect(shortName("Sylhet Sun-Risers")).toBe("SSR");
  });

  it("returns nothing visible rather than whitespace for a blank name", () => {
    // Untrimmed, this returned three spaces - a label that is present in the
    // DOM, occupies width, and shows nothing, which is harder to notice than
    // an empty one.
    expect(shortName("   ")).toBe("");
    expect(shortName("")).toBe("");
  });

  it("keeps the name when every word is a joining word", () => {
    expect(shortName("XI")).toBe("XI");
  });
});

describe("isRecent", () => {
  const now = Date.parse("2026-09-23T12:00:00Z");

  it("accepts a prediction from seconds ago", () => {
    expect(isRecent("2026-09-23T11:59:45Z", now)).toBe(true);
  });

  it("rejects one older than the window", () => {
    expect(isRecent("2026-09-23T11:54:00Z", now)).toBe(false);
  });

  it("is exclusive at exactly the window", () => {
    const exactly = new Date(now - LIVE_WINDOW_MS).toISOString();
    expect(isRecent(exactly, now)).toBe(false);
  });

  it("rejects a timestamp from the future", () => {
    // Clock skew between the worker and the renderer. A negative age is not a
    // live match, it is a misconfiguration, and reporting live would hide it.
    expect(isRecent("2026-09-23T12:05:00Z", now)).toBe(false);
  });

  it("rejects an unparseable timestamp explicitly, not via NaN", () => {
    // Standing rule 15. Date.parse returns NaN and every comparison against
    // NaN is false, so this would pass without the isFinite guard - but for
    // the wrong reason, and it would break the moment someone reversed the
    // comparison to `age > windowMs`.
    expect(isRecent("not a date", now)).toBe(false);
    expect(isRecent("", now)).toBe(false);
  });

  it("rejects the September 2026 rows that matches.status still calls live", () => {
    // The concrete case this whole module exists for: the worker tracked
    // three matches in September 2026 and nothing ever cleared their status.
    expect(isRecent("2026-09-18T14:30:00Z", now)).toBe(false);
  });
});
