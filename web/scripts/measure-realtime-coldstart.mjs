/**
 * How long after SUBSCRIBED does a postgres_changes channel actually
 * deliver? (Phase 2 session 5.)
 *
 * The anon gate's round trip failed twice with "nothing arrived in 15000ms"
 * and passed on an immediate re-run, both times on the first channel opened
 * after an idle period. Two occurrences with the same shape is a pattern,
 * not noise, and the thing it would break is the page's whole premise: a
 * subscription that reports SUBSCRIBED and silently delivers nothing is
 * §7.4's failure mode wearing a different hat, and every user arriving after
 * a quiet period would hit it.
 *
 * So measure it rather than argue about it. Each round opens a BRAND NEW
 * client - a new websocket, not a reused one - waits for SUBSCRIBED, then
 * writes probe rows at increasing offsets and records which arrive and when.
 * If the early probes are lost and the later ones are not, there is a
 * warm-up window, and its width is what the page has to cover.
 *
 *   node --env-file=.env.local scripts/measure-realtime-coldstart.mjs
 *   node --env-file=.env.local scripts/measure-realtime-coldstart.mjs --rounds 5
 *
 * Writes and deletes rows in `predictions` with the secret key. Run it
 * against a match you are not simultaneously demoing.
 */

import { createClient } from "@supabase/supabase-js";

// Offsets after SUBSCRIBED, in ms. The first is the case the gate exercises;
// the rest establish whether delivery starts working and when.
const PROBE_OFFSETS_MS = [0, 1_000, 3_000, 6_000, 12_000];
const DRAIN_MS = 20_000;

const rounds = Number(process.argv[process.argv.indexOf("--rounds") + 1]) || 3;
const gapSeconds = Number(process.argv[process.argv.indexOf("--gap") + 1]) || 0;

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
const secret = process.env.SUPABASE_SECRET_KEY;
if (!url || !anonKey || !secret) {
  console.error("NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY and SUPABASE_SECRET_KEY must be set");
  process.exit(2);
}

const admin = createClient(url, secret, { auth: { persistSession: false } });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function pickMatch() {
  const { data, error } = await admin
    .from("matches")
    .select("match_id")
    .order("match_id", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error || !data) {
    console.error("no rows in `matches` on Supabase - run the replay driver once first");
    process.exit(2);
  }
  return data.match_id;
}

async function activeModelVersion() {
  const { data } = await admin
    .from("model_versions")
    .select("model_version")
    .eq("is_active", true)
    .limit(1)
    .maybeSingle();
  return data?.model_version;
}

/** One cold channel: subscribe, write probes on a schedule, see what lands. */
async function round(index, matchId, modelVersion) {
  // A NEW client per round. Reusing one would reuse its websocket, which is
  // precisely the thing being measured.
  const anon = createClient(url, anonKey, {
    auth: { persistSession: false },
    realtime: { params: { eventsPerSecond: 20 } },
  });

  const arrivals = new Map(); // prediction_id -> ms since that row was inserted
  const inserted = new Map(); // prediction_id -> { offset, at }

  const channel = anon.channel(`coldstart:${matchId}:${Date.now()}:${index}`).on(
    "postgres_changes",
    { event: "INSERT", schema: "public", table: "predictions", filter: `match_id=eq.${matchId}` },
    (message) => {
      const id = message.new?.prediction_id;
      const sent = inserted.get(id);
      if (sent) arrivals.set(id, Date.now() - sent.at);
    }
  );

  const subscribeStarted = Date.now();
  const status = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve("TIMEOUT_WAITING_FOR_STATUS"), 30_000);
    channel.subscribe((state) => {
      if (state === "SUBSCRIBED" || state === "CHANNEL_ERROR" || state === "TIMED_OUT") {
        clearTimeout(timer);
        resolve(state);
      }
    });
  });
  const subscribeMs = Date.now() - subscribeStarted;

  if (status !== "SUBSCRIBED") {
    console.log(`round ${index}: ${status} after ${subscribeMs} ms`);
    await anon.removeChannel(channel);
    return { subscribeMs, status, results: [] };
  }

  const subscribedAt = Date.now();
  for (const offset of PROBE_OFFSETS_MS) {
    const wait = offset - (Date.now() - subscribedAt);
    if (wait > 0) await sleep(wait);
    const { data, error } = await admin
      .from("predictions")
      .insert({
        match_id: matchId,
        model_version: modelVersion,
        prediction_type: "win_prob",
        match_phase: "innings2",
        payload: { p: 0.5, probe: "coldstart", offset_ms: offset },
      })
      .select("prediction_id")
      .single();
    if (error) {
      console.log(`round ${index}: probe at +${offset} ms failed to insert: ${error.message}`);
      continue;
    }
    inserted.set(data.prediction_id, { offset, at: Date.now() });
  }

  await sleep(DRAIN_MS);
  await anon.removeChannel(channel);

  const results = [...inserted.entries()].map(([id, sent]) => ({
    offset: sent.offset,
    latency: arrivals.get(id) ?? null,
    id,
  }));
  results.sort((a, b) => a.offset - b.offset);

  const line = results
    .map((r) => `+${r.offset / 1000}s:${r.latency === null ? "LOST" : `${r.latency}ms`}`)
    .join("  ");
  console.log(`round ${index}: SUBSCRIBED in ${subscribeMs} ms   ${line}`);

  await admin
    .from("predictions")
    .delete()
    .in("prediction_id", [...inserted.keys()]);

  return { subscribeMs, status, results };
}

const matchId = await pickMatch();
const modelVersion = await activeModelVersion();
console.log(
  `measuring ${rounds} cold channel(s) against match ${matchId}, ` +
    `probes at ${PROBE_OFFSETS_MS.map((m) => `+${m / 1000}s`).join(" ")}, ` +
    `${DRAIN_MS / 1000}s drain\n`
);

const all = [];
for (let i = 1; i <= rounds; i += 1) {
  if (i > 1 && gapSeconds) {
    console.log(`  (idling ${gapSeconds}s)`);
    await sleep(gapSeconds * 1000);
  }
  all.push(await round(i, matchId, modelVersion));
}

console.log("\nby offset, across rounds:");
for (const offset of PROBE_OFFSETS_MS) {
  const seen = all.flatMap((r) => r.results.filter((x) => x.offset === offset));
  const lost = seen.filter((x) => x.latency === null).length;
  const got = seen.filter((x) => x.latency !== null).map((x) => x.latency);
  const avg = got.length ? Math.round(got.reduce((a, b) => a + b, 0) / got.length) : null;
  console.log(
    `  +${String(offset / 1000).padStart(3)}s  delivered ${got.length}/${seen.length}` +
      (avg === null ? "" : `  mean ${avg} ms`) +
      (lost ? `  LOST ${lost}` : "")
  );
}
process.exit(0);
