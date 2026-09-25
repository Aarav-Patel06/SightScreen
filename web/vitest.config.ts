/**
 * jsdom, so the §12.2 rules on the match page are asserted rather than
 * eyeballed. The merge tests are pure and would run under `node`; the
 * component test is what needs a DOM.
 *
 * The `@/` alias is declared here as well as in tsconfig.json because Vitest
 * resolves imports itself and does not read tsconfig paths.
 */

import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  // tsconfig.json says jsx: "preserve" because Next.js does its own JSX
  // transform. Vite's oxc transform reads that same field, and preserved JSX
  // is not valid JavaScript by the time Rolldown parses it - so the runtime
  // is set explicitly here for tests only.
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // Every directory that can hold a test, listed explicitly.
    //
    // `app/**/*.test.ts` as well as `.tsx`: route handlers are plain
    // TypeScript, and the pattern that only matched components would have
    // silently collected nothing for app/api/agent/route.test.ts - a test
    // file that exists, passes locally when named directly, and never runs
    // in `npm test`. That is the quietest way to have no coverage at all.
    //
    // `components/**` added 2026-09-25, and it is the SAME BUG a second time:
    // the first test written under components/ reported "filter: ... no test
    // files found" because the list above never covered that directory. A
    // glob that enumerates directories fails silently every time a new one
    // appears, and it fails by passing.
    //
    // tests/ops/test_include_pattern is the guard: it asserts that every
    // *.test.* file in this package is matched by one of these globs, so the
    // next directory cannot be missed the same way.
    include: [
      "lib/**/*.test.ts",
      "app/**/*.test.ts",
      "app/**/*.test.tsx",
      "components/**/*.test.ts",
      "components/**/*.test.tsx",
    ],
    globals: false,
  },
});
