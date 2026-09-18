/**
 * Environment values that are safe in the browser bundle.
 *
 * Split out from env.ts on 2026-09-18 because env.ts eagerly requires
 * SUPABASE_SECRET_KEY at module load. That is correct for a server module
 * and fatal for a client one: importing it from a client component throws,
 * since the secret is - by design - not in the browser bundle. The split is
 * the difference between "this module happens to work on the server" and
 * "this module is safe to import anywhere".
 *
 * Only NEXT_PUBLIC_* values may live here. RLS is what protects the data
 * behind the publishable key, not secrecy of the key
 * (see web/scripts/check-anon-access.mjs, which proves that is true).
 */

const PLACEHOLDER_MARKERS = ["changeme", "your-project", "..."];

function publicValue(name: string, raw: string | undefined): string {
  if (!raw) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  if (PLACEHOLDER_MARKERS.some((marker) => raw.toLowerCase().includes(marker))) {
    throw new Error(
      `${name} still holds an example placeholder value - put a real value in your .env.local file`
    );
  }
  return raw;
}

function supabaseUrl(name: string, raw: string | undefined): string {
  const value = publicValue(name, raw);
  if (value.includes("/rest/")) {
    throw new Error(
      `${name} must be the bare project URL (e.g. https://xxxx.supabase.co) with no /rest/... path suffix`
    );
  }
  return value;
}

// Next.js inlines NEXT_PUBLIC_* at build time only for statically analysable
// references, so these must be written out in full rather than indexed.
export const envPublic = {
  NEXT_PUBLIC_SUPABASE_URL: supabaseUrl(
    "NEXT_PUBLIC_SUPABASE_URL",
    process.env.NEXT_PUBLIC_SUPABASE_URL
  ),
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: publicValue(
    "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
  ),
} as const;
