/**
 * Which nav item is current.
 *
 * The rule is not "pathname equals href": /match/8429 is a match page and
 * should light Matches, /player/12 should light Players. That mapping is the
 * only logic in the component, so it is the thing worth testing — and it is
 * pure, so it needs no DOM.
 */

import { describe, expect, it } from "vitest";

import { isCurrent } from "./active-nav";

describe("isCurrent", () => {
  it("matches the exact page", () => {
    expect(isCurrent("/matches", "/matches")).toBe(true);
    expect(isCurrent("/ask", "/ask")).toBe(true);
  });

  it("lights Matches on a single match page", () => {
    expect(isCurrent("/match/8429", "/matches")).toBe(true);
  });

  it("lights Players on a single player page", () => {
    expect(isCurrent("/player/12", "/players")).toBe(true);
  });

  it("lights nothing on the landing page", () => {
    for (const href of ["/matches", "/players", "/ask", "/accuracy"]) {
      expect(isCurrent("/", href)).toBe(false);
    }
  });

  it("does not light Matches on an unrelated route", () => {
    expect(isCurrent("/accuracy", "/matches")).toBe(false);
    expect(isCurrent("/model-card", "/players")).toBe(false);
  });

  it("marks exactly one item current on any real route", () => {
    const items = ["/matches", "/players", "/ask", "/accuracy"];
    for (const path of ["/matches", "/players", "/ask", "/accuracy", "/match/1", "/player/1"]) {
      expect(items.filter((h) => isCurrent(path, h)), path).toHaveLength(1);
    }
  });

  it("survives a null pathname", () => {
    expect(isCurrent(null, "/matches")).toBe(false);
  });
});
