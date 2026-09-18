/**
 * The browser Supabase client (SPEC.md section 7.4).
 *
 * Publishable key only. Everything it can reach is gated by the anon RLS
 * policies in 20260826180008, and by 20260918000001/2 which close what those
 * policies were silently not gating.
 */

import { createClient } from "@supabase/supabase-js";

import { envPublic } from "./env.public";
import type { Database } from "./types";

let client: ReturnType<typeof createClient<Database>> | null = null;

export function supabaseBrowser() {
  // One client per tab. supabase-js multiplexes every channel over a single
  // websocket, so constructing a second one opens a second socket for no
  // reason and makes the connection count per viewer nondeterministic.
  if (client === null) {
    client = createClient<Database>(
      envPublic.NEXT_PUBLIC_SUPABASE_URL,
      envPublic.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
      { auth: { persistSession: false } }
    );
  }
  return client;
}
