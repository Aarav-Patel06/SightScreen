/**
 * The /ask gate, proven before anything is deployed.
 *
 * An unauthenticated LLM endpoint with a real API key behind it, on a public
 * repository, is the one unbounded expense in this project. Every refusal
 * path it has is asserted here, including the ones a visitor should never be
 * able to tell apart.
 */

import { describe, expect, it } from "vitest";

import {
  ASK_COOKIE,
  MAX_MESSAGES_PER_SESSION,
  SESSION_TTL_SECONDS,
  cookieOptions,
  incrementCookie,
  issueCookie,
  passwordMatches,
  refusalMessage,
  verifyCookie,
} from "./ask-gate";

const SECRET = "test-session-secret-not-a-real-one";
const NOW = Date.UTC(2026, 8, 21, 12, 0, 0);

describe("the password", () => {
  it("accepts the right one", () => {
    expect(passwordMatches("hunter2", "hunter2")).toBe(true);
  });

  it("rejects the wrong one", () => {
    expect(passwordMatches("hunter3", "hunter2")).toBe(false);
  });

  it("rejects a prefix, so length alone is not the check", () => {
    expect(passwordMatches("hunter", "hunter2")).toBe(false);
    expect(passwordMatches("hunter22", "hunter2")).toBe(false);
  });

  it("treats an UNSET password as closed, never as open", () => {
    // The failure that would matter: deploying without ASK_PASSWORD and
    // getting an open endpoint rather than a broken one.
    expect(passwordMatches("", "")).toBe(false);
    expect(passwordMatches("anything", "")).toBe(false);
  });
});

describe("the cookie", () => {
  it("round-trips a freshly issued session", () => {
    const cookie = issueCookie(SECRET, NOW);
    const result = verifyCookie(cookie, SECRET, NOW);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.session.messages).toBe(0);
      expect(result.session.expiresAt).toBe(Math.floor(NOW / 1000) + SESSION_TTL_SECONDS);
    }
  });

  it("rejects a missing cookie", () => {
    const result = verifyCookie(undefined, SECRET, NOW);
    expect(result).toEqual({ ok: false, reason: "missing" });
  });

  it("rejects a cookie signed with a different secret", () => {
    const forged = issueCookie("some-other-secret", NOW);
    const result = verifyCookie(forged, SECRET, NOW);
    expect(result).toEqual({ ok: false, reason: "tampered" });
  });

  it("rejects an EDITED EXPIRY", () => {
    // The obvious attack: take a real cookie, push the expiry out a year.
    const cookie = issueCookie(SECRET, NOW);
    const [, messages, mac] = cookie.split(".");
    const farFuture = Math.floor(NOW / 1000) + 365 * 24 * 3600;
    const tampered = `${farFuture}.${messages}.${mac}`;
    expect(verifyCookie(tampered, SECRET, NOW)).toEqual({ ok: false, reason: "tampered" });
  });

  it("rejects an EDITED MESSAGE COUNT", () => {
    // The other obvious one: reset your own per-session counter to zero.
    const exhausted = issueCookie(SECRET, NOW, MAX_MESSAGES_PER_SESSION);
    const [expiry, , mac] = exhausted.split(".");
    const tampered = `${expiry}.0.${mac}`;
    expect(verifyCookie(tampered, SECRET, NOW)).toEqual({ ok: false, reason: "tampered" });
  });

  it("rejects a wrong HMAC", () => {
    const cookie = issueCookie(SECRET, NOW);
    const [expiry, messages] = cookie.split(".");
    const tampered = `${expiry}.${messages}.${"0".repeat(64)}`;
    expect(verifyCookie(tampered, SECRET, NOW)).toEqual({ ok: false, reason: "tampered" });
  });

  it("rejects structurally malformed values without throwing", () => {
    for (const bad of ["", "a", "a.b", "a.b.c.d", "....", "x.y.z"]) {
      const result = verifyCookie(bad, SECRET, NOW);
      expect(result.ok).toBe(false);
    }
  });

  it("rejects an EXPIRED cookie", () => {
    const cookie = issueCookie(SECRET, NOW);
    const later = NOW + (SESSION_TTL_SECONDS + 1) * 1000;
    expect(verifyCookie(cookie, SECRET, later)).toEqual({ ok: false, reason: "expired" });
  });

  it("checks the signature BEFORE the expiry", () => {
    // Otherwise a forgery whose expiry happens to be in the past reports
    // "expired", which tells the forger their signature was never examined.
    const forged = issueCookie("wrong-secret", NOW);
    const later = NOW + (SESSION_TTL_SECONDS + 1) * 1000;
    expect(verifyCookie(forged, SECRET, later)).toEqual({ ok: false, reason: "tampered" });
  });
});

describe("the per-session message cap", () => {
  it("counts up and stays valid until the limit", () => {
    let cookie = issueCookie(SECRET, NOW);
    for (let i = 1; i < MAX_MESSAGES_PER_SESSION; i += 1) {
      const result = verifyCookie(cookie, SECRET, NOW);
      expect(result.ok).toBe(true);
      if (result.ok) cookie = incrementCookie(result.session, SECRET);
    }
    const result = verifyCookie(cookie, SECRET, NOW);
    expect(result.ok).toBe(true);
  });

  it("refuses at the limit, and says so rather than erroring", () => {
    const cookie = issueCookie(SECRET, NOW, MAX_MESSAGES_PER_SESSION);
    const result = verifyCookie(cookie, SECRET, NOW);
    expect(result).toEqual({ ok: false, reason: "exhausted" });

    const message = refusalMessage("exhausted");
    expect(message).toContain(String(MAX_MESSAGES_PER_SESSION));
    expect(message.toLowerCase()).toContain("reload");
    // A cap that surfaces as a stack trace reads like a broken site.
    expect(message.toLowerCase()).not.toContain("error");
  });

  it("an incremented cookie is still verifiable", () => {
    const first = verifyCookie(issueCookie(SECRET, NOW), SECRET, NOW);
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    const next = verifyCookie(incrementCookie(first.session, SECRET), SECRET, NOW);
    expect(next.ok).toBe(true);
    if (next.ok) expect(next.session.messages).toBe(1);
  });
});

describe("the cookie's own flags", () => {
  it("is httpOnly and sameSite, so it cannot be read by script or sent cross-site", () => {
    const options = cookieOptions();
    expect(options.httpOnly).toBe(true);
    expect(options.sameSite).toBe("lax");
    expect(options.path).toBe("/");
  });

  it("is named predictably", () => {
    expect(ASK_COOKIE).toBe("ask_session");
  });
});
