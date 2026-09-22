/**
 * Exchange the shared password for a signed session cookie.
 *
 * Split from the agent route deliberately: this endpoint touches a password
 * and never a model, and the agent endpoint calls a model and never sees a
 * password. Keeping them separate means the expensive route has exactly one
 * job - verify a signature - and cannot be reached by anything that has not
 * already been through here.
 */

import { NextResponse } from "next/server";

import { ASK_COOKIE, cookieOptions, issueCookie, passwordMatches } from "@/lib/ask-gate";

export const runtime = "nodejs"; // node:crypto, and no reason to be on the edge.

export async function POST(request: Request) {
  const secret = process.env.ASK_SESSION_SECRET ?? "";
  const expected = process.env.ASK_PASSWORD ?? "";

  // A misconfigured deployment must be CLOSED, not open. Without this, an
  // unset ASK_PASSWORD would make `passwordMatches("", "")` the only check
  // standing between a public URL and an API key.
  if (!expected || !secret) {
    console.error("ASK_PASSWORD or ASK_SESSION_SECRET is unset; refusing all sign-ins");
    return NextResponse.json(
      { error: "This page is not configured for sign-in yet." },
      { status: 503 }
    );
  }

  let supplied = "";
  try {
    const body = await request.json();
    supplied = typeof body?.password === "string" ? body.password : "";
  } catch {
    supplied = "";
  }

  if (!passwordMatches(supplied, expected)) {
    // One message, one status, whether the password was empty, short, or
    // nearly right. Anything that varies is a hint.
    return NextResponse.json({ error: "Incorrect password." }, { status: 401 });
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set(ASK_COOKIE, issueCookie(secret), cookieOptions());
  return response;
}
