/**
 * Generate docs/column-census.md — which columns are actually populated.
 *
 *   node scripts/make-column-census.mjs
 *
 * WHY THIS EXISTS. Three features have now been built on columns that looked
 * populated because their neighbours were, and all three were backed out:
 *
 *   - `players.batting_hand` / `bowling_style` / `dob` — 0 of 18,468, and in
 *     the reference-sync tuple, which is what made them look live.
 *   - `teams.short_name` — 0 of 350, one line below those in the same tuple.
 *   - `matches.winner` / `target_runs` / `result_method` — 0 of 107, because
 *     `mirror_match_rows` copied eight of thirteen columns.
 *
 * Nothing in a schema listing distinguishes "synced and full" from "synced
 * and empty". A `\\d matches` shows thirteen columns whether or not five of
 * them are NULL for every row, and the generated TypeScript types say
 * `winner: number | null` either way. The only thing that tells you is a
 * count, and nobody runs a count before writing a SELECT.
 *
 * So the count is run for every column of every table, on both databases, and
 * committed. The DIVERGENCE column is the point: a column that is populated
 * locally and empty on Supabase is a column somebody will build on and have
 * to back out.
 *
 * Needs Docker running. Local counts go through the container's psql; the
 * Supabase side goes through the same psql against the session pooler, which
 * the container can reach.
 */

import { writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

import { corpusQuery } from "./corpus.mjs";

const CONTAINER = process.env.CORPUS_CONTAINER ?? "sightscreen-postgres-1";
const OUT = new URL("../../docs/column-census.md", import.meta.url);

function pooler() {
  const line = execFileSync("node", ["-e", `
    const fs = require("node:fs");
    const text = fs.readFileSync("../api/.env", "utf8");
    const match = text.match(/^SUPABASE_SESSION_POOLER_URL=(.*)$/m);
    process.stdout.write(match ? match[1].trim() : "");
  `], { encoding: "utf8" });
  if (!line) {
    console.error("SUPABASE_SESSION_POOLER_URL not found in api/.env");
    process.exit(1);
  }
  return line;
}

/** Run a query on Supabase, through the container so no local psql is needed. */
function supabaseQuery(url, sql) {
  const out = execFileSync(
    "docker",
    ["exec", CONTAINER, "psql", url, "-t", "-A", "-F", "\u0001", "-c", sql],
    { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }
  );
  return out
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.split("\u0001"));
}

/**
 * One query PER TABLE, not per column and not one giant UNION.
 *
 * Per column would be ~190 sequential scans, and `deliveries` alone is 3.78M
 * rows nineteen times over. One UNION ALL across everything was the first
 * attempt and produced `spawnSync docker ENAMETOOLONG` - the generated SQL
 * exceeded the command-line length limit.
 *
 * Per table is the middle: `count(*) FILTER (WHERE col IS NULL)` for every
 * column in a single pass, so each table is scanned once and each statement
 * stays short enough to pass as an argument.
 */
function tablesOf(runner) {
  return runner(
    "SELECT table_name FROM information_schema.tables " +
      "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
  ).map(([name]) => name);
}

function columnsOf(runner, table) {
  return runner(
    "SELECT column_name FROM information_schema.columns " +
      `WHERE table_schema='public' AND table_name='${table}' ORDER BY ordinal_position`
  ).map(([name]) => name);
}

function census(runner) {
  const out = new Map();
  for (const table of tablesOf(runner)) {
    const columns = columnsOf(runner, table);
    if (columns.length === 0) continue;

    const filters = columns
      .map((c) => `count(*) FILTER (WHERE "${c}" IS NULL)`)
      .join(", ");
    const [row] = runner(`SELECT count(*), ${filters} FROM "${table}"`);
    if (!row) continue;

    const total = Number(row[0]);
    columns.forEach((column, index) => {
      out.set(`${table}.${column}`, { total, nulls: Number(row[index + 1]) });
    });
  }
  return out;
}

const url = pooler();
const local = census(corpusQuery);
const remote = census((sql) => supabaseQuery(url, sql));

const keys = [...new Set([...local.keys(), ...remote.keys()])].sort();

function pct(stat) {
  if (!stat || stat.total === 0) return "—";
  if (stat.nulls === 0) return "full";
  if (stat.nulls === stat.total) return "**EMPTY**";
  return `${((1 - stat.nulls / stat.total) * 100).toFixed(1)}%`;
}

const lines = [];
let divergences = 0;

for (const key of keys) {
  const l = local.get(key);
  const r = remote.get(key);

  // The signature. Populated locally, empty on Supabase, and the destination
  // actually has rows - an empty table is not a divergence, it is an empty
  // table.
  const diverges =
    l && r && l.total > 0 && r.total > 0 && l.nulls < l.total && r.nulls === r.total;
  if (diverges) divergences += 1;

  lines.push(
    `| \`${key}\` | ${l ? l.total.toLocaleString() : "—"} | ${pct(l)} | ` +
      `${r ? r.total.toLocaleString() : "—"} | ${pct(r)} | ${diverges ? "**DIVERGES**" : ""} |`
  );
}

const today = new Date().toISOString().slice(0, 10);

const doc = `# Column census

**Check this before building on a Supabase column.** If the column is not in
this table, regenerate it: \`cd web && node scripts/make-column-census.mjs\`.

Counted ${today}. Regenerate after any migration or mirror change — a census
is only worth reading if it is newer than the pipeline it describes.

## Why

Three features have been built on columns that looked populated because their
neighbours were, and all three were backed out:

| Column | Was assumed | Actually |
|---|---|---|
| \`players.batting_hand\`, \`bowling_style\`, \`dob\` | populated | 0 of 18,468 |
| \`teams.short_name\` | populated | 0 of 350 |
| \`matches.winner\`, \`target_runs\`, \`result_method\` | populated | 0 of 107 until session 3 mirrored them |

Nothing in a schema listing separates "synced and full" from "synced and
empty". \`\\d matches\` shows the column either way, and the generated types say
\`winner: number | null\` either way. Only a count tells you, and nobody runs a
count before writing a SELECT.

**${divergences} column(s) currently diverge** — populated in the local corpus
and empty on Supabase. That is the shape of all three bugs above, and a column
marked DIVERGES is one somebody will build on and have to back out.

An **EMPTY** cell on one side alone is not necessarily a problem:
\`deliveries\` and \`match_states\` are empty on Supabase by design (SPEC.md
§2.1 — 3.78M rows do not fit the free tier), and \`player_state\` is empty
everywhere until Phase 5.

## The census

"full" means no NULLs. A percentage is the populated fraction.

| Column | Local rows | Local | Supabase rows | Supabase | |
|---|---|---|---|---|---|
${lines.join("\n")}
`;

writeFileSync(OUT, doc);
console.log(
  `wrote docs/column-census.md\n` +
    `  ${keys.length} columns across both databases\n` +
    `  ${divergences} divergence(s)`
);
