# UI Phase 2, Step 2 — tokens

Palette, colour-for-emphasis, gradient, elevation, typography and spacing.
Everything below is measured against the served stylesheet rather than the
brief's table, because those two had drifted apart.

---

## 1. §3.1 measured — and the brief is wrong in four places

Contrast against `web/app/globals.css` as shipped.

| §3.1 claim | measured | verdict |
|---|---|---|
| teal on chrome 3.15:1, fails for text | **3.15** | confirmed |
| teal on paper 5.02:1 | **5.02** | confirmed |
| crimson on paper 6.44:1 | **6.43** | confirmed |
| ink on chrome 8.6:1 | **8.84** | understated |
| ink on paper ~15:1 | **14.08** | overstated |
| ink-soft "verify ≥4.5" | **5.56** | clears |

### The rule is broader than the brief states

§3.1 says "Header navigation is `--ink`, not `--teal`" as though teal were the
exception. It is not. On the served palette **only `--ink` clears 4.5:1 on
chrome at all**:

```
                paper  paper-raised   chrome  chrome-deep    rule
--ink           14.12        14.88      8.92         6.70   10.58
--ink-soft       5.58         5.87      3.52*        2.64*    4.18*
--bat-text       5.14         5.41      3.25*        2.44*    3.85*
--bowl-text      5.11         5.38      3.23*        2.42*    3.83*
--flag           5.99         6.31      3.78*        2.84*    4.48*
                                        (* below 4.5:1)
```

`--flag` at 3.78 is the one that matters in practice: §3.2 assigns crimson to
wickets and failure states, and table header bands are chrome. `--chrome-deep`
is worse throughout (2.42–2.84).

`lib/tokens.ts` already encoded this as `CHROME_SAFE_TEXT = ["--ink"]`, so the
implementation was right and the brief's framing was too narrow.

Also recorded: **`--flag` on `--rule` is 4.48:1** — a near-miss that would pass
a 4.5 check only through rounding. Nothing sets text on `--rule` today; noted
so that nothing starts to.

### Two data colours or three

§3.1 lists `--teal` and `--crimson`. The implementation carries three —
`--bat`, `--bowl`, `--flag` — derived when the reference image never arrived.
Both palettes clear every contrast and texture floor, so this was a design
call, not a measurement:

**Three stands.** `--bat` and `--bowl` are a semantic pair that must be
distinguishable from each other *and* from the failure colour. Collapsing
`--bowl` into crimson would make a wicket and the bowling side the same hue,
which §3.2's own rule forbids — crimson marks a failure, and the bowling team
is not one.

---

## 2. Texture re-measured — no adjustment needed

| | vs paper | solid/hollow | solid/hatch | hatch/hollow |
|---|---|---|---|---|
| `--bat` #1D727C | 5.14 | 4.48 | 3.07 | **1.46** |
| `--bowl` #935B2B | 5.11 | 4.45 | 3.06 | **1.46** |
| `--flag` #A8324A | 5.99 | 5.19 | 3.49 | **1.49** |
| brief `--teal` #42707A | 5.02 | 4.38 | 3.01 | **1.45** |
| brief `--crimson` #943B4B | 6.43 | 5.57 | 3.72 | **1.50** |

Every candidate clears the ~1.3 floor on the weakest pair, so **hatch spacing
and stroke weight are untouched** — which was the instruction if it had
dropped.

Hatch is only used at ≥24px (`HATCH_MIN_PX`), so hatch↔hollow is measured at
that size: a 10px pattern tile at 50% duty, against a 1px stroke on a 24px
cell.

**One honesty note.** The model reproduces solid/hatch exactly (3.07 against
the recorded 3.07 — a 50% duty cycle is unambiguous) but gives solid/hollow
4.48 where `globals.css` records 4.81. The difference is the assumed hollow
stroke coverage. The session-1 figures cannot be reconciled because the paper
hues they were measured against **only ever existed in an uncommitted working
tree** and were overwritten by the repalette — a small, concrete cost of the
five sessions that were never committed. So both palettes were measured under
one model instead of compared across two. The conclusion is unaffected.

---

## 3. Gradient — measured against the shadows, not separately

`--paper` → `--paper-deep` (`#F2EEE3`, ~3% darker), one vertical wash.

| | ratio |
|---|---|
| wash, top to foot | **1.064:1** |
| `--shadow-1` at its darkest, over paper | 1.098:1 |
| `--shadow-2` at its darkest, over paper | 1.140:1 |
| `--shadow-1` over the **foot** of the wash | 1.097:1 |
| panel vs page at the **top** | 1.053:1 |
| panel vs page at the **foot** | **1.121:1** |

Two results:

1. **The wash cannot flatten the shadows.** A shadow composites over its local
   background, so its separation is 1.098 at the top and 1.097 at the foot.
2. **The wash helps the panels.** `--paper-raised` is lighter than both, so
   panel-to-page separation *rises* down the page, 1.053 → 1.121.

The wash's 1.064 exceeds the panel edge's 1.053, which reads backwards until
you count pixels: the wash spends that difference over the whole document
height, the panel spends its at a single edge. A hard edge at 1.053 is
visible; a ramp of 1.064 across three thousand pixels is not. That is the
"should not consciously notice it" property, stated as a measurement.

### Two things I got wrong and corrected

- **`background-attachment: fixed`** was in the first version. It is the
  opposite of the intent: a fixed wash is viewport-sized, so the entire span
  sits inside every screenful and stays put while content scrolls past it.
  That is a gradient somebody notices. Removed; the wash spans the document.
- **The figures in the stylesheet comment were written before they were
  measured** (1.030/1.052/1.074 against the real 1.064/1.098/1.140). Corrected.
  Asserting unverified numbers in a comment is the same error the safeguard
  audit is about, in the cheapest possible place to make it.

### The diagonal pattern is not implemented

§3.3 asks for "the faint diagonal pattern visible in the reference". The
reference never reached any session. Inventing a texture nobody has seen is
how a wash becomes decoration, so it is omitted and recorded rather than
guessed — the same treatment the derived hues got.

---

## 4. Elevation — and level 0 is a real level

| Level | Treatment | Applied to |
|---|---|---|
| 0 | `.panel` — transparent, no radius, one hairline between siblings | prose, `/model-card`, `/about/model`, `/accuracy` sections |
| 1 | `--paper-raised`, 1px `--rule`, radius 10px, `--shadow-1` | content sections, table containers |
| 2 | same fill, radius 12px, `--shadow-2` | the hero, the landing scorecard |

`.panel` already rendered as level 0 and **was left that way**. The temptation
in a step called "elevation" is to convert it into a card, which would box
`/model-card` — the one page that should read as a document rather than an
interface — into the templated default the design guidance names. The rule
written at the class: *if it is prose, it is a panel; if it is a thing, it is
a level.*

`.level-1` and `.level-2` are defined here and applied in step 4, which is the
elevation pass across existing pages.

Two levels, not a ramp. A third would have to mean something, and nothing here
is a third thing.

---

## 5. Typography and spacing — the drift list

### Typography: no drift inside the theme

47 of 58 `font-size` declarations are tokenised. All 11 that are not sit in the
legacy dark-theme block (lines 1–109), and `.theme-paper` overrides every one
with a `--t-*` token. `.theme-paper` is on `<body>`, so the scale is in effect
everywhere.

**Flagged, not fixed:** that legacy block is now unreachable. Out of scope for
this step.

### Spacing: there was no scale to be off

That is the finding. Seven type-scale tokens existed and **zero spacing
tokens**, so five sessions accumulated raw pixels against an implied 4px
rhythm nobody had written down. 8/16/24/32 dominate (77 uses) — the intent was
there — alongside:

| value | where it had drifted to |
|---|---|
| **3px** | `.fig` padding |
| **5px** | filter-label gap, a margin-bottom, a flex gap |
| **6px** | `h3` margin-bottom, matches-table `th` padding |
| **7px** | matches-table `td` padding, a flex gap, a button padding |
| **10px** | `.fig`, matches-table `td`, button padding |
| **14px** | panel padding, `.hero-strip` margin, `.band p` margin |
| **18px** | header nav gap, `.not-built` margin, `.ask-exchange` padding |

`--space-1` … `--space-8` (4/8/12/16/24/32/48/64) added, and **25
declarations** snapped to them. Not tokenised, deliberately: 1px and 2px
hairlines, every border width, and the pill radii — a border is one or three
device pixels of a drawn line, not a unit of rhythm.

**A bug worth recording.** The first migration pass turned
`border-left: 3px solid var(--bat)` into `var(--space-1)` — a 4px border —
because `border-left` ends in `left` and the regex read it as a position. Found
by checking the diff rather than the test output, since no test covers a border
width. Reverted and redone with a lookbehind.

---

## 6. What now guards this

`lib/tokens.test.ts`: **25 → 39 tests.**

- the spacing scale exists, every step is on the 4px rhythm, the steps
  increase monotonically and no two are equal
- text and fill tokens are checked against **every paper surface**, not just
  `--paper`

That last one came out of the gate catching my own change: adding
`--paper-deep` failed "has no token that nobody decided about", which forced
the question of whether text sits on it. It does — the foot of a long page
*is* that colour — and the wash is not free:

| | on `--paper` | on `--paper-deep` |
|---|---|---|
| `--bat-text` | 5.14 | **4.83** |
| `--bowl-text` | 5.11 | **4.80** |

Still clear, but the margin over 4.5 falls from 0.64 to 0.33. Now measured on
every run rather than on the day someone deepens the wash.

**Proven to fire**, all of it: `--space-3` off-rhythm → 1 failure;
`--space-5` duplicated → the ordering and uniqueness tests; `--paper-deep`
deepened to `#E8E2D2` → 4 failures naming both text and fill tokens. Reverted
each time, stylesheet confirmed restored.
