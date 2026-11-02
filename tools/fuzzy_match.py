"""
Deterministic fuzzy matching for resolving a task's phrasing against site data.

Benchmark task scripts (generated/<site>/task_<id>/task_<id>.py) constantly have
to answer one question: of the candidates the API returned, which one did the
task mean? A product name against GET /fuzzy_search results, a project name
against GET /projects. This module answers that without an LLM call, with one
threshold and one set of semantics shared across every task.

    from tools.fuzzy_match import best_match, rank_matches, matches

    m = best_match("Hawaiian Bamboo Orchid Roots #zc50", results,
                   key=("name", "sku", "url"))
    if m is None:
        ...             # a real "not found" — see the note on None below
    log.info("matched via %s on %s (%.2f)", m.method, m.matched_on, m.score)
    item = m.item

The ladder, tried in order and reported through Match.method:

    exact                 normalized forms are equal                      1.00
    substring             query is a case-insensitive substring of text   0.95
    normalized_substring  whitespace-stripped query inside stripped text  0.90
    all_tokens            every query token appears in the text           0.85
    similarity            difflib SequenceMatcher ratio                   ratio

Ranking follows the rung, not the raw number: a difflib ratio is an unbounded
0.0-1.0 and often beats 0.85, but a candidate matched structurally always
outranks one that merely scored well. The score is the honest strength of the
match, and is what threshold compares against.

Two conventions worth knowing:

`None` means not found, and that is an answer.
    best_match returns None when nothing clears the threshold. Under the
    ERROR HANDLING contract in agent/baya_code_agent/codegen/run_claude_task.sh, that is a legitimate
    negative result to report, not a cue to lower the threshold or fall back to
    the nearest item.

`.method` / `.matched_on` / `.score` are safe to log; the matched value is not.
    A generated script's python_output.log is fed back into a later re-planning
    session, so it may not contain anything that came out of a successful
    response. These three fields are strategy names and a number, carrying no
    site content, so logging them is allowed — and they are the only evidence a
    replan has about how a match was reached. `.item` is site content: never log
    it.

Relationship to agent/baya_json_agent/execution_agent.py
    The `*=` JSONPath operator there (see `_matches`, in `_follow_accessor`)
    implements this same ladder inline for the planner/executor pipeline. The
    two are deliberately separate — this module exists to be importable by
    generated scripts, which cannot reach into the agent package's internals —
    so a behavioural change to either ladder should be mirrored in the other.
    This module additionally folds punctuation during normalization, which makes
    it strictly more permissive; every case the operator matches, it matches too.

Stdlib only, and pure: no network, no I/O, no configuration. Tests need no mocks.
"""

import re
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Tuple, Union

__all__ = [
    "DEFAULT_THRESHOLD",
    "Match",
    "normalize",
    "similarity",
    "score",
    "matches",
    "best_match",
    "rank_matches",
]

# Default acceptance threshold. Matches the value the `*=` operator in
# agent/baya_json_agent/execution_agent.py has used since it was introduced; changing it here
# changes every generated script at once, which is the point of this module.
DEFAULT_THRESHOLD = 0.6

# Scores for the non-similarity rungs, ordered so a Match.score reads as how
# strong the evidence was. They do NOT order the ranking on their own: a difflib
# ratio is an unbounded 0.0-1.0 and can easily exceed 0.85, so ranking goes by
# rung first (see _METHOD_RANK) and uses the score only to break ties within a
# rung. Keeping the raw ratio as the score is what preserves threshold parity
# with the `*=` operator in agent/baya_json_agent/execution_agent.py.
_SCORE_EXACT = 1.0
_SCORE_SUBSTRING = 0.95
_SCORE_NORMALIZED_SUBSTRING = 0.90
_SCORE_ALL_TOKENS = 0.85

# Ladder precedence, most specific first. A rung that fires is categorically
# better evidence than any later rung, however high the later rung scores:
# "Bamboo Orchid Deluxe Gift Set" matching every token of "bamboo orchid" beats
# "bamboo orchard" scraping a 0.93 ratio, and ranking has to reflect that.
_METHOD_RANK = {
    "exact": 0,
    "substring": 1,
    "normalized_substring": 2,
    "all_tokens": 3,
    "similarity": 4,
    "no_match": 5,
}

_NO_MATCH: Tuple[float, str] = (0.0, "no_match")

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# What key= may be: nothing (candidates are plain strings), one field name,
# several field names, or a callable that produces the text itself.
KeySpec = Union[None, str, Sequence[str], Callable[[Any], Any]]


class Match(NamedTuple):
    """One candidate that cleared the threshold.

    item       the original candidate object, unchanged — site content, do not log
    score      0.0 - 1.0
    method     which rung matched: exact | substring | normalized_substring
               | all_tokens | similarity
    matched_on which key produced the winning score (e.g. "sku"); "" when the
               candidate was matched as a plain string
    """

    item: Any
    score: float
    method: str
    matched_on: str


# ── Normalization ────────────────────────────────────────────────────────────

def normalize(text: Any, *, strip_punct: bool = True) -> str:
    """Lowercase, fold punctuation to spaces, and collapse whitespace.

    None-safe and type-safe: anything that is not a string is stringified, and
    None becomes "". Site data routinely delivers a missing name as None or a
    SKU as an int, and a matching helper that raised on those would be useless
    inside the loops it exists to serve.

        normalize("Hawaiian Bamboo Orchid Roots #zc50")
        'hawaiian bamboo orchid roots zc50'
    """
    if text is None:
        return ""
    lowered = str(text).lower()
    if strip_punct:
        lowered = _NON_ALNUM_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", lowered).strip()


def _nospace(text: Any, *, strip_punct: bool = True) -> str:
    """Normalized form with all whitespace removed ("deep learning" -> "deeplearning")."""
    return normalize(text, strip_punct=strip_punct).replace(" ", "")


def similarity(a: Any, b: Any) -> float:
    """difflib ratio (0.0 - 1.0) over the whitespace-stripped normalized forms.

    Comparing the stripped forms is what lets "future technology" score against
    "futurology" at all; comparing raw strings would be dominated by the spaces.
    """
    a_key = _nospace(a)
    b_key = _nospace(b)
    if not a_key or not b_key:
        return 0.0
    return SequenceMatcher(None, a_key, b_key).ratio()


# ── The ladder ───────────────────────────────────────────────────────────────

def score(query: Any, text: Any) -> Tuple[float, str]:
    """Return (score, method) for one query/text pair — the ladder, in order.

    An empty query scores 0.0 rather than matching everything. The bare
    substring rung would otherwise accept every candidate ("" is a substring of
    anything), which silently turns a missing search term into a confident wrong
    answer instead of a visible no-match.
    """
    query_norm = normalize(query)
    text_norm = normalize(text)
    if not query_norm or not text_norm:
        return _NO_MATCH

    if query_norm == text_norm:
        return (_SCORE_EXACT, "exact")

    # Substring on the space-preserving form, so a multi-word query still has to
    # appear as a contiguous phrase to earn this rung.
    if query_norm in text_norm:
        return (_SCORE_SUBSTRING, "substring")

    query_key = query_norm.replace(" ", "")
    text_key = text_norm.replace(" ", "")
    if query_key in text_key:
        return (_SCORE_NORMALIZED_SUBSTRING, "normalized_substring")

    # Every token present, in any order and anywhere — catches a query whose
    # words are split or reordered relative to the site's own phrasing.
    tokens = query_norm.split()
    if tokens and all(token in text_key for token in tokens):
        return (_SCORE_ALL_TOKENS, "all_tokens")

    ratio = SequenceMatcher(None, query_key, text_key).ratio()
    if ratio > 0.0:
        return (ratio, "similarity")
    return _NO_MATCH


def matches(query: Any, text: Any, *, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """True if text matches query by any rung of the ladder.

    The predicate form, for filtering a collection. Use best_match instead when
    the goal is to pick one winner out of candidates.
    """
    return score(query, text)[0] >= threshold


# ── Key resolution ───────────────────────────────────────────────────────────

def _field(candidate: Any, name: str) -> Any:
    """Read one field from a candidate — mapping key first, then attribute."""
    if isinstance(candidate, Mapping):
        return candidate.get(name)
    return getattr(candidate, name, None)


def _iter_texts(candidate: Any, key: KeySpec) -> Iterable[Tuple[str, Any]]:
    """Yield (matched_on, text) pairs to score this candidate against.

    key may be:
      None       the candidate is itself the text            -> matched_on ""
      "name"     one mapping key or attribute                 -> matched_on "name"
      ("a","b")  several, each scored, best one wins          -> matched_on "a"/"b"
      callable   returns a string, a mapping of name->string,
                 or a sequence of strings                     -> matched_on key/index
    """
    if key is None:
        yield ("", candidate)
        return

    if callable(key):
        produced = key(candidate)
        if isinstance(produced, Mapping):
            for name, text in produced.items():
                yield (str(name), text)
        elif isinstance(produced, (list, tuple)):
            for index, text in enumerate(produced):
                yield (str(index), text)
        else:
            yield ("", produced)
        return

    if isinstance(key, str):
        yield (key, _field(candidate, key))
        return

    for name in key:
        yield (str(name), _field(candidate, name))


# ── Selection ────────────────────────────────────────────────────────────────

def rank_matches(
    query: Any,
    candidates: Iterable[Any],
    *,
    key: KeySpec = None,
    threshold: float = DEFAULT_THRESHOLD,
    limit: Optional[int] = None,
) -> List[Match]:
    """Every candidate scoring at or above threshold, best first.

    Ordered by ladder rung first and by score only within a rung, so a candidate
    matched structurally always outranks one that merely scored a high difflib
    ratio. Ties keep the input order (the sort is stable), so the result is
    deterministic for a given candidate list — a task's answer must not drift
    between runs.

    One Match per candidate: when key names several fields, the candidate is
    scored on each and only its strongest field is reported.
    """
    scored: List[Match] = []
    for candidate in candidates:
        best: Optional[Tuple[int, float, str, str]] = None
        for matched_on, text in _iter_texts(candidate, key):
            value, method = score(query, text)
            ranked_by = (_METHOD_RANK[method], -value)
            if best is None or ranked_by < (best[0], -best[1]):
                best = (_METHOD_RANK[method], value, method, matched_on)
        if best is None:
            continue
        _, value, method, matched_on = best
        if value >= threshold:
            scored.append(Match(item=candidate, score=value, method=method, matched_on=matched_on))

    scored.sort(key=lambda m: (_METHOD_RANK[m.method], -m.score))
    return scored if limit is None else scored[:limit]


def best_match(
    query: Any,
    candidates: Iterable[Any],
    *,
    key: KeySpec = None,
    threshold: float = DEFAULT_THRESHOLD,
    ambiguity_margin: float = 0.0,
) -> Optional[Match]:
    """The single best candidate, or None if there isn't one.

    Returns None when nothing clears threshold — a legitimate "not found" to be
    reported as such, never a reason to retry with a lower threshold.

    ambiguity_margin > 0 also returns None when the top two candidates matched
    on the same rung and score within that margin of each other. Some queries
    genuinely have no single best answer ("future technology" scores identically
    against "futurology" and "technology"), and reporting the ambiguity beats
    picking whichever happened to sort first. The same-rung condition matters: a
    0.98 similarity does not make an exact match ambiguous, however close the
    two numbers look. Left at 0.0 the check is off and the top candidate always
    wins.
    """
    ranked = rank_matches(query, candidates, key=key, threshold=threshold)
    if not ranked:
        return None
    if ambiguity_margin > 0.0 and len(ranked) > 1:
        first, second = ranked[0], ranked[1]
        if first.method == second.method and (first.score - second.score) <= ambiguity_margin:
            return None
    return ranked[0]
