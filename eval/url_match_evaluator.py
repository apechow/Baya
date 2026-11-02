# url_match_evaluator.py
#
# Evaluates url_match tasks from WebArena task definitions.
#
# A url_match task passes when the agent's final_url satisfies the
# reference_url condition defined in the task eval spec.
#
# Three matching modes, determined by the url_note and reference_url fields:
#
#   Mode              | url_note       | reference_url        | Condition
#   ------------------+----------------+----------------------+------------------
#   GOLD in PRED      | "GOLD in PRED" | single URL           | expected in actual
#   exact / prefix    | ""             | single URL           | actual == expected
#                     |                |                      | OR actual starts with expected
#   OR alternatives   | any            | "URL1 |OR| URL2 ..." | any alternative matches
#
# Only the URL is graded here, but the result also REPORTS the task's other
# expectations — string_match reference_answers and program_html
# required_contents — under "expected_answers", alongside the agent's raw answer
# under "actual_answer". Most url_match tasks are graded on a second eval type
# too, so a report that shows only the URL comparison hides half of what the
# answer was measured against.
#
# Usage:
#   from url_match_evaluator import UrlMatchEvaluator
#
#   evaluator = UrlMatchEvaluator()
#   result = evaluator.evaluate(task, actual_url="http://...")
#   result = evaluator.evaluate(task, normalized_url, actual_answer=raw_answer)
#
# Run tests:
#   python3 -m pytest tests/test_url_match_evaluator.py -v

import re
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from eval.program_html_evaluator import DEFAULT_BASE_URLS

_OR_SEPARATOR = " |OR| "

# Matches an http(s) base URL (scheme://host[:port]) up to — but not
# including — the path, query, whitespace, or a closing quote/paren.
# Used to strip the base so that base-only differences (e.g. localhost
# vs 127.0.0.1, or a random forwarded port like :20016 vs :8023) don't
# cause an otherwise-matching URL to be reported as a mismatch.
_BASE_URL_RE = re.compile(r"https?://[^/\s\"'()]+")

# Matches a "/" that sits immediately before the query string ("?") or the end
# of the URL — an empty trailing path segment. Removing it makes e.g.
# "/-/issues/?sort=x" and "/-/issues?sort=x" (the same page) compare equal.
_TRAILING_SLASH_RE = re.compile(r"/(?=\?|$)")


class UrlMatchEvaluator:
    """
    Evaluates the url_match component of a WebArena task.

    For each task, it:
      1. Extracts reference_url from the eval spec
      2. Splits on |OR| to get the list of acceptable URLs
      3. Substitutes __PLACEHOLDER__ tokens with real base URLs
      4. Checks actual_url against all alternatives using the match mode
         determined by url_note ("GOLD in PRED" vs exact/prefix)

    Returns a structured result dict — never raises.
    """

    def __init__(self, base_urls: Optional[Dict[str, str]] = None):
        self.base_urls = base_urls if base_urls is not None else DEFAULT_BASE_URLS

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def evaluate(
        self,
        task: Dict[str, Any],
        actual_url: Optional[str],
        actual_answer: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate the url_match section of a task.

        Args:
            task:          Full task dict (must contain task["eval"]).
            actual_url:    The final URL the agent navigated to. May be None.
            actual_answer: The agent's raw, un-normalized answer, when the caller
                           has it (run_url_match_eval.py rewrites the answer's
                           host before passing it as actual_url). Defaults to
                           actual_url so single-argument callers keep working.

        Returns:
            {
                "applicable":       bool   # False when eval_type is not url_match
                "passed":           bool
                "eval_types":       list   # Every eval type on the task
                "expected_urls":    list   # Resolved alternatives (after |OR| + placeholder sub)
                "actual_url":       str | None
                "match_mode":       str    # "gold_in_pred" | "exact_or_prefix"
                "matched_url":      str | None  # Which expected_url matched, if any
                "actual_answer":    str | None  # What the agent actually answered
                "expected_answers": dict   # Non-url expectations: string_match
                                           # reference answers and/or program_html
                                           # required_contents (may be empty)
                "error":            str | None
            }

        `passed` still reflects the URL comparison alone — the string_match /
        program_html expectations are reported, not graded, here.
        """
        eval_section = task.get("eval", {})
        eval_types = eval_section.get("eval_types", [])

        # Carried on every return path so a report can always show what the task
        # expected on each axis, not only the ones that got as far as matching.
        common = {
            "eval_types": list(eval_types),
            "actual_answer": actual_answer if actual_answer is not None else actual_url,
            "expected_answers": self._expected_answers(eval_section),
        }

        if "url_match" not in eval_types:
            return self._result(
                applicable=False,
                passed=False,
                expected_urls=[],
                actual_url=actual_url,
                match_mode="",
                error="eval_types does not include url_match",
                **common,
            )

        raw_reference = eval_section.get("reference_url", "") or ""
        if not raw_reference:
            return self._result(
                applicable=True,
                passed=False,
                expected_urls=[],
                actual_url=actual_url,
                match_mode="",
                error="reference_url is empty",
                **common,
            )

        # Split |OR| alternatives and resolve placeholders in each
        alternatives = [
            self._resolve_placeholder(alt.strip())
            for alt in raw_reference.split(_OR_SEPARATOR)
            if alt.strip()
        ]

        url_note = eval_section.get("url_note", "") or ""
        match_mode = "gold_in_pred" if url_note == "GOLD in PRED" else "exact_or_prefix"

        if not actual_url:
            return self._result(
                applicable=True,
                passed=False,
                expected_urls=alternatives,
                actual_url=actual_url,
                match_mode=match_mode,
                error="actual_url is None or empty — agent did not return a final_url",
                **common,
            )

        # Check each alternative; pass on first match
        for expected in alternatives:
            if self._matches(actual_url, expected, match_mode):
                return self._result(
                    applicable=True,
                    passed=True,
                    expected_urls=alternatives,
                    actual_url=actual_url,
                    match_mode=match_mode,
                    matched_url=expected,
                    **common,
                )

        return self._result(
            applicable=True,
            passed=False,
            expected_urls=alternatives,
            actual_url=actual_url,
            match_mode=match_mode,
            **common,
        )

    # ------------------------------------------------------------------
    # Matching logic
    # ------------------------------------------------------------------

    def _matches(self, actual: str, expected: str, mode: str) -> bool:
        """Return True if actual satisfies expected under the given mode.

        Both sides are percent-decoded first so that URL-encoding differences
        alone don't cause a mismatch — e.g. ``label_name%5B%5D=flaky-test``
        (browser form) and ``label_name[]=flaky-test`` (unencoded) compare equal.

        The base URL (scheme://host:port) is then stripped from both sides so
        that base-only differences don't cause a mismatch — e.g. the agent
        reporting ``http://localhost:8023/foo`` while the gold URL is
        ``http://127.0.0.1:8023/foo``, or a forwarded docker port appearing in
        place of the canonical one. Only the path/query is compared. This is
        site-agnostic: gitlab, shopping, and any other __<SITE>__ placeholder
        are resolved via ``base_urls`` before comparison.

        Surrounding whitespace is stripped from both sides so that a trailing
        newline after the URL — the agent (or the reference_url) ending the
        value with ``\\n`` — doesn't cause an otherwise-matching URL to be
        reported as a mismatch.

        A slash sitting immediately before the query string or the end of the
        URL (an empty trailing path segment) is also collapsed, so that
        ``/-/issues/?sort=..`` and ``/-/issues?sort=..`` — the same GitLab page
        — compare equal.
        """
        actual = self._strip_trailing_slash(self._strip_base(unquote(actual)).strip())
        expected = self._strip_trailing_slash(self._strip_base(unquote(expected)).strip())
        if mode == "gold_in_pred":
            # Expected URL must appear as a substring of actual.
            # Use case-insensitive comparison for URL paths (e.g. /f/DIY vs /f/diy).
            return expected.lower() in actual.lower()
        else:
            # Exact equality OR actual begins with expected
            # (handles trailing query params or fragment the agent appended)
            return actual == expected or actual.startswith(expected)

    # ------------------------------------------------------------------
    # Non-url expectations (string_match / program_html)
    # ------------------------------------------------------------------

    @staticmethod
    def _expected_answers(eval_section: Dict[str, Any]) -> Dict[str, Any]:
        """The task's non-url expectations, keyed by eval type.

        A url_match task is usually graded on a second eval type as well
        (program_html across most of the WebArena set, occasionally
        string_match), so the URL alone does not tell the whole story of what
        the answer was supposed to be. Collect those expectations here so a
        report can show them next to the URL ones. Returns {} for a task that
        is url_match only.
        """
        expected: Dict[str, Any] = {}

        ra = eval_section.get("reference_answers") or {}
        string_expected = {
            k: ra[k]
            for k in ("exact_match", "fuzzy_match", "must_include", "must_exclude")
            if ra.get(k) is not None
        }
        if string_expected:
            expected["string_match"] = string_expected

        checks = [
            {
                "url": chk.get("url", ""),
                "locator": chk.get("locator", ""),
                "required_contents": chk.get("required_contents") or {},
            }
            for chk in (eval_section.get("program_html") or [])
        ]
        if checks:
            expected["program_html"] = checks

        return expected

    # ------------------------------------------------------------------
    # Placeholder substitution
    # ------------------------------------------------------------------

    def _resolve_placeholder(self, url: str) -> str:
        """Replace __PLACEHOLDER__ tokens with real base URLs."""
        for token, base in self.base_urls.items():
            url = url.replace(token, base)
        return url

    # ------------------------------------------------------------------
    # Base-URL stripping
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_base(text: str) -> str:
        """Remove any http(s) base URL (scheme://host:port) prefix from text.

        Works on both bare URLs and free-form answer blobs that mention a URL
        inline, leaving the path/query so that only the meaningful part of the
        URL is compared. e.g. ``http://localhost:8023/foo/-/issues/1`` becomes
        ``/foo/-/issues/1``.
        """
        return _BASE_URL_RE.sub("", text)

    @staticmethod
    def _strip_trailing_slash(text: str) -> str:
        """Collapse an empty last path segment — a ``/`` right before the query
        string or the end of the URL.

        ``/-/issues/?sort=created_date`` becomes ``/-/issues?sort=created_date``
        and ``/-/issues/`` becomes ``/-/issues``, so that a page and the same
        page written with a trailing slash compare equal. Only a slash directly
        before ``?`` or end-of-string is removed, so genuinely different paths
        are left untouched.
        """
        return _TRAILING_SLASH_RE.sub("", text)

    # ------------------------------------------------------------------
    # Result factory
    # ------------------------------------------------------------------

    @staticmethod
    def _result(
        applicable: bool,
        passed: bool,
        expected_urls: List[str],
        actual_url: Optional[str],
        match_mode: str,
        matched_url: Optional[str] = None,
        error: Optional[str] = None,
        eval_types: Optional[List[str]] = None,
        actual_answer: Optional[str] = None,
        expected_answers: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "applicable": applicable,
            "passed": passed,
            "eval_types": eval_types or [],
            "expected_urls": expected_urls,
            "actual_url": actual_url,
            "match_mode": match_mode,
            "matched_url": matched_url,
            "actual_answer": actual_answer if actual_answer is not None else actual_url,
            "expected_answers": expected_answers or {},
            "error": error,
        }
