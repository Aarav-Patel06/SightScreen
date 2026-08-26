/**
 * Fail-fast environment validation for the Next.js app.
 *
 * Import `env` from this module instead of reading `process.env` directly.
 * Vars needed only by later phases are optional here and will be tightened
 * to required as those phases land (see README's env var table).
 */

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function optional(name: string): string | undefined {
  return process.env[name] || undefined;
}

export const env = {
  // Public - safe in the browser bundle, RLS protects the data
  NEXT_PUBLIC_SUPABASE_URL: required("NEXT_PUBLIC_SUPABASE_URL"),
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: required("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"),

  // Server-only - never expose these under a NEXT_PUBLIC_ prefix
  SUPABASE_SECRET_KEY: required("SUPABASE_SECRET_KEY"),

  // Phase 6 - agent (optional until then)
  API_BASE_URL: optional("API_BASE_URL"),
  AGENT_TOOL_SHARED_SECRET: optional("AGENT_TOOL_SHARED_SECRET"),
  ANTHROPIC_API_KEY: optional("ANTHROPIC_API_KEY"),
} as const;
