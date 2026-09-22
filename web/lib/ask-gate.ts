/**
 * The shared-password gate for /ask, and the per-session message counter.
 *
 * An unauthenticated LLM endpoint with a real API key behind it, on a public
 * repository, is the one unbounded expense in this project. This is the
 * smallest thing that actually closes it.
 *
 * WHAT THIS IS NOT, and why:
 *
 *   Vercel Deployment Protection - a Pro-tier feature that guards the whole
 *   deployment. /accuracy and /match are the portfolio and must stay public,
 *   so protecting everything to protect one route is the wrong shape.
 *
 *   A shared secret in a request header - a browser visitor cannot set one.
 *   Any client that could would carry the secret in its bundle, which makes
 *   it public and therefore not a secret.
 *
 * So: a password the visitor types once, exchanged for a signed cookie. No
 * accounts, no session store, no database read on the hot path.
 *
 * The cookie is `<expiry>.<hmac>` where the HMAC covers the expiry and a
 * message count. Signing rather than storing means a tampered cookie is
 * detected without any server state - edit the expiry and the HMAC no longer
 * matches, forge the HMAC and you need ASK_SESSION_SECRET.
 *
 * THE PER-SESSION CAP IS NOT THE REAL PROTECTION and should not be mistaken
 * for it. It lives in the cookie, so a visitor who clears cookies resets it.
 * It stops runaway client loops and casual over-use; the DAILY COST CAP in
 * agent-usage.ts is what actually bounds the bill, because it lives in a
 * database the visitor cannot reach.
 */

import { createHmac, timingSafeEqual } from "node:crypto";

export const ASK_COOKIE = "ask_session";

/** How long one password entry is good for. */
export const SESSION_TTL_SECONDS = 12 * 60 * 60;

/**
 * Messages per session before the visitor is asked to start a new one.
 * Generous for a demo conversation, and finite.
 */
export const MAX_MESSAGES_PER_SESSION = 25;

export type SessionState = { expiresAt: number; messages: number };

export type GateResult =
  | { ok: true; session: SessionState }
  | { ok: false; reason: "missing" | "malformed" | "tampered" | "expired" | "exhausted" };

function sign(payload: string, secret: string): string {
  return createHmac("sha256", secret).update(payload).digest("hex");
}

/**
 * Constant-time compare that does not leak length through an exception.
 *
 * `timingSafeEqual` throws on unequal lengths, and a throw is itself an
 * observable difference - so lengths are checked first and a mismatch
 * returns false rather than propagating.
 */
function safeEqual(a: string, b: string): boolean {
  const left = Buffer.from(a, "utf8");
  const right = Buffer.from(b, "utf8");
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

export function passwordMatches(supplied: string, expected: string): boolean {
  if (!expected) return false; // An unset ASK_PASSWORD must never mean "open".
  return safeEqual(supplied, expected);
}

export function issueCookie(
  secret: string,
  now: number = Date.now(),
  messages = 0
): string {
  const expiresAt = Math.floor(now / 1000) + SESSION_TTL_SECONDS;
  const payload = `${expiresAt}.${messages}`;
  return `${payload}.${sign(payload, secret)}`;
}

/**
 * Verify a cookie and return the session, or say precisely why not.
 *
 * The reasons are for the SERVER's logs and for tests. What the visitor sees
 * is one message either way: distinguishing "tampered" from "expired" in the
 * response tells someone probing the gate which half of it they beat.
 */
export function verifyCookie(
  raw: string | undefined,
  secret: string,
  now: number = Date.now()
): GateResult {
  if (!raw) return { ok: false, reason: "missing" };

  const parts = raw.split(".");
  if (parts.length !== 3) return { ok: false, reason: "malformed" };
  const [expiryText, messagesText, mac] = parts;

  const expiresAt = Number(expiryText);
  const messages = Number(messagesText);
  if (!Number.isInteger(expiresAt) || !Number.isInteger(messages) || messages < 0) {
    return { ok: false, reason: "malformed" };
  }

  // Signature BEFORE expiry: an expired-but-authentic cookie and a forged one
  // are different situations, and checking expiry first would report
  // "expired" for a forgery whose expiry happened to be in the past.
  if (!safeEqual(mac, sign(`${expiresAt}.${messages}`, secret))) {
    return { ok: false, reason: "tampered" };
  }
  if (expiresAt * 1000 <= now) return { ok: false, reason: "expired" };
  if (messages >= MAX_MESSAGES_PER_SESSION) return { ok: false, reason: "exhausted" };

  return { ok: true, session: { expiresAt, messages } };
}

/** The same session, one message further on, re-signed. */
export function incrementCookie(session: SessionState, secret: string): string {
  const payload = `${session.expiresAt}.${session.messages + 1}`;
  return `${payload}.${sign(payload, secret)}`;
}

/**
 * What the visitor is told. Deliberately the same shape for every refusal
 * the gate can produce, and never a 500: a person who mistypes a password
 * and a person probing the HMAC should both see a sentence that reads like
 * it was written on purpose.
 */
export function refusalMessage(reason: GateResult extends { ok: false } ? never : string): string {
  switch (reason) {
    case "exhausted":
      return (
        `This session has reached its ${MAX_MESSAGES_PER_SESSION}-message limit. ` +
        `Reload the page and sign in again to start a new one.`
      );
    case "expired":
      return "This session has expired. Enter the password again to continue.";
    default:
      return "This page needs a password. Enter it to ask a question.";
  }
}

export function cookieOptions(maxAgeSeconds = SESSION_TTL_SECONDS) {
  return {
    httpOnly: true, // Not readable from JS, so an XSS cannot exfiltrate it.
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}
