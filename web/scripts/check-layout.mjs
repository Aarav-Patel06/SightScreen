/**
 * Does any page scroll sideways on a phone?
 *
 *   npx next start -p 3140 &
 *   node scripts/check-layout.mjs                    # defaults to :3140
 *   BASE_URL=http://localhost:3000 node scripts/check-layout.mjs
 *
 * WHY THIS EXISTS. On 2026-09-25 a browser was pointed at this site at 340px
 * for the first time, and **every route scrolled sideways**. The header row
 * measured 415px inside a 340px viewport: wordmark, live slot and a four-item
 * nav on one line that never wrapped.
 *
 * It was not new. Overriding the stylesheet back to the pre-step-6 type scale
 * in the live page gave 373px — wordmark 127 plus nav 214 already exceeded
 * 340 — so the header had never fitted at that width, on any page, since the
 * shell was built in session 2. UI-PHASE.md §1.6 says "mobile-first" and names
 * 340px explicitly.
 *
 * THE REASON NOTHING CAUGHT IT IS THE POINT. No test in this repository has
 * ever laid out a page. The web suite runs under jsdom, which parses and
 * styles but does no layout: `getBoundingClientRect` returns zeros there, so
 * an overflow assertion written in vitest would have passed on a page that
 * was 400px too wide. The Python suite never sees the front end. The build
 * renders HTML and says nothing about where boxes land.
 *
 * Viewport overflow was therefore an entirely uncovered class, and it took
 * installing a browser to find the first instance. This is the guard that was
 * missing; the header bug is its proof-of-firing.
 *
 * BROWSER. playwright-core is a devDependency and ships no browser binary, so
 * this drives one that is already on the machine: PLAYWRIGHT_BROWSER, or the
 * system Edge on Windows, or a chromium on PATH. If none is found it says so
 * loudly and exits non-zero locally, while CI — which has no browser and no
 * server — is expected to skip it at the workflow level rather than here.
 */

import { existsSync } from "node:fs";

import { chromium } from "playwright-core";

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3140";

/**
 * 340px because SPEC.md §12.2 says most cricket viewing is second-screen and
 * UI-PHASE.md §1.6 names this width; 1140px because that is `--page`, the
 * widest the layout is designed for. A failure at either end is a failure.
 */
const WIDTHS = [340, 1140];

const ROUTES = [
  "/",
  "/accuracy",
  "/matches",
  "/players",
  "/ask",
  "/model-card",
  "/design",
  "/match/8429",
];

const CANDIDATES = [
  process.env.PLAYWRIGHT_BROWSER,
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/usr/bin/google-chrome",
].filter(Boolean);

function findBrowser() {
  return CANDIDATES.find((path) => existsSync(path)) ?? null;
}

let failures = 0;

function fail(message) {
  failures += 1;
  console.log(`  FAIL  ${message}`);
}

console.log("horizontal overflow\n");

const executablePath = findBrowser();
if (executablePath === null) {
  console.log(
    "  FAIL  no browser found. playwright-core ships no binary on purpose.\n" +
      "        Set PLAYWRIGHT_BROWSER to a Chromium-family executable, or install\n" +
      `        one of:\n          ${CANDIDATES.slice(1).join("\n          ")}\n`
  );
  process.exit(1);
}
console.log(`  using ${executablePath}\n`);

const browser = await chromium.launch({ executablePath });

try {
  for (const width of WIDTHS) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    for (const route of ROUTES) {
      try {
        await page.goto(`${BASE_URL}${route}`, { waitUntil: "load", timeout: 90_000 });
      } catch (error) {
        fail(`${route} at ${width}px did not load: ${String(error).split("\n")[0]}`);
        continue;
      }

      const result = await page.evaluate(() => {
        const root = document.documentElement;
        // The widest thing that is NOT inside something that scrolls. A table
        // in a `.table-panel` is allowed to be wide; the document is not.
        const culprits = [];
        for (const el of document.querySelectorAll("body *")) {
          const box = el.getBoundingClientRect();
          if (box.width === 0 || box.right <= root.clientWidth + 1) continue;
          let contained = false;
          for (let parent = el.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
            const overflowX = getComputedStyle(parent).overflowX;
            if (overflowX === "auto" || overflowX === "scroll" || overflowX === "hidden") {
              contained = true;
              break;
            }
          }
          if (!contained) {
            const name = `${el.tagName.toLowerCase()}.${String(el.className || "").split(" ")[0]}`;
            culprits.push(`${name} right=${Math.round(box.right)}`);
          }
        }
        return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth, culprits: culprits.slice(0, 4) };
      });

      /*
       * TWO LAYOUT PROPERTIES BEYOND OVERFLOW, both added after they broke.
       *
       * Nested boxes: /matches and /players wrapped their title, prose AND
       * the table's own panel in a second panel, so the page rendered a box
       * inside a box. Invisible in the source - the outer element is a
       * <section> and the inner one is three components away.
       *
       * Header clearance: the header is sticky, and five pages had their
       * first panel's top edge meeting its bottom edge exactly, because
       * `main` had no top padding and those pages do not open with a heading
       * that brings its own margin.
       */
      const structure = await page.evaluate(() => {
        const SEL = ".band, .panel, .level-1, .level-2, .page-links, .ask-intro";
        const nested = [];
        for (const el of document.querySelectorAll(SEL)) {
          const inner = el.querySelector(SEL);
          if (inner) {
            const name = (e) => `${e.tagName.toLowerCase()}.${String(e.className || "").split(" ")[0]}`;
            nested.push(`${name(el)} > ${name(inner)}`);
          }
        }
        const header = document.querySelector(".site-header");
        const first = document.querySelector("main")?.firstElementChild;
        const clearance =
          header && first
            ? Math.round(first.getBoundingClientRect().top - header.getBoundingClientRect().bottom)
            : null;
        return { nested, clearance };
      });

      if (structure.nested.length > 0) {
        fail(`${route} at ${width}px has a box inside a box: ${structure.nested.join(", ")}`);
      }
      if (structure.clearance !== null && structure.clearance < 8) {
        fail(`${route} at ${width}px: only ${structure.clearance}px between the sticky header and the first panel`);
      }

      if (result.scrollWidth > result.clientWidth + 1) {
        fail(
          `${route} at ${width}px scrolls sideways: ${result.scrollWidth}px of ${result.clientWidth}px` +
            (result.culprits.length ? `\n          ${result.culprits.join("\n          ")}` : "")
        );
      } else {
        console.log(`  ok    ${route.padEnd(14)} ${width}px`);
      }
    }
    await page.close();
  }

  /*
   * THE SIGNED-IN /ask, which the loop above cannot reach.
   *
   * Added 2026-09-25 after the example chips and the wide input shipped: they
   * only render behind the password gate, so every route above measured a
   * page that did not contain them. A guard that checks the logged-out state
   * of a gated page is measuring the wrong page - it was only luck that a
   * manual check caught it.
   *
   * Skipped loudly when ASK_PASSWORD is absent rather than passing silently.
   */
  const password = process.env.ASK_PASSWORD;
  if (!password) {
    console.log("");
    console.log("  ----  signed-in /ask NOT CHECKED: ASK_PASSWORD is not set.");
    console.log("        The chips and the wide input only exist behind the gate.");
  } else {
    for (const width of WIDTHS) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      try {
        await page.goto(`${BASE_URL}/ask`, { waitUntil: "load", timeout: 90_000 });
        await page.fill(".ask-input-password", password);
        await page.click(".ask-button");
        await page.waitForSelector(".ask-chip", { timeout: 30_000 });
        const result = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        }));
        if (result.scrollWidth > result.clientWidth + 1) {
          fail(`/ask signed in at ${width}px scrolls sideways: ${result.scrollWidth}px of ${result.clientWidth}px`);
        } else {
          console.log(`  ok    /ask (signed in) ${width}px`);
        }
      } catch (error) {
        fail(`/ask signed in at ${width}px: ${String(error).slice(0, 120)}`);
      }
      await page.close();
    }
  }
} finally {
  await browser.close();
}

console.log("");
if (failures > 0) {
  console.log(
    `${failures} route/width combination(s) overflow. A page that scrolls sideways on\n` +
      `a phone is the failure UI-PHASE.md §1.6 exists to prevent.\n`
  );
  process.exit(1);
}
console.log(`verified: no horizontal overflow at ${WIDTHS.join("px or ")}px.\n`);
