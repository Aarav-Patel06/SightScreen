/**
 * Did the work actually land? Read the answer from git, do not assert it.
 *
 *   node scripts/verify-session.mjs              # against origin/main
 *   node scripts/verify-session.mjs <baseline>   # against a specific sha
 *
 * WHY THIS EXISTS. Five sessions of the UI phase were reported as done —
 * "Session 5 is done and verified", "the UI phase is closed" — while every
 * file in them sat untracked in a working tree. `git reflog` shows no commit
 * between 03c706a (2026-09-22) and the one the repository owner eventually
 * made by hand on 2026-09-24. Nothing failed. Nothing was reset. The commits
 * were never run.
 *
 * What makes that worse than a check that never fired is that the evidence
 * was on screen every time. `git status --short` was run at the end of
 * nearly every session and printed `?? web/components/`, `?? docs/...` — and
 * it was read as an inventory of work completed rather than as proof that
 * none of it existed outside one directory. Vercel served 03c706a
 * throughout.
 *
 * The rule this encodes: **"committed as <sha>" is a claim about the
 * repository, and a claim about the repository has to be read back from the
 * repository.** A summary of files touched is not that. Neither is the
 * absence of an error from a command nobody ran.
 *
 * NOT A HOOK, deliberately. The pre-push hook written to catch this class of
 * problem was itself untracked, so it was never installed and never ran —
 * the fix and the bug had the same shape. This is a tracked script whose
 * output is meant to be quoted verbatim in the session report. If the report
 * does not contain its output, the session did not verify.
 */

import { execFileSync } from "node:child_process";

function git(...args) {
  return execFileSync("git", args, { encoding: "utf8" }).trim();
}

function gitOrNull(...args) {
  try {
    return git(...args);
  } catch {
    return null;
  }
}

const baseline = process.argv[2] ?? "origin/main";
const problems = [];

console.log("session verification\n");

// --- 1. Is anything still only in the working tree? -----------------------
//
// --porcelain covers modified, staged AND untracked. Untracked is the one
// that mattered: every new file of the UI phase was `??`, which `git diff`
// does not show at all.

const dirty = git("status", "--porcelain");
const dirtyLines = dirty ? dirty.split("\n") : [];
const untracked = dirtyLines.filter((l) => l.startsWith("??"));

if (dirtyLines.length === 0) {
  console.log("  ok    working tree is clean - nothing left behind");
} else {
  problems.push(
    `${dirtyLines.length} path(s) are not committed` +
      (untracked.length ? `, ${untracked.length} of them untracked` : "")
  );
  console.log(`  FAIL  ${dirtyLines.length} uncommitted path(s):`);
  for (const line of dirtyLines.slice(0, 12)) console.log(`          ${line}`);
  if (dirtyLines.length > 12) console.log(`          ... and ${dirtyLines.length - 12} more`);
}

// --- 2. Did HEAD move? ----------------------------------------------------

const head = git("rev-parse", "HEAD");
const base = gitOrNull("rev-parse", baseline);

if (base === null) {
  console.log(`\n  ----  baseline ${baseline} not found; skipping the "HEAD moved" check`);
} else if (head === base) {
  problems.push(`HEAD is still ${head.slice(0, 8)} - no commit was made`);
  console.log(`\n  FAIL  HEAD has not moved from ${baseline} (${base.slice(0, 8)})`);
} else {
  const commits = git("log", "--format=%H %s", `${base}..${head}`).split("\n").filter(Boolean);
  console.log(`\n  ok    ${commits.length} commit(s) since ${baseline}, read back from git:`);
  for (const line of commits) {
    const [sha, ...subject] = line.split(" ");
    console.log(`          ${sha.slice(0, 8)}  ${subject.join(" ")}`);
  }
}

// --- 3. Did it leave the machine? -----------------------------------------
//
// Committed and pushed are different claims, and standing rule 17 is about
// the gap between them: CI is triggered by `push`, so work that is committed
// and unpushed is still invisible to every gate in the repository.

const remote = gitOrNull("rev-parse", "origin/main");
if (remote === null) {
  console.log("\n  ----  no origin/main ref; cannot tell whether this was pushed");
} else if (remote === head) {
  console.log("\n  ok    origin/main matches HEAD - pushed, and CI can see it");
} else {
  const ahead = gitOrNull("rev-list", "--count", `origin/main..HEAD`);
  console.log(
    `\n  ----  HEAD is ${ahead ?? "?"} commit(s) ahead of origin/main - committed but NOT pushed.` +
      "\n        CI is triggered by push, so nothing has checked this yet (standing rule 17)."
  );
}

// --- verdict --------------------------------------------------------------

console.log("");
if (problems.length > 0) {
  console.log("THE WORK DID NOT LAND:");
  for (const problem of problems) console.log(`  - ${problem}`);
  console.log("\nDo not report this session as done.\n");
  process.exit(1);
}
console.log("verified: the work is committed and nothing is left in the working tree.\n");
