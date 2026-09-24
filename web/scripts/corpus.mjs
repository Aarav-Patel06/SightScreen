/**
 * Read-only access to the local corpus, for build-time generator scripts.
 *
 * Three facts the deployed app needs are not on Supabase and never will be:
 * the delivery count (3.78M rows do not fit the free tier), each match's
 * winner and target, and who batted second. The last two are the awkward
 * ones - `matches.winner`, `target_runs` and `result_method` exist on
 * Supabase with the right types and are NULL for every row, because
 * `replay_log.py` mirrors only the columns the serving path reads. A schema
 * listing shows the columns; `count(*) WHERE winner IS NOT NULL` shows zero.
 * That is the third instance of this shape in the project, after
 * `players.batting_hand` and `teams.short_name`, so it is treated as the
 * default expectation rather than a surprise: a mirrored column is empty
 * until something is shown to write it.
 *
 * Goes through `docker exec psql` rather than a Postgres client because
 * web/ has no `pg` dependency and adding one to ship a dev-time script would
 * put a driver in the production dependency tree for no runtime benefit.
 * These scripts run on a developer's machine, next to the container, and
 * their output is committed.
 *
 * NOT importable from application code. Nothing under app/ or lib/ may depend
 * on this - the whole point is that the results are committed ahead of time.
 */

import { execFileSync } from "node:child_process";

const CONTAINER = process.env.CORPUS_CONTAINER ?? "sightscreen-postgres-1";
const DATABASE = process.env.CORPUS_DATABASE ?? "cricket_training";

/**
 * Run a query and return rows as arrays of strings.
 *
 * Uses psql's unaligned tuples-only mode with an explicit separator rather
 * than JSON, so the parsing has no ambiguity to get wrong. Empty strings mean
 * SQL NULL, which callers must distinguish from a genuine empty value.
 */
export function corpusQuery(sql) {
  let stdout;
  try {
    stdout = execFileSync(
      "docker",
      ["exec", CONTAINER, "psql", "-U", "postgres", "-d", DATABASE, "-t", "-A", "-F", "\u0001", "-c", sql],
      { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }
    );
  } catch (error) {
    console.error(
      `\ncould not query the local corpus.\n\n` +
        `  container: ${CONTAINER}\n  database:  ${DATABASE}\n\n` +
        `Start it with \`docker compose up -d\` from the repository root, or ` +
        `set CORPUS_CONTAINER / CORPUS_DATABASE if yours differ.\n\n` +
        `${error.stderr?.toString().trim() ?? error.message}\n`
    );
    process.exit(1);
  }

  return stdout
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => line.split("\u0001"));
}

/** A query expected to return exactly one row. Fails loudly if it does not. */
export function corpusRow(sql) {
  const rows = corpusQuery(sql);
  if (rows.length !== 1) {
    console.error(`expected exactly one row, got ${rows.length}, for:\n${sql}`);
    process.exit(1);
  }
  return rows[0];
}
