"""Turn a spoken name into the forms a Cricsheet corpus actually stores.

`resolve_entity`'s scoring was measured failing on the common case: a user
types a full name, and fuzzy similarity cannot separate the right player from
his namesakes.

    "Virat Kohli"      -> A Kohli 83.3, S Kohli 83.3, T Kohli 83.3,
                          V Kohli 83.3        four-way tie
    "Lasith Malinga"   -> E Malinga 87.5, T Malinga 87.5,
                          SL Malinga 82.4     wrong ones rank HIGHER
    "Yashasvi Jaiswal" -> AV Jaiswal 82.4, YBK Jaiswal 77.8   same

`fuzz.ratio` compares "Virat Kohli" against "A Kohli" and "V Kohli" and sees
almost the same edit distance, because one character of difference in a
short token is swamped by the surname both share. Weighting the initial
would make the score less wrong. Generating the form makes similarity
irrelevant for the whole class, which is the same move as
registry-id-is-terminal: decide deterministically where you can, and leave
heuristics only the remainder.

**The obvious version of that does not work, and it is worth saying why.**
"Virat Kohli" -> "V Kohli" is a clean transformation and matches exactly. But
Cricsheet stores `SL Malinga` and `YBK Jaiswal` - initials for given names
the speaker never said, because Separamadu Lasith Malinga and Yashasvi
Bhupendra Kumar Jaiswal have more names than anyone uses. Generating
"L Malinga" and exact-matching finds nothing, so exact-match-only would fix
Kohli and leave the other two exactly as broken.

So the rule is a SUBSEQUENCE one: the initials of what the user said must
appear in order within the stored initials.

    said "Lasith"    -> L     stored "SL"  -> L is in S,L        MATCH
    said "Yashasvi"  -> Y     stored "YBK" -> Y is in Y,B,K      MATCH
    said "Yashasvi"  -> Y     stored "AV"  -> no Y               no
    said "Virat"     -> V     stored "A"                         no

Still deterministic - an order-preserving membership test, not a distance.
It narrows "Lasith Malinga" from four candidates to two (SL and LN Malinga,
both of whom really do have an L), which is the honest answer: those are
genuinely ambiguous on initials, and the agent should ask rather than guess.

Where the stored given name is a WORD rather than initials ("Vivek Kohli",
"Parth Kohli"), it is compared as a word, because "Vivek" and "Virat" are
different names and an initial test would wrongly keep both.
"""

from __future__ import annotations

import re

# Lowercase particles that belong to the SURNAME, not the given names.
# "AB de Villiers" is stored with the particle, so the surname is
# "de Villiers" and treating "de" as a given name would generate "A d
# Villiers" and match nothing. Verified against the corpus before being
# relied on - see tests/agent/test_name_forms.py.
SURNAME_PARTICLES = frozenset(
    {"de", "del", "della", "der", "den", "di", "du", "la", "le", "van", "von", "bin", "ibn", "ten", "ter", "al"}
)

# A token that is already an initials cluster: "V", "AB", "YBK", "MS".
_INITIALS = re.compile(r"^[A-Z]{1,4}$")


def split_name(name: str) -> tuple[list[str], str]:
    """(given tokens, surname). The surname absorbs trailing particles."""
    tokens = [t for t in (name or "").split() if t]
    if len(tokens) < 2:
        return [], " ".join(tokens)
    start = len(tokens) - 1
    # Walk left over particles, but never consume the whole name.
    while start - 1 >= 1 and tokens[start - 1].lower() in SURNAME_PARTICLES:
        start -= 1
    return tokens[:start], " ".join(tokens[start:])


def initials_of(given: list[str]) -> str:
    """The initials a stored name would carry for these given tokens.

    A token that is ALREADY initials contributes all its letters: "AB de
    Villiers" must yield "AB", not "A", or the generated form would not be
    the one stored.
    """
    out = []
    for token in given:
        cleaned = token.strip(".")
        if _INITIALS.match(cleaned):
            out.append(cleaned)
        elif cleaned:
            out.append(cleaned[0].upper())
    return "".join(out)


def canonical_forms(name: str) -> list[str]:
    """Stored forms this name could plausibly be, most specific first.

    "Mahendra Singh Dhoni" -> ["MS Dhoni", "M Dhoni"]
    "Virat Kohli"          -> ["V Kohli"]
    "AB de Villiers"       -> ["AB de Villiers", "A de Villiers"]
    """
    given, surname = split_name(name)
    if not given or not surname:
        return []
    initials = initials_of(given)
    if not initials:
        return []
    forms = [f"{initials} {surname}"]
    if len(initials) > 1:
        forms.append(f"{initials[0]} {surname}")
    # Deduplicate, keep order.
    return list(dict.fromkeys(forms))


def _is_subsequence(needle: str, haystack: str) -> bool:
    """Every letter of `needle`, in order, somewhere in `haystack`."""
    it = iter(haystack)
    return all(ch in it for ch in needle)


def initials_compatible(spoken: str, stored: str) -> bool:
    """Could `stored` be the corpus's spelling of `spoken`?

    Deterministic. Surnames must match exactly (after casefolding); the given
    part is compared as initials when the stored form uses initials, and as
    words when it does not.
    """
    spoken_given, spoken_surname = split_name(spoken)
    stored_given, stored_surname = split_name(stored)
    if not spoken_surname or not stored_surname:
        return False
    if spoken_surname.casefold() != stored_surname.casefold():
        return False
    if not spoken_given or not stored_given:
        # One side is a bare surname: the surname alone is all the evidence
        # there is, and it matched. Genuinely ambiguous, and saying so is
        # the point - this is what makes the agent ask.
        return True

    stored_is_initials = all(_INITIALS.match(t.strip(".")) for t in stored_given)
    if stored_is_initials:
        return _is_subsequence(initials_of(spoken_given), initials_of(stored_given))

    # Stored a full word: compare words, so "Virat" does not match "Vivek".
    spoken_words = [t.casefold() for t in spoken_given if not _INITIALS.match(t.strip("."))]
    stored_words = [t.casefold() for t in stored_given]
    if not spoken_words:
        # User gave initials, corpus gave words: compare the initials.
        return _is_subsequence(initials_of(spoken_given), initials_of(stored_given))
    return all(w in stored_words for w in spoken_words)


def narrow(spoken: str, candidates: list[str]) -> list[str]:
    """The candidates deterministically compatible with what was said.

    Returns [] when nothing is compatible, and the CALLER falls back to fuzzy
    scoring - deliberately, so an unusual spelling still resolves. Returning
    everything on no match would put this back where it started.
    """
    return [c for c in candidates if initials_compatible(spoken, c)]
