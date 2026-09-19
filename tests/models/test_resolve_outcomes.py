"""Outcome resolution correctness (Phase 3 session 1).

The failure these guard against is not a crash. It is a `prediction_outcomes`
table full of plausible rows where some labels are wrong, feeding a
reliability diagram that renders beautifully and misleads. Every test here
is about a prediction that must NOT be resolved, or a label that must come
from the one place it is computed.

Run against the real corpus deliberately: ties, no-results and flagged
matches are real rows with real shapes, and a synthetic fixture would encode
my assumption about them rather than the fact.
"""

from __future__ import annotations

import os

import numpy as np
import psycopg
import pytest
from dotenv import dotenv_values

from eval.metrics import brier_score, log_loss
from eval.splits import SECOND_INNINGS_CLAUSES, second_innings_labels, second_innings_predicate
from models.resolve_outcomes import _metrics

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_PATH = os.path.join(REPO_ROOT, "api", ".env")

# Real matches, found by querying the corpus for each exclusion category.
TIED_MATCH = 123
NO_RESULT_MATCH = 40
ANOMALY_MATCH = 834
NORMAL_MATCH = 9337


@pytest.fixture(scope="module")
def corpus():
    url = os.environ.get("LOCAL_DATABASE_URL") or dotenv_values(ENV_PATH).get(
        "LOCAL_DATABASE_URL"
    )
    if not url:
        pytest.skip("LOCAL_DATABASE_URL not set")
    with psycopg.connect(url, connect_timeout=20) as conn:
        yield conn


def _innings_two_ball_count(corpus, match_id: int) -> int:
    with corpus.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM match_states WHERE match_id = %s AND innings = 2",
            (match_id,),
        )
        return cur.fetchone()[0]


@pytest.mark.parametrize(
    "match_id,category",
    [(TIED_MATCH, "tie"), (NO_RESULT_MATCH, "no_result"), (ANOMALY_MATCH, "anomaly")],
)
def test_excluded_matches_yield_no_labels_at_all(corpus, match_id, category):
    """A prediction on one of these must stay unresolved forever.

    The tie case is the one worth stating: `matches.winner` is NULL for a tie
    AND for a no-result, so code that resolved the label itself by comparing
    winner to the batting team would quietly record BOTH as a loss for the
    chasing side - a wrong label, on a real match, with nothing to flag it.
    """
    assert _innings_two_ball_count(corpus, match_id) > 0, (
        f"match {match_id} has no innings-2 rows, so this test proves nothing "
        f"about {category} exclusion"
    )
    assert second_innings_labels(corpus, [match_id]) == {}


def test_a_normal_match_labels_every_includable_ball(corpus):
    labels = second_innings_labels(corpus, [NORMAL_MATCH])
    assert labels, "the positive control returned nothing - the query is broken"
    with corpus.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM match_states ms WHERE ms.match_id = %s "
            f"AND {second_innings_predicate('ms')}",
            (NORMAL_MATCH,),
        )
        includable = cur.fetchone()[0]
    assert len(labels) == includable
    # One match, one outcome: every ball of a chase shares the match's result.
    assert len(set(labels.values())) == 1


def test_the_label_is_the_corpus_label_not_a_derivation(corpus):
    """Cross-check against `matches.winner` the long way round.

    If these two ever disagree, the corpus label is right and whatever
    derived the other one is wrong - this test exists so the disagreement
    surfaces here rather than inside a Brier score.
    """
    labels = second_innings_labels(corpus, [NORMAL_MATCH])
    with corpus.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT (m.winner = d.batting_team_id)::int
            FROM matches m
            JOIN deliveries d ON d.match_id = m.match_id AND d.innings = 2
            WHERE m.match_id = %s
            """,
            (NORMAL_MATCH,),
        )
        derived = {row[0] for row in cur.fetchall()}
    assert derived == set(labels.values())


def test_mixed_batch_resolves_only_the_resolvable(corpus):
    """The realistic case: a manifest that happens to include a tie."""
    labels = second_innings_labels(corpus, [NORMAL_MATCH, TIED_MATCH, NO_RESULT_MATCH])
    assert {match_id for match_id, _i, _o, _b in labels} == {NORMAL_MATCH}


def test_keys_are_the_ball_key_predictions_carry(corpus):
    """The join between a logged prediction and its label is
    (match_id, innings, over_num, ball_in_over) - if these drift apart,
    every prediction silently fails to resolve."""
    labels = second_innings_labels(corpus, [NORMAL_MATCH])
    with corpus.cursor() as cur:
        cur.execute(
            f"""
            SELECT ms.match_id, ms.innings, d.over_num, d.ball_in_over
            FROM match_states ms
            JOIN deliveries d ON d.delivery_id = ms.delivery_id
            WHERE ms.match_id = %s AND {second_innings_predicate('ms')}
            """,
            (NORMAL_MATCH,),
        )
        expected = {tuple(row) for row in cur.fetchall()}
    assert set(labels) == expected
    assert len(labels) == len(expected), "duplicate ball keys within one match"


@pytest.mark.parametrize("probability", [0.0001, 0.25, 0.5, 0.7378, 0.9999])
@pytest.mark.parametrize("label", [0, 1])
def test_per_row_metrics_match_the_array_functions(probability, label):
    """resolve_outcomes calls eval/metrics.py on one element rather than
    inlining the arithmetic. This pins that the shortcut is exact, including
    log_loss's epsilon clipping, which an inlined copy would drift from the
    moment anyone tuned it."""
    y = np.array([label], dtype=np.int8)
    p = np.array([probability], dtype=np.float64)
    assert _metrics(probability, label) == (brier_score(y, p), log_loss(y, p))


def test_the_exclusion_predicate_has_one_definition():
    """splits.py is section 9.1's only sanctioned home for this. The clause
    list was copied by hand into five other places before session 1; the
    fragment exists so the sixth copy is an import."""
    assert set(SECOND_INNINGS_CLAUSES) == {
        "innings = 2",
        "batting_team_won IS NOT NULL",
        "NOT has_reconciliation_anomaly",
        "required_run_rate IS NOT NULL",
    }
    aliased = second_innings_predicate("ms")
    # One alias per clause, including the negated one - the template form
    # exists so a NOT clause is aliased like any other.
    assert aliased.count("ms.") == len(SECOND_INNINGS_CLAUSES)
    assert "NOT ms.has_reconciliation_anomaly" in aliased
