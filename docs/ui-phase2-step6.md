# UI Phase 2, Step 6 — polish pass: tokens and chrome

Lighter chrome, header shadow, larger type, centred `h1`, corner graphics,
active nav, one control style, hover states. No page layouts, no components.

---

## 1. `CHROME_SAFE_TEXT` is deleted, not relaxed

`--chrome` goes `#C6B592` → **`#EEEAD8`**, and `--chrome-deep` is **removed**.

This is the item that was never only cosmetic. `--flag` measured **3.49:1** on
the old chrome, which is why crimson — the colour §3.2 assigns to failure
states — could not appear in a table header band, the one place a failure
state most wants to be.

| on `--chrome` | `#C6B592` before | `#EEEAD8` after |
|---|---|---|
| `--ink` | 7.64 | **12.75** |
| `--flag` | 3.49 ✗ | **5.83** |
| `--ink-soft` | 3.02 ✗ | **5.03** |
| `--bat-text` | 2.94 ✗ | **4.90** |
| `--bowl-text` | 2.76 ✗ | **4.61** |

Every text token clears 4.5:1, so there is no safe-list left to keep. **The
constraint was removed by changing the surface, not by lowering a floor.**

### What it unlocks

- crimson in table header bands — failure states where they belong
- teal links in the header and footer
- `--ink-soft` for secondary text there, which `.live-teams` had been
  compensating for with a weight change

### The second tier could not survive it

At the lightest chrome that carries every token, the darkest possible
`--chrome-deep` that *also* carries them separates from `--chrome` by about
**1.00** — no tier at all. So table header rows are now marked by **weight
plus a rule** rather than by a darker fill.

### The test is deliberately inverted

`tokens.test.ts` used to iterate `CHROME_SAFE_TEXT` and assert the listed
tokens cleared. It now iterates **`TEXT_TOKENS`** and asserts *every* token
clears on chrome — the inverse claim. Named here because this phase leans on
unmodified tests as evidence, and this one was flipped on purpose. The
stylesheet-level "only ink on a chrome background" test was **deleted**: it
encoded the constraint being removed, and keeping it would have blocked the
change it was written before.

### `--bowl-text` was missed, and the test caught it immediately

The value was first set to `#E9E3CF`, chosen as the darkest chrome clearing
`--ink`, `--flag`, `--ink-soft` and `--bat`. The inverted test failed on the
fifth token at **4.34:1** on the first run.

The lesson is cheap: **a safe-list has a definite length and you can check it;
"every token" has to be enumerated from the registry, not from memory.** Four
were measured because four came to mind.

---

## 2. Header shadow — from the existing scale

`--shadow-1`, already defined. No new shadow. Chrome against paper falls from
**1.853 to 1.110**, so the edge the fill used to carry moves into the shadow.
The footer takes a `--rule` top border only — a shadow above a footer points
the wrong way.

---

## 3. Type scale to a 17px base

**13.6 / 17 / 21.3 / 26.6 / 33.2 / 41.5 / 51.9**, ratio 1.25 unchanged.

Chosen by measuring the 340px overflow risk: at 17px the hero teams line needs
~280px of the 308px available; at 18px it needs ~296px and a longer pairing
overflows.

---

## 4. `h1` centred; everything else left

`h1` centres and takes `--t-2xl`. `h2`, `h3` and body stay left — a centred
`h2` would make the page a poster.

**UI-PHASE.md §1.6 is updated** to record the exception rather than left
contradicting the stylesheet, and §1.5's type scale is updated to the 17px
base with the reason.

---

## 5. Corner graphics — kept off text by position, not by opacity

Ball bottom-left, batsman bottom-right, `--chrome` via `currentColor`,
`pointer-events: none`, `aria-hidden`.

The hard constraint was that they must not sit behind body text. Solving that
with opacity would mean measuring a composite and hoping. **It is solved with
position instead:** `main` is capped at `--column` and centred, so the
graphics render only above `min-width: 1280px` (720px column + 2×280px
graphic) and are `display: none` below. No contrast pair changes at all, which
the contrast suite then confirms rather than approximates.

---

## 6. Active nav

Ink text, 2px `--flag` underline, measured as a **graphical object** against
WCAG 1.4.11's 3:1 rather than as text: **5.83:1** on chrome, **6.47:1** on
paper. `aria-current="page"` carries the state for screen readers, and the
stylesheet hangs the underline off that same attribute so the two cannot
drift.

`isCurrent` is pure and tested: `/match/8429` lights Matches, `/player/12`
lights Players, the landing page lights nothing, and exactly one item is
current on every real route.

---

## 7. The control audit — the deliverable

Five controls, no two alike:

| | padding | radius | font-size | height | border |
|---|---|---|---|---|---|
| `.skip-link` | **`8px 12px` raw** | none | inherit | — | 1px `--ink` |
| `.live-slot` | tokens | **999px** | `--t-xs` | — | 1px `--rule` |
| `.ask-input` | tokens | **3px** | **`--t-s`** | — | 1px `--rule` |
| `.player-search input` | tokens | **3px** | **`--t-s`** | — | 1px `--rule` |
| `.ask-button` | **`8px 16px` raw** | **3px** | **`--t-xs`** | — | 1px `--ink` |

Four defects:

1. **Two controls carried raw px padding.** Step 2's migration walked past
   them because `8px` and `16px` are *on* the 4px scale — it only snapped the
   strays. On-scale and untokenised is a different defect from off-scale, and
   the earlier pass was not looking for it.
2. **No control set a height anywhere**, so height was implied by padding plus
   line-height — which is how the Ask form ended up with a button visibly
   shorter than the input beside it, purely because one used `--t-xs` and the
   other `--t-s`. Nobody chose that.
3. Three radii in use for controls (999px, 3px, none) with no scale.
4. Border colour doing emphasis work inconsistently.

**Fixed** with `--control-h: 40px`, `--control-radius: 4px`,
`--control-pad-x`, applied to every control; buttons and inputs share
`--t-s`. The button keeps the heavier `--ink` border — it is the one control
that asserts itself — and the pill radius stays only on `.live-slot`, which is
a status marker rather than a control.

---

## 8. Hover, and what it must never be

Colour and background changes only — no transform, no shadow change, so there
is no motion for `prefers-reduced-motion` to suppress.

**`:focus-visible` is untouched** (`2px solid var(--bat)`, offset 2) and is
never merged into a hover rule. Hover is a mouse affordance; collapsing the
two leaves a keyboard user with nothing. Re-measured on the new chrome: the
focus ring is **4.90:1** there, well past the 3:1 a graphical object needs.

---

## The header had never fitted at 340px

The 340px gate was run with a real browser, and it failed on **every route**.

The cause was not the type scale. The header row was **415px wide at a 340px
viewport** — wordmark, live slot and a four-item nav on one unwrapping line.

Step 6 made it worse but did not cause it. Measured by overriding the
stylesheet back to the pre-step-6 scale in the live page:

```
current (17px base, --t-l wordmark): 415
with the pre-step-6 scale          : 373
parts at the old scale: wordmark 127, nav 214, inner 340
```

**373px at the old scale.** 127 + 214 already exceeded 340 before any of this.
UI-PHASE §1.6 says mobile-first and names 340px explicitly, and the header had
never fitted there — on any page, since the shell was built.

Nothing caught it because nothing had ever laid the page out at that width.
The suite asserts contrast and content; jsdom does no layout; and a build that
renders HTML says nothing about where boxes land. The fix is
`flex-wrap: wrap` plus a nav that takes its own row below 560px.

### Then two more, both mine

**A `position: relative` I added broke `/accuracy` by 21px.** The corner
graphics needed content to sit above them, and the first version did that with
`position: relative; z-index: 1` on `main`. That made `main` the containing
block for every absolutely positioned descendant — including the 1px
`.visually-hidden` spans inside the horizontally scrolled decile table. Their
offsets resolved against `main` and extended its scroll area to 361px, while
**every direct child of `main` measured a well-behaved 308**. Fixed by giving
the graphics `z-index: -1` instead, so nobody else needs a stacking context.

**Then the same spans escaped their scroll container.** A scroll container only
clips an absolutely positioned descendant if it is that descendant's
containing block, and the scrollers were unpositioned — so the spans'
containing block was the viewport and the document grew by 21px regardless.
Fixed by pairing `overflow-x: auto` with `position: relative` on every
scroller.

**Final state: zero horizontal overflow on all eight routes at both 340px and
1140px.**

---

## Verification

| | |
|---|---|
| Contrast | 249 web tests, including the inverted chrome assertion over all five text tokens on all four surfaces |
| Texture ladder | hatch↔hollow **1.46–1.50**, unmoved — hues did not change, confirmed rather than assumed |
| Overflow | **0 of 8 routes** overflow at 340px or 1140px, measured in a real browser |
| Focus ring | `--bat` on the new chrome **4.90:1** against a 3:1 floor |
| Reduced motion | no new motion introduced; the LIVE dot's `prefers-reduced-motion` rule is unchanged |
| Build output check | **still fires** — 3 assertions red on a placeholder build, and its marker self-check passes |

### On perishability

`check-build-output.mjs` went vacuous last phase when a capital letter moved,
so it was re-proven here rather than assumed: it was run against a build with
unreachable credentials and confirmed to go red on all three assertions,
*and* its marker self-check was confirmed still to pass. **Proven-to-fire is a
property of a moment, not of a file.**

A browser was needed for the 340px gate and none was installed.
`playwright-core` was added with `--no-save` and driven against the system
Edge binary, so `package.json` and the lockfile are untouched — confirmed with
`git diff`.
