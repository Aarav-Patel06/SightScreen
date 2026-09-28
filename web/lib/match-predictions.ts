/**
 * The match page's predictions (app/match/[matchId]/page.tsx), in lib so the
 * one-version rule is testable against the fake PostgREST like every other
 * loader.
 */

import { loadModelVersions } from "./load-model-versions";
import { activeVersion, chooseVersion } from "./model-version";
import { parsePrediction, type WinProbPrediction } from "./prediction";
import { supabaseServer } from "./supabase-server";

export interface MatchPredictions {
  predictions: WinProbPrediction[];
  /** The version shown, which the live page's stream and resync keep to. */
  modelVersion: string | null;
}

/**
 * One model version per match (lib/model-version.ts). A match with no rows
 * yet - a live match before its chase - locks onto the active version, which
 * is the one the live worker writes.
 */
export async function loadMatchPredictions(matchId: number): Promise<MatchPredictions> {
  const [rows, versions] = await Promise.all([loadPredictions(matchId), loadModelVersions()]);
  const modelVersion = chooseVersion(rows, versions) ?? activeVersion(versions);
  return { predictions: rows.filter((row) => row.model_version === modelVersion), modelVersion };
}

async function loadPredictions(matchId: number): Promise<WinProbPrediction[]> {
  const supabase = supabaseServer();
  const { data, error } = await supabase
    .from("predictions")
    .select("prediction_id, created_at, model_version, payload, match_id, batting_team_id")
    .eq("match_id", matchId)
    .eq("prediction_type", "win_prob")
    // Migration 20260918000003's stated contract: "every Phase 3 reader
    // filters on innings IS NOT NULL". This page did not, and was the only
    // user-facing surface rendering unkeyed rows - so for match 13143 it drew
    // a curve out of ten interleaved runs of a twelve-ball deploy smoke test,
    // while /matches listed the same match as never replayed. Two surfaces
    // disagreeing about what counts as a prediction meant one of them was
    // wrong, and it was this one.
    //
    // The unkeyed rows are not merely old. They sit outside the partial
    // unique index, so nothing dedupes them: match 13143 holds 121 such rows
    // carrying 13 distinct payloads.
    .not("innings", "is", null)
    .order("prediction_id", { ascending: true });
  if (error || !data) return [];
  // A malformed row - one written before the payload was enriched, say -
  // is dropped rather than throwing. A single bad row must not blank a page.
  return data
    .map((row) => parsePrediction(row as never))
    .filter((x): x is WinProbPrediction => x !== null);
}
