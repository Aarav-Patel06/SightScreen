"""The agent's SQL guard (SPEC.md §10.3).

Five layers. Two of them are the database's job and cannot be proven here:
the read-only role's grants, and the statement timeout. They are declared in
LAYERS with enforced_by="database" so that nobody can "cover" layer 1 with a
Python assertion that proves nothing about the grants.

The three Python layers are in this file. Each is a named function with its
own reason code, and each has a non-vacuity proof in
tests/agent/test_sql_guard_adversarial.py: a query that only it rejects,
shown rejected with everything on and admitted with that one layer off. The
`skip` parameter exists for those proofs and for nothing else - the served
path must never pass it, which is itself asserted by
test_the_production_entry_point_never_skips_a_layer.

Ordering matters. single_statement runs first, so that "SELECT 1; DROP TABLE
deliveries" is reported as batching rather than as a bad statement: the
batch is the thing that got past the caller's intent, and the DROP is only
its payload.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

DIALECT = "postgres"


@dataclass(frozen=True)
class Layer:
    """One defence. `enforced_by` records who actually stops the query."""

    name: str
    enforced_by: str
    description: str


LAYERS: tuple[Layer, ...] = (
    Layer(
        name="readonly_role",
        enforced_by="database",
        description=(
            "A LOGIN role holding SELECT on five views and nothing else. No "
            "grant on any base table, so a query naming one fails at the "
            "database even if every Python layer is removed."
        ),
    ),
    Layer(
        name="single_statement",
        enforced_by="python",
        description="Exactly one statement. No semicolon batching.",
    ),
    Layer(
        name="single_select",
        enforced_by="python",
        description=(
            "One SELECT expression, with no write, DDL, session-tampering or "
            "file-access node anywhere in its tree, reading only the views."
        ),
    ),
    Layer(
        name="statement_timeout",
        enforced_by="database",
        description="SET LOCAL statement_timeout = '5s' inside the transaction.",
    ),
    Layer(
        name="forced_limit",
        enforced_by="python",
        description="LIMIT 1000 appended, or clamped down to it.",
    ),
)


class GuardRejection(Exception):
    """A refusal. The caller sees PUBLIC_MESSAGE and nothing else.

    Decision 5: an error saying `relation "users" does not exist` is free
    reconnaissance, and a message that differs by cause is an oracle telling
    the caller which layer they tripped. `layer` and `reason` are for the
    server-side log only.
    """

    PUBLIC_MESSAGE = "query rejected"

    def __init__(self, layer: str, reason: str) -> None:
        super().__init__(reason)
        self.layer = layer
        self.reason = reason

    def public_message(self) -> str:
        return self.PUBLIC_MESSAGE


def _parse(sql: str) -> list[exp.Expression]:
    """Parse, and drop the nodes that are not statements.

    sqlglot represents a trailing comment as an exp.Semicolon statement and
    an empty one (the middle of "SELECT 1 ; ; SELECT 2") as None. Counting
    raw list length would reject "SELECT count(*) FROM agent_deliveries;",
    the most ordinary SQL anyone writes.
    """
    try:
        parsed = sqlglot.parse(sql, dialect=DIALECT)
    except Exception as err:  # sqlglot raises ParseError and TokenError
        raise GuardRejection("single_select", f"unparseable: {type(err).__name__}") from err

    statements = [s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)]
    if not statements:
        raise GuardRejection("single_select", "no statement")
    return statements


def _single_statement(statements: list[exp.Expression]) -> None:
    if len(statements) != 1:
        raise GuardRejection("single_statement", f"{len(statements)} statements")


# The five views of Decision 3. Nothing else is readable, and the guard says
# so before the database does, so a caller never learns which tables exist.
ALLOWED_RELATIONS = frozenset(
    {
        "agent_deliveries",
        "agent_matches",
        "agent_players",
        "agent_teams",
        "agent_venues",
    }
)

# Node types that must not appear ANYWHERE in the tree, not merely at the
# root. Postgres executes data-modifying CTEs, so "WITH x AS (INSERT ...)
# SELECT * FROM x" has a Select at the root and an Insert three levels down;
# that shape was verified against sqlglot rather than assumed.
#
# exp.Command is the parser's fallback for syntax it does not model - SET
# ROLE, RESET ROLE, ALTER ROLE, EXPLAIN ANALYZE all land there, and a Command
# node round-trips its raw text unchanged. Anything the parser did not
# understand is something the guard cannot reason about, so it is refused.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Comment,
    exp.Copy,
    exp.Set,
    exp.Command,
    exp.Into,  # SELECT ... INTO evil creates a table
    exp.Lock,  # FOR UPDATE takes write locks; a locking read is not a read
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)

# Layer 3 is the real answer to resource exhaustion and layer 1 to privilege;
# this list is defence in depth, and is a denylist rather than an allowlist
# because an allowlist of every legitimate aggregate would be wrong far more
# often than it was right.
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "generate_series",
        "exploding_generate_series",
        "repeat",
        "dblink",
        "dblink_connect",
        "query_to_xml",
        "set_config",
        "current_setting",
    }
)
FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_")


def _function_names(node: exp.Expression) -> set[str]:
    if isinstance(node, exp.Anonymous):
        return {node.name.lower()}
    names = {type(node).__name__.lower()}
    names.update(name.lower() for name in node.sql_names())
    return names


def _single_select(statement: exp.Expression) -> None:
    if not isinstance(statement, exp.Select):
        # A UNION arrives here as exp.Union, not exp.Select, and is refused.
        # That is a deliberate over-refusal: §10.3 says one SELECT expression,
        # and a set operation is two. Session 2 can relax it with evidence.
        raise GuardRejection("single_select", f"root is {type(statement).__name__}")

    for node in statement.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise GuardRejection("single_select", f"forbidden node {type(node).__name__}")

    for func in statement.find_all(exp.Func):
        for name in _function_names(func):
            if name in FORBIDDEN_FUNCTIONS or name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
                raise GuardRejection("single_select", f"forbidden function {name}")

    known = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    for table in statement.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            # FROM generate_series(...) is an exp.Table whose `this` is a
            # function call, not a name.
            raise GuardRejection("single_select", "relation is not a plain name")
        if table.db or table.catalog:
            # pg_catalog.pg_tables, information_schema.columns, public.deliveries
            raise GuardRejection("single_select", "qualified relation")
        if table.name.lower() not in ALLOWED_RELATIONS | known:
            raise GuardRejection("single_select", "relation not in the view allowlist")


MAX_ROWS = 1000


def _forced_limit(statement: exp.Expression, *, probe_extra_row: bool = False) -> exp.Expression:
    """Bound the result set, without overriding a caller who asked for less.

    Only touches a Select. Leaving anything else alone is what lets the
    single_select non-vacuity proof observe a SET statement surviving the
    rest of the pipeline when that one layer is disabled.

    `probe_extra_row` raises the ceiling to MAX_ROWS + 1 so the CALLER can
    tell a complete result of exactly MAX_ROWS from one the limit cut off.
    Counting rows cannot: both come back as 1000. That ambiguity is the
    dangerous half of layer 4 - a LIMIT that silently truncates a GROUP BY
    turns "how many players have done X" into a confident wrong total, the
    same failure shape as a fallback that narrows a query and returns the
    narrowed rows as though they answered the original question.

    The extra row is never returned; routes slice it off and set a flag. It
    exists only to be counted.
    """
    if not isinstance(statement, exp.Select):
        return statement

    ceiling = MAX_ROWS + 1 if probe_extra_row else MAX_ROWS
    limit = statement.args.get("limit")
    if limit is not None:
        try:
            requested = int(limit.expression.name)
        except (AttributeError, ValueError):
            # A non-literal LIMIT (a parameter, an expression) cannot be
            # compared, so it is replaced rather than trusted.
            return statement.limit(ceiling)
        if requested < MAX_ROWS:
            # The caller asked for less than the ceiling. That is their
            # limit, they know they set it, and probing it would make every
            # deliberate "top 10" report itself as truncated.
            return statement
        if requested == MAX_ROWS:
            # Written by the caller but identical to the guard's ceiling, so
            # it has the same silent-truncation problem and gets the same
            # probe. With probe_extra_row off this re-sets the same value
            # and emits the same SQL.
            return statement.limit(ceiling)
    return statement.limit(ceiling)


def check(
    sql: str, *, skip: frozenset[str] = frozenset(), probe_extra_row: bool = False
) -> str:
    """Return the SQL to execute, or raise GuardRejection.

    `skip` disables a named layer. Tests only - see the module docstring.

    `probe_extra_row` asks for one row beyond MAX_ROWS so the caller can
    report truncation rather than presenting a cut-off result as a total.
    It defaults to off, which keeps the emitted SQL byte-identical to what
    every existing test asserts; layer 4 still caps what is RETURNED at
    MAX_ROWS either way, because the route slices the probe row off.
    """
    statements = _parse(sql)

    if "single_statement" not in skip:
        _single_statement(statements)

    if "single_select" not in skip:
        for statement in statements:
            _single_select(statement)

    if "forced_limit" not in skip:
        statements = [
            _forced_limit(statement, probe_extra_row=probe_extra_row)
            for statement in statements
        ]

    return "; ".join(s.sql(dialect=DIALECT, comments=False) for s in statements)
