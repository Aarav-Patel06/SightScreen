# SightScreen — UI Mini-Phase 2

**Follows UI-PHASE.md. Inserts before Phase 4.** Does not renumber anything in SPEC.md.
**Cost: $0.** All local. No model calls, no provider calls except three optional recovery requests in Step 1.
**Status:** plan.

---

## 0. Read this first

Two rules carry from the previous phase and are the ones most likely to be dropped in a visual pass:

- **SPEC.md §12.2 still governs.** Every number carries an uncertainty or a sample size. Uncertainty must be specific to the number shown, never derived from an aggregate.
- **The unmodified-tests criterion.** `/ask`'s gate, cookie, HMAC and cap tests must not be edited. If a visual change requires editing one, it isn't a visual change.

### 0.1 Three corrections to the brief

**"Live Brier Score: 0.1020" is wrong.** 0.1020 is the backfilled figure over 100 replayed matches. The live population is `n=0`, unscored. Labelling it live is the exact conflation `/accuracy`'s two-section split exists to prevent. The landing scorecard must say *backfilled*, show its clustered CI, and state that the live population has no score yet. That sentence is more interesting than a number anyway — it's the only site on the internet that will tell you its headline figure isn't the honest one.

**Teal on tan fails.** Measured: `#42707a` on `#d0c3a9` is 3.15:1, below the 4.5:1 `tokens.test.ts` enforces. Header navigation uses ink, not teal. Verified-good pairs: teal on cream 5.02:1, crimson on cream 6.44:1, ink on tan 8.6:1.

**The logo needs two forms.** The full mark is six shapes in a circle; at 32px it's noise and at 16px it's a smudge. Full mark on the landing page, simplified mark for favicon and header.

---

## 1. Build order

Data before tokens, tokens before components, components before pages. Each step's output is the next step's input, so the order is not arbitrary.

| Step | Why here |
|---|---|
| 1. Data — Full Member priority and the three defects | Every visual decision below depends on what matches exist. Building a landing page around big matches before those matches have predictions means building against a fixture twice. |
| 2. Tokens — palette, elevation, gradient, type scale | Every component reads these. Changing them after components are styled means touching every component again. |
| 3. Identity — logo, favicon, header | Small, isolated, unblocks the header work in step 4. |
| 4. Elevation pass across existing pages | Applies step 2 to what already exists, before anything new is built on top. |
| 5. Landing page — previews, LIVE NOW, scorecard | Consumes steps 1–4. Built last because it's the only page that summarises all the others. |
| 6. Verification | Contrast, greyscale, focus order, reduced motion, all routes. |

---

## 2. Step 1 — Data

### 2.1 Full Member priority

Add `full_member BOOLEAN NOT NULL DEFAULT FALSE` to `teams`. Set it for the twelve ICC Full Members: Australia, England, India, New Zealand, Pakistan, South Africa, Sri Lanka, West Indies, Bangladesh, Zimbabwe, Afghanistan, Ireland.

**Name-matching trap:** "India A", "India U19", "India Women" and similar share the prefix. Check how the corpus actually spells them before writing the match condition, and assert the flagged count is exactly 12.

Then replay Full Member vs Full Member T20Is and ODIs to generate predictions. **Report the candidate count before running anything.**

**Hard constraint, asserted in code rather than observed:** test split only, `start_time >= 2025-01-01`. These rows land in the same `predictions` table `/accuracy` reads. Anything earlier would have the accuracy page measuring the model on data it trained on — the failure the whole evaluation apparatus exists to prevent. The replay must refuse a match outside the window.

**Report `/accuracy`'s backfilled figures before and after.** Adding ~50 matches to a 100-match population moves them. That movement should be visible and explained, not discovered later.

### 2.2 Ordering, not filtering

The brief says the twelve are the priority and it doesn't matter whether it's Ireland or India. So: order by recency, with Full Member fixtures surfaced first. `/matches` keeps everything and gains a Full Member filter. The landing hero picks the most recent Full Member match, live if one exists.

No tiering between the eight established sides and the four newer ones. If the candidate list turns out to be dominated by Zimbabwe and Ireland fixtures, revisit — but don't build a tier speculatively.

### 2.3 Three defects

**"Unknown v Unknown" — ids 1, 2, 3.** `team_a`, `team_b` and `venue_id` are permanently NULL from the backfill incident. Provider UUIDs survive in `external_ids`. Either recover them with three CricketData calls or exclude them from the index. Recommend one; the matches are from September and the endpoint may no longer serve them.

**Four rows stuck at `status = 'live'`** with no result and no strip. These predate the Session 3 status fix. Backfill their status from the data — a match with a `winner` is complete.

**Two rows show a result but say "Not replayed."** Explain before fixing. A match with a recorded result should have been replayable, so either the replay skipped it for a reason worth knowing, or the index is reading the wrong signal.

### 2.4 The landing hero becomes a query

The fixture existed because the hero must never fail to render, and that reason still holds. So: query for the most recent Full Member match with predictions, and fall back to a committed fixture if the query fails or returns nothing. The fixture stays in the repo as the floor.

Test the fallback by pointing at an unreachable database, as Session 2 did.

---

## 3. Step 2 — Tokens

### 3.1 Palette

Base values from the brief, with roles assigned. Sample the reference and correct these if they differ.

| Token | Hex | Role | Measured |
|---|---|---|---|
| `--paper` | `#F7F5EB` | Page base (under the gradient) | — |
| `--paper-raised` | `#FDFCF6` | Panel fill, one step lighter than paper | — |
| `--chrome` | `#D0C3A9` | Header, footer, table header bands | ink on it: 8.6:1 |
| `--rule` | `#E2D9C6` | Hairlines, dividers | — |
| `--ink` | `#2A2419` | Body text, header nav | on paper: ~15:1 |
| `--ink-soft` | `#6B6152` | Secondary text, captions | verify ≥4.5:1 |
| `--teal` | `#42707A` | Batting side, primary emphasis, links | on paper: 5.02:1 |
| `--crimson` | `#943B4B` | Bowling side, wickets, failure states | on paper: 6.44:1 |

**Header navigation is `--ink`, not `--teal`.** Teal on chrome is 3.15:1 and fails. If you want teal in the header, it has to be on a paper-coloured band, not on chrome.

`tokens.test.ts` already parses `globals.css` at runtime and fails on unclassified tokens. Extend it rather than replacing it, and keep its both-directions property.

### 3.2 Colour for emphasis

The brief asks for colour instead of bold. Two rules so it doesn't become decoration:

- **Teal marks the subject** — a team name, a player name, a figure the sentence is about.
- **Crimson marks a failure or a loss** — a wicket, a miscalibrated band, a cap reached, a prediction that was wrong.

Nothing else gets colour. A third use makes the first two meaningless. Never colour a whole sentence, and never use colour as the only signal for something — it pairs with the texture system, it doesn't replace it.

### 3.3 Gradient

Very subtle, and one gradient only: a vertical wash on the page background from `--paper` to about 3% darker at the bottom, plus the faint diagonal pattern visible in the reference at very low opacity.

The test is that someone should not consciously notice it. A gradient that reads as a gradient is decoration, and the design skill flags gradient washes as a default tell. This one exists to stop a large cream field looking flat behind raised panels.

Must not interfere with panel shadows — check both together, not separately.

### 3.4 Elevation

One shadow scale, three levels, assigned by hierarchy rather than applied uniformly:

| Level | Use | Treatment |
|---|---|---|
| 0 | Prose, tables, anything that isn't a discrete object | No panel |
| 1 | Content sections, table containers | `--paper-raised`, 1px `--rule`, radius 10px, shadow `0 1px 2px rgba(42,36,25,.05)` |
| 2 | The hero, the landing scorecard | Same fill, radius 12px, shadow `0 2px 8px rgba(42,36,25,.07)` |

**Do not box everything.** Boxing every section into identical rounded cards with the same shadow is the templated default the design skill names explicitly. `/model-card` stays an unboxed document — it's the one page that should read as a document rather than an interface. Prose on the landing page stays unboxed. Box things that are discrete objects: the hero, the preview modules, the scorecard, table containers.

### 3.5 Typography and spacing

Audit before changing. Find every place a size, weight, line-height or spacing value is set outside the token scale and bring it in. Report what was off — that list is the deliverable, not just the fix.

The scale stays as established: 1.25 ratio from 16px, condensed at 600 for scoreboard figures, 1.6 line-height for prose, 68-character measure.

Spacing on a 4px base: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64. One vertical rhythm between sections, one internal padding for level-1 panels, one for level-2.

---

## 4. Step 3 — Identity

### 4.1 Two forms

| Form | Use | Format |
|---|---|---|
| Full mark | Landing page, README, social preview | The supplied artwork, exported to SVG if possible |
| Simplified mark | Favicon, header | The ball alone, or an "S" in the teal/crimson split |

The full mark is a batsman, a bar chart, a ball and a circle. At 32px that's illegible; at 16px it's a smudge. Design the simplified mark deliberately rather than scaling the full one down and hoping.

Favicon: `app/icon.svg` for modern browsers, `app/icon.png` at 32px as fallback, `app/apple-icon.png` at 180px. Next's file conventions handle the rest.

### 4.2 Header

Simplified mark plus the wordmark, larger than now. The wordmark keeps the two-colour split from the logo — "Sight" in teal, "Screen" in crimson — but **only if both clear 4.5:1 against `--chrome`**. Teal does not (3.15:1). So either the wordmark sits on a paper-coloured band, or it renders in ink with the split reserved for the landing page.

Measure before committing to the split. Do not ship it failing.

---

## 5. Step 4 — Elevation pass

Apply step 2 to the existing pages. No new functionality.

| Page | Treatment |
|---|---|
| `/matches` | Table in a level-1 panel. Filters above it, unboxed. |
| `/match/[id]` | Hero at level 2. Curve and strip share one level-1 panel. Realtime, resync, watermark and latency copy untouched. |
| `/players` | Search unboxed, table in level-1. |
| `/player/[id]` | Each stat group in level-1. Hollow cells unchanged. |
| `/accuracy` | Each measurement section in level-1. The live/backfilled structural distinction stays. |
| `/model-card` | **No panels.** Document. |
| `/ask` | Message list in level-1. Gate and cap logic untouched. |
| `/design` | Gains the elevation scale and the new palette. |

---

## 6. Step 5 — Landing page

Order, top to bottom:

**1. Hero.** Most recent Full Member match, or the live one. Level-2 panel. If live, a `LIVE NOW` tag — small, crimson dot, ink text, top-right of the panel. It should look like a fact rather than a badge: no pill background, no pulse animation beyond a slow dot if any. Respect `prefers-reduced-motion`.

The strip keeps its draw-once animation and its one-sentence explanation.

**2. What this is.** Two paragraphs, unboxed, 68-character measure. Teal on the subject nouns only.

**3. Scorecard.** Level-2 panel, and the most carefully worded thing on the page:

- Backfilled Brier with its match-clustered CI, labelled *backfilled*
- The count of miscalibrated deciles, in crimson
- One line stating the live population is not yet scored, with `n`
- Link to `/accuracy`

No number on this panel may be labelled "live" until the live population has one.

**4. Preview modules.** Three or four level-1 panels, each linking to a section:

| Module | Shows | Links to |
|---|---|---|
| Matches | Three most recent Full Member rows with sparklines | `/matches` |
| Players | Three or four top players by runs, with a figure | `/players` |
| Ask | One real example question and its cited answer, static | `/ask` |
| Accuracy | The reliability diagram thumbnail | `/accuracy` |

Each is a real preview rendered from real data, not an icon and a description. The Ask module's example is a committed static exchange, not a live call — no model spend on page load.

**5. Footer.** Short. GitHub link, Cricsheet credit as the licence requires, and the analytics-not-betting line. The long caveat about which database counted what belongs in a tooltip on the figure, not as running footer text.

---

## 7. Verification

Measured, not asserted. Same standard as the last phase.

- Contrast against the **served** stylesheet: all text ≥4.5:1, all fills ≥3.0:1
- **Re-measure the texture system.** Solid↔hollow, solid↔hatch, hatch↔hollow were 4.77 / 3.05 / 1.56 against the old palette. New hues change all three and hatch↔hollow was already weakest. If it drops, adjust hatch spacing or stroke weight — never the hue.
- Focus order walked across all nine routes; skip link first; no positive `tabindex`
- Reduced motion with the preference actually set, not by reading the media query
- All nine routes 200, at 340px and 1140px
- `/ask` logic files byte-identical, md5 recorded before and after
- Favicon renders at 16px and is recognisable

---

## 8. Open questions

| Question | Decide when |
|---|---|
| Recover ids 1–3 from CricketData, or exclude? | Step 1, after checking whether the endpoint still serves September matches |
| Full Member candidate count | Step 1, before replaying |
| Two tiers among the twelve | Only if the candidate list is dominated by the four newer sides |
| Logo as SVG | Step 3. If the artwork is raster only, ship PNG at multiple sizes and note it. |
| Wordmark colour split in the header | Step 3, after measuring against `--chrome` |
