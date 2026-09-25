# SightScreen — UI Mini-Phase

**Inserts between Phase 3 and Phase 4.** Does not renumber anything in SPEC.md.
**Cost: zero.** No model calls, no new infrastructure, no Railway or Vercel spend. Entirely local until you choose to deploy.
**Status:** plan. Read alongside SPEC.md §12.1 and §12.2, which still govern.

---

## 0. Why this phase exists

Four pages exist and work: `/match/[id]`, `/accuracy`, `/model-card`, `/ask`. There is no way to reach any of them without typing a URL, no shared visual language, and no front door. A recruiter given the link currently lands nowhere.

This phase builds the shell, the design language, and the missing navigational surfaces. It adds **one** new data page (`/player/[id]`) that is buildable from the corpus today.

### 0.1 Non-goals

- **No `/upcoming` page.** No fixtures are ingested and no pre-match model exists. A page that looks like predictions but isn't is worse than no page. It gets a one-line mention on the landing page under what's not built yet, and nothing more.
- **No form curves or next-innings predictions on the player page.** `player_state` is empty until Phase 5. The player page shows the descriptive record only, labelled as such — the same distinction `get_player_form` already enforces in the agent.
- **No WPA leaderboard or batter/bowler cards on the match page.** §12.1 items 3, 4 and 5 are Phase 5 and partially provider-blocked.
- **No first-innings display.** The model is second-innings only until Phase 4.
- **No auth, accounts, or user state** beyond `/ask`'s existing password gate.

### 0.2 The honest-gap principle

Where a surface is specced but not buildable, say so in the interface in plain language, in one sentence, where the thing would have been. Do not hide it and do not fill it with a placeholder that looks functional.

This is not an apology — it is the same stance as the accuracy page publishing its own failing deciles, and it is the most distinctive thing about this product. An interface that tells you what it doesn't have is consistent with a model that tells you what it doesn't know.

---

## 1. Design direction

### 1.1 The subject, honestly

Cricket is a **sequence of discrete events**. A T20 innings is exactly 120 legal balls. Nothing else in mainstream sport is so cleanly quantised, and no existing cricket app uses that structurally — they all render commentary as a scrolling list.

The second fact: this product's entire differentiator is **saying what it doesn't know**. Both of those need to be visible in the design, not just in the copy.

### 1.2 The signature element: the ball strip

One memorable thing, per the restraint principle. It is this.

An innings renders as a horizontal strip of discrete marks, one per legal ball, left to right. Each mark encodes:

- **Height or weight** — the absolute win-probability swing that ball caused
- **Direction** — above the axis if it helped the batting side, below if it helped the bowling side
- **Fill** — solid for a scoring shot, hollow for a dot, a distinct notch for a wicket

Read at a glance, the strip *is* the match narrative: a flat run of dots, a spike where a wicket fell, a widening band in the death overs. It replaces the generic line chart as the hero, and it is the object the whole visual language is built around. Hovering a mark reveals that delivery; the same component renders at four sizes — hero, match page, player page row, and a 120px sparkline in a list.

Build this component first. Everything else is arrangement around it.

### 1.3 Uncertainty is texture, not colour

The second distinctive rule, and it directly implements §12.2.

Colour carries **who** (which team, which side of a comparison). Texture carries **how confident**:

| State | Treatment |
|---|---|
| Well-evidenced | Solid fill |
| Wide interval / low confidence | Diagonal hatch at 8px, same hue |
| Sample below threshold | Hollow — 1px stroke, no fill |
| Not measured / unavailable | Empty slot with a hairline baseline and a one-line reason |

The payoff: a pre-toss prediction and a 19th-over prediction are visibly different objects even at the same percentage, which is exactly what §12.2 demands and what "62% in the same font at ball one and ball 119" was written against. It also degrades correctly in greyscale and for colour-blind readers, because the confidence signal is not a hue.

### 1.4 Palette

Light base. The credibility surfaces — accuracy page, model card — are dense text and tables read on a laptop, and light serves them better. Dark is also the default for every live-sports product, so light is the less-templated choice here.

| Token | Hex | Role |
|---|---|---|
| `--paper` | `#FBFBF9` | Page base. Barely warm, not cream. |
| `--ink` | `#16202A` | Text and rules. A navy-black, not a tinted grey. |
| `--ink-soft` | `#5A6A78` | Secondary text, axis labels |
| `--rule` | `#DFE3E0` | Hairlines, table borders, strip baseline |
| `--bat` | `#1F6FB2` | Batting side. Blue. |
| `--bowl` | `#C4741A` | Bowling side. Amber. |
| `--flag` | `#A8324A` | Wickets, failed calibration bands, cap-reached states |

Blue/amber rather than red/green: distinguishable under all three common colour-vision deficiencies, and neither reads as good/bad, which matters when the two sides are morally neutral.

`--flag` is used sparingly and only where something genuinely needs attention. It is not a decorative accent.

Dark mode: ship a `prefers-color-scheme` variant only if it costs little. It is not required and should not consume a session.

### 1.5 Typography

One family, two roles, both grounded in the subject.

**IBM Plex Sans** for prose, UI, and body. Real tabular figures, a slightly technical character, and not Inter.

**IBM Plex Sans Condensed** for scoreboard figures — scores, win percentages, over counts, the large numbers in stat blocks. Cricket scoreboards are condensed for the same reason: they hold a lot of numbers in a fixed width. This is a subject-grounded choice, not a decorative one.

Both from `next/font/google`, self-hosted, subset to Latin.

**Every number that can change while you watch it must be `font-variant-numeric: tabular-nums`.** A win probability that jitters horizontally as digits change width reads as broken. This applies to the live percentage, the score, the clock, and every table column.

Type scale, 1.25 ratio from a **17px** base (16px until 2026-09-25): 13.6 / 17 / 21.3 / 26.6 / 33.2 / 41.5 / 51.9. The base moved after measuring the 340px overflow risk — at 18px the hero teams line leaves only 12px of the 308px available, and at 17px it leaves 28. Scoreboard figures use the top three at condensed weight 600, tracked tight (-0.02em). Prose stays at the base size with 1.6 line-height, measure capped at 68 characters.

Avoid: all-caps labels, single-word colour accents in headings, eyebrow labels above every section, meta strings joined with middle dots, and `→` appended to link text.

### 1.6 Layout

Left-aligned throughout, **with one exception recorded 2026-09-25: `h1` is centred.** Page titles centre and take `--t-2xl`; section headings (`h2`, `h3`) and all body text stay left. The exception is written here rather than left as a stylesheet rule contradicting this paragraph — a centred `h2` would make the page a poster, which is why the exception stops at the page title.

A single 1140px maximum with a 720px reading column for prose surfaces (`/model-card`, the methodology notes). Mobile-first — §12.2 says most cricket viewing is second-screen on a phone, and the ball strip is designed to work at 340px.

Structural devices earn their place: a rule under a section heading only where it separates genuinely different content, no cards around things that are not discrete objects. The reliability table is a table, not eleven cards.

### 1.7 Motion

One orchestrated moment: on the landing page, the hero ball strip draws itself left to right once on load, roughly 1.2 seconds, then stops. That is the page's single piece of non-user-triggered motion.

Everything else responds to action only — a tooltip appearing, a row expanding, the live percentage transitioning between values when a new prediction arrives. No fade-and-slide-up on scroll, no hover lift on cards.

`prefers-reduced-motion: reduce` disables the hero draw and renders the strip complete.

---

## 2. Technology

Additions to `web/`, all small:

| Library | Purpose | Notes |
|---|---|---|
| Tailwind CSS | Already present via Next 15 | Define the palette as CSS custom properties in `globals.css`, expose via `@theme` |
| `lucide-react` | Icons | Already in the artifact-library set; use sparingly |
| Recharts | Already used for the WP curve | Keep. The ball strip is hand-rolled SVG, not Recharts. |
| `next/font/google` | IBM Plex Sans + Condensed | Self-hosted, no layout shift |

**shadcn/ui: selectively.** Take `tooltip`, `tabs`, `dialog`, and `table` for their accessibility behaviour — Radix handles keyboard and ARIA correctly and hand-rolling that is a waste of a session. Do **not** take `card`, and do not let the default shadcn look set the visual tone. Restyle every component taken to the tokens above before using it. If the result looks like a shadcn demo, the tokens have not been applied.

No animation library. No charting library beyond Recharts. No state manager — server components plus the existing Realtime subscription cover everything here.

---

## 3. Routes

| Route | Status | This phase |
|---|---|---|
| `/` | Does not exist | **Build** |
| `/matches` | Does not exist | **Build** — index of matches with predictions |
| `/match/[id]` | Exists, works | **Retrofit** to the design system |
| `/player/[id]` | Does not exist | **Build** — descriptive record only |
| `/players` | Does not exist | **Build** — searchable index, needed to reach player pages |
| `/accuracy` | Exists, works | **Retrofit** |
| `/model-card` | Exists, works | **Retrofit** |
| `/ask` | Exists, gated | **Retrofit** the shell around it; do not touch the gate logic |
| `/upcoming` | Not buildable | **Not built.** One honest line on the landing page. |

### 3.1 Shared shell

A single `app/layout.tsx` header on every page: wordmark left, navigation right (Matches, Players, Ask, Accuracy), and nothing else. No footer beyond a single line with the GitHub link and the data-source credit to Cricsheet, which the licence requires.

The header carries **one live element**: if a match is currently being predicted, a small ball-strip sparkline with the two team short names, linking to it. If nothing is live, the slot is empty — not a "no live matches" placeholder.

---

## 4. Page specifications

### 4.1 `/` — landing

Above the fold, in order:

1. **Hero.** The ball strip at full width, rendering a real innings — the live match if one exists, otherwise a replayed match that ended well (a close chase). Above it, the match identity and final or current state in condensed figures. Below it, one sentence of plain prose saying what you are looking at: that every mark is one delivery, and its height is how much that ball changed who was going to win. This is the product explained in a single object plus one sentence.

2. **What this is.** Two short paragraphs, 68-character measure. What it predicts, and the one thing that makes it different — that it publishes its own accuracy including where it fails. Link to `/accuracy` and `/model-card` inline, not as buttons.

3. **Three entry points.** Matches, Players, Ask — each a heading, one line of description, and a live figure that proves it is real (matches with predictions; players in the corpus; deliveries queryable). Real numbers from the database, not hardcoded.

4. **What isn't built yet.** A short honest list: first-innings predictions, player form estimates, upcoming-match predictions, live player impact. One line each saying why, in plain language. This is a feature, not a disclaimer — it is the landing-page expression of the same stance as the accuracy page.

No testimonials, no feature grid, no gradient washes, no "Get started" button.

### 4.2 `/matches` — index

A table, not cards. Columns: date, competition, teams, result, and a 120px ball-strip sparkline. Sorted most recent first, paginated or virtualised at 50.

Filters: competition, format, and a toggle for "has predictions" — since only a subset has been replayed. That toggle must be honest: a match without predictions shows a scorecard, not an empty chart.

### 4.3 `/match/[id]` — retrofit

Keep every behaviour that works: the Realtime subscription, the 30s resync, the prediction history strip, the latency line. Restyle only, plus:

- The WP curve gains the ball strip beneath it, sharing an x-axis
- The latency indicator keeps its existing honest copy — "updates every 15s · provider lag not published" — per the §4.3 clarification. Do not let a redesign reintroduce a false-precision figure.
- Phase confidence renders as texture per §1.3, not as a chip with a colour
- The absent §12.1 items (batter card, bowler card, matchup, WPA leaderboard) get one honest line each, in place, saying they need Phase 5

### 4.4 `/players` and `/player/[id]` — new

`/players`: a search field and a table. Name, matches, and a sparkline of runs by year. 18,468 rows, so search is required and the table must be virtualised.

`/player/[id]`: **descriptive record only.** Career aggregates, splits by phase and by format, a by-venue table, and recent innings. Every cell carries its sample size, and cells below the threshold render hollow per §1.3.

At the top of the page, where a form curve would go, one sentence: that the system does not yet estimate current ability, only record what happened, and that the two are different questions. Link to the model card's section on it. This is the same refusal `get_player_form` makes, rendered in the interface — and it is the single clearest demonstration of the product's stance a visitor will see.

Handle missing attributes honestly: `batting_hand`, `bowling_style` and `dob` are populated for zero of 18,468 players. Omit those fields entirely rather than rendering empty rows.

### 4.5 `/accuracy` — retrofit

The content is right and should not change. Restyle only, with two additions:

- The backfilled/live distinction becomes structural, not just labelled: two visually separated sections with the live one first, since it is the honest column
- Reliability bands use texture per §1.3 — a band whose clustered interval excludes its predicted mean renders in `--flag`, the rest solid

### 4.6 `/model-card` — retrofit

Pure prose. 720px column, 68-character measure, generous line-height, no cards, no icons. The one page in the product that should look like a document rather than an interface.

### 4.7 `/ask` — retrofit

Restyle the shell, the password gate, and the message list. **Do not modify** the gate logic, the cookie handling, the cap enforcement, or the route handler — those are tested and proven, and a visual pass must not touch them.

Tool calls render as a collapsed line the visitor can expand — "queried 3.78M deliveries" opening to show the SQL. That transparency is the feature: it shows the answer came from the data, not from the model's memory.

---

## 5. Sessions

Each ends with something visible in a browser. Zero spend throughout.

### Session 1 — Tokens and the ball strip

- Palette and type as CSS custom properties in `globals.css`, exposed to Tailwind
- `next/font` set up for both faces
- The ball-strip component, hand-rolled SVG, at all four sizes
- The texture system as reusable SVG patterns — solid, hatch, hollow, empty
- A `/design` route rendering every token, type size, and strip variant on one page

**Acceptance:** `/design` renders. The strip renders a real innings from the database at 340px and 1140px without overflow. Hatch and hollow are distinguishable in greyscale and at 50% zoom.

### Session 2 — Shell and landing

- `app/layout.tsx` with the header, nav, and live slot
- `/` per §4.1, with real figures from the database
- The hero draw animation, and its `prefers-reduced-motion` path

**Acceptance:** Every existing page inherits the shell without breaking. The landing page's three figures come from queries, not constants. Keyboard tab order through the header is correct and focus is visible.

### Session 3 — Matches

- `/matches` with filters and sparklines
- `/match/[id]` retrofitted, Realtime behaviour unchanged

**Acceptance:** A replayed match still renders its full curve and still updates live. The latency copy is unchanged from the §4.3 wording. A match without predictions renders its scorecard rather than an empty chart.

### Session 4 — Players

- `/players` with search and virtualisation
- `/player/[id]` per §4.4

**Acceptance:** Search returns results for both "Kohli" and "Virat Kohli" — reuse the resolver's canonical-form generation rather than writing a second matcher. A player with fewer than 20 innings renders hollow cells rather than misleadingly precise numbers. No empty rows for the unpopulated attributes.

### Session 5 — Credibility surfaces

- `/accuracy` and `/model-card` retrofitted
- `/ask` shell retrofitted, logic untouched
- Full accessibility pass: focus states, colour contrast at AA, reduced motion, screen-reader labels on the strip

**Acceptance:** `/ask`'s gate and cap tests still pass unmodified — that is the proof the visual pass did not touch logic. Contrast checked, not assumed. The ball strip has a text alternative describing the innings.

---

## 6. Rules carried forward

These are not new. They are SPEC.md §12.2 plus the Phase 2 clarification, restated because a visual pass is exactly when they get quietly dropped.

- **Every number carries an uncertainty or a sample size.** No bare point estimates anywhere in the interface.
- **Uncertainty must be specific to the number shown.** An interval derived from an aggregate is decoration that looks principled. Where no per-prediction interval exists, say so and link to the real evidence.
- **Small samples render hollow**, not as precise numbers in a lighter grey.
- **Confidence is visible by phase.** A pre-toss prediction must not look like a 19th-over one.
- **Mobile-first.** Test at 340px before 1140px.
- **Honest gaps.** Where something isn't built, one plain sentence in its place.

Added by session 1, because they were measured rather than assumed:

- **Never place hatch and hollow adjacent without a third distinguishing cue.** Their greyscale separation is 1.56:1, the weakest pair in the system, and the margin is carried almost entirely by hollow's outline — which is the first thing anyone removes when tidying a component. See `docs/ui-session1.md`.
- **Fills use `--bat`/`--bowl`; text uses `--bat-text`/`--bowl-text`.** `--bowl` is 3.46:1 against `--paper`: a correct chart fill and a WCAG AA failure for words. `lib/tokens.test.ts` parses the stylesheet and holds each class to its floor.
- **Colour is never the only cue separating the two sides.** The two text tokens are matched at ~5.1:1 so neither side reads lighter in prose, which leaves them near-identical in greyscale.
- **Shared constants live in non-client modules.** Standing rule 15 — a value imported into a Server Component from a `"use client"` file arrives as an opaque reference, and comparing it returns `false` rather than throwing.

---

## 7. Open decisions

| Decision | Notes |
|---|---|
| Dark mode | Ship only if cheap. Not worth a session. |
| Deploy timing | This phase is local-only. Deploying the new pages needs no new infrastructure — Vercel already serves `web/`. The Railway replica is only needed for `/ask`'s data tools. |
| `/players` data source | 18,468 rows with aggregates. Decide whether to precompute a summary table or query live, and measure before choosing. |
| Landing hero match | Which replayed match. Pick one with a genuinely close finish — the strip is more legible when the swings are large. |
