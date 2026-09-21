# Phase 6, session 1 — corpus access and the SQL guard

Scope: the corpus replica, the read-only role and views, the five-layer SQL
guard, the adversarial suite, the four buildable tools, and query logging.

**Out of scope, deliberately:** the agent loop, `web/app/api/agent/route.ts`,
the Anthropic SDK, streaming, the system prompt, §10.4's citation enforcement
and §10.5's scouting/narrative calls. All session 2. Security-critical code
got its own session with its own gate.

---

## 1. Decision 1 — where the corpus lives

§10.1 wants natural-language Q&A over the full ball-by-ball database. §2.1 put
all 3.78M deliveries in local Postgres, Railway cannot reach a laptop, and
Supabase holds reference tables plus predictions. **The agent had nothing to
query.**

Three options, measured rather than argued:

| | Supabase Pro | T20-only on free | **Separate Postgres** |
|---|---|---|---|
| Cost | $25/mo ≈ **$300/yr** | $0 | ~$3–5/mo |
| Corpus | full | **T20 only, forever** | full |
| Space after load | comfortable (8 GB) | **457 of 465 MB free** | not capped |
| Blast radius if it fills | serving stops | serving stops | agent degrades only |

**§2.1's sizing sentence is wrong twice.** It says T20-only "roughly halves the
row count and fits comfortably". T20 is 2,417,673 of 3,780,368 deliveries —
**64%, not ~50%** — and at the corpus's measured density it comes to 457 MB
against 465 MB free. That is 8 MB of headroom before `predictions` grows at
79 kB/match. The only way to shrink it further is to drop the
`batter_id`/`bowler_id` indexes, which turns every matchup question into a
sequential scan of 2.4M rows that the 5 s timeout then kills. Not a cheaper
option — a broken one.

**The deciding argument is blast radius, not money.** Filling the *serving*
database to ~79% to host an analytical replica risks the live product: at the
500 MB cap, predictions stop being written and both the match page and the
accuracy page break. A separate replica isolates that completely.

### Built and measured, not estimated

The replica was built end to end against local PostgreSQL 17.6 with the real
corpus before any cloud service existed:

```
bootstrap  schema, role, five views, grants
load       3,780,368 deliveries / 13,143 matches / 18,468 players in 3m42s
verify     layers 1 and 3, empirically
```

| | planned | **measured** |
|---|---|---|
| replica size | ~690 MB | **577 MB** |

The estimate was conservative by ~16%: it assumed the source's full index set,
and the projection carries three indexes rather than six.

**Decision 1 survived a measurement that moved against it, and that is the
point worth recording.** The argument for a separate service rested partly on
the replica being far too large for Supabase's free tier. Building it cut the
headroom in that argument roughly in half — from ~225 MB of overflow to
112 MB. The conclusion is unchanged, because 112 MB of overflow is still
overflow and the blast-radius argument never depended on the size at all. But
a conclusion that holds *after* the number moves the wrong way is worth more
than one nobody rechecked, and the honest way to record that is to state both
figures and which direction the correction ran — not to quietly substitute
the new number and leave the original argument reading as though it had been
right all along.

Note also what the correction does *not* rescue: T20-only still does not fit.
The 457 MB figure was measured the same way, and it is against 465 MB.

**The replica is not a third member of the schema-parity regime.** Its schema
is created by `agent_tools/replica.py`'s own bootstrap, not by
`supabase/migrations/`, so `apply_migrations.py` keeps two targets and
`test_schema_parity.py` keeps comparing exactly two databases. It excludes
`match_states`, which also keeps that table's four `REAL` columns exempt from
the NUMERIC conversion, since nothing serving-side writes them.

---

## 2. The gate: the suite was written first, and watched fail

The adversarial suite was committed (`7c514ea`) before `sql_guard.py` existed.
Two claims about it turned out to be false on inspection and were corrected
before anything was built on them: it was **not** committed (it was untracked),
and it had **never been executed** — so "it fails correctly" was asserted by
construction.

Running it produced a collection error with the right cause
(`ModuleNotFoundError: No module named 'agent_tools'`, not a typo in the test
file). But that is a weak observation: pytest stopped at the import line and
never parsed the other 386 lines. So the package was created with a stub that
implements nothing, and the suite was run again:

```
73 cases collected — 69 failed, 4 passed
```

**The four that passed against a do-nothing guard are all must-be-admitted
cases** (`test_an_explicit_smaller_limit_is_left_alone`,
`test_a_cte_that_only_reads_is_admitted`, and two comment cases). They exist to
catch *over*-refusal, so a passthrough satisfies them by construction. Every
case asserting a refusal was red. That is the correct shape, and it was seen
rather than argued.

Each layer was then built in turn and its non-vacuity proof confirmed green
before the next was started.

---

## 3. Four AST shapes measured rather than reasoned about

The plan flagged CTE-wrapped writes and `SELECT … INTO` as the ones to check
empirically. Probing found two more, and **one case where the reasoning was
simply wrong**:

| Input | What sqlglot actually does |
|---|---|
| `WITH x AS (INSERT … RETURNING) SELECT * FROM x` | `Select` at the root, `Insert` three levels down — the check must walk the tree |
| `SELECT * INTO evil FROM …` | a `Select` carrying an `Into` node |
| `SET ROLE`, `RESET ROLE`, `ALTER ROLE`, `EXPLAIN ANALYZE` | all fall back to `exp.Command`, which **round-trips its raw text unchanged** |
| `SELECT count(*) FROM agent_deliveries;` vs `SELECT 1; -- x` | a trailing comment parses as a *second* statement (`exp.Semicolon`) |
| **`SELECT 1 -- ; DROP TABLE deliveries`** | **regenerates as `SELECT 1 /* ; DROP TABLE deliveries */`** |

The last one is the one that matters. sqlglot carries comments **into its
output**, so a guard that parses the injection and emits `expression.sql()`
hands the database a string still containing `DROP`. Parsing an injection is
not the same as removing it. The guard emits with `comments=False`.

`exp.Command` being a verbatim passthrough is why the guard refuses anything
the parser could not model, rather than treating an unmodelled statement as
inert.

---

## 4. The five layers, and why each one is not vacuous

| Layer | Enforced by | Query only it catches |
|---|---|---|
| `readonly_role` | database | `SELECT * FROM deliveries` — parses fine, one statement, limitable |
| `single_statement` | python | `SELECT 1; SELECT 2` — each statement is individually valid |
| `single_select` | python | `SET statement_timeout = '1h'` — one statement, nothing to limit |
| `statement_timeout` | database | `SELECT pg_sleep(10)` — a valid single SELECT |
| `forced_limit` | python | `SELECT * FROM agent_deliveries` — permitted, valid, single |

Each is proven **twice**: rejected with every layer on, and admitted with that
one layer disabled. The `skip` seam exists for this and is itself guarded —
`test_the_production_entry_point_never_skips_a_layer` parses `routes.py` and
fails if the served path ever passes it.

### The views are a boundary, not a `SELECT *`

Each view does something a `SELECT * FROM <table>` could not: it exposes names
rather than ids (so the agent never touches the alias tables, which hold
unresolved junk and a human review audit trail); it applies the project's own
correctness filters (`NOT is_super_over`, `NOT has_reconciliation_anomaly`) so
the agent **cannot compute a statistic over data the project already knows is
bad**; it omits surrogate keys and provider ids; and it omits the three
always-NULL player attribute columns entirely.

Views execute with their **owner's** rights — PG15+ defaults `security_invoker`
to false — which is precisely the mechanism that lets `agent_ro` read through
them without holding a grant on `deliveries`. `views.sql` states that
explicitly, because setting `security_invoker = true` looks like a hardening
improvement and would break every one of them.

### What the empirical verification found

`replica.py --verify` connects **as `agent_ro`** and establishes what it can
and cannot do. Two findings the code review would not have produced:

**1. A denial for the wrong reason is not a denial.** Writes against the
*join* views come back with SQLSTATE **55000** (`object_not_in_prerequisite_state`)
rather than 42501: a non-auto-updatable view is rejected at rewrite time,
*before* PostgreSQL consults the grants at all. The write is refused either
way — but "refused because the view has a join in it" stops protecting
anything the day someone adds an `INSTEAD OF` trigger. `verify()` now reads the
privileges straight out of `has_table_privilege`, and a 55000 only counts as a
pass because that matrix independently confirms the privilege is absent.
`UPDATE` on `agent_players` — a simple single-table view — *does* reach the
privilege check and returns 42501, which is what made the difference visible.

**2. Layer 3 gets the same non-vacuity treatment as the Python layers.**
`pg_sleep(10)` is cancelled with 57014 under a 5 s `SET LOCAL`, and
`pg_sleep(1)` completes with no timeout set. Without the second half, "we set a
timeout" is a line of code nobody has watched do anything.

```
DENIED   ok        SELECT * FROM deliveries          (42501)
DENIED   ok (55000)INSERT INTO agent_deliveries …    (privilege absent per catalogue)
ok       pg_sleep(10) cancelled with the timeout set
ok       pg_sleep(1) completed with no timeout set
ok       agent_ro holds SELECT on 5 views, nothing on 5 base tables,
         and no role attributes
```

---

## 5. The three gaps

### Gap 1 — `match_id` means two different things

`query_ball_data` reads **corpus** ids (1–13,143); `get_live_prediction` reads
**Supabase** ids, a different SERIAL space over the same fixtures. Passing one
to the other returns a different match, silently — the Phase 3 ball-key
collision one layer up.

**Resolution:** the views expose `'corpus:' || match_id AS match_ref` and no
bare `match_id`. `get_live_prediction` accepts only a bare integer. A
mis-passed id is now a validation error rather than a wrong answer — the same
"make the wrong thing impossible" move as the ball key.

### Gap 2 — `resolve_entity` must not call `resolve_player`

Even with `allow_create=False`, `resolve_player` still `INSERT`s an alias on a
fuzzy match (`entity_resolution.py:402-409`) and `INSERT`s into
`unresolved_entities` on a miss (`:428`). Wired to a tool, an agent asking
about "Viraat Kolhi" would mint permanent corpus rows from a model-generated
string.

**Resolution:** a read-only resolver reusing the **pure** helpers — none of
which take a `conn` — reading the views and returning **ranked candidates with
scores** rather than committing to one. Returning candidates is also the better
tool contract: the agent can disambiguate, and §10.4's citation rule gets a
sample size to quote.

`test_the_helpers_the_resolver_reuses_take_no_connection` asserts the
structural reason this holds, so the guarantee cannot decay into a convention.

### Gap 3 — the `get_player_form` refusal is NOT theatre, and does not belong at the data layer

`get_player_form` refusing while `query_ball_data` can compute a recent average
looks like theatre. It is not, because they answer different questions:

- *"How many runs has X scored recently?"* — **descriptive**. `query_ball_data`
  should answer it. Refusing would make the SQL tool useless.
- *"Is X in form?"* — **inferential**, and needs a posterior with uncertainty.
  That is `player_state`, and it is empty until Phase 5.

Moving the refusal down to the data layer fails because **the data layer cannot
tell the two apart** — the same `SELECT` serves both. What differs is the
*claim made about the number*, not the rows returned. Hiding the rows would
break the descriptive case to prevent a framing error in the inferential one.

Nor is as-of leakage the issue: `venue_stats.py`'s discipline exists because a
*training* feature must not see its own match. An agent answering a question
about the past is not training anything.

**Resolution: the refusal stays at the tool layer**, returning a structured
unavailability that names the missing artifact and points at what
`query_ball_data` *can* legitimately provide. The real safeguard against
presenting an average as an ability estimate is **§10.4's citation
requirement**, enforced in session 2's evals.

> **Session 2 inherits an obligation here, not a solved problem.** §10.4 is the
> actual mechanism, not a nicety. If session 2 ships the agent loop without
> enforcing citations in its evals, this gap is open again and the tool-layer
> refusal is exactly the theatre it was accused of being.
>
> **And it has to be in the eval set, not only the system prompt.** The
> behaviour at issue is the agent declining to present a computed average as
> an ability estimate — that is a behaviour, and a prompt instruction with no
> eval behind it is an intention, not a control. Concretely, the 20-question
> set §11 already requires should include at least one question phrased to
> invite the confusion ("is X in form?", "how good is X right now?") where the
> *passing* answer is a descriptive number with its sample size plus an
> explicit statement that this is a record, not an estimate — and the failing
> answer is a fluent, correct-looking average with no such hedge. If that case
> is not in the set, §10.3's guard is tested and §10.4's is not, which would
> leave the session's two halves held to different standards.

---

## 6. Found by

Carrying the column forward from the Phase 2 and Phase 3 close-outs.

| What | Found by |
|---|---|
| The suite was untracked and had never run — "fails correctly" was asserted by construction | Checking `git status` instead of trusting the resumption note |
| 4 of 73 cases pass against a guard that implements nothing | Running the suite against a deliberate stub rather than reasoning about what would fail |
| **sqlglot carries comments into its output, so `SELECT 1 -- ; DROP …` re-emits with `DROP` still in it — the recommended defence preserves the payload the discouraged one would have destroyed** | Probing the AST empirically; reasoning had concluded the comment was inert. See standing rule 11 |
| `exp.Command` round-trips unmodelled syntax verbatim | Same probe |
| A trailing comment parses as a second statement, so a naive statement count rejects ordinary SQL | Same probe |
| Writes against join views are refused with 55000 before privileges are consulted | Running `--verify` against a real database and reading the SQLSTATEs |
| `resolve_entity` returned "Rahat Ali" for "Viraat Kolhi" | Exercising the endpoint against the real corpus |
| The `narrowed or rows` fallback turned "no match" into a confident wrong answer | Same — and it is the worse of the two bugs |
| A grep-based test failed on the docstring that *explains* why `resolve_player` is unused | Running the test; switched to AST parsing |
| The replica is 577 MB, not the ~690 MB the plan argued from | Building it and measuring |
| `players.batting_hand`/`bowling_style`/`dob` are NULL for all 18,468 rows and always have been | Counting, after an exploration agent asserted they were populated |

### The resolver bug is worth keeping

`resolve_entity` blocked candidates on **exact** surname-key equality, where
the real resolver uses `fuzz.ratio(...) >= 80`. "kolhi" therefore never matched
"kohli", the bucket came out empty — and a `narrowed or rows` fallback then
scored the query against all 18,468 players and returned **"Rahat Ali" at
66.7**.

Two mistakes, and **the fallback is the worse one**: it converted "I found
nothing" into a confident wrong answer, which is precisely the failure mode
§10.4 exists to prevent. The fix removes the fallback entirely — an empty
bucket now returns no candidates — and extracts `surname_blocks()` so both
paths share one threshold instead of two copies of it.

This is the second time in the project that a *duplicated* rule diverged from
its original. It is the same shape as the `sslmode` duplication that broke CI
twice.

---

## 7. Standing rules

Rules 1–8 are in `docs/phase2-closeout.md`, 9 and 10 in
`docs/phase3-closeout.md`.

**11. Round-tripping is not sanitising. When you replace a string operation
with a structural one, check what the structure PRESERVES — the safer tool
can carry the payload through in a form the cruder one would have
destroyed.**

§10.3 says "Parse, don't regex — regex blocklists are trivially bypassed",
and that is correct. But `SELECT 1 -- ; DROP TABLE deliveries` parses to a
single clean `Select`, and sqlglot then regenerates it as
`SELECT 1 /* ; DROP TABLE deliveries */`. The comment is not part of the
query's semantics, so the parser treats it as trivia to be *preserved* rather
than content to be evaluated — and hands the payload straight through to the
database. A naive `"DROP" in sql.upper()` blocklist, the approach §10.3
explicitly discourages, would have refused it. By accident, for the wrong
reason, and while being bypassable a dozen other ways — but refused it.

**The inversion is the lesson.** It is easy to reason that the structural
tool is a superset of the textual one: it understands everything the string
check understood, plus grammar. It is not a superset. A parser partitions its
input into what it models and what it carries, and **the carried part is
invisible to every check written against the model.** Comments are the
obvious case; whitespace, optimiser hints, dialect-specific pragmas and
anything falling back to a catch-all node (sqlglot's `exp.Command`, which
round-trips unmodelled text verbatim) are the same category. This guard hit
two of them in one afternoon.

So the check is not "does the AST contain anything dangerous", it is **"is
everything in the output accounted for by the AST"**. Concretely: emit with
comments stripped, refuse anything the parser did not model, and diff what
went in against what comes out when you want to know which of those two you
have.

This one generalises past SQL. The same shape is an HTML sanitiser that
preserves comments or `<![CDATA[`, a YAML round-trip that keeps anchors, a
Markdown renderer that passes raw HTML through, a JSON parser that tolerates
duplicate keys and silently picks one. In each case the structural tool is
still the right choice — the fix is never to go back to regex — but choosing
it obliges you to ask what it decided was none of its business.

**Anyone implementing §10.3 from its own text would inherit this bug**, which
is why the section now records it as an as-built deviation rather than
leaving the guidance reading as though parsing alone were sufficient.

**12. When a measurement looks anomalous, rule out the instrument before
the subject. Specifically: verify encoding by codepoint, read out of the
database - never by reading a console.**

Third instance of this shape, which is why it is a rule and not a note. The
first was Phase 2's uniform-gap timing model, whose own assumptions made its
own result impossible. The second was a buffered log that made a running
process look killed. The third is this one.

Reading a competition name in a terminal, I saw `ICC Men?s T20 World Cup`
with a replacement character and reported mojibake in the corpus - a Phase 0
data defect that, if real in venues, would have split `venue_chase_win_rate`
silently inside the shipped model since Phase 1. It was not real. The
terminal was rendering UTF-8 through a non-UTF-8 codepage. Measured
properly, `U+FFFD` across `matches.competition`, `venues.name`, `venues.city`,
`teams.name` and `players.canonical_name` is **zero**, and the value is a
correctly encoded `U+2019 RIGHT SINGLE QUOTATION MARK`.

**The console is a lossy renderer and therefore an instrument, not a
window.** Any pipeline that re-encodes on the way to a display can
manufacture the exact defect you are looking for, and a mojibake hunt is
uniquely vulnerable because the artifact and the defect are the same glyph.
The check that settles it costs one query:

```sql
SELECT count(*) FILTER (WHERE col LIKE '%'||chr(65533)||'%') FROM t
```

and in Python, `ord()` per character or
`value.encode('ascii','backslashreplace')`, which prints `’` rather
than trying to draw it.

**The generalisation is not about encoding.** Before believing an anomaly,
ask what the measuring apparatus could have added: a console codepage, a
buffered stream, a timing model's own assumptions, a log shipper that
reorders, a float rendered through `extra_float_digits`. That last one is
already in this project, from Phase 2 session 3 - the same stored `float4`
read back as `1496.445` locally and `1496.44` through the pooler. The
difference between it and this one is only that it was a real defect; the
question that found it and the question that should have been asked here are
the same question.

**And the retraction has to be as loud as the claim was.** I reported the
defect before measuring it, and the user was prepared to treat it as a
model-affecting Phase 0 defect on my word. A wrong finding that reaches
someone's decisions costs more than the bug it imagined.

---

---

## 8. What is NOT verified

Standing rule 10: name what cannot be established here, and who can close it.

| Claim | Status |
|---|---|
| The guard's three Python layers | **Verified.** 99 tests, each layer non-vacuously proven |
| Layers 1 and 3 against a real database | **Verified**, against local PostgreSQL 17.6 with the full corpus |
| The four tools return correct answers | **Verified** end to end — Kohli 28,118 runs; Kohli vs Starc 225 balls, SR 104.89 |
| Uniform rejections | **Verified** — five different causes, byte-identical payloads differing only in `ref` |
| Auth fails closed | **Verified** — absent, wrong, placeholder and too-short all refused, on all five endpoints |
| **The same holds on Railway** | **NOT verified.** No Railway CLI on this machine and provisioning needs the account owner |
| **`agent_query_log` exists on Supabase** | **NOT verified.** The migration is written; it has not been pushed |

Both open items need the account owner. `replica.py --verify` is the command
that closes the first one, and it is written to be run against the real service
rather than described.
