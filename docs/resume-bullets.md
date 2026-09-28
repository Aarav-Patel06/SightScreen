# Resume bullets — SightScreen

Drafted from SPEC.md §14's claim list, but using **measured** figures from the
project rather than the spec's targets. Every number here traces to a
committed report or a close-out document; none is aspirational.

Seven drafts. Cut to the three or four that fit the role — notes below each say
what it is doing and when to drop it.

---

**1.**
> Built and deployed an end-to-end live cricket win-probability system over
> 3.8M ball-by-ball records — LightGBM model, FastAPI service, live polling
> worker, Next.js frontend — running on Railway, Supabase and Vercel, with
> predictions rendering in the browser a median 702 ms after they are written
> (p95 918 ms).

*The scope-and-shipped bullet. Leads with breadth and ends with a latency
figure measured by a headless browser against the deployed app, not
estimated. Keep this one; it is the only bullet that says "this is live".*

---

**2.**
> Beat a three-feature logistic baseline by 0.0149 Brier and a historical
> base-rate baseline by 0.0386, both with 95% match-clustered bootstrap
> intervals excluding zero — clustering by match because deliveries within one
> innings are not independent, which understates the error bar by roughly an
> order of magnitude if ignored.

*The statistical-rigour bullet, and the one most likely to start a good
conversation. The clustering clause is the whole point: it shows you know why
n=12,081 balls is really n=100 matches. Drop the clause if space is tight;
never drop the intervals.*

---

**3.**
> Evaluated five probability-calibration methods against an identity control
> on a held-out selection split; none beat leaving the model alone, so it
> shipped uncalibrated with the reliability shortfall published rather than
> cosmetically corrected — 4 of 10 deciles fail a clustered calibration check
> and this is disclosed on the public accuracy page.

*The judgement bullet. It says you ran an experiment, got a null result, and
resisted the temptation to keep fitting until something won. Interviewers who
have seen people p-hack a calibration curve will notice.*

---

**4.**
> Shipped a public model-accuracy page that separates predictions made before
> the result was known from replayed ones, and states that the 100-match
> replay sample is the same data used to select the model — so the flattering
> number is labelled a demonstration and the honest one, currently 25
> predictions with nothing yet scored, is shown first.

*The product-judgement bullet, and the rarest thing on the list. Almost
nobody builds the page that can embarrass them. If you only keep two bullets,
keep this and #2.*

---

**5.**
> Established a verification practice of exercising every new component
> against its real dependency — three deliberate database outages, a live
> provider feed, a public-anonymous access probe — which surfaced 22 defects
> across four phases, none of which code review or tests-written-first had
> caught; including a silent key collision that dropped a prediction after
> every wide, and an outcome-resolution path that would have scored 2026
> predictions against a 2017 match.

*The methodology bullet, and the strongest material in the project. The two
concrete examples matter more than the count — "22 defects" alone reads as
padding, while "scored 2026 predictions against a 2017 match" is memorable
and obviously real. Trim the parenthetical list of methods before trimming
the examples.*

---

**6.**
> Automated a nightly calibration monitor that re-scores the prediction log,
> publishes the reliability table, and refits the model only if a candidate
> beats doing nothing by a statistically significant margin — a job whose
> expected and normal outcome is to report honestly and change nothing.

*The operational-maturity bullet. The last clause is the interesting half:
most monitoring jobs are written as if acting is success. Drop this one first
if you are over length — #3 already carries the "identity won" idea.*

---

**7.** *(added 2026-09-27. Every `[VERIFY]` stays until production confirms
it: P registered, shadowed, and switched to active.)*
> Replaced the live model with one that uses only features the
> snapshot-reconstructed live feed can actually deliver `[VERIFY: switched to
> active in production]`, under a non-inferiority rule committed before the
> held-out data was scored. On 2,287 held-out chases (test split, Jan 2025 -
> Sep 2026), the Brier difference was +0.00009 [−0.00076, +0.00096], with
> 0.12119 overall and 0.06354 in the final three overs, against a 0.0020
> margin. Thirteen alternative run-rate treatments were selected on
> validation and rejected, because none beat the existing one.

*The discipline bullet: it pairs a pre-registered rule with a negative
result that was kept. Label the test set every time. The 0.12119 and 0.06354
are on the current test split (2,287 chases, to 2026-09-17), NOT the Phase 1
test split behind bullet 1's figures, and the two must never be quoted side
by side as if comparable. Until the `[VERIFY]` clears, say "promoted under the
rule" rather than "replaced the live model".*

---

## Notes on using these

**The one-line version**, if you need a single sentence for a profile header:

> Live cricket win-probability system over 3.8M deliveries — deployed
> end-to-end, publicly measured against its own logged predictions, with the
> miscalibration it still has disclosed rather than hidden.

**What to bring to the interview.** The [model card](../web/app/model-card/page.tsx)
is the artifact behind "communicate technical decisions to non-technical
stakeholders" — one page, no jargon, including why a nightly job that changes
nothing is succeeding. The [Phase 3 close-out](phase3-closeout.md) carries the
"found by" table if anyone asks what bullet #5 actually means.

**What not to claim.** The 0.1232 test-set Brier and the 0.1020 figure on the
replayed sample are different populations and are not interchangeable. The
live population has no accuracy figure yet — 25 predictions, none scored,
because the match archive has not caught up. If asked "how good is it really",
the honest answer is that the offline evidence is strong, the live evidence
does not exist yet, and the page says so.
