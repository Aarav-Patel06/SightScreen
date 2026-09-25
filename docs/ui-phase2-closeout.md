# UI Phase 2 — close-out

Five steps: data, tokens, identity, elevation, landing page. The phase set out
to make the site look designed. What it mostly did was find out what was
already wrong.

---

## What the phase actually delivered

| | before | after |
|---|---|---|
| Rows on `/matches` | 107, five saying "Not replayed" | **341**, none |
| Full Member fixtures | 11 | **248** |
| Predictions | 12,617 | **51,213** |
| `innings IS NULL` | 496 | **0** |
| `status = 'live'` stuck | 4 | **0** |
| Scored matches on `/accuracy` | 100 | **340** |
| Palette | derived, marked PROVISIONAL | **sampled from the artwork** |
| Spacing scale | none | `--space-1..8`, 25 declarations snapped |
| Elevation | none | three levels, assigned by hierarchy |
| Web tests | 165 | **243** |
| Python tests | 673 | **697** |

---

## The found-by table

Every defect this phase surfaced, and what surfaced it. Ranked by what it
would have cost.

| # | Defect | Found by | Would have cost |
|---|---|---|---|
| 1 | `.grid tr.thin td` rendered at **2.35:1** on cream — the dark palette's `--muted`, on every `/accuracy` decile under 30 matches | the cascade analysis attached to a delete instruction | shipped WCAG failure on the page whose subject is honesty |
| 2 | The legacy block was **not** unreachable — it carried `main { margin: 0 auto }`, the rule centring every page | the same analysis | every page left-aligned |
| 3 | `assert_same_matches`, the guard against resolving Supabase match 3 against corpus match 3, had **zero tests** | the safeguard audit | outcomes attached to the wrong game, undetectable downstream |
| 4 | `PARITY_TOLERANCE` was **unreachable** — inline in a 120-line loop | the safeguard audit | deployed and local scoring diverging silently |
| 5 | The pre-push hook had **never run once** — `core.hooksPath` unset | the safeguard audit | every gate it claimed to run, unrun, while appearing to exist |
| 6 | CI was green on a `/matches` that prerendered **zero rows** | reading `ci.yml`'s justification and testing it | a dead site passing CI |
| 7 | The model's margin over a logistic baseline **does not survive** 340 matches | the replay itself | a headline claim that was not true |
| 8 | `20260924000003` asserted a corpus property, breaking **every fresh-schema build** | the Python suite | broken clones, CI provisioning, the restore path |
| 9 | A warm `.next/cache` served **seven stale rows** inside an otherwise correct page | checking the built page rather than the database | a plausible-looking page with the old world in it |
| 10 | `check-build-output.mjs` went **vacuous** when the marker's capitalisation changed | checking whether my own new guard could still fail | a guard reporting success unconditionally |
| 11 | The vitest include pattern missed `components/` — **second instance** | writing the first test in a new directory | tests that exist, pass locally, and never run |
| 12 | `verify-session.mjs` reported the **success path as a failure** | running it at the moment it was designed for | the rule-18 safeguard training people to ignore it |
| 13 | Recharts palette copies had **no test**, by their own admission | the repalette | charts silently drawn in the previous palette |
| 14 | Border widths were an **uncovered surface** | a regex turning a 3px border into 4px | caught only because the diff was short enough to read |
| 15 | The reference image had been in the repo for **five sessions** | `ls web/public/` | a palette marked PROVISIONAL that never needed to be |
| 16 | Afghanistan's absence is **Cricsheet policy**, not a bug | reading the archive's own README | chasing a data gap that cannot be closed |
| 17 | Eval `results.json` carries a **wrong dismissal count** | verifying a quote before shipping it | a landing page quoting a number the system no longer produces |

**Eleven of seventeen were found by a check, not by looking.** The six that
weren't — 6, 10, 14, 15, 16, 17 — were each found by asking *"how would I know
if this were false?"* about something already believed.

---

## Standing rules 18–20

**18. "Done" is a claim about the repository, and a claim about the repository
must be READ BACK from it.** Five sessions were reported complete while every
file sat untracked. `git status --short` had printed the evidence at the end of
nearly every one and was read as an inventory of work done; `??` means the file
exists in one directory on one machine and nowhere else. Closed by
`scripts/verify-session.mjs` — which then had to be fixed itself, because it
reported the success path as a failure.

**19. A migration's assertions get exactly one chance to be observed. After it
is applied they are documentation. Anything meant to hold continuously belongs
in a test.** The migration asserts at apply time; the test asserts forever. Both
sides of this boundary were crossed in the same file within days: a continuous
invariant that would have been lost after one run, and a corpus property that
broke every empty-database build.

**20. When something is recorded as missing, check whether it is present under
a name that hid it.** `web/public/logo.png` sat in the repository for five
sessions while every session recorded the reference image as never having
arrived. It was filed as a logo, so it was read as a logo. The artifact's
filename decided its use and five sessions inherited that decision. The missing
thing was never missing; it was mis-shelved, and "we don't have X" was recorded
as a fact rather than as a search that had failed.

---

## What the UI still doesn't show, and why

Stated here rather than discovered later.

**Per-player projections** — the current batter's or bowler's expected
contribution, and this batter against this bowler. `player_state`, the table
that would hold a per-player model, has no rows. The corpus has every ball
between any two players, but turning that into a prediction needs a model that
does not exist and a sample large enough to beat the prior. Phase 5.

**Who moved the match.** The per-ball swings are drawn on every strip.
Attributing each one to the batter or the bowler is a separate model, not a
different view of this one.

**First-innings predictions.** The model is second-innings only. A first-innings
score projection is a different target with a different feature set; §12.1 lists
it and nothing pretends otherwise.

**Anything before a match starts.** No pre-match probability, no toss modelling.
The model takes a match situation, and before the first ball there isn't one.

**The SQL behind each `/ask` answer.** The agent logs every query it makes, but
the route returns only the answer. Wiring the two together is a change to a
proven path rather than a visual one, so it stayed out of a UI phase.

**Afghanistan.** Not a gap this project can close: Cricsheet withholds all
Afghanistan men's matches as policy — 374 of them. Eleven Full Members are
flagged, and `tests/db/test_full_member.py` will say so if that ever changes.

**A live population with a score.** `/accuracy` reports `n = 0` live
predictions, with 40 logged across 2 matches. They cannot be scored until the
match archive contains those matches. This is the one gap that closes itself,
given time and a running worker.

---

## The one open item

**Read-only CI credentials.** `check-build-output.mjs` is written, proven to
fire, and wired in — but skipped loudly, because CI builds with placeholder
credentials and every prerendered page is empty by construction. Adding
`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` and
`SUPABASE_SECRET_KEY` as repository secrets switches both the build and the
check to real data **with no code change**. The first two are public by
construction; the third wants a read-only role rather than the service key.

Until then, CI proves the app compiles. It does not prove any page renders
data, and the `::warning::` on every run says so.

---

## What this phase was actually about

The brief was a visual pass. Two of its five steps were visual.

The rest was the same discovery in different places: **a check is only worth
the state it has actually been observed in.** The types.ts gate was correct and
never ran. The parity test enumerated three dimensions and called itself a
parity test. The pre-push hook was written, documented, referenced in two
session docs, and never installed. The build output check went vacuous one
commit after being proven to fire.

None of those failed loudly. All of them failed by passing.

The habit that came out of it is narrow enough to state: **construct the
failure.** Not "does the guard exist", not "does it pass" — make the thing it
is built to catch, and watch it catch it. Every guard this phase added was
proven that way, and two of them were wrong when first written.
