/**
 * The server Supabase client. Secret key, bypasses RLS - server components
 * and route handlers only, never imported from a client component.
 *
 * Used for the two things the browser deliberately cannot read:
 * `model_versions` (for /about/model's honesty note) has no anon policy, and
 * server-rendered reads avoid a round trip the page would otherwise wait on.
 */

import { createClient } from "@supabase/supabase-js";

import type { Database } from "./types";

export function supabaseServer() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const secret = process.env.SUPABASE_SECRET_KEY;
  if (!url || !secret) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SECRET_KEY must be set for server-side reads"
    );
  }
  return createClient<Database>(url, secret, { auth: { persistSession: false } });
}
