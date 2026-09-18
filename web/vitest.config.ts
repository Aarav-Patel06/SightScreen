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
    include: ["lib/**/*.test.ts", "app/**/*.test.tsx"],
    globals: false,
  },
});
