#!/usr/bin/env python3
"""
Benchmark Runner — Core engine for the PTE evaluation harness.

Handles all three evaluation types: program_html, url_match, and string_match.

For program_html tasks the agent runs first (async), then a separate sync
Playwright session navigates to the evaluation URL(s) and runs DOM checks
via ProgramHtmlEvaluator. url_match and string_match tasks are evaluated
directly from the agent's returned final_url / answer.

This file also defines BaseAgentRunner (the abstract interface teammates
subclass to plug in a custom agent) and AgentRunner (the default PTE
ToolCallAgent implementation).

CLI usage (run from project root):
    python3 eval/run_program_html_benchmark.py --limit 10
    python3 eval/run_program_html_benchmark.py --limit 50 --output results.json
    python3 eval/run_program_html_benchmark.py --agent-only --limit 5
    python3 eval/run_program_html_benchmark.py --no-reset --limit 5

The recommended way to evaluate is via pytest (see eval/tests/):
    python3 -m pytest eval/tests/test_agent_program_html.py --task-limit 2 -v -s
"""

import difflib
import json
import re
import sys
import asyncio
import os
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from api import gitlab_pw
from config.servers import SERVER_URLS as _SERVER_URLS
from eval.program_html_evaluator import ProgramHtmlEvaluator, DEFAULT_BASE_URLS
from eval.url_match_evaluator import UrlMatchEvaluator
from eval.gitlab_state_reset import GitLabStateReset

# Load API keys and site credentials from config/.env automatically.
# This means any BaseAgentRunner subclass gets all keys without extra setup.
_env_path = Path(__file__).parent.parent / "config" / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path, override=False)


# ---------------------------------------------------------------------------
# Helpers shared by both runners
# ---------------------------------------------------------------------------


def _resolve_placeholder(url: str) -> str:
    """Replace __PLACEHOLDER__ tokens with real base URLs."""
    for token, base in DEFAULT_BASE_URLS.items():
        url = url.replace(token, base)
    return url


def _extract_final_url_from_outputs(outputs: Dict[str, Any]) -> Optional[str]:
    """Pull a post/comment URL out of the agent's raw step outputs.

    The API-based agent doesn't navigate, so it can't report a `final_url` from
    a browser. But mutating MCP tools (create_post, reply_to_comment, …) return
    the URL of what they just created, and ProgramHtmlEvaluator's
    `func:reddit_get_post_url('__last_url__')` lookup needs that URL to know
    which page to inspect. Without it the eval raises and every "post to a
    forum" task fails regardless of agent correctness.

    Scan steps from the highest-numbered step backward and return the first
    `post_url` (preferred) or `comment_url` we see. Returns None if the run
    didn't produce one — voting and subscribe tasks fall here, and that's fine
    because their evals use hardcoded URLs.
    """
    if not isinstance(outputs, dict):
        return None

    def _step_num(key: str) -> int:
        m = re.match(r"step_(\d+)", str(key))
        return int(m.group(1)) if m else -1

    for step_id in sorted(outputs.keys(), key=_step_num, reverse=True):
        val = outputs[step_id]
        if not isinstance(val, dict):
            continue
        for url_key in ("post_url", "comment_url"):
            url = val.get(url_key)
            if isinstance(url, str) and url:
                return url
    return None


def _login_for_task(
    page,
    task: Dict[str, Any],
    gitlab_base_url: str = _SERVER_URLS["gitlab"],
    shopping_base_url: Optional[str] = None,
    shopping_admin_base_url: Optional[str] = None,
    reddit_base_url: Optional[str] = None,
) -> bool:
    """
    Log in to the appropriate site for the given task.

    Returns True on success, False on failure.
    Supports: gitlab, reddit, shopping (customer + admin).

    gitlab_base_url: base URL of the GitLab instance to log into.
        Defaults to localhost:8023.  Pass the worker's URL when running
        against a Docker worker on a different port.
    shopping_base_url: base URL of the Magento storefront to log into.
        None falls back to $SHOPPING_DOMAIN / the configured default.  Pass
        the worker's URL under --multi-docker: the session cookie is scoped
        to the host it was issued by, so logging into the shared instance
        leaves every later navigation to a worker unauthenticated.
    shopping_admin_base_url: base URL of the Luma admin instance to log into,
        as the BARE root (no /admin suffix) — admin_base_url() appends the
        path itself.  Same cookie-scoping rule as shopping_base_url; None
        falls back to $SHOPPING_ADMIN_DOMAIN / the configured default.
    reddit_base_url: base URL of the Postmill instance to authenticate against.
        Under --multi-docker each worker is a separate container with its own
        session store, so the companion server's PHPSESSID has to be fetched for
        THIS worker (base_url pins /session to it) and the cookie scoped to the
        worker's host.  None falls back to the companion server's default
        instance and localhost/127.0.0.1 cookies — the single-instance behaviour.
    """
    sites = task.get("sites", [])

    if "gitlab" in sites:
        from api import gitlab_pw
        import time as _login_time
        username, password = gitlab_pw.get_default_gitlab_credentials()

        if gitlab_base_url != _SERVER_URLS["gitlab"]:
            # Non-default port: navigate directly instead of relying on the
            # module-level LOGIN_URL constant (which is baked in as 8023).
            login_url = f"{gitlab_base_url}/users/sign_in"
            for _login_attempt in range(3):
                try:
                    page.goto(login_url, wait_until="networkidle")
                    page.fill("#user_login", username)
                    page.fill("#user_password", password)
                    page.locator('button[type="submit"]').click()
                    page.wait_for_load_state("networkidle")
                    if "/users/sign_in" not in page.url:
                        return True
                except Exception:
                    pass
                _login_time.sleep(2 ** _login_attempt)
            return False

        # Default localhost:8023 — use the library function.
        # Retry login up to 3 times — GitLab can be slow to respond after
        # state-reset API operations (returns networkidle timeout or 500).
        for _login_attempt in range(3):
            result = gitlab_pw.login_user(page, username, password)
            if result.success:
                return True
            _login_time.sleep(2 ** _login_attempt)
        return False

    if "reddit" in sites:
        # Postmill UI login (reddit_login.login_user) authenticates against the
        # REDDIT_DOMAIN host (default localhost:9999) and stores the cookie
        # scoped to that host. But our eval URLs come from SERVER_URLS["reddit"]
        # (127.0.0.1:9999), so the cookie never travels to the post page and the
        # form's user-vote class appears absent — even though the vote did
        # persist server-side.
        #
        # Use the requests-based session refresh (same one the MCP server uses)
        # and set PHPSESSID on BOTH hosts so the cookie covers whichever the
        # eval URL ends up resolving to. Mirrors _make_browser_page in
        # api/servers/reddit.py.
        # Cache the eval-side PHPSESSID process-wide so we don't re-login per
        # task. The eval always logs in as the same Postmill user, so the same
        # session is valid for the lifetime of the test run. Per-task refresh
        # was both slow (~1s/task of /login overhead) and creating session-state
        # divergence between the MCP server's actions and the eval's checks.
        # Always fetch the current session from the MCP server rather than
        # calling refresh_session() here. Postmill invalidates previous sessions
        # on new logins, so an independent login would kill the session the MCP
        # server just used for the task's write operations.
        #
        # Under --multi-docker each worker is a distinct Postmill container with
        # its own session store, so the fetch must pin /session to this worker's
        # base_url (the companion server caches one PHPSESSID per instance and
        # returns the one the task's writes already used) and the cookie must be
        # scoped to the worker's own host. reddit_base_url=None keeps the
        # single-instance behaviour: default instance, localhost/127.0.0.1 only.
        try:
            import requests as _http
            r = _http.get(
                f"{_SERVER_URLS['reddit_extra']}/session",
                params={"base_url": reddit_base_url} if reddit_base_url else None,
                timeout=5,
            )
            r.raise_for_status()
            eval_phpsessid = r.json()["phpsessid"]
        except Exception as exc:
            print(f"   ⚠️  reddit session fetch from MCP server failed: {exc}")
            return False
        hosts = {"localhost", "127.0.0.1"}
        if reddit_base_url:
            worker_host = urlparse(reddit_base_url).hostname
            if worker_host:
                hosts.add(worker_host)
        page.context.add_cookies([
            {"name": "PHPSESSID", "value": eval_phpsessid, "domain": d, "path": "/"}
            for d in hosts
        ])
        return True

    if "shopping_admin" in sites:
        from api.shopping_admin_pw import login as shopping_admin_login
        username = os.getenv("SHOPPING_ADMIN_USER", "admin")
        password = os.getenv("SHOPPING_ADMIN_PASS", "admin1234")
        result = shopping_admin_login.login_admin(
            page, username, password, base_url=shopping_admin_base_url
        )
        return result.success

    if "shopping" in sites:
        from api.shopping_pw import login as shopping_login
        username = os.getenv("SHOPPING_USER", "emma.lopez@gmail.com")
        password = os.getenv("SHOPPING_PASS", "Password.123")
        result = shopping_login.login_customer(
            page, username, password, base_url=shopping_base_url
        )
        return result.success

    # Unknown site — skip login, let the page navigate unauthenticated
    return True


# ---------------------------------------------------------------------------
# Baseline runner (url_match only — unchanged from run_utility_benchmark.py)
# ---------------------------------------------------------------------------

class BaselineRunner:
    """Runs baseline tests using Playwright direct navigation (url_match only)."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.results: Dict[str, Any] = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "details": [],
        }

    def _test_url_navigation(self, page, task: Dict[str, Any]) -> Tuple[bool, str, str]:
        expected_url = _resolve_placeholder(task["eval"]["reference_url"])
        start_url = _resolve_placeholder(task.get("start_url", "__GITLAB__"))
        intent = task["intent"].lower()

        if "todos" in intent:
            target_url = _resolve_placeholder("__GITLAB__/dashboard/todos")
        elif "recent open issues" in intent:
            target_url = start_url + "/-/issues/?sort=created_asc&state=opened" \
                if start_url != _resolve_placeholder("__GITLAB__") else start_url
        else:
            target_url = start_url

        try:
            page.goto(target_url, timeout=10000)
            page.wait_for_load_state("networkidle", timeout=10000)
            actual_url = page.url

            note = task["eval"].get("url_note", "")
            if note == "GOLD in PRED":
                passed = expected_url in actual_url
            else:
                passed = actual_url == expected_url or actual_url.startswith(expected_url)

            return passed, expected_url, actual_url
        except Exception as exc:
            return False, expected_url, f"Error: {exc}"

    def run(self, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        print("\n" + "=" * 70)
        print("BASELINE: Playwright Direct Navigation (url_match only)")
        print("=" * 70)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()

            print("🔐 Logging in to GitLab...")
            username, password = gitlab_pw.get_default_gitlab_credentials()
            login_result = gitlab_pw.login_user(page, username, password)

            if not login_result.success:
                print(f"❌ Login failed: {login_result.message}")
                browser.close()
                return self.results

            print(f"✅ Logged in as {username}\n")

            for i, task in enumerate(tasks, 1):
                self.results["total"] += 1

                if "url_match" not in task["eval"]["eval_types"]:
                    print(f"[{i}/{len(tasks)}] Task {task['task_id']}: SKIPPED (not url_match)")
                    continue

                print(f"[{i}/{len(tasks)}] Task {task['task_id']}: {task['intent'][:60]}...")

                passed, expected, actual = self._test_url_navigation(page, task)

                if passed:
                    self.results["passed"] += 1
                    print("   ✅ PASS")
                else:
                    self.results["failed"] += 1
                    print("   ❌ FAIL")

                self.results["details"].append({
                    "task_id": task["task_id"],
                    "intent": task["intent"],
                    "passed": passed,
                    "expected_url": expected,
                    "actual_url": actual,
                    "eval_type": task["eval"]["eval_types"][0],
                })

            browser.close()

        return self.results


# ---------------------------------------------------------------------------
# Agent runner — with program_html evaluation wired in
# ---------------------------------------------------------------------------

class BaseAgentRunner:
    """
    Abstract base class for agent runners used by the test suite.

    Subclass this to plug in any custom agent implementation. The base class
    handles all evaluation logic (program_html, url_match, string_match) and
    GitLab state resets. Subclasses only need to implement two methods:

        async _init_agent(self) -> None
            Initialise your agent (called once per test session).

        async _run_task(self, task: dict) -> dict
            Run your agent on a single task. Return a dict with at least:
                {
                    "final_url": str | None,   # URL the agent ended on
                    "answer":    str | None,   # agent's text answer
                }
            Return {"success": False, "error": "..."} to signal a hard failure.

    Minimal example
    ───────────────
        class MyAgentRunner(BaseAgentRunner):
            async def _init_agent(self):
                self.my_agent = MyAgent()

            async def _run_task(self, task):
                result = await self.my_agent.solve(task["intent"])
                return {"final_url": result.url, "answer": result.text}

    Then point the test suite at your runner:

        python3 -m pytest tests/ --agent-runner mymodule.MyAgentRunner -v
    """

    def __init__(
        self,
        headless: bool = True,
        enable_reset: bool = True,
        force_reset: bool = False,
        gitlab_base_url: str = _SERVER_URLS["gitlab"],
    ):
        self.headless = headless
        self.enable_reset = enable_reset
        # When force_reset=True every task is treated as require_reset=True,
        # regardless of the value stored in the task JSON.  This lets you do a
        # clean re-run after a previous run left state (duplicate milestones,
        # issues, MRs, etc.) without having to modify any task file.
        self.force_reset = force_reset
        # gitlab_base_url: the GitLab instance Playwright should log into and
        # check DOM against after agent execution.  Defaults to localhost:8023.
        # Multi-worker runs override this per-worker (e.g. localhost:8024).
        self.gitlab_base_url = gitlab_base_url
        self._evaluator = ProgramHtmlEvaluator()
        self._url_match_evaluator = UrlMatchEvaluator()
        self._resetter = GitLabStateReset(gitlab_base=gitlab_base_url) if enable_reset else None

    # ------------------------------------------------------------------
    # Subclasses must implement these two methods
    # ------------------------------------------------------------------

    async def _init_agent(self) -> None:  # pragma: no cover
        raise NotImplementedError("Subclasses must implement _init_agent()")

    async def _run_task(self, task: Dict[str, Any]) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError("Subclasses must implement _run_task(task)")

    # ------------------------------------------------------------------
    # Evaluation helpers (shared — subclasses do not need to override)
    # ------------------------------------------------------------------

    def _evaluate_url_match(self, task: Dict[str, Any], result: Dict[str, Any]) -> bool:
        actual_url = result.get("final_url")
        detail = self._url_match_evaluator.evaluate(
            task, actual_url, actual_answer=result.get("answer")
        )
        if not detail["passed"]:
            if detail.get("error"):
                print(f"         error        : {detail['error']}")
            else:
                print(f"         expected url : {detail['expected_urls']}")
                print(f"         actual   url : {actual_url}")
            # Most url_match tasks are also graded on program_html (a few on
            # string_match), so show what the answer itself was expected to
            # contain alongside the URL comparison.
            print(f"         answer       : {str(detail.get('actual_answer') or '')[:200]!r}")
            for eval_type, expected in (detail.get("expected_answers") or {}).items():
                print(f"         expected {eval_type} : {expected}")
        return detail["passed"]

    # Month name → number, used by _parse_dates. Long form first so the
    # sorted-by-length regex prefers 'september' over 'sep' when both would
    # match, avoiding a truncated month capture.
    _MONTH_NUM = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
        "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    @classmethod
    def _parse_dates(cls, text: str) -> List[Any]:
        """Extract calendar dates from `text`, in order of appearance.

        Recognizes:
          - YYYY-MM-DD or YYYY/MM/DD
          - Month D YYYY / Month D, YYYY (with optional 'st|nd|rd|th')
          - M/D/YYYY (US) — requires 4-digit year so ambiguous 6/30 doesn't parse

        Requires a 4-digit year in all forms to avoid false hits on random
        number sequences. Silently skips components that don't form a valid
        date (e.g. month 13, day 45).
        """
        from datetime import date as _date
        found: List[_date] = []
        # 1. YYYY-MM-DD / YYYY/MM/DD
        for m in re.finditer(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text):
            try:
                found.append(_date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                pass
        # 2. Month D[st|nd|rd|th][,] YYYY
        month_alt = "|".join(sorted(cls._MONTH_NUM, key=len, reverse=True))
        for m in re.finditer(
            rf"\b({month_alt})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
            text, re.IGNORECASE,
        ):
            try:
                found.append(_date(
                    int(m.group(3)),
                    cls._MONTH_NUM[m.group(1).lower()],
                    int(m.group(2)),
                ))
            except ValueError:
                pass
        # 3. M/D/YYYY (US) — reject impossible components.
        for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text):
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                try:
                    found.append(_date(y, mo, d))
                except ValueError:
                    pass
        return found

    @staticmethod
    def _dates_match(ref_dates: List[Any], ans_dates: List[Any],
                     tolerance_days: int = 1) -> bool:
        """True iff any ref date is within `tolerance_days` of any answer date.

        The default ±1 day covers timezone-offset ambiguity: a Magento order
        timestamp of 2023-05-24 01:20 UTC displays as May 23 in Pacific admin
        settings, and either date is a defensible answer.
        """
        for rd in ref_dates:
            for ad in ans_dates:
                if abs((ad - rd).days) <= tolerance_days:
                    return True
        return False

    @classmethod
    def _fuzzy_check_labeled_item(cls, answer_plain: str, ref: str) -> bool:
        """
        Structured check for '<label>: <value> [<unit>]' shaped fuzzy references.

        Raw sliding-window fuzzy on 'May: 8 orders' fails against a
        Markdown-table answer like '| May | 8 |' — after markdown stripping
        the answer reads 'May 8', and pulling in enough surrounding text to
        fill a 20-char comparison window dilutes the ratio below 0.8.

        Instead: split on the first colon, locate the label in the answer
        (tolerating typos like 'Sepetember' vs 'September'), then check the
        value token in the SHORT TAIL immediately after each label match.
        Two subtleties handled by that tail restriction:
          - Numeric values must match the FIRST number after the label, so a
            wrong-count answer ('June: 8' when June is actually 13) fails
            even though the correct value appears later in the row.
          - Single-char values like 'S' can't accidentally hit the letter
            inside the label itself ('Hoodie-S-Blue') because the tail
            starts after the label.

        Returns False for references that don't fit the label:value shape,
        so the caller can fall back to _fuzzy_contains.
        """
        if ":" not in ref:
            return False
        label, value_part = ref.split(":", 1)
        label = label.strip()
        value_part = value_part.strip()
        if not label or not value_part:
            return False
        num_m = re.search(r"-?\d+(?:\.\d+)?", value_part)
        value_is_numeric = num_m is not None
        if value_is_numeric:
            value_token = num_m.group(0)
        else:
            first_word = value_part.split()[0] if value_part.split() else ""
            if not first_word:
                return False
            value_token = first_word
        word_pat = re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(value_token) + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        number_pat = re.compile(r"-?\d+(?:\.\d+)?")
        from decimal import Decimal, InvalidOperation
        for label_end in cls._fuzzy_label_end_positions(answer_plain, label):
            tail = answer_plain[label_end : label_end + 120]
            if value_is_numeric:
                m_tail = number_pat.search(tail)
                if m_tail is None:
                    continue
                # Numeric equality, not string equality — WebArena refs
                # sometimes wrap order ids with leading zeros ('000000299')
                # while the API returns bare '299'. Decimal handles both
                # integers and decimals uniformly.
                try:
                    if Decimal(m_tail.group(0)) == Decimal(value_token):
                        return True
                except InvalidOperation:
                    if m_tail.group(0) == value_token:
                        return True
            else:
                if word_pat.search(tail):
                    return True
        return False

    @staticmethod
    def _fuzzy_label_end_positions(
        answer: str, label: str, threshold: float = 0.7
    ):
        """Yield end positions of each match of `label` in `answer`.

        Exact substrings first; if none, fall back to a sliding-window
        fuzzy pass at `threshold` (looser than _fuzzy_contains' 0.8 because
        labels are short and the value-in-tail check provides a second gate).
        """
        ans_l = answer.lower()
        ref_l = label.lower()
        ref_len = len(ref_l)
        if ref_len == 0:
            return
        idx = 0
        found_exact = False
        while True:
            i = ans_l.find(ref_l, idx)
            if i < 0:
                break
            found_exact = True
            yield i + ref_len
            idx = i + 1
        if found_exact:
            return
        for i in range(len(ans_l) - ref_len + 1):
            window = ans_l[i : i + ref_len]
            if difflib.SequenceMatcher(None, ref_l, window).ratio() >= threshold:
                yield i + ref_len

    @staticmethod
    def _fuzzy_contains(answer: str, ref: str, threshold: float = 0.8) -> bool:
        """Return True if *ref* appears in *answer* exactly or fuzzily (>= threshold)."""
        answer_l = answer.lower()
        ref_l = ref.lower()
        if ref_l in answer_l:
            return True
        ref_len = len(ref_l)
        if ref_len == 0:
            return True
        # Sliding-window fuzzy search over the answer
        for i in range(len(answer_l) - ref_len + 1):
            window = answer_l[i : i + ref_len]
            if difflib.SequenceMatcher(None, ref_l, window).ratio() >= threshold:
                return True
        # Fallback: full-string comparison (handles short answers)
        return difflib.SequenceMatcher(None, ref_l, answer_l).ratio() >= threshold

    # Sentinel tokens injected when the agent's answer clearly represents a
    # 'no results' or 'zero value' outcome — used by _augment_from_json_envelope
    # to satisfy the OR-groups in refs like ['none', 'no reviewer', 'zero', ...]
    # without changing the semantics for any other ref shape.
    _NO_RESULTS_SENTINELS = (
        " none no reviewer no review no result not found cannot find "
        "unable to find not available not listed no order no purchase "
        "no food no product not mention did not mention do not mention "
        "no title no rating no phone does not exist no information "
        "zero 0.00 $0 nothing 0 review 0 order 0 item"
    )

    @classmethod
    def _augment_from_json_envelope(cls, raw_answer: str) -> str:
        """Return extra text to concatenate onto answer_plain when the answer
        is a JSON envelope like {"task_type": "RETRIEVE", ..., "retrieved_data":
        <payload>}.

        Two augmentations:
          E4 — flatten every string/number LEAF in the parsed JSON into a
               space-joined blob so a ref like '101 S San Mateo Dr' appears
               as a substring even when the raw envelope splits the address
               into separate ("house_number", "street") fields.
          E3 — when retrieved_data is [] / null / {} / [0] (any structure
               whose numeric leaves are all zero and whose string leaves are
               all empty), also append 'no results / zero' sentinel tokens.

        Returns '' if no JSON object/array is found in `raw_answer` or if it
        doesn't parse.
        """
        import json as _json
        # Find the outermost balanced { … } or [ … ]. Naive but fine for the
        # single-object envelopes agents emit.
        obj = None
        for opener, closer in (("{", "}"), ("[", "]")):
            start = raw_answer.find(opener)
            if start < 0:
                continue
            depth = 0
            for i in range(start, len(raw_answer)):
                if raw_answer[i] == opener:
                    depth += 1
                elif raw_answer[i] == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = _json.loads(raw_answer[start:i + 1])
                        except (ValueError, TypeError):
                            obj = None
                        break
            if obj is not None:
                break
        if obj is None:
            return ""

        leaves: List[Any] = []
        def _walk(x):
            if isinstance(x, dict):
                for v in x.values():
                    _walk(v)
            elif isinstance(x, list):
                for v in x:
                    _walk(v)
            else:
                leaves.append(x)
        _walk(obj)
        # E4: concatenate stringified non-null leaves.
        flat = " ".join(str(v) for v in leaves if v is not None and v != "")

        # E3: detect no-results / zero outcome from the retrieved_data payload.
        payload = obj.get("retrieved_data") if isinstance(obj, dict) else obj
        if cls._is_no_results_payload(payload):
            flat = flat + " " + cls._NO_RESULTS_SENTINELS
        return flat

    @staticmethod
    def _is_no_results_payload(payload) -> bool:
        """True when `payload` is null / empty / all-zeros — the shapes that
        semantically mean 'no results found' or 'zero'."""
        if payload is None:
            return True
        if isinstance(payload, (list, dict)) and len(payload) == 0:
            return True
        # Recursively check that every leaf is a zero/empty value.
        def _leaves(x):
            if isinstance(x, dict):
                for v in x.values():
                    yield from _leaves(v)
            elif isinstance(x, list):
                for v in x:
                    yield from _leaves(v)
            else:
                yield x
        seen_any = False
        for leaf in _leaves(payload):
            seen_any = True
            if leaf in (None, "", 0):
                continue
            if isinstance(leaf, float) and leaf == 0.0:
                continue
            return False
        return seen_any

    @staticmethod
    def _strip_markdown(s: str) -> str:
        """Strip common markdown formatting so eval comparisons aren't brittle
        to whether the agent wrapped the answer in **bold**, [links](url), code
        spans, headers, or bullet markers.

        Conservative: keeps the visible text, drops the formatting markers and
        the URL portion of links. Tables and code-block fences are stripped to
        their inner content. Trailing/leading whitespace collapsed.
        """
        if not isinstance(s, str):
            return s
        # Strip fenced code blocks (keep inner content)
        s = re.sub(r"```[a-zA-Z]*\n?", "", s)
        s = s.replace("```", "")
        # Inline code: keep the text, drop the backticks
        s = re.sub(r"`([^`]*)`", r"\1", s)
        # Links / images: [text](url) and ![alt](url) → text / alt
        s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
        # Bold / italic markers (**, __, *, _) — drop the marker, keep text.
        # The underscore rules are anchored to non-word boundaries (as
        # CommonMark requires for `_`): without that, snake_case identifiers in
        # an answer read as an italic span and lose their underscores. An
        # answer containing two of them —
        #   [{"order_number": 170, ...}, {"order_number": 189, ...}]
        # — stripped to "ordernumber", while a reference holding a single
        # `order_number` kept its underscore, so the two could never match.
        # Asterisk emphasis needs no such guard: `*` never appears in the
        # identifiers these references cite.
        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
        s = re.sub(r"(?<![A-Za-z0-9_])__([^_]+)__(?![A-Za-z0-9_])", r"\1", s)
        s = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)", r"\1", s)
        s = re.sub(r"(?<![A-Za-z0-9_])_(?!_)([^_\n]+)_(?![A-Za-z0-9_])", r"\1", s)
        # Headings: leading # …
        s = re.sub(r"^\s*#{1,6}\s+", "", s, flags=re.MULTILINE)
        # Bullets / numbered list markers at line start
        s = re.sub(r"^\s*([-*+]|\d+\.)\s+", "", s, flags=re.MULTILINE)
        # Blockquote markers
        s = re.sub(r"^\s*>\s?", "", s, flags=re.MULTILINE)
        # Horizontal rules
        s = re.sub(r"^\s*[-*_]{3,}\s*$", "", s, flags=re.MULTILINE)
        # Pipe characters from tables (keep cell text)
        s = s.replace("|", " ")
        # Collapse runs of whitespace
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _evaluate_string_match(self, task: Dict[str, Any], result: Dict[str, Any]) -> bool:
        reference_answers = task["eval"].get("reference_answers", {})
        must_include = reference_answers.get("must_include", [])
        must_exclude = reference_answers.get("must_exclude", [])
        exact_match  = reference_answers.get("exact_match")
        fuzzy_match  = reference_answers.get("fuzzy_match")

        # E1 — 'N/A' is the auto-pass sentinel for unscoreable tasks (e.g.
        # "buy the highest-rated product within a budget of unknown"). Honor
        # it BEFORE the empty-answer short-circuit so a task with no correct
        # answer never penalises the agent.
        if fuzzy_match == "N/A" or fuzzy_match == ["N/A"]:
            return True

        answer = result.get("answer", "")
        if not answer:
            return False

        # Strip markdown once — agents often format answers with bold, headings,
        # bullets, code spans, or links. Eval expectations are plain text.
        answer_plain = self._strip_markdown(answer)

        # E3+E4 — some agents wrap their answer in a JSON envelope
        # `{"task_type": "RETRIEVE", "status": ..., "retrieved_data": ...}`.
        # Two knock-on issues that this augmentation defuses:
        #   • Concatenated-string refs ('101 S San Mateo Dr') don't appear in
        #     the raw envelope because JSON keys/quotes/commas split them
        #     across fields — parse and flatten values so the concatenated
        #     form appears as a substring.
        #   • 'No results' ref alternatives ('none', 'no reviewer', 'zero',
        #     '$0', '0.00') don't appear when retrieved_data is [] / null /
        #     {} / [0] — inject sentinel tokens for those cases.
        #
        # Both changes ONLY grow the haystack; refs that already matched keep
        # matching, and refs that shouldn't match still can't (a wrong number
        # or wrong name is still absent from the flattened text).
        answer_plain = answer_plain + " " + self._augment_from_json_envelope(answer)
        answer_lower = answer_plain.lower()

        for item in must_include:
            if isinstance(item, list):
                # OR group: at least one alternative must appear
                if not any(self._strip_markdown(str(alt)).lower() in answer_lower for alt in item):
                    return False
            elif isinstance(item, str) and self._strip_markdown(item).lower() not in answer_lower:
                return False
            elif isinstance(item, (int, float)) and str(item) not in answer_plain:
                return False
        for item in must_exclude:
            if isinstance(item, str) and self._strip_markdown(item).lower() in answer_lower:
                return False

        if exact_match is not None:
            # Substring semantics, not full-string equality: agents produce
            # explanatory prose (e.g. "The email is X. This order was placed…")
            # so the reference is embedded, never the whole answer. Case-fold
            # + whitespace-collapse + markdown-strip both sides so wrapping in
            # **bold**, > blockquotes, tables, or emoji doesn't matter.
            # All curated exact_match references are ≥6 chars (product names,
            # emails, full person names), which keeps false-positive risk low.
            normalize = lambda s: re.sub(r"\s+", " ", self._strip_markdown(s)).strip().lower()
            if normalize(str(exact_match)) not in normalize(answer):
                return False

        if fuzzy_match is not None:
            # N/A handled at top of the function (E1); don't re-check here.
            items = fuzzy_match if isinstance(fuzzy_match, list) else [fuzzy_match]
            ans_dates_cache: Optional[List[Any]] = None
            for ref_item in items:
                ref_str = str(ref_item)
                # Date-shaped refs (bare 'May 23 2023' or 'purchase date:
                # May 31, 2023') route through calendar-date parsing so a
                # YYYY-MM-DD answer matches a 'Month D YYYY' reference —
                # and so a ±1-day timezone offset is tolerated. See S4/S5
                # in the eval audit.
                ref_dates = self._parse_dates(ref_str)
                if ref_dates:
                    if ans_dates_cache is None:
                        ans_dates_cache = self._parse_dates(answer_plain)
                    if not self._dates_match(ref_dates, ans_dates_cache):
                        return False
                    continue
                if ":" in ref_str:
                    # Colon-shaped refs use ONLY the structured check —
                    # falling back to _fuzzy_contains would let a strong
                    # label match alone pass even when the value is wrong
                    # (see S3 in the eval audit — task 186's label already
                    # embeds the size letter it's supposed to verify).
                    if not self._fuzzy_check_labeled_item(answer_plain, ref_str):
                        return False
                    continue
                if not self._fuzzy_contains(answer_plain, ref_str):
                    return False

        return True

    def _reddit_program_html_base(self) -> str:
        """Postmill SITE URL that program_html grading/login target for this task.

        Carried on the dedicated self.reddit_base_url attribute (injected per task,
        e.g. the worker's URL under --multi-docker) so grading never depends on
        self.base_url, which some suites point at the companion API server
        (reddit_extra). Mirrors self.gitlab_base_url. Pure — no I/O — so the
        routing decision is unit-testable without Playwright.
        """
        return (getattr(self, "reddit_base_url", None) or _SERVER_URLS["reddit"]).rstrip("/")

    def _run_program_html_check(
        self,
        task: Dict[str, Any],
        last_url: Optional[str],
    ) -> Dict[str, Any]:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()
            try:
                # __SHOPPING__ gets the same worker-aware treatment as
                # __GITLAB__. Under --multi-docker each worker owns a separate
                # Magento instance and the generated script is handed its URL
                # via SHOPPING_DOMAIN (see ClaudeCodeAgentRunner._run_script),
                # so leaving this at the module default grades the shared 7770
                # instance the script never touched. Tasks 431-435 are the
                # worked example: four correct cart writes landed on worker
                # instances, were graded against 7770, and failed — while the
                # fifth "passed" off an unrelated item left in 7770's cart.
                # Resolved before login so the session cookie is issued by the
                # same host the checks then navigate to.
                _shopping_base = _SERVER_URLS["shopping"]
                if getattr(self, "server", None) == "shopping":
                    _shopping_base = (
                        getattr(self, "base_url", None) or _shopping_base
                    ).rstrip("/")

                # Same treatment for shopping_admin, and for the same reason:
                # under --multi-docker the generated script is handed the
                # worker's admin URL via SHOPPING_ADMIN_DOMAIN, so grading
                # against the module default would check an instance the script
                # never wrote to. Kept bare here (no /admin suffix) because both
                # the login helper and the REST-backed func: locators want the
                # instance root; the browser-facing suffix is added below.
                _shopping_admin_base = _SERVER_URLS["shopping_admin"]
                if getattr(self, "server", None) == "shopping_admin":
                    _shopping_admin_base = (
                        getattr(self, "base_url", None) or _shopping_admin_base
                    ).rstrip("/")

                # __REDDIT__ grading/login target this task's Postmill SITE instance,
                # carried on the dedicated self.reddit_base_url attribute (set per task
                # to the worker's URL under --multi-docker) so it never depends on
                # self.base_url — which some suites point at the companion API server
                # reddit_extra. Feeds ProgramHtmlEvaluator._reddit_get_post_url, which the
                # func:reddit_get_post_url checks use to build absolute post URLs.
                _reddit_base = self._reddit_program_html_base()

                logged_in = _login_for_task(
                    page,
                    task,
                    gitlab_base_url=self.gitlab_base_url,
                    shopping_base_url=_shopping_base,
                    shopping_admin_base_url=_shopping_admin_base,
                    reddit_base_url=_reddit_base,
                )
                if not logged_in and _shopping_base != _SERVER_URLS["shopping"]:
                    # Worker containers are provisioned with Magento's
                    # web/unsecure/base_url still pointing at the shared
                    # instance, so their login form posts there and comes back
                    # "Invalid Form Key". Falling back to the default host
                    # keeps the REST-backed locators (cart, reviews) running —
                    # they never touch the browser session, and aborting here
                    # would skip them along with the DOM ones.
                    print(
                        f"   ⚠️  [program_html] storefront login failed at "
                        f"{_shopping_base}; falling back to "
                        f"{_SERVER_URLS['shopping']} (DOM locators on the "
                        f"worker instance will be unauthenticated)"
                    )
                    logged_in = _login_for_task(
                        page,
                        task,
                        gitlab_base_url=self.gitlab_base_url,
                        shopping_base_url=None,
                        shopping_admin_base_url=_shopping_admin_base,
                        reddit_base_url=_reddit_base,
                    )
                if not logged_in:
                    return {
                        "applicable": True,
                        "passed": False,
                        "checks": [],
                        "error": "Login failed for program_html check",
                    }
                # Build a per-check evaluator that resolves __GITLAB__ to this
                # worker's URL rather than the module-level default (8023).
                # __SHOPPING_ADMIN__ also gets the "/admin" suffix appended:
                # the Luma admin UI lives at <host>:7780/admin/<module>/... ,
                # not <host>:7780/<module>/... (which hits the storefront 404).
                # The agent's REST base URL stays bare (port 7780 root) since
                # Magento REST paths begin with /rest/V1/, not /admin/rest/V1/.
                evaluator = ProgramHtmlEvaluator(base_urls={
                    **DEFAULT_BASE_URLS,
                    "__GITLAB__": self.gitlab_base_url,
                    "__REDDIT__": _reddit_base,
                    "__SHOPPING__": _shopping_base,
                    "__SHOPPING_ADMIN__": _shopping_admin_base.rstrip("/") + "/admin",
                })
                eval_result = evaluator.evaluate(task, page, last_url=last_url)
            finally:
                browser.close()
        return eval_result

    # ------------------------------------------------------------------
    # Single-task runner (calls _run_task then evaluates)
    # ------------------------------------------------------------------

    async def run_agent_on_task(
        self, task: Dict[str, Any], skip_reset: bool = False
    ) -> Tuple[bool, Any, Optional[str], Optional[Dict]]:
        """
        Run the agent on a single task and evaluate the result.

        skip_reset suppresses the pre-task state reset in Step 0. It exists for
        callers that re-run the same task in a loop and want the reset to happen
        on the first attempt only (see eval/tests/test_agent_verified_all_sites.py,
        which retries a task whose generated script failed to execute). Defaults
        to False, so a single call behaves exactly as before.

        Returns:
            (passed, agent_result, error_message, program_html_detail)
        """
        eval_types = task["eval"]["eval_types"]

        # ---- Step 0: pre-task state reset ----
        if self._resetter is not None and not skip_reset:
            # When force_reset is enabled, treat every task as require_reset=True
            # so that TASK_RESET_CONFIG cleanup runs unconditionally.  We work on
            # a shallow copy so the original task dict is never mutated.
            reset_task = dict(task, require_reset=True) if self.force_reset else task
            try:
                self._resetter.reset_for_task(reset_task)
            except Exception as reset_exc:
                print(f"   ⚠️  [reset] Uncaught reset error: {reset_exc}")
            import asyncio as _asyncio2
            await _asyncio2.sleep(8)

        # ---- Step 1: run the agent ----
        agent_error: Optional[str] = None
        try:
            agent_result = await self._run_task(task)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            agent_error = str(exc)
            agent_result = {"success": False, "error": agent_error, "final_url": None, "answer": None}

        if not agent_result.get("success", True) and agent_result.get("error"):
            agent_error = agent_result["error"]

        # For non-program_html tasks short-circuit immediately on agent failure —
        # there is nothing to check without a final_url or answer.
        #
        # agent_result is still returned rather than dropped: it carries why the
        # run failed (ClaudeCodeAgentRunner tags it with failure_kind, plus the
        # script's own output), which callers need to decide whether a retry
        # could help, and which otherwise never reaches the result logs. It
        # holds answer=None on this path, so callers reading .get("answer") see
        # what they always did.
        if agent_error and "program_html" not in eval_types:
            return False, agent_result, agent_error, None

        final_url: Optional[str] = agent_result.get("final_url")

        # ---- Step 2: evaluate ----
        if "program_html" in eval_types:
            loop = asyncio.get_event_loop()
            html_detail = await loop.run_in_executor(
                None, self._run_program_html_check, task, final_url
            )
            html_passed = html_detail.get("passed", False)
            # url_match is intentionally skipped here, even for tasks that carry
            # both eval_types ("url_match" + "program_html"). When _run_task can
            # recover a final_url (a post URL from create_post) it's a deep link
            # like /f/headphones/9/... while the url_match reference is the bare
            # forum page /f/headphones, so they won't compare equal. The
            # program_html DOM checks on the post page are a stronger signal
            # than a forum-root URL match anyway.
            passed = html_passed
            # If the agent errored but the eval still passed (e.g. comment was
            # posted before the error), report the pass.  If both failed, surface
            # the agent error so it's visible in the results.
            error_to_report = None if passed else agent_error
            return passed, agent_result, error_to_report, html_detail

        if "url_match" in eval_types:
            passed = self._evaluate_url_match(task, agent_result)
            return passed, agent_result, None, None

        if "string_match" in eval_types:
            passed = self._evaluate_string_match(task, agent_result)
            return passed, agent_result, None, None

        return False, None, f"Unknown eval type(s): {eval_types}", None


class AgentRunner(BaseAgentRunner):
    """
    Runs tasks through the PTE ToolCallAgent (async, MCP tools) then evaluates
    results using BaseAgentRunner's evaluation logic.

    For program_html tasks a fresh sync Playwright session is opened after
    agent execution to run the ProgramHtmlEvaluator checks.
    """

    def __init__(
        self,
        headless: bool = True,
        enable_reset: bool = True,
        force_reset: bool = False,
        api_dir: str = "api",
        env_file: str = "config/.server_env",
        gitlab_base_url: str = _SERVER_URLS["gitlab"],
        debug: bool = False,
    ):
        super().__init__(
            headless=headless,
            enable_reset=enable_reset,
            force_reset=force_reset,
            gitlab_base_url=gitlab_base_url,
        )
        # api_dir lets parallel workers point at per-worker copies of the API
        # schema (with the correct GitLab port patched in).  Defaults to the
        # canonical "api/" directory for single-worker / non-orchestrator runs.
        self.api_dir = api_dir
        # env_file lets parallel workers use a per-worker .server_env with the
        # correct GITLAB_TOKEN for that container.  Defaults to the shared env.
        self.env_file = env_file
        self.debug = debug
        self.results: Dict[str, Any] = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "details": [],
        }
        self.agent = None
        self.task_runner = None

    # ------------------------------------------------------------------
    # Agent initialisation
    # ------------------------------------------------------------------

    async def _init_agent(self):
        from agent.baya_json_agent.agent import Agent

        print("🔧 Initializing agent...")
        self._agent = Agent(api_dir=self.api_dir, env_file=self.env_file, debug=self.debug)
        server_name = getattr(self, "server", "gitlab")
        base_url = getattr(self, "base_url", self.gitlab_base_url)
        init_servers = {server_name: base_url}
        if server_name == "shopping":
            init_servers["shopping_extra"] = _SERVER_URLS["shopping_extra"]
        if server_name == "shopping_admin":
            init_servers["shopping_admin_extra"] = _SERVER_URLS["shopping_admin_extra"]
        if server_name == "gitlab":
            init_servers["gitlab_extra"] = _SERVER_URLS["gitlab_extra"]
        self._agent.initialize(init_servers)
        print("✓ Agent initialized\n")

    # ------------------------------------------------------------------
    # Context helpers
    # ------------------------------------------------------------------

    def _get_task_context(self, task: Dict[str, Any]) -> Dict[str, Any]:
        context = {
            "gitlab_username": os.getenv("GITLAB_USERNAME", "byteblaze"),
            "gitlab_password": os.getenv("GITLAB_PASSWORD", "hello1234"),
        }
        start_url = task.get("start_url", "")
        if "__GITLAB__" in start_url:
            parts = start_url.replace("__GITLAB__/", "").split("/")
            if len(parts) >= 2:
                context["namespace"] = parts[0]
                context["project"] = parts[1]
        _generic_roots = {"__REDDIT__", "__GITLAB__", "__SHOPPING__", "__SHOPPING_ADMIN__"}
        if start_url and start_url not in _generic_roots:
            context["start_url"] = _resolve_placeholder(start_url)
        return context

    # ------------------------------------------------------------------
    # _run_task: implements BaseAgentRunner interface
    # ------------------------------------------------------------------

    async def _run_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Run the PTE Agent on a single task and return the raw result dict."""
        intent = task.get("intent", "")
        start_url = task.get("start_url", "")
        repo_path = (
            start_url
            .replace("__GITLAB__", "")
            .replace("__SHOPPING__", "")
            .replace("__REDDIT__", "")
            .strip("/")
        )
        prompt = f"Project path: {repo_path}\n\nTask: {intent}" if repo_path else intent

        server_name = getattr(self, "server", "gitlab")
        server_url = getattr(self, "base_url", self.gitlab_base_url)
        run_servers: Dict[str, str] = {server_name: server_url}
        if server_name == "reddit":
            run_servers["reddit"] = _SERVER_URLS["reddit_extra"]
        if server_name == "shopping":
            run_servers["shopping_extra"] = _SERVER_URLS["shopping_extra"]
        if server_name == "shopping_admin":
            run_servers["shopping_admin_extra"] = _SERVER_URLS["shopping_admin_extra"]
        if server_name == "gitlab":
            run_servers["gitlab_extra"] = _SERVER_URLS["gitlab_extra"]
        sites = task.get("sites") or [server_name]
        result = await self._agent.run_task(prompt, servers=run_servers, sites=sites)
        agent_result = {
            "success": True,
            "final_url": _extract_final_url_from_outputs(result.outputs),
            "answer": result.answer,
            "execution_result": result.outputs,
        }

        print(f"\n   DEBUG agent result:")
        print(f"     answer (snippet) : {str(result.answer)[:80]}")

        return agent_result

    # ------------------------------------------------------------------
    # Batch runner
    # ------------------------------------------------------------------

    async def run(
        self,
        tasks: List[Dict[str, Any]],
        output_path: Optional[str] = None,
        start_from: int = 0,
    ) -> Dict[str, Any]:
        print("\n" + "=" * 70)
        print("AGENT: Plan + Execute with MCP Tools (program_html enabled)")
        print("=" * 70)

        if start_from:
            print(f"▶️  Resuming from task index {start_from} (skipping first {start_from} tasks)")
            tasks = tasks[start_from:]

        if self.agent is None:
            await self._init_agent()

        total_tasks = len(tasks)
        for i, task in enumerate(tasks, 1):
            self.results["total"] += 1
            eval_label = ", ".join(task["eval"]["eval_types"])
            print(f"\n[{i}/{total_tasks}] Task {task['task_id']} [{eval_label}]: {task['intent'][:55]}...")

            passed, agent_result, error, html_detail = await self.run_agent_on_task(task)

            detail: Dict[str, Any] = {
                "task_id": task["task_id"],
                "intent": task["intent"],
                "eval_types": task["eval"]["eval_types"],
                "passed": passed,
                "error": error,
            }

            if agent_result:
                detail["final_url"] = agent_result.get("final_url")
                # `or ""` rather than a .get default: a failure dict carries the
                # "answer" key with value None, which a default never replaces —
                # str(None) would then report the snippet as a literal "None".
                detail["answer_snippet"] = str(agent_result.get("answer") or "")[:200]

            if html_detail:
                detail["program_html_checks"] = html_detail.get("checks", [])
                detail["program_html_error"] = html_detail.get("error")

            if passed:
                self.results["passed"] += 1
                print("   ✅ PASS")
            elif error:
                self.results["errors"] += 1
                print(f"   ⚠️  ERROR: {error}")
            else:
                self.results["failed"] += 1
                print("   ❌ FAIL")

            self.results["details"].append(detail)

            # Incrementally flush results after every task so progress
            # is preserved if the run is interrupted.
            if output_path:
                _flush_partial_results(self.results, output_path)

        return self.results


# ---------------------------------------------------------------------------
# Incremental save helper
# ---------------------------------------------------------------------------

def _flush_partial_results(results: Dict[str, Any], output_path: str) -> None:
    """Write current results to disk so progress survives interruption."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "test_type": "agent_only_program_html_partial",
        "task_count": results["total"],
        "results": results,
    }
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)


# ---------------------------------------------------------------------------
# Utility / reporting (unchanged logic)
# ---------------------------------------------------------------------------

def calculate_utility_numbers(baseline_results: Dict, agent_results: Dict) -> Dict:
    baseline_total = max(baseline_results["total"], 1)
    agent_total = max(agent_results["total"], 1)
    return {
        "baseline": {
            "total": baseline_results["total"],
            "passed": baseline_results["passed"],
            "failed": baseline_results["failed"],
            "success_rate": round(baseline_results["passed"] / baseline_total * 100, 2),
        },
        "agent": {
            "total": agent_results["total"],
            "passed": agent_results["passed"],
            "failed": agent_results["failed"],
            "errors": agent_results.get("errors", 0),
            "success_rate": round(agent_results["passed"] / agent_total * 100, 2),
        },
        "comparison": {
            "improvement": round(
                (agent_results["passed"] / agent_total * 100)
                - (baseline_results["passed"] / baseline_total * 100),
                2,
            )
        },
    }


def print_utility_report(utility: Dict):
    print("\n" + "=" * 70)
    print("UTILITY NUMBERS — Success Rate Comparison")
    print("=" * 70)

    print("\nBASELINE (Playwright Direct):")
    print(f"  Total Tasks:   {utility['baseline']['total']}")
    print(f"  Passed:        {utility['baseline']['passed']}")
    print(f"  Failed:        {utility['baseline']['failed']}")
    print(f"  Success Rate:  {utility['baseline']['success_rate']}%")

    print("\nAGENT (Plan + Execute):")
    print(f"  Total Tasks:   {utility['agent']['total']}")
    print(f"  Passed:        {utility['agent']['passed']}")
    print(f"  Failed:        {utility['agent']['failed']}")
    print(f"  Errors:        {utility['agent']['errors']}")
    print(f"  Success Rate:  {utility['agent']['success_rate']}%")

    print("\nCOMPARISON:")
    improvement = utility["comparison"]["improvement"]
    if improvement > 0:
        print(f"  Agent is {improvement}% better than baseline ✅")
    elif improvement < 0:
        print(f"  Agent is {abs(improvement)}% worse than baseline ⚠️")
    else:
        print("  Agent and baseline perform equally")

    print("=" * 70)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Run benchmark with proper program_html evaluation"
    )
    parser.add_argument(
        "--tasks",
        default="eval/tests/raw_webarena_tasks_no_map.json",
        help="Path to tasks JSON file (default: eval/tests/raw_webarena_tasks_no_map.json)",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Max number of tasks to run (default: 10)",
    )
    parser.add_argument(
        "--output", default="program_html_benchmark_results.json",
        help="Output file for results (default: program_html_benchmark_results.json)",
    )
    parser.add_argument("--baseline-only", action="store_true",
                        help="Run only baseline (url_match) tests")
    parser.add_argument("--agent-only", action="store_true",
                        help="Run only agent tests")
    parser.add_argument("--headed", action="store_true",
                        help="Run with visible browser windows")
    parser.add_argument("--no-reset", action="store_true",
                        help="Disable pre-task GitLab state reset (for debugging)")
    parser.add_argument(
        "--start-from", type=int, default=0,
        help="Skip the first N tasks (0-indexed) to resume an interrupted run",
    )

    args = parser.parse_args()

    headless = not args.headed

    # Load tasks
    tasks_path = Path(args.tasks)
    print(f"Loading tasks from {tasks_path}...")
    with open(tasks_path) as f:
        all_tasks = json.load(f)

    tasks = all_tasks[: args.limit]
    print(f"✓ Loaded {len(tasks)} tasks (limited from {len(all_tasks)} total)\n")

    baseline_results = None
    agent_results = None

    # Baseline (sync, run in executor so it doesn't block the event loop)
    if not args.agent_only:
        baseline_runner = BaselineRunner(headless=headless)
        loop = asyncio.get_event_loop()
        baseline_results = await loop.run_in_executor(None, baseline_runner.run, tasks)

    # Agent
    if not args.baseline_only:
        enable_reset = not getattr(args, "no_reset", False)
        if enable_reset:
            print("🔄 Pre-task state reset: ENABLED (use --no-reset to disable)")
        else:
            print("⚠️  Pre-task state reset: DISABLED")
        agent_runner = AgentRunner(headless=headless, enable_reset=enable_reset)
        agent_results = await agent_runner.run(
            tasks,
            output_path=args.output,
            start_from=args.start_from,
        )

    # Report
    if baseline_results and agent_results:
        utility = calculate_utility_numbers(baseline_results, agent_results)
        print_utility_report(utility)
        report = {
            "timestamp": datetime.now().isoformat(),
            "test_type": "program_html_benchmark",
            "task_count": len(tasks),
            "utility_numbers": utility,
            "baseline_details": baseline_results["details"],
            "agent_details": agent_results["details"],
        }
    elif agent_results:
        total = max(agent_results["total"], 1)
        print("\n" + "=" * 70)
        print("AGENT RESULTS")
        print("=" * 70)
        print(f"Total:   {agent_results['total']}")
        print(f"Passed:  {agent_results['passed']} ({agent_results['passed']/total*100:.1f}%)")
        print(f"Failed:  {agent_results['failed']} ({agent_results['failed']/total*100:.1f}%)")
        print(f"Errors:  {agent_results['errors']} ({agent_results['errors']/total*100:.1f}%)")
        print("=" * 70)
        report = {
            "timestamp": datetime.now().isoformat(),
            "test_type": "agent_only_program_html",
            "task_count": len(tasks),
            "results": agent_results,
        }
    else:
        total = max(baseline_results["total"], 1)
        print("\n" + "=" * 70)
        print("BASELINE RESULTS")
        print("=" * 70)
        print(f"Total:   {baseline_results['total']}")
        print(f"Passed:  {baseline_results['passed']} ({baseline_results['passed']/total*100:.1f}%)")
        print(f"Failed:  {baseline_results['failed']} ({baseline_results['failed']/total*100:.1f}%)")
        print("=" * 70)
        report = {
            "timestamp": datetime.now().isoformat(),
            "test_type": "baseline_only",
            "task_count": len(tasks),
            "results": baseline_results,
        }

    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n📄 Full report saved to: {output_path}")
    print("\n✨ Done!")


if __name__ == "__main__":
    asyncio.run(main())
