"""Tests for tools.fuzzy_match.

Pure and offline — the module under test has no I/O, so nothing here is mocked.
"""

import pytest

from tools.fuzzy_match import (
    DEFAULT_THRESHOLD,
    Match,
    best_match,
    matches,
    normalize,
    rank_matches,
    score,
    similarity,
)


# ── normalize / similarity ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Hawaiian Bamboo Orchid Roots #zc50", "hawaiian bamboo orchid roots zc50"),
        ("  Mixed   CASE\twith\nwhitespace ", "mixed case with whitespace"),
        ("dotted.name_and-dashes", "dotted name and dashes"),
        ("", ""),
        (None, ""),
        (42, "42"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_normalize_can_keep_punctuation():
    assert normalize("a-b.c", strip_punct=False) == "a-b.c"


def test_similarity_is_bounded_and_symmetric_on_identity():
    assert similarity("orchid", "orchid") == 1.0
    assert 0.0 < similarity("futuretechnology", "futurology") < 1.0
    assert similarity("", "anything") == 0.0
    assert similarity(None, "anything") == 0.0


# ── the ladder: each rung fires, and reports itself ──────────────────────────

@pytest.mark.parametrize(
    "query, text, expected_method, expected_score",
    [
        ("Bamboo Orchid", "bamboo orchid", "exact", 1.0),
        ("orchid roots", "Hawaiian Bamboo Orchid Roots", "substring", 0.95),
        ("deep learning", "deeplearning", "normalized_substring", 0.90),
        ("orchid hawaiian", "Hawaiian Bamboo Orchid Roots", "all_tokens", 0.85),
    ],
)
def test_fixed_score_rungs(query, text, expected_method, expected_score):
    value, method = score(query, text)
    assert method == expected_method
    assert value == pytest.approx(expected_score)


def test_similarity_rung_reports_the_ratio():
    value, method = score("future technology", "futurology")
    assert method == "similarity"
    assert value == pytest.approx(similarity("future technology", "futurology"))


def test_rungs_are_tried_most_specific_first():
    """A candidate matching an earlier rung must not be reported as a later one.

    "orchid roots" is a contiguous substring AND has all its tokens present; the
    ladder must call it a substring, because that ordering is what makes the
    scores rankable.
    """
    assert score("orchid roots", "Hawaiian Bamboo Orchid Roots")[1] == "substring"
    assert score("Bamboo Orchid", "bamboo orchid")[1] == "exact"


def test_fixed_rung_scores_descend_with_specificity():
    ladder = [
        score("bamboo orchid", "bamboo orchid")[0],
        score("orchid roots", "Hawaiian Bamboo Orchid Roots")[0],
        score("deep learning", "deeplearning")[0],
        score("orchid hawaiian", "Hawaiian Bamboo Orchid Roots")[0],
    ]
    assert ladder == sorted(ladder, reverse=True)


def test_a_high_ratio_never_outranks_a_structural_match():
    """A difflib ratio is unbounded within 0..1 and routinely beats 0.85, so
    ranking cannot go by score alone — the rung has to come first.

    "bamboo orchard" is a near-miss scoring 0.88, above the 0.85 all_tokens
    rung; the plant, which contains every query token, is what the task meant.
    """
    structural = "Orchid Plant, Bamboo Variety"   # every token, not contiguous
    near_miss = "bamboo orchard"                  # one letter off, high ratio
    assert score("bamboo orchid", near_miss)[0] > score("bamboo orchid", structural)[0]

    ranked = rank_matches("bamboo orchid", [near_miss, structural])
    assert ranked[0].item == structural
    assert ranked[0].method == "all_tokens"
    assert ranked[1].method == "similarity"
    assert best_match("bamboo orchid", [near_miss, structural]).item == structural


# ── parity with agent/baya_json_agent/execution_agent.py's `*=` operator ─────────────────────

@pytest.mark.parametrize(
    "query, text",
    [
        # The two cases the operator documents in its own comments.
        ("deep learning", "deeplearning"),
        ("future technology", "futurology"),
        # Its first three rungs, which must keep matching here.
        ("orchid", "Hawaiian Bamboo Orchid Roots"),
        ("bamboo orchid", "bambooorchid"),
        ("hawaiian orchid", "Hawaiian Bamboo Orchid Roots"),
    ],
)
def test_matches_the_execution_agent_ladder(query, text):
    assert matches(query, text)


def test_unrelated_strings_do_not_match():
    assert not matches("orchid roots", "stainless steel watch")
    assert not matches("gitlab", "kubernetes")


# ── degenerate input is never fatal ──────────────────────────────────────────

@pytest.mark.parametrize(
    "query, text",
    [("", "anything"), ("anything", ""), (None, "anything"), ("anything", None), (None, None)],
)
def test_empty_and_none_score_zero_without_raising(query, text):
    assert score(query, text) == (0.0, "no_match")
    assert not matches(query, text)


def test_empty_query_does_not_match_everything():
    """The bare substring rung would accept every candidate for an empty query.

    Guarding it keeps a missing search term a visible no-match rather than a
    confident wrong answer.
    """
    assert best_match("", ["anything", "at", "all"]) is None


def test_non_string_and_missing_fields_are_tolerated():
    candidates = [
        {"name": None, "sku": 12345},
        {"name": "Bamboo Orchid"},          # "sku" absent entirely
        {"name": "", "sku": ""},
    ]
    found = best_match("bamboo orchid", candidates, key=("name", "sku"))
    assert found is not None
    assert found.item["name"] == "Bamboo Orchid"


# ── key= resolution, all four forms ──────────────────────────────────────────

def test_key_none_matches_plain_strings():
    found = best_match("futurology", ["technology", "futurology"])
    assert found.item == "futurology"
    assert found.matched_on == ""


def test_key_single_field_name():
    found = best_match("bamboo orchid", [{"name": "Bamboo Orchid", "sku": "zc50"}], key="name")
    assert found.matched_on == "name"


def test_key_tuple_scores_every_field_and_reports_the_winner():
    """The multi-field case task_466 hand-rolled: name/sku/url in one call."""
    candidates = [
        {"name": "Orchid Fertilizer", "sku": "ab11", "url": "orchid-fertilizer.html"},
        {"name": "Hawaiian Bamboo Orchid Roots", "sku": "zc50", "url": "bamboo-orchid.html"},
    ]
    found = best_match("zc50", candidates, key=("name", "sku", "url"))
    assert found.item["sku"] == "zc50"
    assert found.matched_on == "sku"
    assert found.method == "exact"


def test_key_callable_returning_a_string():
    candidates = [{"first": "Bamboo", "second": "Orchid"}]
    found = best_match(
        "bamboo orchid", candidates, key=lambda c: f"{c['first']} {c['second']}"
    )
    assert found.method == "exact"
    assert found.matched_on == ""


def test_key_callable_returning_a_mapping_names_the_winning_field():
    candidates = [{"title": "Roots", "code": "zc50"}]
    found = best_match("zc50", candidates, key=lambda c: {"title": c["title"], "code": c["code"]})
    assert found.matched_on == "code"


def test_key_callable_returning_a_sequence_uses_the_index():
    found = best_match("zc50", [{"a": "Roots", "b": "zc50"}], key=lambda c: [c["a"], c["b"]])
    assert found.matched_on == "1"


def test_key_reads_attributes_when_the_candidate_is_not_a_mapping():
    class Product:
        def __init__(self, name):
            self.name = name

    found = best_match("bamboo orchid", [Product("Bamboo Orchid")], key="name")
    assert found.item.name == "Bamboo Orchid"


# ── rank_matches ─────────────────────────────────────────────────────────────

def test_rank_matches_orders_best_first_and_filters_by_threshold():
    candidates = ["bamboo orchid", "Hawaiian Bamboo Orchid Roots", "stainless steel watch"]
    ranked = rank_matches("bamboo orchid", candidates)
    assert [m.item for m in ranked] == ["bamboo orchid", "Hawaiian Bamboo Orchid Roots"]
    assert ranked[0].score > ranked[1].score
    assert all(m.score >= DEFAULT_THRESHOLD for m in ranked)


def test_rank_matches_limit():
    candidates = ["bamboo orchid", "Hawaiian Bamboo Orchid Roots", "bamboo orchid plant"]
    assert len(rank_matches("bamboo orchid", candidates, limit=2)) == 2


def test_rank_matches_returns_one_match_per_candidate():
    """Several matching fields on one candidate must not produce several rows."""
    candidates = [{"name": "bamboo orchid", "alias": "bamboo orchid"}]
    assert len(rank_matches("bamboo orchid", candidates, key=("name", "alias"))) == 1


def test_rank_matches_is_stable_for_equal_scores():
    """Deterministic output matters: the same candidate list must always rank the
    same way, so a task's answer does not drift between runs."""
    candidates = ["futurology", "technology"]
    first = [m.item for m in rank_matches("future technology", candidates)]
    assert first == ["futurology", "technology"]
    assert [m.item for m in rank_matches("future technology", candidates)] == first


def test_rank_matches_on_empty_candidates():
    assert rank_matches("anything", []) == []


# ── best_match: None is an answer, and near-ties ─────────────────────────────

def test_best_match_returns_none_below_threshold():
    assert best_match("orchid roots", ["stainless steel watch", "yoga mat"]) is None


def test_best_match_honours_a_raised_threshold():
    candidates = ["Hawaiian Bamboo Orchid Roots"]
    assert best_match("bamboo orchid", candidates, threshold=0.6) is not None
    assert best_match("bamboo orchid", candidates, threshold=0.99) is None


def test_ambiguity_margin_rejects_a_near_tie():
    """api/servers/reddit.py hand-writes a prefix tiebreak for exactly this:
    "future technology" scores identically against both forums."""
    forums = ["futurology", "technology"]
    ranked = rank_matches("future technology", forums)
    assert ranked[0].score == pytest.approx(ranked[1].score)

    assert best_match("future technology", forums) == ranked[0]        # margin off
    assert best_match("future technology", forums, ambiguity_margin=0.1) is None


def test_ambiguity_margin_only_fires_within_one_rung():
    """A close number across two rungs is not ambiguity: an exact match wins
    outright even when a similarity match scores almost as high."""
    candidates = ["zc50", "zc5o"]           # exact 1.0 vs similarity 0.75
    found = best_match("zc50", candidates, ambiguity_margin=0.5)
    assert found is not None
    assert found.item == "zc50"
    assert found.method == "exact"


def test_ambiguity_margin_allows_a_clear_winner():
    candidates = ["bamboo orchid", "stainless steel watch"]
    found = best_match("bamboo orchid", candidates, ambiguity_margin=0.1)
    assert found.item == "bamboo orchid"


def test_ambiguity_margin_with_a_single_candidate():
    found = best_match("bamboo orchid", ["bamboo orchid"], ambiguity_margin=0.5)
    assert found is not None


# ── Match shape ──────────────────────────────────────────────────────────────

def test_match_carries_the_original_candidate_unchanged():
    candidate = {"name": "Bamboo Orchid", "sku": "zc50", "extra": {"nested": True}}
    found = best_match("bamboo orchid", [candidate], key="name")
    assert isinstance(found, Match)
    assert found.item is candidate
