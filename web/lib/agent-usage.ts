/**
 * The daily spend ceiling for /ask. This is the cap that actually bounds the
 * bill.
 *
 * The per-session cap in ask-gate.ts lives in a cookie the visitor holds, so
 * clearing cookies resets it. This one lives in Supabase, which the visitor
 * cannot reach, and is shared across every Vercel function instance - which
 * matters because those are stateless and horizontally scaled, so an
 * in-memory counter would be per-process and therefore per-nothing.
 *
 * COST, NOT MESSAGE COUNT. One eval conversation in session 2 made 17 tool
 * calls across 8 turns. "50 messages" and "50 conversations" differ by more
 * than an order of magnitude in spend, and the thing being protected is a
 * bill, so the bill is what is counted.
 *
 * The check is read-before, write-after, and deliberately NOT a transaction
 * spanning the model call: holding a database row open for the 10-40 seconds
 * a conversation takes would serialise every visitor behind the slowest one.
 * The consequence is that concurrent requests can both pass a check that
 * only one of them should have - so the cap is a ceiling with a small
 * overshoot, not a hard limit. At a $2 cap and ~$0.03 a conversation that
 * overshoot is cents, and the alternative costs responsiveness on every
 * request to save them.
 */

import { createClient } from "@supabase/supabase-js";

import { env } from "./env";

/**
 * Generous for a demo, low enough that a retry loop at 3am is an annoyance
 * rather than a bill. Overridable so the cap can be proven to trip without
 * spending two dollars to watch it.
 */
export const DAILY_COST_CAP_USD = Number(process.env.ASK_DAILY_COST_CAP ?? "2");

// claude-sonnet-5, $ per token. Kept beside the usage they price so a model
// change cannot update one without the other being visibly adjacent.
const INPUT_RATE = 2.0 / 1_000_000;
const OUTPUT_RATE = 10.0 / 1_000_000;
const CACHE_READ_RATE = INPUT_RATE * 0.1;
const CACHE_WRITE_RATE = INPUT_RATE * 1.25;

export type TokenUsage = {
  input_tokens?: number;
  output_tokens?: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
};

export function costOf(usage: TokenUsage): number {
  return (
    (usage.input_tokens ?? 0) * INPUT_RATE +
    (usage.output_tokens ?? 0) * OUTPUT_RATE +
    (usage.cache_read_input_tokens ?? 0) * CACHE_READ_RATE +
    (usage.cache_creation_input_tokens ?? 0) * CACHE_WRITE_RATE
  );
}

export function utcDay(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10);
}

/**
 * What a visitor is told when the day's budget is gone.
 *
 * Written for someone who is not the author - an interviewer clicking the
 * link, most likely - so it says what happened, that it is not their fault,
 * and when it changes. It does NOT say how much has been spent or how much
 * remains: that is a progress bar for anyone trying to exhaust it.
 */
export function capMessage(): string {
  return (
    "This demo has reached its daily budget, so the agent is paused until " +
    "tomorrow (00:00 UTC). Nothing is broken and you have not done anything " +
    "wrong - the cap exists so a public demo cannot run up an unbounded bill. " +
    "Everything else on the site works normally in the meantime."
  );
}

function client() {
  return createClient(env.NEXT_PUBLIC_SUPABASE_URL, env.SUPABASE_SECRET_KEY, {
    auth: { persistSession: false },
  });
}

/** True when today's spend is already at or over the cap. */
export async function isOverDailyCap(now: Date = new Date()): Promise<boolean> {
  const { data, error } = await client()
    .from("agent_usage")
    .select("cost_usd")
    .eq("day", utcDay(now))
    .maybeSingle();

  if (error) {
    // FAIL CLOSED. If the ledger cannot be read, the spend cannot be known,
    // and "unknown" must not mean "proceed" for the one unbounded expense in
    // the project. A broken cap that blocks is recoverable; one that admits
    // is what the cap exists to prevent.
    console.error("agent_usage read failed, refusing to serve:", error.message);
    return true;
  }
  return Number(data?.cost_usd ?? 0) >= DAILY_COST_CAP_USD;
}

/** Add one conversation's usage to today's row. */
export async function recordUsage(
  usage: TokenUsage,
  now: Date = new Date()
): Promise<void> {
  const day = utcDay(now);
  const supabase = client();
  const { data } = await supabase
    .from("agent_usage")
    .select("conversations, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd")
    .eq("day", day)
    .maybeSingle();

  const next = {
    day,
    conversations: (data?.conversations ?? 0) + 1,
    input_tokens: (data?.input_tokens ?? 0) + (usage.input_tokens ?? 0),
    output_tokens: (data?.output_tokens ?? 0) + (usage.output_tokens ?? 0),
    cache_read_tokens: (data?.cache_read_tokens ?? 0) + (usage.cache_read_input_tokens ?? 0),
    cache_write_tokens: (data?.cache_write_tokens ?? 0) + (usage.cache_creation_input_tokens ?? 0),
    cost_usd: Number(data?.cost_usd ?? 0) + costOf(usage),
    updated_at: new Date().toISOString(),
  };

  const { error } = await supabase.from("agent_usage").upsert(next, { onConflict: "day" });
  if (error) {
    // Logged loudly rather than thrown: the visitor already has their answer
    // and failing their request now would be pure punishment. But an
    // unrecorded conversation is spend the cap cannot see, so this must be
    // noisy - see the agent_query_log lesson, where a known-harmless error
    // on every run trained everyone to read past that spot.
    console.error("agent_usage write FAILED - this spend is uncapped:", error.message);
  }
}
