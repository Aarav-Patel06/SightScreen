/**
 * Which commit this deployment was built from (VERCEL_GIT_COMMIT_SHA, set by
 * Vercel at build time). scripts/check-deployed.mjs compares it, and the two
 * Railway services' git_sha, with main - "deployed" is checked, not assumed.
 */

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(
    { service: "web", git_sha: process.env.VERCEL_GIT_COMMIT_SHA?.trim() || "unknown" },
    { headers: { "Cache-Control": "no-store" } }
  );
}
