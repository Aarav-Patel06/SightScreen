/**
 * The gate and both caps, exercised through the REAL route handlers.
 *
 * lib/ask-gate.test.ts and lib/agent-usage.test.ts prove the pieces. This
 * proves they are actually WIRED - that the expensive call really does sit
 * behind all three checks, in the right order, and that nothing reaches
 * Anthropic when any of them refuses.
 *
 * The decisive assertion in most of these is not the status code. It is
 * `expect(createMessage).not.toHaveBeenCalled()` - a gate that returns 401
 * and calls the model anyway has failed at the only job it has.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const createMessage = vi.fn();
vi.mock("@anthropic-ai/sdk", () => ({
  default: class {
    messages = { create: createMessage };
  },
}));

// lib/env.ts validates eagerly at module load, which is right for a server
// module and means any test importing something downstream of it needs the
// real variables. Mocked so these tests assert on the gate rather than on
// Supabase configuration.
vi.mock("@/lib/env", () => ({
  env: {
    NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co",
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "test-publishable",
    SUPABASE_SECRET_KEY: "test-secret",
  },
}));

const overCap = vi.fn(async () => false);
// Typed with its parameter so `record.mock.calls[0][0]` is the usage object
// rather than a zero-length tuple - the assertion that spend is actually
// recorded is worth keeping type-checked.
const record = vi.fn(async (_usage: Record<string, number>) => {});
vi.mock("@/lib/agent-usage", async () => {
  const actual = await vi.importActual<typeof import("@/lib/agent-usage")>("@/lib/agent-usage");
  return { ...actual, isOverDailyCap: overCap, recordUsage: record };
});

vi.mock("@/lib/agent-prompt", () => ({ SYSTEM_PROMPT: "system", PROMPT_FINGERPRINT: "x" }));
vi.mock("@/lib/agent-tools", () => ({ AGENT_TOOLS: [] }));

const { POST: agent } = await import("./route");
const { POST: signIn } = await import("../ask/session/route");
const { ASK_COOKIE, MAX_MESSAGES_PER_SESSION, issueCookie } = await import("@/lib/ask-gate");
const { capMessage } = await import("@/lib/agent-usage");

const SECRET = "route-test-session-secret";
const PASSWORD = "correct-horse-battery-staple";

function ask(cookie?: string, question = "How many runs has Virat Kohli scored?") {
  return new Request("http://localhost/api/agent", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(cookie ? { cookie: `${ASK_COOKIE}=${cookie}` } : {}),
    },
    body: JSON.stringify({ question }),
  });
}

function signInRequest(password: string) {
  return new Request("http://localhost/api/ask/session", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

beforeEach(() => {
  process.env.ASK_SESSION_SECRET = SECRET;
  process.env.ASK_PASSWORD = PASSWORD;
  process.env.ANTHROPIC_API_KEY = "sk-test";
  process.env.API_BASE_URL = "http://api.test";
  process.env.AGENT_TOOL_SHARED_SECRET = "tool-secret";
  createMessage.mockReset();
  createMessage.mockResolvedValue({
    content: [{ type: "text", text: "Kohli faced 26,227 deliveries." }],
    usage: { input_tokens: 100, output_tokens: 20 },
  });
  overCap.mockReset();
  overCap.mockResolvedValue(false);
  record.mockReset();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => vi.restoreAllMocks());

describe("signing in", () => {
  it("rejects the wrong password and sets no cookie", async () => {
    const response = await signIn(signInRequest("wrong"));
    expect(response.status).toBe(401);
    expect(response.cookies.get(ASK_COOKIE)).toBeUndefined();
  });

  it("accepts the right password and sets an httpOnly cookie", async () => {
    const response = await signIn(signInRequest(PASSWORD));
    expect(response.status).toBe(200);
    const cookie = response.cookies.get(ASK_COOKIE);
    expect(cookie).toBeDefined();
    expect(cookie?.httpOnly).toBe(true);
    expect(cookie?.sameSite).toBe("lax");
  });

  it("refuses everything when ASK_PASSWORD is unset", async () => {
    // A misconfigured deployment must be closed, not open.
    process.env.ASK_PASSWORD = "";
    const response = await signIn(signInRequest(""));
    expect(response.status).toBe(503);
    expect(response.cookies.get(ASK_COOKIE)).toBeUndefined();
  });

  it("a cookie from sign-in is accepted by the agent route", async () => {
    const issued = await signIn(signInRequest(PASSWORD));
    const value = issued.cookies.get(ASK_COOKIE)?.value;
    const response = await agent(ask(value));
    expect(response.status).toBe(200);
    expect(createMessage).toHaveBeenCalledTimes(1);
  });
});

describe("the gate stands in front of the model", () => {
  it("no cookie: refuses and never calls Anthropic", async () => {
    const response = await agent(ask(undefined));
    expect(response.status).toBe(401);
    expect(createMessage).not.toHaveBeenCalled();
  });

  it("tampered cookie: refuses and never calls Anthropic", async () => {
    const good = issueCookie(SECRET);
    const [expiry, messages] = good.split(".");
    const forged = `${expiry}.${messages}.${"a".repeat(64)}`;
    const response = await agent(ask(forged));
    expect(response.status).toBe(401);
    expect(createMessage).not.toHaveBeenCalled();
  });

  it("cookie signed with another secret: refuses", async () => {
    const response = await agent(ask(issueCookie("a-different-secret")));
    expect(response.status).toBe(401);
    expect(createMessage).not.toHaveBeenCalled();
  });

  it("expired cookie: refuses", async () => {
    const past = Date.now() - 48 * 60 * 60 * 1000;
    const response = await agent(ask(issueCookie(SECRET, past)));
    expect(response.status).toBe(401);
    expect(createMessage).not.toHaveBeenCalled();
  });

  it("never tells the caller WHICH way the cookie was bad", async () => {
    const tampered = await agent(ask(`${Math.floor(Date.now() / 1000) + 99}.0.${"b".repeat(64)}`));
    const missing = await agent(ask(undefined));
    expect(await tampered.clone().json()).toEqual(await missing.clone().json());
  });
});

describe("the per-session cap", () => {
  it("refuses at the limit with a readable message, not a 500", async () => {
    const exhausted = issueCookie(SECRET, Date.now(), MAX_MESSAGES_PER_SESSION);
    const response = await agent(ask(exhausted));
    expect(response.status).toBe(429);
    expect(createMessage).not.toHaveBeenCalled();
    const body = await response.json();
    expect(body.error).toContain(String(MAX_MESSAGES_PER_SESSION));
    expect(body.error.toLowerCase()).not.toContain("error");
  });

  it("counts up: the response carries an incremented cookie", async () => {
    const response = await agent(ask(issueCookie(SECRET)));
    const next = response.cookies.get(ASK_COOKIE)?.value;
    expect(next).toBeDefined();
    expect(next?.split(".")[1]).toBe("1");
  });
});

describe("the daily cost cap", () => {
  it("refuses when over, and never calls Anthropic", async () => {
    overCap.mockResolvedValue(true);
    const response = await agent(ask(issueCookie(SECRET)));
    expect(response.status).toBe(429);
    expect(createMessage).not.toHaveBeenCalled();
  });

  it("shows the message an interviewer would read", async () => {
    overCap.mockResolvedValue(true);
    const response = await agent(ask(issueCookie(SECRET)));
    const body = await response.json();
    expect(body.error).toBe(capMessage());
    expect(body.error.toLowerCase()).toContain("nothing is broken");
    expect(body.error).not.toMatch(/\$\s?\d/);
  });

  it("is checked AFTER the cookie, so an anonymous request costs no database read", async () => {
    await agent(ask(undefined));
    expect(overCap).not.toHaveBeenCalled();
  });

  it("records spend on a successful conversation", async () => {
    await agent(ask(issueCookie(SECRET)));
    expect(record).toHaveBeenCalledTimes(1);
    expect(record.mock.calls[0][0]).toMatchObject({ input_tokens: 100, output_tokens: 20 });
  });

  it("records spend even when the loop throws", async () => {
    // Otherwise a crash loop is a free one, and the cap never sees it.
    createMessage.mockRejectedValue(new Error("upstream exploded"));
    const response = await agent(ask(issueCookie(SECRET)));
    expect(response.status).toBe(502);
    expect(record).toHaveBeenCalledTimes(1);
  });
});

describe("misconfiguration", () => {
  it("refuses when the API key is absent rather than calling with undefined", async () => {
    process.env.ANTHROPIC_API_KEY = "";
    const response = await agent(ask(issueCookie(SECRET)));
    expect(response.status).toBe(503);
    expect(createMessage).not.toHaveBeenCalled();
  });

  it("rejects an empty question before spending anything", async () => {
    const response = await agent(ask(issueCookie(SECRET), "   "));
    expect(response.status).toBe(400);
    expect(createMessage).not.toHaveBeenCalled();
  });
});
