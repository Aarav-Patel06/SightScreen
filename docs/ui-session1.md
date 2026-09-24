# UI mini-phase, session 1 — tokens and the ball strip

Inserts between Phase 3 and Phase 4 and renumbers nothing. Zero spend: local
Docker Postgres, one free-tier Supabase read to build a committed fixture, no
Railway, no Vercel, no Anthropic calls.

## What is live

`/design` — a static route rendering every token, type size, texture and strip
variant on one page. It imports no Supabase client and reads a committed
fixture, so it renders with Docker stopped, with no environment, and while the
Supabase project is paused. Verified by serving a production build with both
Supabase variables unset.

- `lib/ball-strip.ts` — the strip's data model and its legibility floors
- `components/ball-strip.tsx` — the component, four tiers, hand-rolled SVG
- `components/textures.tsx` — the texture system
- `lib/fixtures` — match 8532, committed

103 tests pass, including the six pre-existing suites. `globals.css` gained 94
lines and lost none; the dark `:root` block is untouched, so the six pages that
predate this phase render exactly as before.

## Three corrections to UI-PHASE.md, all found by measuring

**§2 said Tailwind ships with Next 15.** It does not, and it was not installed
— no `tailwind.config.*`, no `postcss.config.*`, and 89 lines of hand-written
CSS. `lucide-react` and every shadcn/Radix dependency were likewise absent.
Session 1 added **zero npm dependencies**; `next/font` is part of `next`.

**§1.2 asked for four encodings at four sizes, and the smallest cannot carry
them.** A T20 second innings is 113 legal balls at the median and 121 at
maximum; ODIs in the replay manifest run to 301. At 340px less the page
gutters that is a 2.55px pitch. Height and direction survive it. Fill does not
— hollow is a 1px stroke with no fill, so two strokes overlap and every hollow
mark renders solid, which does not degrade the encoding so much as invert it.
Hatch does not either: a 10px diagonal inside a 2.5px mark shows one stripe or
none depending where it falls.

So the component measures its own width and picks a tier, rather than taking a
size prop — a prop can be passed wrongly, a measurement cannot. `full` at ≥6px
carries everything; `reduced` at ≥4px drops per-mark pointing; `minimal` at
≥2px drops fill and carries wickets as full-height hairlines; `area` below 2px
stops drawing marks at all, because at 0.82px per mark they are sub-pixel and
discrete marks would be a lie about the rendering.

**§1.3's texture semantics outran the data.** The table names "wide interval"
and "sample below threshold" as states. For win probability neither exists:
`predictions.payload` has nine keys and none is a confidence, an interval or a
sample size. The only signal is `phase`. Hatching a pre-toss prediction and
letting it read as an interval is exactly what SPEC.md §12.2's 2026-09-18
clarification forbids. Texture now encodes **evidence class**, and
`TextureLegend` takes the basis as a required prop, so a surface cannot ship
texture without naming what it is derived from — §6 rule 2 enforced by the type
checker rather than by review.

## Found by

| Finding | Found by |
|---|---|
| Tailwind was never installed | Reading `package.json` instead of §2 |
| 2.55px per mark at a phone width; fill cannot survive it | Measuring `max(legal_ball_num)` over the corpus, not estimating from "120 balls" |
| A T20 second innings is 113 balls at the median, not 120 | The same query — chases end early, and strip *length* is itself information |
| `predictions` carries no interval or sample size | Reading `lib/prediction.ts` before designing the texture states |
| Ball events are not available to the browser at all | Tracing `deliveries` to its row count on Supabase: zero, by design |
| The final mark of every innings is underivable | Writing the off-by-one test and finding nothing follows the last row |
| Both Plex faces are already tabular; `tnum` does not exist in them | `fontTools` on the built woff2, rather than the eyeball test the page was built to run |
| Hatch is invisible below ~24px | Rendering the ramp at 32/24/12/8/4px and looking |
| `--bowl` is 3.46:1 and fails AA for text | Computing the contrast column on `/design` rather than trusting §1.4's table |
| The "below floor" caption silently vanished | Grepping the built HTML for a string that should have been there |

## Standing rules

Rules 1–8 are in `docs/phase2-closeout.md`, 9 and 10 in
`docs/phase3-closeout.md`, 11–14 in `docs/phase6-session1.md`.

**15. A value imported across the Server/Client boundary arrives as an opaque
reference, and a NaN comparison returns FALSE rather than throwing. Keep shared
constants in non-client modules, and give any numeric guard that can receive
NaN an explicit `isFinite` check rather than a bare comparison.**

`/design` is a Server Component. It imported `HATCH_MIN_PX` from
`components/textures.tsx`, which carries `"use client"`. Next replaces such an
import with a client reference: an opaque object, not the number.

The import looked entirely healthy, because React resolves that reference when
it is rendered as a child. `A {HATCH_PITCH_PX}px diagonal needs roughly
{HATCH_MIN_PX}px` printed `A 10px diagonal needs roughly 24px` — correct, in
the built HTML, two paragraphs above the bug. But the hatch ramp's caption,
`{size}px{size < HATCH_MIN_PX ? " — below floor" : ""}`, is evaluated on the
server, where the comparison is `12 < {}`. That coerces to `12 < NaN`, which is
`false`. The captions marking 12px, 8px and 4px as below the legibility floor
simply were not there.

**Either half alone would have surfaced.** A cross-boundary import that threw
on access would have failed the build. A comparison against a non-number that
threw — as it would in a language with a stricter `<` — would have failed the
render. What made this invisible is that JavaScript's relational operators
answer `false` for NaN on both sides of the comparison, so an unanswerable
question returns a confident negative, and the only symptom was *absent*
output. Absent output has no stack trace and no line number, and it looks
identical to a condition that is legitimately false.

The general form: **a silent failure needs two mechanisms, and code review
finds one at a time.** The import looked fine because it rendered. The
comparison looked fine because comparisons do not throw. Neither line is wrong
in isolation and the pair is wrong together, which is why this was found by
grepping the built HTML for a string that should have been in it rather than by
reading either file.

Scope limit: this is **not** an argument against importing from client modules.
Components must be imported that way and nothing here changes that. It is
specific to values that reach arithmetic, a comparison, or an array index —
anywhere NaN propagates instead of raising.

Closed by moving every shared numeric constant into `lib/ball-strip.ts`, which
has no `"use client"` directive, and recording the reason at the definition
site so the next person to reach for `components/` as a home for a constant
reads why it is not one.

## Layout constraints for later sessions

Measured greyscale separation between the three textures: solid↔hollow 4.77:1,
solid↔hatch 3.05:1, **hatch↔hollow 1.56:1**. The last is above the ~1.3 floor
for telling two flat tints apart, but it is the weakest pair in the system and
the margin is carried almost entirely by hollow's outline — which is the first
thing anyone removes when tidying a component.

So: **never place hatch and hollow adjacent without a third distinguishing
cue.** Recorded in UI-PHASE.md §6 and at the pattern definitions in
`components/textures.tsx`.

## Open, carried forward

1. **`--bowl` fails AA for text at 3.46:1.** Split into fill and text tokens in
   session 2, with `lib/tokens.test.ts` parsing the stylesheet so a future hue
   adjustment cannot regress it silently.
2. **The `area` tier drops the unknown-final-ball hairline**, because it draws
   no discrete marks. Consistent with the ladder, but it means the sparkline
   cannot express the honest gap. Acceptable at 120px; worth revisiting if the
   sparkline ever grows.
3. **Recharts cannot read `var()`**, so `live-match.tsx` and `accuracy/charts.tsx`
   hold duplicated hex literals. The palette retrofit must touch those, not
   only the stylesheet.
4. **`/about/model` and `/model-card` both exist**; UI-PHASE.md mentions only
   the second. Session 5 should retire one.
5. **§4.3's "phase confidence as texture, not a chip"** will break
   `live-match.test.tsx:123`, which asserts on `chip-low`. Session 3 updates it
   deliberately.
