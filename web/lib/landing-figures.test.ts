/**
 * The landing page's fallback behaviour.
 *
 * The figures themselves are uninteresting - they are counts. What is worth
 * testing is the path taken when the database does not answer, because that
 * path is the difference between the front door rendering and the front door
 * being a 500, and it is exercised only when something is broken, which is
 * exactly when nobody is watching.
 *
 * Supabase is stubbed rather than reached. A test that needs the network to
 * pass cannot assert anything about what happens when the network is gone.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CORPUS_FACTS } from "./corpus-facts";

const maybeSingle = vi.fn();
const headCount = vi.fn();

vi.mock("./supabase-server", () => ({
  supabaseServer: () => ({
    from: (table: string) => ({
      select: (_columns: string, options?: { head?: boolean }) => {
        if (options?.head) return headCount(table);
        return {
          order: () => ({ limit: () => ({ maybeSingle }) }),
        };
      },
    }),
  }),
}));

const { loadLandingFigures } = await import("./landing-figures");

const GOOD_REPORT = {
  model_version: "winprob2-20260910",
  populations: {
    backfill: { n: 12081, n_matches: 100, unresolved: { predictions: 0, matches: 0 } },
    live: { n: 0, n_matches: 0, unresolved: { predictions: 0, matches: 0 } },
  },
};

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  maybeSingle.mockReset();
  headCount.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function healthy() {
  maybeSingle.mockResolvedValue({ data: { report: GOOD_REPORT }, error: null });
  headCount.mockResolvedValue({ count: 18468, error: null });
}

describe("when the database answers", () => {
  it("reports live figures and flags nothing stale", async () => {
    healthy();
    const figures = await loadLandingFigures();

    expect(figures.matchesWithPredictions).toEqual({ value: 100, stale: false });
    expect(figures.players).toEqual({ value: 18468, stale: false });
    expect(figures.anyStale).toBe(false);
  });

  it("sums the backfilled and live populations", async () => {
    maybeSingle.mockResolvedValue({
      data: {
        report: {
          ...GOOD_REPORT,
          populations: {
            backfill: { n_matches: 100 },
            live: { n_matches: 3 },
          },
        },
      },
      error: null,
    });
    headCount.mockResolvedValue({ count: 18468, error: null });

    const figures = await loadLandingFigures();
    expect(figures.matchesWithPredictions.value).toBe(103);
  });
});

describe("when the database does not answer", () => {
  it("falls back to committed counts rather than throwing", async () => {
    maybeSingle.mockResolvedValue({ data: null, error: { message: "Tenant or user not found" } });
    headCount.mockResolvedValue({ count: null, error: { message: "Tenant or user not found" } });

    const figures = await loadLandingFigures();

    expect(figures.matchesWithPredictions.value).toBe(CORPUS_FACTS.matchesWithPredictions);
    expect(figures.players.value).toBe(CORPUS_FACTS.players);
    expect(figures.anyStale).toBe(true);
    expect(figures.matchesWithPredictions.countedAt).toBe(CORPUS_FACTS.countedAt);
  });

  it("survives a thrown error, not just a returned one", async () => {
    // A paused project surfaces as a rejected promise from the client, not as
    // an { error } tuple, so the try/catch is load-bearing and not belt-and-
    // braces around the tuple check.
    maybeSingle.mockRejectedValue(new Error("fetch failed"));
    headCount.mockRejectedValue(new Error("fetch failed"));

    const figures = await loadLandingFigures();
    expect(figures.anyStale).toBe(true);
    expect(figures.players.value).toBe(CORPUS_FACTS.players);
  });

  it("says so in the log rather than falling back silently", async () => {
    // Standing rule 14. A silent fallback makes a permanent outage and a
    // two-second blip indistinguishable in hindsight.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    maybeSingle.mockRejectedValue(new Error("fetch failed"));
    headCount.mockRejectedValue(new Error("fetch failed"));

    await loadLandingFigures();

    expect(warn).toHaveBeenCalledTimes(2);
    expect(warn.mock.calls.flat().join(" ")).toContain("fetch failed");
  });

  it("logs a usable reason even when the error carries no message", async () => {
    // The first real run of this logging printed `query failed: ` with
    // nothing after it, because a Supabase error is not always an Error and
    // does not always have `.message`. A line that proves something broke and
    // says nothing about what is the line rule 14 is against.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    maybeSingle.mockResolvedValue({ data: null, error: { code: "PGRST301", hint: "check clock" } });
    headCount.mockResolvedValue({ count: null, error: {} });

    await loadLandingFigures();

    const logged = warn.mock.calls.flat().join(" ");
    expect(logged).toContain("PGRST301");
    expect(logged).toContain("check clock");
    // The messageless one still says something rather than trailing off.
    expect(logged).not.toMatch(/query failed:\s*$/m);
    expect(logged).not.toContain("[object Object]");
  });

  it("does not warn when everything is healthy", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    healthy();

    await loadLandingFigures();
    expect(warn).not.toHaveBeenCalled();
  });
});

describe("partial failure", () => {
  it("keeps the figure that worked and falls back only on the one that did not", async () => {
    maybeSingle.mockResolvedValue({ data: { report: GOOD_REPORT }, error: null });
    headCount.mockResolvedValue({ count: null, error: { message: "boom" } });

    const figures = await loadLandingFigures();

    expect(figures.matchesWithPredictions.stale).toBe(false);
    expect(figures.players.stale).toBe(true);
    expect(figures.anyStale).toBe(true);
  });
});

describe("deliveries", () => {
  it("is always the committed count and is never marked stale", async () => {
    // It is not a fallback - it is the only place the number exists, because
    // `deliveries` is empty on Supabase by design. The page labels its
    // provenance separately rather than calling it out of date.
    healthy();
    const figures = await loadLandingFigures();

    expect(figures.deliveries).toEqual({ value: CORPUS_FACTS.deliveries, stale: false });
    expect(figures.anyStale).toBe(false);
  });
});
