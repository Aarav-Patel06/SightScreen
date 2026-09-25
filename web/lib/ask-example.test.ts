/**
 * The landing page's Ask example is a real recorded exchange, and stays one.
 *
 * §6 requires the module render "one real example question and its cited
 * answer, static" with no model call on page load. So it is committed - and a
 * committed quotation is exactly the thing that drifts from its source while
 * both look fine in isolation.
 *
 * This asserts the fixture is still verbatim from the eval artifact it came
 * from. If someone edits the fixture to read better, this fails.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import EXAMPLE from "./fixtures/ask-example.json";

const PROOF = resolve(process.cwd(), "../api/data/agent_eval/proof-citation-venue.json");
const proof = JSON.parse(readFileSync(PROOF, "utf8"));

describe("the committed Ask exchange", () => {
  it("quotes the recorded question exactly", () => {
    expect(EXAMPLE.question).toBe(proof.question);
  });

  it("quotes the recorded answer's opening verbatim", () => {
    const first = (proof.control.answer as string).split("\n\n")[0].trim();
    expect(EXAMPLE.excerpt).toBe(first);
  });

  it("carries the date it was asked, and the model that answered", () => {
    // A quotation without a date is a claim about now.
    expect(EXAMPLE.askedAt).toBe((proof.run_at as string).slice(0, 10));
    expect(EXAMPLE.model).toBe(proof.model);
  });

  it("is an answer that qualifies itself", () => {
    // Why this exchange and not the Kohli-vs-Starc one: that answer's T20
    // dismissal count came from get_matchup BEFORE its FILTER was corrected,
    // and re-checking it against the corpus on 2026-09-25 gives 0 where the
    // recorded answer says 1. This one re-verified exactly. The property
    // below is also why it is the better example - it volunteers that 81
    // balls is too small to trust.
    expect(EXAMPLE.excerpt).toMatch(/small sample/i);
    expect(EXAMPLE.excerpt).toContain("81");
  });
});
