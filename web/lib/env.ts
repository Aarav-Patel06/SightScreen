/**
 * Fail-fast environment validation for the Next.js app.
 *
 * Import `env` from this module instead of reading `process.env` directly.
 * Vars needed only by later phases are optional here and will be tightened
 * to required as those phases land (see README's env var table).
 */

// Substrings that only ever appear in .env.local.example. If one of these
// shows up in a real value, someone copied the placeholder instead of
// filling it in.
const PLACEHOLDER_MARKERS = ["changeme", "your-project", "..."];

function assertNotPlaceholder(name: string, value: string): void {
  const lowered = value.toLowerCase();
  if (PLACEHOLDER_MARKERS.some((marker) => lowered.includes(marker))) {
    throw new Error(
      `${name} still holds an example placeholder value - put a real value in your .env.local file`
    );
  }
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  assertNotPlaceholder(name, value);
  return value;
}

function optional(name: string): string | undefined {
  const value = process.env[name];
  if (!value) return undefined;
  assertNotPlaceholder(name, value);
  return value;
}

function requiredSupabaseUrl(name: string): string {
  const value = required(name);
  if (value.includes("/rest/")) {
    throw new Error(
      `${name} must be the bare project URL (e.g. https://xxxx.supabase.co) with no /rest/... path suffix`
    );
  }
  return value;
}

export const env = {
  // Public - safe in the browser bundle, RLS protects the data
  NEXT_PUBLIC_SUPABASE_URL: requiredSupabaseUrl("NEXT_PUBLIC_SUPABASE_URL"),
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: required("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"),

  // Server-only - never expose these under a NEXT_PUBLIC_ prefix
  SUPABASE_SECRET_KEY: required("SUPABASE_SECRET_KEY"),

  // Phase 6 - agent (optional until then)
  API_BASE_URL: optional("API_BASE_URL"),
  AGENT_TOOL_SHARED_SECRET: optional("AGENT_TOOL_SHARED_SECRET"),
  ANTHROPIC_API_KEY: optional("ANTHROPIC_API_KEY"),
} as const;
