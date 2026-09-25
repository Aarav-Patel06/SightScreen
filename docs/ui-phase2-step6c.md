# Step 6, part three — boxes everywhere, stronger shadows

Loose paragraphs sitting on the page background made several pages read as
unfinished, and the shadows were too faint to register — the four landing
preview modules and the header bar in particular looked flat.

---

## 1. This reverses a rule, deliberately

Three times this project recorded:

> *If it is prose, it is a panel; if it is a thing, it is a level.*

…with `.panel` deliberately transparent so documents were not boxed into
identical rounded cards — the templated default the design guidance names
explicitly.

**That is now overridden for user-reachable pages.** Loose text on the page
background was the bigger problem in practice. The rule is not deleted; it is
**scoped** rather than global, and the exception survives as exactly one page.

Recorded here so the docs do not quietly contradict the stylesheet.

## 2. Shadows at 14%

| | before | after |
|---|---|---|
| `--shadow-1` | `0 1px 2px` @ 5% — **1.098:1** | `0 2px 8px` @ 14% — **1.310:1** |
| `--shadow-2` | `0 2px 8px` @ 7% — **1.141:1** | `0 4px 16px` @ 14% — **1.310:1** |

Roughly three times the separation, measured as the shadow's darkest point
against `--paper`. **The blur grows with the opacity, not the opacity alone**:
a 2px shadow under a 1px border reads as part of the border, so depth needs
spread as much as darkness.

## 3. Everything is boxed, via two existing hooks

No per-page audit was needed, because two mechanisms already existed:

- **`.band`** was the loose-prose class — 40px of vertical padding and a
  hairline above — used 7 times and *only* on user-reachable pages. It became
  a panel. Its `border-top` went with it: a box needs no rule above it, and
  that rule landing on the preceding panel's bottom edge was its own bug,
  fixed earlier this session.
- **`.panel`** became a box too, **except inside `.document`**.
  `/model-card` already renders `<main className="document">`, and
  `.document .panel` already reset border/padding/margin — it gained
  background/radius/shadow resets. **One selector, no page-by-page branching.**

Four remaining loose elements were found by reading the rendered DOM rather
than the source, and boxed: `/ask`'s intro paragraph, and the cross-link line
at the foot of `/accuracy` and `/match`. Those took a lighter `.page-links`
treatment — navigation, not content, and a full panel around three links would
out-weigh the page above it.

### Verified in the render, not the stylesheet

| page | boxes | loose text |
|---|---|---|
| `/` | 8 | **0** |
| `/matches` | 2 | **0** |
| `/players` | 2 | **0** |
| `/accuracy` | 6 | **0** |
| `/ask` | 1 | **0** |
| `/match/8429` | 5 | **0** |
| `/player/17558` | 4 | **0** |
| `/model-card` | **0 of 8 `.panel` elements visually boxed** | — |

That last row is the one worth checking properly. Counting class names would
have reported "8 boxes" on `/model-card` and been wrong — the elements exist
and are unstyled. Computed style says `bg=rgba(0,0,0,0) border=0px radius=0px
shadow=none`, against `/accuracy`'s `bg=rgb(252,251,246) radius=12px
shadow=…0.14`.

## 4. Landing page order

Hero → **scorecard** → previews → prose. The scorecard stays second: it is the
site's claim, not filler, and burying the one genuinely unusual thing on the
page under four nav modules would be the wrong kind of tidy.

## 5. Header nav

`--t-nav: 15px`, weight 600. Its own token rather than a raw value, because an
unexplained number in this stylesheet is what the last two audits kept
finding. It sits between `--t-xs` (13.6) and `--t-s` (17): heavier than a
caption, smaller than body copy. 17px would have cost 3px more header height
on a phone, where the header is sticky and already 17% of the viewport.

---

## Verification

- **257 web tests** — contrast on every surface (boxing moves text onto
  `--paper-raised`, which is lighter, so ratios improve), the radius scale,
  and the duplicate-declaration guard, which is exactly what catches a second
  `border-radius` creeping in while adding this many panel rules.
- **No horizontal overflow at 340px or 1140px, signed in and out.** More
  nesting and more padding was the main risk of this change.
- `check-build-output.mjs` still fires and is still not vacuous.
- `/model-card` confirmed unboxed by computed style, per above.

## One thing whose justification has weakened

The page wash (§3.3) was measured and justified as stopping *"a large cream
field looking flat behind raised panels"*. With most content now boxed there
is much less bare cream for it to act on, so it does less work than it was
designed for.

**Not changed** — it still costs nothing and still helps at the page edges.
Recorded because a rationale that no longer fully holds is the kind of thing
that later gets cited as if it did.
