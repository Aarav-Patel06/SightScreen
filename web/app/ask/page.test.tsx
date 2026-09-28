/**
 * /ask says what the live site can actually do (SPEC.md section 0a,
 * "production has no corpus", deferred on cost).
 *
 * Production's agent reaches the model, but the three corpus tools answer
 * 503: there is no hosted copy of the ball-by-ball data. So the page is
 * marked beta, says in one line what is missing, offers no example question
 * that needs the corpus (all four did), and carries no notes written for the
 * developer rather than the visitor.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AskPage from "./page";
import { corpusOnThisSite } from "@/lib/ask-corpus";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function signIn() {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
  fireEvent.change(screen.getByLabelText(/password-protected/i), { target: { value: "pw" } });
  fireEvent.click(screen.getByRole("button", { name: "Enter" }));
  await screen.findByLabelText("Your question");
}

describe("/ask on the live site", () => {
  beforeEach(() => {
    render(<AskPage />);
  });

  it("is marked beta beside the heading", () => {
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toMatch(/^Ask\s*Beta$/);
  });

  it("says in one line what is not available yet", () => {
    expect(
      screen.getByText("In beta: questions that need the ball-by-ball corpus aren’t available on the live site yet.")
    ).toBeTruthy();
  });

  it("does not claim the live agent answers from 3.78 million deliveries", () => {
    expect(document.body.textContent).not.toMatch(/3\.78 million/);
  });

  it("offers no example question, since every one needs the corpus", async () => {
    await signIn();
    expect(document.querySelectorAll(".ask-chip")).toHaveLength(0);
  });

  it("carries no notes written for the developer, before or after an answer", async () => {
    await signIn();
    expect(document.body.textContent).not.toMatch(/Clicking one fills the box/);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ answer: "An answer." }), { status: 200 })));
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Who won?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("An answer.");
    expect(document.body.textContent).not.toMatch(/SQL each answer ran/);
  });

  it("states the cost for visitors", async () => {
    await signIn();
    expect(document.body.textContent).toMatch(/about 1\.7\s*cents/);
  });
});

describe("where the corpus is available", () => {
  it("is only the owner's development server today", () => {
    expect(corpusOnThisSite("development")).toBe(true);
    expect(corpusOnThisSite("production")).toBe(false);
    expect(corpusOnThisSite("test")).toBe(false);
  });
});
