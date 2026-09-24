/**
 * Build the committed ball-strip fixture for /design.
 *
 *   node --env-file=.env.local scripts/make-strip-fixture.mjs
 *
 * /design is the design reference: the page you open to decide whether a
 * token, a type size or a strip tier is right. A reference that is sometimes
 * blank is not a reference, so it must not depend on a network call. Supabase
 * free-tier projects pause after about a week idle, and the historical corpus
 * lives only in local Postgres (SPEC.md §2.1) - either of which would leave
 * /design empty exactly when someone opens it to check a colour.
 *
 * So the fixture is generated once, committed, and imported statically. This
 * script exists so that "once" is reproducible rather than a thing someone did
 * by hand and cannot redo.
 *
 * WHY MATCH 8532 - Abu Dhabi Knight Riders v Sharjah Warriorz, ILT20. It is
 * simultaneously the worst case for the strip and the best story for it:
 *
 *   - 120 legal balls, the maximum a T20 second innings can reach. The median
 *     is 113 because chases end early, so this is the densest strip the
 *     component will ever be asked to draw: 2.52px per mark at a 340px
 *     viewport, which is what forces the `minimal` tier to exist at all.
 *   - Five extras, so `balls_bowled` repeats at five points and the
 *     prediction_id ordering in lib/ball-strip.ts is actually exercised rather
 *     than merely asserted.
 *   - Win probability that falls to 18.7% and recovers to 95.8%, with a median
 *     per-ball swing of 2.5pp against a maximum of 34.6pp. That ratio is the
 *     strip's whole aesthetic - a flat run with a few spikes - and a fixture
 *     with uniformly large swings would not test it.
 *   - The target reached off the final delivery. Which means the last mark -
 *     the winning run, the most dramatic ball in the innings - is the one mark
 *     whose outcome cannot be derived, because no prediction follows it. The
 *     signature element demonstrates the honest-gap principle in its most
 *     prominent position. That is not a defect to work around.
 *
 * Reads with the secret key rather than the publishable one purely because
 * this is a build-time script and lib/supabase-server.ts is the established
 * server-side client; the rows are anon-readable either way.
 */

import { writeFileSync } from "node:fs";
import { createClient } from "@supabase/supabase-js";

import { corpusRow } from "./corpus.mjs";

const MATCH_ID = 8532;
const OUT = new URL("../lib/fixtures/innings-8532.json", import.meta.url);

function env(name) {
  const value = process.env[name];
  if (!value) {
    console.error(
      `${name} is not set. Load web/.env.local first, e.g.\n` +
        `  node --env-file=.env.local scripts/make-strip-fixture.mjs`
    );
    process.exit(1);
  }
  return value;
}

const supabase = createClient(
  env("NEXT_PUBLIC_SUPABASE_URL"),
  env("SUPABASE_SECRET_KEY"),
  { auth: { persistSession: false } }
);

// Same query shape as app/match/[matchId]/page.tsx's loadPredictions, ordered
// by prediction_id for the reason merge-predictions.ts documents.
const { data, error } = await supabase
  .from("predictions")
  .select("prediction_id, created_at, model_version, payload")
  .eq("match_id", MATCH_ID)
  .eq("prediction_type", "win_prob")
  .not("innings", "is", null)
  .order("prediction_id", { ascending: true });

if (error) {
  console.error(`query failed: ${error.message}`);
  process.exit(1);
}
if (!data || data.length === 0) {
  console.error(
    `no predictions for match ${MATCH_ID}. It should be one of the 100 ` +
      `backfilled matches in api/data/phase3_manifest.json - check the ` +
      `replay log ran, and note that Supabase match ids >= 1000000 are live ` +
      `matches with no corpus counterpart (migration 20260919000001).`
  );
  process.exit(1);
}

// The match's identity, from the LOCAL corpus rather than from Supabase.
//
// Supabase has a `matches` row for 8532 - the 100 manifest matches were
// mirrored with their corpus ids - but `winner`, `target_runs` and
// `result_method` are NULL on every row of that table, because replay_log.py
// mirrors only the columns the serving path reads. Asking Supabase gives a
// confident `null` for the two facts the hero caption is built on. See
// scripts/corpus.mjs for why that is the expected shape rather than a
// surprise.
//
// Batting order matters and is not in `matches` at all: team_a and team_b are
// the two sides, not an order. It comes from the deliveries themselves.
const [competition, format, matchDate, battingFirst, chasing, winner, target] = corpusRow(`
  SELECT m.competition,
         m.format,
         m.start_time::date,
         first_innings.name,
         second_innings.name,
         w.name,
         m.target_runs
  FROM matches m
  LEFT JOIN teams w ON w.team_id = m.winner
  JOIN LATERAL (
    SELECT t.name FROM deliveries d JOIN teams t ON t.team_id = d.batting_team_id
    WHERE d.match_id = m.match_id AND d.innings = 1 LIMIT 1
  ) first_innings ON true
  JOIN LATERAL (
    SELECT t.name FROM deliveries d JOIN teams t ON t.team_id = d.batting_team_id
    WHERE d.match_id = m.match_id AND d.innings = 2 LIMIT 1
  ) second_innings ON true
  WHERE m.match_id = ${MATCH_ID}
`);

// created_at is dropped deliberately. It is the time the backfill ran, not
// anything about the match, and committing it would make the fixture look
// like it carried a timestamp that meant something.
const rows = data.map((row) => ({
  prediction_id: row.prediction_id,
  model_version: row.model_version,
  payload: row.payload,
}));

const fixture = {
  note:
    "Generated by web/scripts/make-strip-fixture.mjs. Do not hand-edit. " +
    "Match 8532, ILT20: Sharjah Warriorz chased 135 off the final ball.",
  match_id: MATCH_ID,
  competition,
  format,
  match_date: matchDate,
  batting_first: battingFirst,
  chasing,
  winner,
  target: Number(target),
  rows,
};

writeFileSync(OUT, `${JSON.stringify(fixture, null, 2)}\n`);

const legal = new Set(rows.map((r) => r.payload?.balls_bowled)).size;
console.log(
  `wrote ${rows.length} rows (${legal} distinct balls_bowled, so ` +
    `${rows.length - legal} extras) to lib/fixtures/innings-8532.json`
);
