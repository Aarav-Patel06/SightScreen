/**
 * Does the built site actually contain data?
 *
 *   node scripts/check-build-output.mjs        # after `npm run build`
 *
 * WHY THIS EXISTS. `ci.yml`'s web job runs `npm run build` with placeholder
 * Supabase credentials, under a comment claiming "every page is force-dynamic,
 * so nothing is prerendered and nothing connects". That was false: only
 * /accuracy is force-dynamic, while /, /matches and /players use
 * `revalidate = 3600` and ARE prerendered at build time.
 *
 * They do not fail when the database is unreachable, because every loader has
 * a deliberate degradation path so that a paused free-tier project yields a
 * page instead of a 500. So the build succeeds, the pages render empty, and
 * the job goes green. Measured on 2026-09-25 with CI's exact values:
 *
 *     BUILD EXIT=0   12/12 static pages   matches.html rows: 0
 *
 * The build proves the app compiles and that the server/client component
 * boundary holds. It was being read as proving the pages work. This script is
 * the difference between those two claims.
 *
 * THE THRESHOLDS ARE FLOORS, NOT COUNTS. Asserting 341 matches would fail
 * every time a match is added, and a check that needs editing on every data
 * change gets edited without being read. These are set an order of magnitude
 * below the real figures: they catch "empty" and "fell back", not drift.
 */

import { readFileSync, existsSync } from "node:fs";

const APP = ".next/server/app";

/**
 * The landing figures degrade to COMMITTED fallbacks (lib/corpus-facts.ts),
 * so "non-zero" does not distinguish a live page from a dead one - the
 * fallbacks are non-zero by design. What does distinguish them is the
 * staleness marker the page renders when `figures.anyStale` is true.
 *
 * Checking the count alone here would have been the same mistake this script
 * exists to correct: asserting something adjacent to the claim.
 */
const STALE_MARKER = "As of ";

const checks = [
  {
    file: "matches.html",
    label: "/matches rows",
    // 341 today. A build with no database renders 0.
    min: 50,
    count: (html) => Math.max(0, (html.match(/<tr/g) ?? []).length - 1),
  },
  {
    file: "players.html",
    label: "/players rows",
    // 50 today (the page shows a leaderboard, not all 18,468).
    min: 20,
    count: (html) => Math.max(0, (html.match(/<tr/g) ?? []).length - 1),
  },
];

let failures = 0;

function fail(message) {
  failures += 1;
  console.log(`  FAIL  ${message}`);
}

function pass(message) {
  console.log(`  ok    ${message}`);
}

console.log("built output contains real data\n");

for (const check of checks) {
  const path = `${APP}/${check.file}`;
  if (!existsSync(path)) {
    fail(`${check.file} was not prerendered - did the build run, and is the page still static?`);
    continue;
  }
  const n = check.count(readFileSync(path, "utf8"));
  if (n < check.min) {
    fail(`${check.label}: ${n}, expected at least ${check.min}. The page built, and it is empty.`);
  } else {
    pass(`${check.label}: ${n} (floor ${check.min})`);
  }
}

// --- the landing page -----------------------------------------------------

const landingPath = `${APP}/index.html`;
if (!existsSync(landingPath)) {
  fail("index.html was not prerendered");
} else {
  const html = readFileSync(landingPath, "utf8");

  if (html.includes(STALE_MARKER)) {
    fail(
      `the landing figures are flagged stale ("${STALE_MARKER}..."), so at least one ` +
        `came from the committed fallback rather than the database`
    );
  } else {
    pass("landing figures are live, not the committed fallback");
  }

  // Belt and braces, and a different failure: a figure that renders as 0 or
  // as an empty string while the page still believes it is live.
  //
  // SCRIPT TAGS ARE STRIPPED FIRST, and the first version of this did not do
  // that. Next.js inlines its RSC payload into <script> blocks full of chunk
  // hashes and module ids, so the "three non-zero numbers" test matched
  // `59,229,057,476,575,360` out of a bundle filename and would have passed on
  // a completely blank page. An assertion that cannot fail is the exact thing
  // this file was written to catch, so it nearly shipped with one inside it.
  const text = html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<[^>]+>/g, " ");
  const figures = (text.match(/[\d][\d,]{2,}/g) ?? [])
    .map((value) => Number(value.replace(/,/g, "")))
    .filter((value) => Number.isFinite(value) && value > 0);

  if (figures.length < 3) {
    fail(`expected three non-zero landing figures, found ${figures.length}`);
  } else {
    pass(`landing figures present and non-zero (largest ${Math.max(...figures).toLocaleString()})`);
  }
}

console.log("");
if (failures > 0) {
  console.log(
    `${failures} check(s) failed. The build succeeded and the pages are empty - ` +
      `which is exactly what a green build with no database looks like.\n`
  );
  process.exit(1);
}
console.log("verified: the prerendered pages contain data from the database.\n");
