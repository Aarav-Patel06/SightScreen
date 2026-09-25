# UI Phase 2, Step 3 — identity

The legacy stylesheet block, the two logo forms, the favicons, and the header.

---

## 1. The legacy block was not unreachable, and deleting it would have broken things

Step 2's write-up called it "110 unreachable lines". **That was wrong**, and the
way it was wrong is the more useful part: the check behind the claim looked at
`font-size` only, found every one shadowed by a `.theme-paper` rule, and
generalised to the whole block.

A cascade analysis of all 45 declarations — every selector and property in the
block, against every `.theme-paper`-scoped rule — found the block was carrying
**live layout**:

| still live | why it survived |
|---|---|
| `* { box-sizing: border-box }` | never redeclared |
| `body { margin: 0 }` | `.theme-paper` sets background/colour/font, not margin |
| `main { margin: 0 auto }` | the theme overrides `max-width` and `padding` only — this is what centres every page |
| `.row` display/align/justify/gap | there is no `.theme-paper .row` rule at all |
| `.wp-bar` position/overflow/margin | the theme sets background, radius and height only |
| `.strip` display/flex-wrap, `.strip .cell` padding | the theme sets gap and margin only |
| `.grid` width/border-collapse/font-size/margin, `th`/`td` padding | there is no `.theme-paper .grid` rule |

It was also carrying **a live accessibility failure**. `.grid tr.thin td` took
its colour from the *dark* palette's `--muted` (`#9aa3ab`), which is **2.35:1
on cream**, and `/accuracy` applies `thin` to every decile under 30 matches.
The page has been shipping that.

### What was actually done

Not a wholesale delete. The genuinely dead parts went — the dark `:root`
palette, and `.chip`/`.chip-low`, which the phase mark replaced (0 uses in the
codebase, confirmed). The live parts moved into the theme with tokens instead
of the dark variables they were quietly still reading.

**Verification: the rendered HTML is byte-identical** across all seven
prerendered pages after normalising the build ID and asset hashes —
`matches.html` at 12,302,565 characters included. That was expected, since
class names did not change; the real change is in the CSS, and it is
deliberate:

| | before | after |
|---|---|---|
| `.grid tr.thin td` | `--muted`, **2.35:1** | `--ink-soft`, **5.58:1** |
| `.grid` / `.grid th` font-size | 13px / 12px | `--t-xs` (12.8px) |
| `.grid th` / `td` padding | 4px 6px 4px 0 / 3px 6px 3px 0 | 4px 8px 4px 0 |
| `.wp-bar` margin | 10px 0 6px | 8px 0 8px |
| `.row` gap, `.strip .cell` padding, `.grid` margin | 12px / 4px 8px / 8px 0 | **unchanged** |

Three of those are the step-2 spacing snap arriving late; one is the contrast
fix.

### Why it matters beyond this block

The mistake was a **sampled check generalised to a population**. One property
was tested across all selectors, and the conclusion was stated about all
properties. Nothing about the method was wrong — it was the scope of the claim.
That is the same shape as the safeguard audit's core finding, one level down.

---

## 2. The reference image was here all along

`web/public/logo.png` is the artwork: a teal batsman, a teal bar chart, a
crimson ball and a teal ground arc inside a tan circle, with cream separations.

Five sessions recorded `--bat` and `--bowl` as "PROVISIONAL: DERIVED, not
sampled — the reference image did not reach the session". It had. Sampling it:

| sampled | nearest token | ours |
|---|---|---|
| **#366C73** teal | `--bat` | #1D727C |
| **#98374B** ball | `--flag` | #A8324A |
| **#C6B592** tan | `--chrome` | #D5C3A5 |
| **#F7F6E9** cream | `--paper` | #F9F5EA |

The brief's §3.1 values (`--teal #42707A`, `--crimson #943B4B`) are close to
the sampled ones, which is presumably where they came from.

**The sampled colours clear our floors with more headroom than the derived
ones:** teal 5.43:1 on paper and 5.10:1 at the wash foot, against our 5.14 and
4.83; ball 6.46/6.07 against 5.99/5.63.

Not adopted in this step. Changing `--bat` and `--flag` re-touches every page,
the texture ladder and step 2's committed measurements, which is a palette
decision rather than an identity one. **Recorded as an open item with the
measurements already done**, so the change is a two-line edit and a re-run of
the token tests whenever it is wanted.

---

## 3. The marks

### The simplified mark is the ball, and it is drawn rather than scaled

The full mark is four shapes inside a disc — roughly 8px per shape at a 32px
favicon and under 4px at 16px. Scaling it produces a smudge with a red dot in
it.

The ball, not an "S": at 16px all that survives is an outline and one interior
line, and a red disc with a seam is a cricket ball to anyone who has seen one.
A two-colour "S" at that size is a smudge with a kink.

### The seam position was measured, not chosen

Four variants rendered at 16px and compared at true size:

| variant | at 16px |
|---|---|
| near-vertical seam at the edge (first attempt) | reads as a **nick out of the disc** — the seam vanishes, leaving a plain red dot |
| centred vertical seam | reads as the ball being **split in half** |
| tilted arc, offset 0.25, width 3.0 | **reads as a seam** — ball mass on both sides |
| tilted arc, offset 0.45, width 3.4 | also reads; seam closer to the edge |

The third was taken. The tilt is what makes it work: it keeps ball on both
sides of the line and crosses enough pixels to survive antialiasing.

### Does it read? Honestly:

- **32px — yes, unambiguously.** A cricket ball with a seam.
- **16px — yes, but not unmistakably.** The diagonal survives and is visible on
  both chrome and paper, so it reads as a ball if you know the product. In
  isolation it is a red disc with a light diagonal. That is a real improvement
  on the first attempt, which was a plain dot, and it is about the ceiling for
  16 square pixels.

Colours are sampled from the artwork (`#98374B`, `#F7F6E9`), **not** taken from
the CSS tokens: a brand mark should match the thing it is derived from rather
than drift with the interface palette. A test asserts that.

### The file convention does not do what the brief assumed

`app/icon.svg`, `app/icon.png` (32), `app/apple-icon.png` (180) all build. But
with both `icon.svg` and `icon.png` present **Next emits exactly one
`<link rel="icon">`, and picks the PNG** — verified by removing the PNG and
rebuilding, at which point the SVG link appears.

So "SVG for modern browsers, PNG as fallback" had silently become "PNG only,
plus an SVG nothing references". Both are now declared in
`app/layout.tsx`'s `metadata.icons`, SVG first. Emitted:

```html
<link rel="icon" href="/icon.svg" type="image/svg+xml"/>
<link rel="icon" href="/icon.png" type="image/png" sizes="32x32"/>
<link rel="apple-touch-icon" href="/apple-icon.png" sizes="180x180"/>
```

---

## 4. The header, and the wordmark split

Mark at 22px plus the wordmark, up from `--t-m` (20px) to `--t-l` (25px).

**The wordmark is `--ink`, and the split moves to the landing page.** Measured
on `--chrome`: teal **3.25:1**, crimson **3.78:1**. Both fail 4.5:1.

The third option — a paper-coloured band behind the wordmark inside the chrome
header — would make the split legal and was rejected. A cream rectangle
floating in a tan bar reads as a badge, and inventing a container to make a
colour legal is how decoration gets in. On `--paper` both halves clear
comfortably (5.14 and 5.99), which is where the split belongs.

The landing page has no wordmark element today, only the word in prose, so
this is new work in step 5 rather than a retrofit. Recorded in UI-PHASE-2 §4.2.

---

## 5. Two guards this step produced

### The mark is duplicated, and the duplication is asserted

`components/ball-mark.tsx` repeats the seam geometry from `app/icon.svg`,
because importing SVG as a component needs `@svgr/webpack` for one
600-character string. Duplication is fine; *silent* duplication is not, so
`components/ball-mark.test.ts` reads both files and compares the path and the
colours.

### The vitest include pattern failed again — so the class is closed

`components/ball-mark.test.ts` was the first test ever written under
`components/`, and `npm test` reported **"no test files found"**. The include
list did not cover that directory. This is the second instance of the same bug
(the first was `app/api/agent/route.test.ts`, matched only as `.tsx`), and both
were caught by a human noticing a test count.

A glob list that enumerates directories fails silently every time a new one
appears, **and it fails by passing**. Adding `components/**` fixes the
instance; `lib/test-discovery.test.ts` fixes the class. It reads the include
globs out of `vitest.config.ts`, walks the package for `*.test.ts(x)`, and
fails if any file is not matched by any glob — with the orphans named.

Proven to fire: removing `components/**` from the include list produces

```
these exist but `npm test` never runs them - add a glob to
vitest.config.ts's include: components/ball-mark.test.ts
```

It also guards itself: one test asserts the glob list and the file list are
both non-empty, because an empty either side makes the main assertion pass
vacuously.

---

## 6. Two properties recorded, not fixed

### Border widths are an uncovered surface

Step 2's migration turned `border-left: 3px solid var(--bat)` into a 4px
border, because `border-left` ends in `left` and the regex read it as a
position. It was caught by reading the diff — **nothing in this project
asserts on a border width**, so no test could have caught it.

That is a category, not an incident. `tokens.test.ts` covers colours and now
the spacing scale; there is no equivalent for borders, radii or stroke widths.
**Step 4's elevation pass touches borders on every page**, which is the moment
this matters, so it is written down before rather than after.

### CI tests pushes, not commits

A new corner of standing rule 17. A `push` event runs the workflow once, on the
head commit. Pushing three commits together tests the third; the first two are
never built, never tested, and `git bisect` across them would be bisecting
states CI has never seen.

Observed directly: `e03f290` (the safeguard audit) has no run of its own — it
went up with `c907767a` and only the tip was tested. The tree state is covered,
because the tip is a descendant, so nothing is untested *now*.

**Recorded as a known property, not worth fixing.** Testing every commit of
every push costs CI minutes proportional to commit count for a benefit that
only appears during a bisect, and the mitigation is free: bisect on pushes, or
re-run CI on the specific commit if a bisect ever lands inside one.
