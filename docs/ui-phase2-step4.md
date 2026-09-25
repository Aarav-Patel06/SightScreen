# UI Phase 2, Step 4 — the sampled palette, then the elevation pass

---

## 1. The palette is now the artwork's

Sampled from `web/public/logo.png`: teal **#366C73**, ball **#98374B**, tan
**#C6B592**, cream **#F7F6E9**.

The four surfaces between them are **blends between two sampled anchors**,
chosen to reproduce the contrast relationships the derived palette had, so
their hues stay inside the artwork's range rather than being invented:

| token | derived | sampled |
|---|---|---|
| `--paper` | #F9F5EA | **#F7F6E9** |
| `--paper-raised` | #FDFBF6 | #FCFBF6 |
| `--paper-deep` | #F2EEE3 | #F1EEDF |
| `--rule` | #E0D5BF | #DFD6BD |
| `--chrome` | #D5C3A5 | **#C6B592** |
| `--chrome-deep` | #C6A67B | #AC9C7E |
| `--bat` | #1D727C | **#366C73** |
| `--flag` | #A8324A | **#98374B** |
| `--ink`, `--ink-soft`, `--bowl` | — | unchanged |

`--bowl` (#935B2B) stays derived: there is no brown in the artwork, and it
exists because the two sides must differ from each other *and* from the
failure colour, which §3.2 forbids collapsing.

**A method note.** The first derivation scaled RGB toward a target ratio and
produced an orange `--paper-deep` (#FFEDC0) and a pure-white `--paper-raised`.
Scaling a colour's channels moves its hue; blending between two colours that
both exist in the artwork cannot. The method is recorded at the tokens because
the wrong one looks plausible until you read the hex.

### No floor is breached, and the tight one got looser

| | derived | sampled |
|---|---|---|
| `--bat` on `--paper` | 5.14 | **5.44** |
| `--bat` at the foot of the wash | 4.83 | **5.08** — margin 0.33 → **0.58** |
| `--flag` on `--paper` | 5.99 | **6.47** |
| `--bowl` at the foot of the wash | 4.80 | 4.78 (tightest, still clears) |
| texture hatch↔hollow | 1.46–1.50 | 1.46–1.50 |

### Everything on chrome got worse, and the rule is unchanged

The sampled tan is darker than the derived one, so `--chrome` against
`--paper` moves **1.58 → 1.85** — the header and footer bands are more
distinct from the page than they were.

| on `--chrome` | derived | sampled |
|---|---|---|
| `--ink` | 8.92 | **7.64** (clears) |
| `--ink-soft` | 3.52 | 3.02 |
| `--bat` | 3.25 | 2.94 |
| `--flag` | 3.78 | **3.49** |

Only `--ink` clears 4.5:1, exactly as before. The constraint is tighter, not
different.

### Rendered-HTML diff: six of seven pages byte-identical

| page | result |
|---|---|
| `_not-found`, `ask`, `index`, `matches`, `model-card`, `players` | **IDENTICAL** — no colour reaches the markup |
| `design` | differs by exactly the hexes it prints and the ratios it computes (`5.14:1` → `5.44:1`, and so on) — 19 text differences, **nothing structural** |

The palette lives entirely in CSS. The one page that moved is the one whose
job is to display the measurements.

---

## 2. Two surfaces covered before the pass, not after

### `lib/borders.test.ts`

Step 2's regex turned `border-left: 3px solid var(--bat)` into
`var(--space-1)` — a 4px border — and **nothing could have caught it**, because
no test asserts on a border width. Step 4 touches borders on every page.

Asserts that no border takes a spacing token (the bug, stated directly) and
that widths stay in `{0, 1px, 2px, 3px}` — a hairline and a 3px accent are
different objects, not two steps of one ladder. Proven by reintroducing the
exact step-2 bug, and again with a 5px width.

### `lib/chart-colours.test.ts`

Recharts takes JS props, not CSS, so the palette is duplicated in two chart
files. Both carried a comment reading *"lib/tokens.test.ts … cannot see these
copies"* — an accurate description of a gap, left open for five sessions.

This repalette is exactly the event that breaks them: a token moves, the
charts keep the old hex, nothing fails, and the charts are quietly drawn in
the previous palette. Now compared against the stylesheet, and proven to fire.

### `tokens.test.ts` — nothing but ink on chrome

`CHROME_SAFE_TEXT` was a claim about the **registry**. This is a claim about
the **stylesheet**, and the gap between them is where
`.grid thead th .band-off { color: var(--flag) }` would live: legal by the
registry, 3.49:1 on the page.

Proven by setting `.grid thead th` to `--flag` and watching it fail with the
reason. The live constraint is designed around, not exempted: `.band-off`
sits in a `<td>` on paper, never in a header band.

---

## 3. The elevation pass

| Page | Treatment | Applied |
|---|---|---|
| `/matches` | Table in level-1, filters unboxed | ✅ |
| `/players` | Table in level-1, search unboxed | ✅ |
| `/match/[id]` | Hero level-2; curve and strip share **one** level-1 | ✅ |
| `/accuracy` | Each measurement section level-1 | ✅ |
| `/ask` | Message list level-1 | ✅ |
| `/model-card` | **No panels** | ✅ untouched |
| `/design` | Gains the elevation scale | ✅ |

Verified on the built artifact rather than the source:

```
  model-card   level-1: 0   level-2: 0
  matches      level-1: 1   level-2: 0
  players      level-1: 1   level-2: 0
  /accuracy    level-1: 2 sections      (rendered, 200)
  /match/8429  level-1: 1   level-2: 1  (rendered, 200)
```

### What did *not* get a level is the interesting part

- **`/model-card` keeps none.** The one page that should read as a document.
- **Page titles stay level 0.** A title is not an object.
- **`/accuracy`'s "what it doesn't do" stays level 0** — it is prose about
  absence, not a measurement.
- **`/ask` boxes the LIST, not each message.** A turn in a conversation is not
  a discrete object; boxing each would produce the stack of identical cards
  the design guidance names. The panel renders only when there is history, so
  an empty page has no empty box on it.
- **The curve and the strip share one panel.** They share an x-axis and are
  indexed by the same delivery position; two panels would draw a line through
  the middle of one figure.

### The accent and the elevation now say the same thing

`/accuracy`'s two measurement sections already carried the live-versus-replayed
distinction as a 3px left accent — teal for the measurement, rule-grey for the
replay. That accent now sits **on the panel edge** rather than floating beside
it. The elevation and the accent reinforce each other instead of competing,
and `padding-left: 16px` became `var(--space-4)` on the way past.

### Untouched, and checked

The latency copy renders byte-identical:

```
updates every 15s · provider lag not published ·
```

229 tests pass, including the four Realtime resync tests and the `/ask` gate
and cap tests, **all unmodified**.

---

## 4. Two things worth keeping

### Verify-before-delete, in its strongest form yet

The instruction was to delete 110 lines I had called dead weight. **I was
wrong, and the verification step attached to the instruction is the only
reason it did not ship.**

The block was carrying `main { margin: 0 auto }` — the rule that centres every
page — and `.grid tr.thin td { color: var(--muted) }`, where `--muted` was the
*dark* palette's #9aa3ab at **2.35:1 on cream**, on every `/accuracy` decile
under 30 matches.

Both the instruction and the claim it rested on were mine and wrong. The check
was cheap, mechanical, and independent of the reasoning that produced the
claim — which is precisely why it worked. A verification step that shares the
assumption it is testing verifies nothing; this one asked the cascade, not me.

### Standing rule 20

**20. When something is recorded as missing, check whether it is present under
a name that hid it.**

`web/public/logo.png` sat in the repository for five sessions while every
session recorded the reference image as never having arrived, and shipped a
`PROVISIONAL: DERIVED, not sampled` note on the two colours it would have
determined. It was filed as a logo, so it was read as a logo. Nobody looked at
it as a colour source, because its name had already answered the question of
what it was for.

The artifact's filename decided its use, and five sessions inherited that
decision without revisiting it. The missing thing was never missing; it was
**mis-shelved**, and "we don't have X" was recorded as a fact rather than as a
search that had failed.

The check is cheap and the rule is narrow: before recording something as
absent, list what *is* present and ask what each item could be used for,
ignoring its name. Here that is one `ls web/public/` and one look.
