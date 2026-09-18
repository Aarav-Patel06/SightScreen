/**
 * The shape of predictions.payload, narrowed.
 *
 * lib/types.ts is generated and CI diffs it byte-for-byte against the
 * Supabase Management API (ci.yml's "Check web/lib/types.ts is not stale"),
 * so it must not be hand-edited - and it types `payload` as bare `Json`
 * because Postgres cannot describe a JSONB's interior. This module is where
 * the application's knowledge of that interior lives.
 *
 * Written by api/src/serving/app.py. Keep the two in step; a mismatch is
 * silent, because Json accepts anything.
 */

import type { Database } from "./types";

export type PredictionRow = Database["public"]["Tables"]["predictions"]["Row"];

/** SPEC.md 12.2's confidence labelling operates on this. */
export type Phase = "powerplay" | "middle" | "death";

export interface WinProbPayload {
  p: number;
  innings: number;
  balls_bowled: number;
  balls_remaining: number;
  runs_required: number;
  score: number;
  wickets: number;
  target: number;
  phase: Phase;
}

export interface WinProbPrediction extends WinProbPayload {
  prediction_id: number;
  created_at: string;
  model_version: string;
}

function isPhase(value: unknown): value is Phase {
  return value === "powerplay" || value === "middle" || value === "death";
}

/**
 * Parse a row, or return null.
 *
 * Returns null rather than throwing because this runs on rows arriving from
 * a Realtime stream: one malformed row - an older row written before the
 * payload was enriched, say - must not take down a live page. The caller
 * drops it and carries on.
 */
export function parsePrediction(row: PredictionRow): WinProbPrediction | null {
  const payload = row.payload as Record<string, unknown> | null;
  if (payload === null || typeof payload !== "object") return null;

  const numbers = [
    "p",
    "innings",
    "balls_bowled",
    "balls_remaining",
    "runs_required",
    "score",
    "wickets",
    "target",
  ] as const;
  for (const key of numbers) {
    if (typeof payload[key] !== "number") return null;
  }
  if (!isPhase(payload.phase)) return null;

  return {
    prediction_id: row.prediction_id,
    created_at: row.created_at,
    model_version: row.model_version,
    p: payload.p as number,
    innings: payload.innings as number,
    balls_bowled: payload.balls_bowled as number,
    balls_remaining: payload.balls_remaining as number,
    runs_required: payload.runs_required as number,
    score: payload.score as number,
    wickets: payload.wickets as number,
    target: payload.target as number,
    phase: payload.phase,
  };
}
