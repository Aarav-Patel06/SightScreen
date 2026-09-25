/**
 * Every test file in this package is actually collected by `npm test`.
 *
 * WHY THIS EXISTS. Vitest's `include` is a list of directory-shaped globs, and
 * a test file outside all of them is not an error - it is silence. `npm test`
 * prints a pass, the file is never executed, and the only symptom is a number
 * that did not go up.
 *
 * This has now happened twice:
 *
 *   1. `app/api/agent/route.test.ts` - the pattern matched only `.tsx`, so a
 *      route-handler test written in plain TypeScript never ran.
 *   2. `components/ball-mark.test.ts` (2026-09-25) - the first test ever
 *      written under `components/`, and no glob covered that directory.
 *
 * Both were found by hand, both times by someone noticing the test count.
 * Adding another directory to the list fixes one instance; this fixes the
 * class, because it fails the moment a test file exists that the globs do not
 * reach - including a directory nobody has thought of yet.
 *
 * It deliberately reads the config rather than hardcoding the globs. A copy of
 * the list here would be one more thing to drift.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = process.cwd();
const IGNORED = new Set(["node_modules", ".next", ".git", "coverage", "public"]);

function testFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (IGNORED.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) testFiles(full, found);
    else if (/\.test\.(ts|tsx)$/.test(entry)) {
      found.push(relative(ROOT, full).split(sep).join("/"));
    }
  }
  return found;
}

/** The subset of glob syntax the include list actually uses. */
function globToRegExp(glob: string): RegExp {
  const source = glob
    .split("**/")
    .map((part) =>
      part.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, "[^/]*")
    )
    .join("(?:.*/)?");
  return new RegExp(`^${source}$`);
}

function includeGlobs(): string[] {
  const config = readFileSync(resolve(ROOT, "vitest.config.ts"), "utf8");
  const block = config.match(/include:\s*\[([\s\S]*?)\]/);
  expect(block, "vitest.config.ts has no include array").not.toBeNull();
  return [...block![1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

describe("test discovery", () => {
  const globs = includeGlobs();
  const patterns = globs.map(globToRegExp);
  const files = testFiles(ROOT);

  it("finds the include globs and some test files", () => {
    // Guards the guard: if either of these is empty the loop below passes
    // vacuously, which is the failure mode this whole file is about.
    expect(globs.length).toBeGreaterThan(0);
    expect(files.length).toBeGreaterThan(5);
  });

  it("matches this file, proving the matcher works at all", () => {
    const self = "lib/test-discovery.test.ts";
    expect(files).toContain(self);
    expect(patterns.some((p) => p.test(self))).toBe(true);
  });

  it("rejects a path outside every glob, proving it can fail", () => {
    expect(patterns.some((p) => p.test("scripts/nowhere.test.ts"))).toBe(false);
  });

  it("collects every test file in the package", () => {
    const orphans = files.filter((file) => !patterns.some((p) => p.test(file)));
    expect(
      orphans,
      `these exist but \`npm test\` never runs them - add a glob to ` +
        `vitest.config.ts's include: ${orphans.join(", ")}`
    ).toEqual([]);
  });
});
