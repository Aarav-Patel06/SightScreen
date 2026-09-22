/**
 * The daily cost cap - the one that actually bounds the bill.
 *
 * The per-session cap lives in a cookie the visitor holds and resets when
 * they clear it. This one lives in a database they cannot reach, so these
 * are the assertions that matter for "a determined visitor cannot run up a
 * bill".
 *
 * Supabase is mocked rather than reached: the point of every test here is a
 * DECISION made from a number, and a network round trip would add nothing
 * except a reason for the suite to be flaky.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const selectResult = { data: null as unknown, error: null as unknown };
const upsertResult = { error: null as unknown };
const upsertSpy = vi.fn(async (row: unknown) => {
  upsertSpy.mock.calls.at(-1);
  return upsertResult;
});

vi.mock("@supabase/supabase-js", () => ({
  createClient: () => ({
    from: () => ({
      select: () => ({
        eq: () => ({ maybeSingle: async () => selectResult }),
      }),
      upsert: upsertSpy,
    }),
  }),
}));

vi.mock("./env", () => ({
  env: {
    NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co",
    SUPABASE_SECRET_KEY: "test-secret",
  },
}));

const { capMessage, costOf, isOverDailyCap, recordUsage, utcDay, DAILY_COST_CAP_USD } =
  await import("./agent-usage");

beforeEach(() => {
  selectResult.data = null;
  selectResult.error = null;
  upsertResult.error = null;
  upsertSpy.mockClear();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("cost", () => {
  it("prices every token class, not just input and output", () => {
    // Cache reads are a tenth of input and writes are 1.25x. A cap that
    // ignored them would undercount a cached agent by roughly half.
    const cost = costOf({
      input_tokens: 1_000_000,
      output_tokens: 1_000_000,
      cache_read_input_tokens: 1_000_000,
      cache_creation_input_tokens: 1_000_000,
    });
    expect(cost).toBeCloseTo(2.0 + 10.0 + 0.2 + 2.5, 6);
  });

  it("treats missing fields as zero rather than NaN", () => {
    // A NaN total compares false against the cap, which would silently
    // disable it - the worst possible failure for this particular check.
    const cost = costOf({});
    expect(cost).toBe(0);
    expect(Number.isNaN(cost)).toBe(false);
  });

  it("a realistic conversation costs cents", () => {
    const cost = costOf({
      input_tokens: 6_000,
      output_tokens: 900,
      cache_read_input_tokens: 4_000,
      cache_creation_input_tokens: 2_000,
    });
    expect(cost).toBeLessThan(0.05);
  });
});

describe("the daily cap", () => {
  it("defaults to $2", () => {
    expect(DAILY_COST_CAP_USD).toBe(2);
  });

  it("admits when today's spend is under it", async () => {
    selectResult.data = { cost_usd: 0.42 };
    expect(await isOverDailyCap()).toBe(false);
  });

  it("admits when there is no row for today at all", async () => {
    selectResult.data = null;
    expect(await isOverDailyCap()).toBe(false);
  });

  it("refuses AT the cap, not only past it", async () => {
    selectResult.data = { cost_usd: DAILY_COST_CAP_USD };
    expect(await isOverDailyCap()).toBe(true);
  });

  it("refuses past the cap", async () => {
    selectResult.data = { cost_usd: DAILY_COST_CAP_USD + 0.01 };
    expect(await isOverDailyCap()).toBe(true);
  });

  it("handles NUMERIC arriving as a string", async () => {
    // Supabase renders NUMERIC as text. `"2.5" >= 2` is true in JS by
    // coercion, but `"10" >= 2` compares as numbers only because both sides
    // coerce - relying on that is how a cap silently stops working.
    selectResult.data = { cost_usd: "2.50" };
    expect(await isOverDailyCap()).toBe(true);
    selectResult.data = { cost_usd: "0.50" };
    expect(await isOverDailyCap()).toBe(false);
  });

  it("FAILS CLOSED when the ledger cannot be read", async () => {
    // The decisive one. If spend is unknowable, "unknown" must not mean
    // "proceed" for the only unbounded expense in the project.
    selectResult.error = { message: "connection refused" };
    expect(await isOverDailyCap()).toBe(true);
  });
});

describe("what the visitor is told", () => {
  it("reads like it was written on purpose", () => {
    const message = capMessage();
    expect(message.toLowerCase()).toContain("daily budget");
    expect(message.toLowerCase()).toContain("tomorrow");
    expect(message.toLowerCase()).toContain("nothing is broken");
  });

  it("does NOT reveal how much has been spent or how much remains", () => {
    // That is a progress bar for anyone trying to exhaust it.
    const message = capMessage();
    expect(message).not.toMatch(/\$\s?\d/);
    expect(message.toLowerCase()).not.toContain("remaining");
  });
});

describe("recording", () => {
  it("adds to an existing day rather than overwriting it", async () => {
    selectResult.data = {
      conversations: 3,
      input_tokens: 100,
      output_tokens: 20,
      cache_read_tokens: 5,
      cache_write_tokens: 2,
      cost_usd: "0.10",
    };
    await recordUsage({ input_tokens: 50, output_tokens: 10 });
    const row = upsertSpy.mock.calls.at(-1)?.[0] as Record<string, number>;
    expect(row.conversations).toBe(4);
    expect(row.input_tokens).toBe(150);
    expect(row.output_tokens).toBe(30);
    expect(row.cost_usd).toBeGreaterThan(0.1);
  });

  it("creates the first row of a new day", async () => {
    selectResult.data = null;
    await recordUsage({ input_tokens: 10, output_tokens: 5 });
    const row = upsertSpy.mock.calls.at(-1)?.[0] as Record<string, number>;
    expect(row.conversations).toBe(1);
    expect(row.input_tokens).toBe(10);
  });

  it("does not throw when the write fails, but complains loudly", async () => {
    // The visitor already has their answer; failing their request now is
    // pure punishment. But unrecorded spend is spend the cap cannot see, so
    // it must be noisy - the agent_query_log lesson, where a known-harmless
    // error on every run trained everyone to read past that spot.
    upsertResult.error = { message: "write failed" };
    await expect(recordUsage({ input_tokens: 1 })).resolves.toBeUndefined();
    expect(console.error).toHaveBeenCalled();
    const logged = (console.error as unknown as { mock: { calls: string[][] } }).mock.calls
      .flat()
      .join(" ");
    expect(logged.toLowerCase()).toContain("uncapped");
  });
});

describe("the day boundary", () => {
  it("is UTC, so the cap resets at a time that does not depend on the reader", () => {
    expect(utcDay(new Date("2026-09-21T23:59:59Z"))).toBe("2026-09-21");
    expect(utcDay(new Date("2026-09-22T00:00:01Z"))).toBe("2026-09-22");
  });
});
