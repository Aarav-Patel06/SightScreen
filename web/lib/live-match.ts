/**
 * Is a match being predicted right now? (UI-PHASE.md §3.1)
 *
 * The header carries one live element: a sparkline and two team short names
 * if something is live, and an empty slot if not — not a "no live matches"
 * placeholder.
 *
 * DO NOT KEY THIS OFF `matches.status`. The column exists, is indexed, and
 * holds 'live' for exactly the matches you would want — and it is wrong.
 * Nothing in the repository ever runs `UPDATE matches`: `_ensure_match_row`
 * is `ON CONFLICT DO NOTHING`, and `live_loop.py` detects the end of a match
 * from the provider snapshot in memory without writing that back. A match
 * inserted as 'live' stays 'live' forever, so the three matches the worker
 * tracked in September 2026 are still marked live and always will be. A
 * status-keyed header would show a permanently frozen sparkline, which is the
 * worst available outcome: it looks like the product is working.
 *
 * The signal that cannot lie is recency. `predictions.source` is written by
 * the producer and never inferred — 'live' means predicted before the result
 * existed — so a 'live' row created in the last few minutes means a worker is
 * running right now. When the worker stops, rows stop arriving, and the slot
 * empties on its own without anything needing to update a status column.
 */

import type { WinProbPrediction } from "./prediction";

/**
 * How recent a live prediction must be for the header to call a match live.
 *
 * The worker's cadence is 15–30s while a match is on. Five minutes is roughly
 * ten missed cycles: long enough to survive a provider hiccup or a redeploy,
 * short enough that a finished match clears the header while a visitor is
 * still looking at it.
 */
export const LIVE_WINDOW_MS = 5 * 60_000;

export interface LiveMatch {
  matchId: number;
  battingShort: string;
  bowlingShort: string;
  prediction: WinProbPrediction;
}

/**
 * Abbreviate a team name for the header.
 *
 * `teams.short_name` exists in the schema, is in the reference-sync tuple, and
 * is NULL for all 350 rows — the same shape as `players.batting_hand` and
 * `matches.winner`, where a column syncs and therefore looks populated. So the
 * abbreviation is derived rather than read, and derived in a way that never
 * invents an abbreviation nobody uses: initials of the significant words.
 *
 * "Sharjah Warriorz" becomes SW, not SHA. Three-letter codes are a convention
 * with real answers per team ("CSK", "RCB") and guessing one produces a string
 * that looks official and is wrong. Initials are visibly a derivation.
 */
export function shortName(name: string): string {
  const skip = new Set(["of", "the", "and", "xi", "cricket", "club"]);
  const words = name
    .split(/[\s-]+/)
    .filter((word) => word.length > 0 && !skip.has(word.toLowerCase()));

  // Everything was a joining word, so there is nothing to take initials of:
  // fall back to a prefix of the name itself. Trimmed, because the untrimmed
  // slice of a whitespace-only name is whitespace, which renders as an
  // invisible label rather than as an obviously missing one.
  if (words.length === 0) return name.trim().slice(0, 3).toUpperCase();
  if (words.length === 1) return words[0].slice(0, 3).toUpperCase();

  return words
    .slice(0, 3)
    .map((word) => word[0].toUpperCase())
    .join("");
}

/**
 * Is this prediction recent enough to mean a match is live?
 *
 * Takes `now` so it is testable without mocking the clock, and guards the
 * parse explicitly: an unparseable timestamp yields NaN, and `NaN < window` is
 * `false`, which would report a match as not-live for the right reason by
 * accident. Standing rule 15 — a numeric guard that can receive NaN needs an
 * isFinite check, not just a comparison.
 */
export function isRecent(createdAt: string, now: number, windowMs = LIVE_WINDOW_MS): boolean {
  const age = now - Date.parse(createdAt);
  if (!Number.isFinite(age)) return false;
  return age >= 0 && age < windowMs;
}
