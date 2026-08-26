import { beforeEach, describe, expect, it, vi } from "vitest";

const REQUIRED_VARS = {
  NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co",
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  SUPABASE_SECRET_KEY: "sb_secret_test",
};

describe("env", () => {
  beforeEach(() => {
    vi.resetModules();
    for (const key of Object.keys(REQUIRED_VARS)) delete process.env[key];
  });

  it("loads when all required vars are present", async () => {
    Object.assign(process.env, REQUIRED_VARS);
    const { env } = await import("./env");
    expect(env.NEXT_PUBLIC_SUPABASE_URL).toBe(REQUIRED_VARS.NEXT_PUBLIC_SUPABASE_URL);
    expect(env.API_BASE_URL).toBeUndefined();
  });

  it("throws when a required var is missing", async () => {
    process.env.NEXT_PUBLIC_SUPABASE_URL = REQUIRED_VARS.NEXT_PUBLIC_SUPABASE_URL;
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY =
      REQUIRED_VARS.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
    // SUPABASE_SECRET_KEY intentionally left unset
    await expect(import("./env")).rejects.toThrow(/SUPABASE_SECRET_KEY/);
  });

  it("throws when NEXT_PUBLIC_SUPABASE_URL has a /rest/ path suffix", async () => {
    Object.assign(process.env, REQUIRED_VARS);
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co/rest/v1/";
    await expect(import("./env")).rejects.toThrow(/\/rest\//);
  });

  it("throws when a required var still holds a placeholder value", async () => {
    Object.assign(process.env, REQUIRED_VARS);
    process.env.SUPABASE_SECRET_KEY = "sb_secret_...";
    await expect(import("./env")).rejects.toThrow(/placeholder/);
  });
});
