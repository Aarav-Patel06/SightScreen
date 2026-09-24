/**
 * Generate lib/corpus-facts.ts — the landing page's fallback figures.
 *
 *   node scripts/make-corpus-facts.mjs
 *
 * Two jobs, and they are different.
 *
 * FIRST, the delivery count, which is not a fallback at all. UI-PHASE.md §4.1
 * asks the landing page for three "live figures from the database, not
 * hardcoded", and one of them - deliveries queryable - cannot be answered by
 * the database the page can reach. `deliveries` is empty on Supabase by
 * design, because 3.78M rows do not fit the free tier, and it is additionally
 * denied to the anonymous role as a deliberate negative control. The real
 * number lives in local Postgres and in the Railway replica the /ask tools
 * query. So it is counted here, committed, and the page states where it was
 * counted rather than implying the serving database holds it.
 *
 * SECOND, last-known values for the two figures Supabase CAN answer. Free-tier
 * projects pause after about a week idle - this project has recorded pauses of
 * 12 minutes, 12 minutes and 3.3 hours - and the front door must not be a 500
 * for that whole window. The page renders these with an "as of" date whenever
 * a live query fails.
 *
 * Both are honest only because the date travels with the number. A stale
 * figure presented as current is worse than no figure; a stale figure labelled
 * with when it was true is just a fact.
 */

import { writeFileSync } from "node:fs";

import { corpusRow } from "./corpus.mjs";

const OUT = new URL("../lib/corpus-facts.ts", import.meta.url);

const [deliveries, matches, players] = corpusRow(`
  SELECT (SELECT count(*) FROM deliveries),
         (SELECT count(*) FROM matches),
         (SELECT count(*) FROM players)
`);

// Predictions live only on Supabase, so the fallback for "matches with
// predictions" is the manifest the backfill was driven from - a committed
// file, which is the right kind of source for a fallback: it cannot drift
// between the generator running and the page rendering.
const manifest = await import("../../api/data/phase3_manifest.json", {
  with: { type: "json" },
});
const manifestMatches = manifest.default.matches.length;

const countedAt = new Date().toISOString().slice(0, 10);

const source = `/**
 * Counted from the local corpus. DO NOT HAND-EDIT.
 *
 * Regenerate with \`node scripts/make-corpus-facts.mjs\` from web/, which
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
  deliveries: ${Number(deliveries)},
  matches: ${Number(matches)},
  players: ${Number(players)},
  matchesWithPredictions: ${manifestMatches},
  countedAt: ${JSON.stringify(countedAt)},
};
`;

writeFileSync(OUT, source);

console.log(
  `wrote lib/corpus-facts.ts\n` +
    `  deliveries               ${Number(deliveries).toLocaleString()}\n` +
    `  matches                  ${Number(matches).toLocaleString()}\n` +
    `  players                  ${Number(players).toLocaleString()}\n` +
    `  matches with predictions ${manifestMatches}\n` +
    `  counted at               ${countedAt}`
);
