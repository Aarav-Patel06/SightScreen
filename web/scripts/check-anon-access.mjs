/**
 * Is the anonymous read path actually working? (SPEC.md sections 7.4 and 13)
 *
 * Run this BEFORE debugging a subscription that delivers nothing. Both §7.4
 * and §13 warn that a missing RLS policy presents identically to a broken
 * subscription, and until this script existed the anonymous path had never
 * been executed at all: the policies have been in
 * 20260826180008_realtime_and_rls.sql since Phase 0 session 3, and no test,
 * script or application code had ever authenticated as `anon` and read a row.
 *
 *   npm run check:anon
 *
 * Node rather than Python deliberately. This exercises the exact client stack
 * the browser uses, including the websocket. A REST-only check in Python
 * would verify the policies while leaving the actual "silent subscription"
 * failure untested, which is the one thing worth testing.
 *
 * WHAT THIS DOES NOT COVER, stated here because a gate's name overstates its
 * breadth: it does not check the policies' WHERE clauses beyond
 * reachability, does not verify REPLICA IDENTITY (irrelevant for INSERT-only
 * subscriptions, load-bearing the moment anyone subscribes to UPDATE or
 * DELETE), and does not compare local Postgres against Supabase - that is
 * tests/db/test_schema_parity.py's job.
 */

import { createClient } from "@supabase/supabase-js";

const URL_VAR = "NEXT_PUBLIC_SUPABASE_URL";
const ANON_VAR = "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY";
const SECRET_VAR = "SUPABASE_SECRET_KEY";

// Tables a browser reads directly. The first five were opened by
// 20260826180008; `prediction_outcomes` and `calibration_runs` by
// 20260919000001, for the public accuracy page - which reads a Brier per
// prediction and the daily monitor's report, both of which that page exists
// to show. Each was opened with the same three statements: GRANT SELECT,
// ENABLE ROW LEVEL SECURITY, and a FOR SELECT policy.
const READABLE = [
  "predictions",
  "matches",
  "teams",
  "venues",
  "players",
  "prediction_outcomes",
  "calibration_runs",
];

// The negative control, and the load-bearing check in this script. If someone
// ever replaces `USING (true)` with a blanket
// `GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon`, every positive check
// above still passes and the policy boundary is silently gone. match_states
// has no policy and no grant, so anon MUST be refused. The three as-of
// summary tables are in the same category - RLS-disabled with no grants.
const MUST_BE_DENIED = ["match_states", "deliveries", "venue_asof_summary"];

const REALTIME_TIMEOUT_MS = 15_000;

let failures = 0;

function pass(label, detail = "") {
  console.log(`  PASS  ${label}${detail ? `  ${detail}` : ""}`);
}

function fail(label, detail = "") {
  failures += 1;
  console.log(`  FAIL  ${label}${detail ? `  ${detail}` : ""}`);
}

function env(name) {
  const value = process.env[name];
  if (!value) {
    console.log(`  FAIL  env ${name} is not set`);
    console.log(
      `\n  Load web/.env.local first, e.g.  node --env-file=.env.local scripts/check-anon-access.mjs`
    );
    process.exit(1);
  }
  return value;
}

// --- 1. environment -------------------------------------------------------

console.log("\nanonymous access check (SPEC.md 7.4 / 13)\n");

const supabaseUrl = env(URL_VAR);
const anonKey = env(ANON_VAR);
const secretKey = env(SECRET_VAR);
pass("env", `${URL_VAR}, ${ANON_VAR}, ${SECRET_VAR} present`);

const anon = createClient(supabaseUrl, anonKey, {
  auth: { persistSession: false },
});
const admin = createClient(supabaseUrl, secretKey, {
  auth: { persistSession: false },
});

// --- 2. anon can read what the browser needs ------------------------------

for (const table of READABLE) {
  const { error } = await anon.from(table).select("*").limit(1);
  if (error) {
    fail(`REST anon SELECT ${table}`, `${error.code ?? "?"} ${error.message}`);
  } else {
    pass(`REST anon SELECT ${table}`);
  }
}

// --- 3. the negative control ----------------------------------------------

for (const table of MUST_BE_DENIED) {
  const { error } = await anon.from(table).select("*").limit(1);
  if (error) {
    pass(`REST anon DENIED ${table}`, `${error.code ?? "?"} (as designed)`);
  } else {
    fail(
      `REST anon DENIED ${table}`,
      "anon could read it - RLS is NOT the thing gating access, so every " +
        "positive check above proves nothing"
    );
  }
}

// --- 4. publication membership --------------------------------------------
// Needs the secret key: anon cannot read pg_publication_tables. Checked
// because a table with perfect policies that is not in the publication
// delivers nothing, and looks the same from the browser.

// PostgREST exposes tables, not catalog views, and there is deliberately no
// arbitrary-SQL RPC (SPEC 10.3). So this script cannot read
// pg_publication_tables at all, and says so rather than implying coverage it
// does not have. tests/db/test_schema_parity.py asserts publication
// membership against both databases with a real Postgres connection.
//
// It is not left unchecked, though: step 6's round trip fails if predictions
// is missing from the publication, which is the observable consequence.
console.log(
  "  ----  publication membership  not readable over REST; asserted by " +
    "tests/db/test_schema_parity.py, and implied by the round trip below"
);

// --- 5 and 6. the Realtime round trip -------------------------------------
// The check that matters. Subscribe as anon, insert a probe row with the
// secret key, and assert the anon subscriber receives it. This is the exact
// path the match page depends on, and the exact failure §13 describes.

async function realtimeRoundTrip() {
  const { data: match, error: matchError } = await admin
    .from("matches")
    .select("match_id")
    .limit(1)
    .maybeSingle();
  if (matchError || !match) {
    fail(
      "RT probe setup",
      "no rows in matches on Supabase - run the smoke test or the replay " +
        "driver once so there is a match to attach a probe prediction to"
    );
    return;
  }

  const { data: model, error: modelError } = await admin
    .from("model_versions")
    .select("model_version")
    .eq("is_active", true)
    .limit(1)
    .maybeSingle();
  if (modelError || !model) {
    fail(
      "RT probe setup",
      "no active model_versions row - run models.publish_model_version"
    );
    return;
  }

  const matchId = match.match_id;
  let received = null;
  let subscribed = false;

  const channel = anon
    .channel(`anon-check:${matchId}:${Date.now()}`)
    .on(
      "postgres_changes",
      {
        event: "INSERT",
        schema: "public",
        table: "predictions",
        filter: `match_id=eq.${matchId}`,
      },
      (payload) => {
        received = payload.new;
      }
    );

  const status = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve("TIMED_OUT"), REALTIME_TIMEOUT_MS);
    channel.subscribe((state) => {
      if (state === "SUBSCRIBED") {
        clearTimeout(timer);
        subscribed = true;
        resolve(state);
      } else if (state === "CHANNEL_ERROR" || state === "TIMED_OUT") {
        clearTimeout(timer);
        resolve(state);
      }
    });
  });

  if (!subscribed) {
    fail("RT anon subscribe", `status=${status}`);
    await anon.removeChannel(channel);
    return;
  }
  pass("RT anon subscribe", "SUBSCRIBED");

  const startedAt = Date.now();
  const { data: probe, error: insertError } = await admin
    .from("predictions")
    .insert({
      match_id: matchId,
      model_version: model.model_version,
      prediction_type: "win_prob",
      match_phase: "innings2",
      payload: { p: 0.5, probe: "check-anon-access" },
    })
    .select("prediction_id")
    .single();

  if (insertError) {
    fail("RT probe INSERT", insertError.message);
    await anon.removeChannel(channel);
    return;
  }

  const deadline = Date.now() + REALTIME_TIMEOUT_MS;
  while (received === null && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 100));
  }

  if (received === null) {
    fail(
      "RT anon received the INSERT",
      `nothing arrived in ${REALTIME_TIMEOUT_MS}ms. The subscription is open ` +
        `and the row exists, so this is the publication or Realtime itself, ` +
        `not RLS. NOTE: observed once as a transient on 2026-09-18 shortly ` +
        `after the project was resumed from pause - a fresh diagnostic passed ` +
        `minutes later with identical parameters. Re-run once before ` +
        `investigating; if it fails twice it is real. Deliberately not ` +
        `auto-retried, because a retry that hides a genuine failure is worse ` +
        `than a re-run you had to type`
    );
  } else if (received.prediction_id !== probe.prediction_id) {
    fail("RT anon received the INSERT", "a different row arrived than the probe");
  } else {
    pass("RT anon received the INSERT", `${Date.now() - startedAt} ms`);
  }

  await anon.removeChannel(channel);

  const { error: cleanupError } = await admin
    .from("predictions")
    .delete()
    .eq("prediction_id", probe.prediction_id);
  if (cleanupError) {
    fail("cleanup", `probe row ${probe.prediction_id} left behind: ${cleanupError.message}`);
  } else {
    console.log(`  ----  cleanup  probe row ${probe.prediction_id} removed`);
  }
}

await realtimeRoundTrip();

// --- verdict --------------------------------------------------------------

console.log("");
if (failures > 0) {
  console.log(`${failures} check(s) failed. The anonymous path is not usable.\n`);
  process.exit(1);
}
console.log("all checks passed - anon can read and Realtime delivers\n");
process.exit(0);
