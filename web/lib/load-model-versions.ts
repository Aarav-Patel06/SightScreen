/**
 * The registered model versions, for the one-version-per-match rule
 * (lib/model-version.ts). Server only: `model_versions` has no anon policy.
 */

import type { ModelVersionInfo } from "./model-version";
import { supabaseServer } from "./supabase-server";

/** Every registered version, or [] if they cannot be read - never throws. */
export async function loadModelVersions(): Promise<ModelVersionInfo[]> {
  try {
    const { data, error } = await supabaseServer()
      .from("model_versions")
      .select("model_version, is_active, trained_at");
    if (error || !Array.isArray(data)) {
      console.warn(`[model-version] model_versions unavailable, newest rows win: ${error?.message ?? "no data"}`);
      return [];
    }
    return data as ModelVersionInfo[];
  } catch (error) {
    console.warn(`[model-version] model_versions unavailable, newest rows win: ${String(error)}`);
    return [];
  }
}
