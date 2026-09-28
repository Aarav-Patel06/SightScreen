/**
 * The commit this Vercel deployment was built from, so "deployed" can be
 * checked against main rather than assumed (scripts/check-deployed.mjs).
 */

import { afterEach, describe, expect, it } from "vitest";

import { GET } from "./route";

afterEach(() => {
  delete process.env.VERCEL_GIT_COMMIT_SHA;
});

describe("/api/version", () => {
  it("reports the commit Vercel built", async () => {
    process.env.VERCEL_GIT_COMMIT_SHA = "fbff394504a8edd2350da229f759c8f91bab379a";
    const body = await (await GET()).json();
    expect(body).toEqual({ service: "web", git_sha: "fbff394504a8edd2350da229f759c8f91bab379a" });
  });

  it("says unknown outside a Vercel build", async () => {
    const body = await (await GET()).json();
    expect(body.git_sha).toBe("unknown");
  });
});
