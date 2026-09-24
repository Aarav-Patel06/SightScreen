/**
 * Counted from the local corpus. DO NOT HAND-EDIT.
 *
 * Regenerate with `node scripts/make-corpus-facts.mjs` from web/, which
 * needs the Docker Postgres running. Every value carries the date it was
 * counted, because the landing page renders these when a live query fails and
 * a stale figure is only honest if it says when it was true.
 *
 * See scripts/make-corpus-facts.mjs for why the delivery count cannot come
 * from the serving database at request time.
 */

export interface CorpusFacts {
  /** Deliveries in the corpus the /ask tools query. Not on Supabase. */
  deliveries: number;
  /** Matches in the corpus. Not on Supabase, which holds only recent ones. */
  matches: number;
  /** Players in the corpus. Supabase has these too; this is the fallback. */
  players: number;
  /** Matches replayed through the model, from the Phase 3 manifest. */
  matchesWithPredictions: number;
  /** ISO date these were counted. Rendered whenever a live query fails. */
  countedAt: string;
}

export const CORPUS_FACTS: CorpusFacts = {
  deliveries: 3780368,
  matches: 13143,
  players: 18468,
  matchesWithPredictions: 100,
  countedAt: "2026-09-23",
};
