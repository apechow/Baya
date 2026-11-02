"""
program_html_evaluator.py

Evaluates program_html tasks from WebArena task definitions.

Usage:
    from program_html_evaluator import ProgramHtmlEvaluator

    evaluator = ProgramHtmlEvaluator(base_urls={...})
    result = evaluator.evaluate(task, page, last_url="http://...")
"""

import re
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page

from config.servers import SERVER_URLS as _SERVER_URLS

DEFAULT_BASE_URLS: dict = {f"__{k.upper()}__": v for k, v in _SERVER_URLS.items()}


class ProgramHtmlEvaluator:
    """
    Evaluates the program_html component of a WebArena task.

    For each entry in task["eval"]["program_html"], it:
      1. Resolves the URL using the decision tree (literal, "last", func:...)
      2. Navigates to that URL with Playwright
      3. Evaluates the locator expression (JS or func:) to extract content
      4. Checks required_contents (exact_match / must_include / must_exclude)

    All program_html entries must pass for the overall evaluation to pass.
    """

    def __init__(self, base_urls: Optional[Dict[str, str]] = None):
        """
        Args:
            base_urls: Mapping of placeholder tokens to real base URLs.
                       Defaults to DEFAULT_BASE_URLS.
        """
        self.base_urls = base_urls if base_urls is not None else DEFAULT_BASE_URLS

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def evaluate(
        self,
        task: Dict[str, Any],
        page: Page,
        last_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate the program_html section of a task.

        Args:
            task:     Full task dict (must contain task["eval"]).
            page:     An authenticated Playwright Page ready to navigate.
            last_url: The final URL the agent navigated to during execution.
                      Required when program_html url is "last" or a func that
                      references '__last_url__'.

        Returns:
            {
                "applicable":   bool   # False when eval_type is not program_html
                "passed":       bool
                "checks":       list   # Per-entry detail
                "error":        str | None
            }
        """
        eval_section = task.get("eval", {})
        eval_types = eval_section.get("eval_types", [])

        if "program_html" not in eval_types:
            return {
                "applicable": False,
                "passed": False,
                "checks": [],
                "error": "eval_type does not include program_html",
            }

        entries = eval_section.get("program_html", [])
        if not entries:
            return {
                "applicable": True,
                "passed": True,
                "checks": [],
                "error": None,
            }

        # Fall back to reference_url when last_url is unknown.
        # reference_url may carry |OR| alternatives (e.g. Reddit tasks that
        # accept several subreddits).  Resolve each alternative separately so
        # we never try to navigate to a URL containing the literal " |OR| ".
        reference_url = eval_section.get("reference_url") or ""
        if last_url:
            resolved_last_alternatives = [last_url]
        else:
            resolved_last_alternatives = [
                self._resolve_placeholder(alt.strip())
                for alt in reference_url.split(" |OR| ")
                if alt.strip()
            ] or [""]

        checks: List[Dict[str, Any]] = []
        all_passed = True

        for entry in entries:
            # For entries whose url resolves to multiple |OR| alternatives, try
            # each one and accept the first that passes (or keep the last
            # failure for diagnostics if none passes).
            best_check: Optional[Dict[str, Any]] = None
            for resolved_last in resolved_last_alternatives:
                check = self._evaluate_entry(entry, page, resolved_last)
                if check["passed"]:
                    best_check = check
                    break
                if best_check is None:
                    best_check = check
            checks.append(best_check)  # type: ignore[arg-type]
            if not best_check["passed"]:
                all_passed = False

        return {
            "applicable": True,
            "passed": all_passed,
            "checks": checks,
            "error": None,
        }

    # ------------------------------------------------------------------
    # Per-entry evaluation
    # ------------------------------------------------------------------

    def _evaluate_entry(
        self,
        entry: Dict[str, Any],
        page: Page,
        last_url: Optional[str],
    ) -> Dict[str, Any]:
        """Evaluate a single program_html entry."""
        raw_url = entry.get("url", "")
        locator = entry.get("locator", "")
        required_contents = entry.get("required_contents", {})

        # Step 1: resolve URL
        try:
            resolved_url = self._resolve_url(raw_url, page, last_url)
        except Exception as exc:
            return self._fail_check(entry, error=f"URL resolution failed: {exc}")

        # Step 2: navigate (with retry on interrupted/crashed navigation)
        # Try networkidle first (needed for Vue.js comment components), fall back to
        # domcontentloaded if networkidle times out (GitLab project pages with activity feeds).
        import time as _time_eval
        nav_error = None
        for _nav_attempt in range(2):
            try:
                page.goto(resolved_url, wait_until="networkidle", timeout=45000)
                nav_error = None
                break
            except Exception:
                try:
                    page.goto(resolved_url, wait_until="domcontentloaded", timeout=20000)
                    # Wait briefly for Vue to mount critical components (comments, notes-list)
                    page.wait_for_timeout(3000)
                    nav_error = None
                    break
                except Exception as exc2:
                    nav_error = exc2
                    if _nav_attempt == 0:
                        _time_eval.sleep(3)  # Brief pause before retry
        if nav_error is not None:
            return self._fail_check(entry, resolved_url=resolved_url, error=f"Navigation failed: {nav_error}")

        # Step 3: if the locator references 'notes-list', wait for it to appear in the DOM.
        # Vue.js renders the comment list asynchronously after networkidle; we must poll.
        if locator and "notes-list" in locator:
            try:
                page.wait_for_selector('[id="notes-list"]', timeout=15000)
                # Also wait for at least one child element (the actual notes)
                page.wait_for_function(
                    'document.querySelector(\'[id="notes-list"]\') && '
                    'document.querySelector(\'[id="notes-list"]\').children.length > 0',
                    timeout=10000,
                )
                # Scroll to the bottom to trigger lazy-loading of later notes (e.g. a
                # newly posted comment that appears after the initial viewport batch).
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)
                # Re-scroll in case the page extended further after first scroll
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1000)
                # Wait for at least one .timeline-discussion-body to be rendered —
                # this is the specific class the lastElementChild locator queries.
                try:
                    page.wait_for_selector(".timeline-discussion-body", timeout=5000)
                except Exception:
                    pass
            except Exception:
                pass  # Fall through to locator eval which will surface the error

        # Step 3: extract content via locator
        try:
            content = self._extract_content(page, locator)
        except Exception as exc:
            return self._fail_check(entry, resolved_url=resolved_url, error=f"Locator failed: {exc}")

        # Step 4: check required_contents
        passed, missing, excluded_found = self._check_contents(content, required_contents)

        return {
            "passed": passed,
            "raw_url": raw_url,
            "resolved_url": resolved_url,
            "locator": locator,
            "required_contents": required_contents,
            "extracted_content": content[:500] if content else None,
            "missing": missing,
            "excluded_found": excluded_found,
            "error": None,
        }

    # ------------------------------------------------------------------
    # URL resolution (decision tree)
    # ------------------------------------------------------------------

    def _resolve_url(
        self,
        raw_url: str,
        page: Page,
        last_url: Optional[str],
    ) -> str:
        """
        Resolve a program_html url field to a navigable URL.

        Patterns handled:
          - "last"                                  → last_url
          - "func:reddit_get_post_url('__last_url__')" → newest post on subreddit
          - "func:shopping_get_latest_order_url()"  → latest order detail page
          - "__PLACEHOLDER__/path"                  → substitute base URL
          - direct URL / relative path              → as-is or resolved
        """
        if not raw_url:
            return last_url or page.url

        # 1. "last" → use tracked last URL
        if raw_url.strip().lower() == "last":
            if not last_url:
                raise ValueError(
                    '"last" URL requested but no last_url was provided. '
                    "Pass last_url= to evaluate()."
                )
            return last_url

        # 2. func: patterns
        if raw_url.startswith("func:"):
            return self._resolve_func_url(raw_url, page, last_url)

        # 3. Literal URL with placeholder tokens
        resolved = self._resolve_placeholder(raw_url)

        # 4. Relative path (starts with / or ../): treat relative to any known base
        if resolved.startswith("/") or resolved.startswith(".."):
            # Prefix with the first matching base or fall back to current origin
            origin = page.url.split("/")[0] + "//" + page.url.split("/")[2]
            resolved = origin + "/" + resolved.lstrip("/")

        return resolved

    def _resolve_func_url(
        self,
        raw_url: str,
        page: Page,
        last_url: Optional[str],
    ) -> str:
        """Resolve func: URL patterns."""

        # func:reddit_get_post_url('__last_url__')
        if "reddit_get_post_url" in raw_url:
            subreddit_url = last_url
            if not subreddit_url:
                raise ValueError(
                    "reddit_get_post_url requires a last_url (subreddit URL) "
                    "but none was provided."
                )
            return self._reddit_get_post_url(page, subreddit_url)

        # func:shopping_get_latest_order_url()
        if "shopping_get_latest_order_url" in raw_url:
            return self._shopping_get_latest_order_url(page)

        raise ValueError(f"Unknown func: pattern in URL: {raw_url!r}")

    def _reddit_get_post_url(self, page: Page, subreddit_url: str) -> str:
        """
        Return the URL of the most recently submitted post in a subreddit.

        If subreddit_url is already a post URL (contains a numeric post ID),
        return it directly — this is the post the agent just created.
        Otherwise navigate to the subreddit's /new sort and return the first post.
        """
        import re as _re
        reddit_base = self.base_urls.get("__REDDIT__", DEFAULT_BASE_URLS["__REDDIT__"])

        # If last_url is already a post URL (/f/{forum}/{id}/{slug}), use it directly.
        # This handles create_post tasks where final_url is the newly created post.
        post_url_m = _re.match(
            r'(https?://[^/]+/f/[^/]+/\d+/[^/]*)',
            subreddit_url or "",
        )
        if post_url_m:
            return post_url_m.group(1)

        # Extract just the subreddit base from the URL.
        # Handles /f/{forum} forms.
        m = _re.match(r'(https?://[^/]+/f/[^/]+)', subreddit_url)
        if m:
            subreddit_url = m.group(1)

        # Postmill uses path-based sort: /f/{forum}/new  (not ?sort=new)
        sort_url = subreddit_url.rstrip("/") + "/new"
        page.goto(sort_url, wait_until="networkidle", timeout=20000)

        # Find the first local post link on the page.
        # For text posts: h1.submission__title a links to /f/{forum}/{id}/{slug}.
        # For link posts: that same element links to the external article.
        # Use article-scoped search so we prefer the local post URL.
        forum_slug = subreddit_url.rstrip("/").split("/f/")[-1]
        local_pattern = _re.compile(rf"^/f/{_re.escape(forum_slug)}/\d+/", _re.IGNORECASE)

        for article in page.query_selector_all("article"):
            # Prefer a link that matches /f/{forum}/{id}/
            for lnk in article.query_selector_all("a[href]"):
                href = lnk.get_attribute("href") or ""
                if local_pattern.match(href):
                    return reddit_base.rstrip("/") + href
            # Fallback: any /f/ link in the article
            for lnk in article.query_selector_all("a[href^='/f/']"):
                href = lnk.get_attribute("href") or ""
                if _re.search(r"/\d+/", href) and "/edit" not in href:
                    return reddit_base.rstrip("/") + href

        # Last-resort: any matching link on the page
        for lnk in page.query_selector_all(f"a[href*='/f/{forum_slug}/']"):
            href = lnk.get_attribute("href") or ""
            if _re.search(r"/\d+/", href):
                return reddit_base.rstrip("/") + href

        raise ValueError(
            f"Could not find any post link on subreddit page: {sort_url}"
        )

    def _shopping_get_latest_order_url(self, page: Page) -> str:
        """
        Navigate to the shopping order history page and return the view URL
        for the most recently placed order (first row in the table).
        """
        from api.shopping_pw.order import get_order_history

        # Follow this evaluator's storefront, not the package default — under
        # --multi-docker the order lives on the worker instance.
        orders = get_order_history(
            page,
            base_url=self.base_urls.get(
                "__SHOPPING__", DEFAULT_BASE_URLS["__SHOPPING__"]
            ),
        )
        if not orders:
            raise ValueError("No orders found in shopping order history.")
        return orders[0].view_url

    def _resolve_placeholder(self, url: str) -> str:
        """Replace __PLACEHOLDER__ tokens with real base URLs."""
        for token, base in self.base_urls.items():
            url = url.replace(token, base)
        return url

    # ------------------------------------------------------------------
    # Content extraction
    # ------------------------------------------------------------------

    def _extract_content(self, page: Page, locator: str) -> str:
        """
        Extract content from the current page using the locator expression.

        Handles:
          - Empty locator      → full page outerText via document.body.outerText
          - JS expression      → page.evaluate(locator)
          - func: expression   → custom Python-side function (e.g. gitlab member role)
        """
        if not locator:
            return page.evaluate("document.body.outerText") or ""

        if locator.startswith("func:"):
            return self._resolve_func_locator(page, locator)

        # Standard JS expression evaluated in page context
        result = page.evaluate(locator)
        if result is None:
            return ""
        return str(result)

    def _resolve_func_locator(self, page: Page, locator: str) -> str:
        """
        Handle func: patterns in the locator field.

        Currently supported:
          func:gitlab_get_project_memeber_role(__page__, '<username>')
          func:shopping_get_sku_latest_review_rating('<sku>')
          func:shopping_get_sku_latest_review_author('<sku>')
          func:shopping_admin_get_product_attr_by_id('<product_id>', '<attr>')
          func:shopping_admin_get_configurable_children_by_id('<product_id>')
        """
        # func:gitlab_get_project_memeber_role(__page__, 'username')
        match = re.match(
            r"func:gitlab_get_project_memeber_role\(__page__,\s*'([^']+)'\)",
            locator,
        )
        if match:
            username = match.group(1)
            return self._gitlab_get_project_member_role(page, username)

        # func:shopping_get_sku_latest_review_rating('SKU')
        match = re.match(
            r"func:shopping_get_sku_latest_review_rating\('([^']+)'\)",
            locator,
        )
        if match:
            sku = match.group(1)
            return self._shopping_get_sku_latest_review_rating(sku)

        # func:shopping_get_sku_latest_review_author('SKU')
        match = re.match(
            r"func:shopping_get_sku_latest_review_author\('([^']+)'\)",
            locator,
        )
        if match:
            sku = match.group(1)
            return self._shopping_get_sku_latest_review_author(sku)

        # func:shopping_get_latest_order_items('<email>')
        match = re.match(
            r"func:shopping_get_latest_order_items\('([^']+)'\)",
            locator,
        )
        if match:
            return self._shopping_get_latest_order_items(match.group(1))

        # func:shopping_get_order_delivery_addresses('<email>')
        match = re.match(
            r"func:shopping_get_order_delivery_addresses\('([^']+)'\)",
            locator,
        )
        if match:
            return self._shopping_get_order_delivery_addresses(match.group(1))

        # func:shopping_get_customer_cart_items('email', 'password')
        match = re.match(
            r"func:shopping_get_customer_cart_items\('([^']+)',\s*'([^']+)'\)",
            locator,
        )
        if match:
            email, password = match.group(1), match.group(2)
            return self._shopping_get_customer_cart_items(email, password)

        # func:shopping_admin_get_product_attr_by_id('<product_id>', '<attr>')
        # Sidesteps Knockout-rendered admin forms that don't expose
        # <input name="product[X]"> at DOM-query time (see P1 in the eval
        # audit — tasks 423/453/455/459/460/461 all hit querySelector-null).
        match = re.match(
            r"func:shopping_admin_get_product_attr_by_id\('([^']+)',\s*'([^']+)'\)",
            locator,
        )
        if match:
            product_id, attr_name = match.group(1), match.group(2)
            return self._shopping_admin_get_product_attr_by_id(product_id, attr_name)

        # func:shopping_admin_get_configurable_children_by_id('<product_id>')
        match = re.match(
            r"func:shopping_admin_get_configurable_children_by_id\('([^']+)'\)",
            locator,
        )
        if match:
            product_id = match.group(1)
            return self._shopping_admin_get_configurable_children_by_id(product_id)

        # func:shopping_admin_get_order_status('<order_id>')  -- P4
        match = re.match(r"func:shopping_admin_get_order_status\('([^']+)'\)", locator)
        if match:
            return self._shopping_admin_get_order_status(match.group(1))

        # func:shopping_admin_get_order_last_comment('<order_id>')  -- P2
        match = re.match(r"func:shopping_admin_get_order_last_comment\('([^']+)'\)", locator)
        if match:
            return self._shopping_admin_get_order_last_comment(match.group(1))

        # func:shopping_admin_get_order_shipment_tracks('<order_id>')  -- P3
        match = re.match(r"func:shopping_admin_get_order_shipment_tracks\('([^']+)'\)", locator)
        if match:
            return self._shopping_admin_get_order_shipment_tracks(match.group(1))

        # func:shopping_admin_get_cms_page_title('<page_id>')  -- P5
        match = re.match(r"func:shopping_admin_get_cms_page_title\('([^']+)'\)", locator)
        if match:
            return self._shopping_admin_get_cms_page_title(match.group(1))

        # func:shopping_admin_review_exists('<review_id>')  -- P6
        match = re.match(r"func:shopping_admin_review_exists\('([^']+)'\)", locator)
        if match:
            return self._shopping_admin_review_exists(match.group(1))

        raise ValueError(f"Unknown func: pattern in locator: {locator!r}")

    def _gitlab_get_project_member_role(self, page: Page, username: str) -> str:
        """
        Extract the role of a project member using the GitLab REST API.

        Called after navigating to the project members page, so the browser
        session is already authenticated. Uses fetch() in page context to call
        the members API — this avoids DOM/pagination fragility entirely.

        Access level → role name mapping (GitLab standard):
          10 → Guest, 20 → Reporter, 30 → Developer,
          40 → Maintainer, 50 → Owner
        """
        ACCESS_LEVEL_NAMES = {
            10: "Guest",
            20: "Reporter",
            30: "Developer",
            40: "Maintainer",
            50: "Owner",
        }

        # Extract project path from the current URL.
        # URL pattern: /namespace/project/-/project_members
        current_url = page.url
        path = current_url.split("//", 1)[-1].split("/", 1)[-1]  # strip origin
        # Drop everything from "/-/" onward
        project_path = path.split("/-/")[0].strip("/")

        encoded_path = project_path.replace("/", "%2F")
        api_url = f"/api/v4/projects/{encoded_path}/members/all?query={username}&per_page=100"

        result = page.evaluate(
            """
            async (apiUrl) => {
                try {
                    const r = await fetch(apiUrl, {credentials: 'include'});
                    if (!r.ok) return null;
                    const members = await r.json();
                    return members;
                } catch (e) {
                    return null;
                }
            }
            """,
            api_url,
        )

        if result and isinstance(result, list):
            for member in result:
                if member.get("username", "").lower() == username.lower():
                    level = member.get("access_level")
                    return ACCESS_LEVEL_NAMES.get(level, str(level))

        return ""

    def _shopping_get_admin_token(self, force_refresh: bool = False) -> str:
        """Return a cached or freshly-minted Magento admin token.

        force_refresh bypasses the on-disk cache.  Callers use it after a 401:
        .server_env can hold a token minted against a previous container
        lifetime, which Magento rejects with "The consumer isn't authorized"
        rather than an expiry message.
        """
        from pathlib import Path as _Path

        shopping_base = self.base_urls.get("__SHOPPING__", DEFAULT_BASE_URLS["__SHOPPING__"])
        server_env = _Path(__file__).parent.parent / "config" / ".server_env"

        # config/.server_env is a single shared file, but under --multi-docker
        # each worker is a distinct Magento with its own admin session. A token
        # minted against a worker is therefore read-only-in-memory: writing it
        # back would hand it to the next task on a different instance, which
        # answers 401.
        is_default_instance = (
            shopping_base.rstrip("/")
            == DEFAULT_BASE_URLS["__SHOPPING__"].rstrip("/")
        )
        cache: Dict[str, str] = getattr(self, "_shopping_token_cache", None) or {}
        if not force_refresh and cache.get(shopping_base):
            return cache[shopping_base]

        token = ""
        if not force_refresh and is_default_instance and server_env.exists():
            for line in server_env.read_text().splitlines():
                if line.strip().startswith("ADMIN_AUTH_TOKEN="):
                    token = line.strip().split("=", 1)[1].strip()
                    break
        if not token:
            from config.init_tokens.refresh_shopping_tokens import (
                refresh_tokens,
                write_admin_token_to_env,
            )
            token = refresh_tokens(base_url=shopping_base)
            if is_default_instance:
                try:
                    write_admin_token_to_env(token, server_env)
                except Exception:
                    pass  # A stale cache is recoverable; a failed write is not.

        cache[shopping_base] = token
        self._shopping_token_cache = cache
        return token

    def _shopping_get_json(self, url: str) -> Any:
        """GET a storefront (7770) REST endpoint with an admin token.

        Retries once with a freshly-minted token on 401.  Raises on any other
        non-200 so the caller can report why a check could not be evaluated.
        """
        import requests as _requests

        token = self._shopping_get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = _requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 401:
            token = self._shopping_get_admin_token(force_refresh=True)
            resp = _requests.get(
                url, headers={"Authorization": f"Bearer {token}"}, timeout=20
            )
        if resp.status_code != 200:
            endpoint = url.split("/rest/", 1)[-1].split("?", 1)[0]
            raise RuntimeError(
                f"GET {endpoint} -> HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    def _shopping_get_customer_id(self, email: str) -> int:
        """Return the numeric storefront customer id for `email`."""
        shopping_base = self.base_urls.get(
            "__SHOPPING__", DEFAULT_BASE_URLS["__SHOPPING__"]
        )
        data = self._shopping_get_json(
            f"{shopping_base}/rest/V1/customers/search"
            f"?searchCriteria[filter_groups][0][filters][0][field]=email"
            f"&searchCriteria[filter_groups][0][filters][0][value]={email}"
            f"&searchCriteria[filter_groups][0][filters][0][conditionType]=eq"
            f"&searchCriteria[pageSize]=1"
        )
        items = (data or {}).get("items") or []
        if not items:
            raise RuntimeError(f"no customer found for {email!r}")
        return int(items[0]["id"])

    def _shopping_get_latest_review_for_sku(self, sku: str) -> dict:
        """
        Fetch the most recently created review for a product SKU via the
        Magento REST API.  Returns the review dict, or {} if none found.

        Checks approved reviews first (product reviews endpoint), then falls
        back to the admin review search endpoint to catch pending reviews too —
        agents may submit reviews that land in "Pending" status rather than
        "Approved", and both represent a successful submission.
        """
        import requests as _requests

        shopping_base = self.base_urls.get("__SHOPPING__", DEFAULT_BASE_URLS["__SHOPPING__"])
        token = self._shopping_get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}

        # First try the product reviews endpoint (approved reviews only).
        url = f"{shopping_base}/rest/V1/products/{sku}/reviews"
        resp = _requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            reviews = resp.json()
            if reviews:
                return max(reviews, key=lambda r: r.get("id", 0))

        # Fall back to admin search to also find pending/unapproved reviews.
        # Requires knowing the product's entity_id — get it from the product API.
        prod_resp = _requests.get(
            f"{shopping_base}/rest/V1/products/{sku}",
            headers=headers,
            timeout=15,
        )
        if prod_resp.status_code != 200:
            return {}
        product_id = prod_resp.json().get("id")
        if not product_id:
            return {}

        search_url = (
            f"{shopping_base}/rest/V1/reviews"
            f"?searchCriteria[filter_groups][0][filters][0][field]=entity_pk_value"
            f"&searchCriteria[filter_groups][0][filters][0][value]={product_id}"
            f"&searchCriteria[sortOrders][0][field]=id"
            f"&searchCriteria[sortOrders][0][direction]=DESC"
            f"&searchCriteria[pageSize]=1"
        )
        search_resp = _requests.get(search_url, headers=headers, timeout=15)
        if search_resp.status_code != 200:
            return {}
        data = search_resp.json()
        items = data.get("items", [])
        if not items:
            return {}
        return items[0]

    def _shopping_get_sku_latest_review_rating(self, sku: str) -> str:
        """
        Return the rating percentage (e.g. "100", "80", "60", "40", "20")
        of the most recent review for the given SKU.
        5 stars = 100, 4 = 80, 3 = 60, 2 = 40, 1 = 20.
        Returns "" if no reviews exist.

        Used by: func:shopping_get_sku_latest_review_rating('<sku>')
        """
        review = self._shopping_get_latest_review_for_sku(sku)
        ratings = review.get("ratings", [])
        if not ratings:
            return ""
        return str(ratings[0].get("percent", ""))

    def _shopping_get_sku_latest_review_author(self, sku: str) -> str:
        """
        Return the nickname (author) of the most recent review for the
        given SKU.  Returns "" if no reviews exist.

        Used by: func:shopping_get_sku_latest_review_author('<sku>')
        """
        review = self._shopping_get_latest_review_for_sku(sku)
        return review.get("nickname", "")

    def _shopping_get_latest_order_items(self, email: str) -> str:
        """
        Return the SKU and product name of every item on the customer's most
        recent order, one per line.

        Replaces the browser round-trip of ``func:shopping_get_latest_order_url()``
        + ``document.querySelector(".order-details-items.ordered").outerText``.
        That path cannot work under --multi-docker: the worker containers still
        advertise the shared instance as Magento's ``web/unsecure/base_url``, so
        every storefront navigation to a worker bounces to the shared host and
        the check grades *that* instance's newest order. Tasks 436-440 are the
        worked example — five different reorders, all graded against the same
        stale floor-lamp order sitting on the shared instance.

        "Most recent" is by ``entity_id``, a monotonic autoincrement. Sorting by
        ``created_at`` (what the storefront grid does) is wrong here: seed orders
        carry backdated timestamps, so a freshly placed order does not reliably
        sort first.

        Used by: func:shopping_get_latest_order_items('<email>')
        """
        shopping_base = self.base_urls.get(
            "__SHOPPING__", DEFAULT_BASE_URLS["__SHOPPING__"]
        )
        data = self._shopping_get_json(
            f"{shopping_base}/rest/V1/orders"
            f"?searchCriteria[filter_groups][0][filters][0][field]=customer_email"
            f"&searchCriteria[filter_groups][0][filters][0][value]={email}"
            f"&searchCriteria[filter_groups][0][filters][0][conditionType]=eq"
            f"&searchCriteria[sortOrders][0][field]=entity_id"
            f"&searchCriteria[sortOrders][0][direction]=DESC"
            f"&searchCriteria[pageSize]=1"
        )
        items = (data or {}).get("items") or []
        if not items:
            raise RuntimeError(f"no orders found for {email!r} at {shopping_base}")

        lines: List[str] = []
        for item in items[0].get("items") or []:
            # Emit both so a must_include can cite either form, matching what
            # the old .order-details-items.ordered outerText contained.
            for field in ("sku", "name"):
                value = item.get(field)
                if value and value not in lines:
                    lines.append(str(value))
        return "\n".join(lines)

    def _shopping_get_order_delivery_addresses(self, email: str) -> str:
        """
        Return every street/city/region/postcode line across ALL of the
        customer's orders (billing + shipping), one per line.

        Ground truth for the order delivery-address change tasks (794-798).
        Those tasks were authored for a customer-only agent that cannot edit a
        placed order, so their original reference expected a refusal. Our agent
        has admin API access and CAN rewrite an order's shipping/billing
        address, so the correct ground truth is to verify the mutation landed:
        a successful change makes the requested (distinctive) street appear on
        one of the customer's orders. Scanning every order mirrors
        eval/verify/verify_shopping_mutations.py's order_address_contains, and
        sidesteps having to re-resolve "most recent non-cancelled / oldest /
        first" order-selection rules inside the evaluator.

        Used by: func:shopping_get_order_delivery_addresses('<email>')
        """
        shopping_base = self.base_urls.get(
            "__SHOPPING__", DEFAULT_BASE_URLS["__SHOPPING__"]
        )
        data = self._shopping_get_json(
            f"{shopping_base}/rest/V1/orders"
            f"?searchCriteria[filter_groups][0][filters][0][field]=customer_email"
            f"&searchCriteria[filter_groups][0][filters][0][value]={email}"
            f"&searchCriteria[filter_groups][0][filters][0][conditionType]=eq"
            f"&searchCriteria[pageSize]=100"
        )
        items = (data or {}).get("items") or []
        if not items:
            raise RuntimeError(f"no orders found for {email!r} at {shopping_base}")

        lines: List[str] = []

        def _add_address(addr: Dict[str, Any]) -> None:
            for part in addr.get("street") or []:
                if part and str(part) not in lines:
                    lines.append(str(part))
            for field in ("city", "region", "postcode"):
                value = addr.get(field)
                if value and str(value) not in lines:
                    lines.append(str(value))

        for order in items:
            _add_address(order.get("billing_address") or {})
            for assignment in (
                (order.get("extension_attributes") or {}).get("shipping_assignments") or []
            ):
                _add_address((assignment.get("shipping") or {}).get("address") or {})
        return "\n".join(lines)

    def _shopping_get_customer_cart_items(self, email: str, password: str) -> str:
        """
        Return a newline-joined list of the product names in the customer's
        cart, unioned across every store scope.  Returns "" only when the
        customer genuinely has no cart items.

        Two sources are read and merged:

          1. ``GET /V1/carts/mine/items`` with a *customer* token — the quote
             the storefront itself renders.
          2. Every **active** quote owned by the customer, via the admin
             ``GET /V1/carts/search`` — regardless of store scope.

        Source 2 is what makes this correct.  A cart created with an admin
        token can land in the admin store scope (``store_id = 0``), while
        ``carts/mine`` only resolves quotes inside the current website's
        shared store ids.  Items added to such a quote are invisible to source
        1 even though the write succeeded — tasks 431-435 all added the right
        SKU to a ``store_id = 0`` quote and were then graded against an empty
        ``store_id = 1`` quote.

        Inactive quotes are skipped: they are abandoned carts or quotes already
        converted into an order, so their items are no longer "in the cart".

        Raises when *both* sources fail, so an auth or network problem surfaces
        as an eval error instead of masquerading as an empty cart.

        Used by: func:shopping_get_customer_cart_items('<email>', '<password>')
        """
        import requests as _requests

        shopping_base = self.base_urls.get("__SHOPPING__", DEFAULT_BASE_URLS["__SHOPPING__"])

        names: List[str] = []
        seen: set = set()
        errors: List[str] = []

        def _collect(items: Any) -> None:
            for item in items or []:
                name = item.get("name")
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)

        any_source_ok = False

        # --- source 1: the quote the storefront shows ----------------------
        try:
            token_resp = _requests.post(
                f"{shopping_base}/rest/V1/integration/customer/token",
                json={"username": email, "password": password},
                timeout=15,
            )
            if token_resp.status_code != 200:
                raise RuntimeError(f"customer token -> HTTP {token_resp.status_code}")
            token = token_resp.json()
            if not isinstance(token, str):
                raise RuntimeError("customer token response was not a string")
            items_resp = _requests.get(
                f"{shopping_base}/rest/V1/carts/mine/items",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if items_resp.status_code != 200:
                raise RuntimeError(f"carts/mine/items -> HTTP {items_resp.status_code}")
            payload = items_resp.json()
            if not isinstance(payload, list):
                raise RuntimeError("carts/mine/items did not return a list")
            _collect(payload)
            any_source_ok = True
        except Exception as exc:
            errors.append(f"carts/mine: {exc}")

        # --- source 2: every active quote for the customer, any store ------
        try:
            customer_id = self._shopping_get_customer_id(email)
            data = self._shopping_get_json(
                f"{shopping_base}/rest/V1/carts/search"
                f"?searchCriteria[filter_groups][0][filters][0][field]=customer_id"
                f"&searchCriteria[filter_groups][0][filters][0][value]={customer_id}"
                f"&searchCriteria[filter_groups][0][filters][0][conditionType]=eq"
                f"&searchCriteria[pageSize]=50"
            )
            for quote in (data or {}).get("items") or []:
                if quote.get("is_active"):
                    _collect(quote.get("items"))
            any_source_ok = True
        except Exception as exc:
            errors.append(f"carts/search: {exc}")

        if not any_source_ok:
            raise RuntimeError("cart lookup failed — " + "; ".join(errors))

        return "\n".join(names)

    # ------------------------------------------------------------------
    # shopping_admin (Luma admin, port 7780) — REST-API-backed locators
    # ------------------------------------------------------------------
    #
    # The Luma admin panel is a separate Magento instance from the storefront
    # (__SHOPPING__ / 7770) with its own DB and its own admin token.  Its
    # product-edit UI is rendered by Knockout components, so DOM locators like
    # `document.querySelector('[name="product[status]"]').value` return null
    # even when the write succeeded.  These helpers hit the admin REST API
    # directly, which is what the agent used to make the change in the first
    # place.

    def _shopping_admin_api_base(self) -> str:
        """
        Return the shopping_admin REST base (no `/admin` suffix).

        The evaluator receives __SHOPPING_ADMIN__ with `/admin` appended so
        browser-side URLs land on the admin UI.  Magento REST endpoints live
        at `<host>/rest/V1/...`, not `<host>/admin/rest/V1/...`, so we strip
        the suffix here.
        """
        base = self.base_urls.get(
            "__SHOPPING_ADMIN__", DEFAULT_BASE_URLS["__SHOPPING_ADMIN__"]
        ).rstrip("/")
        if base.endswith("/admin"):
            base = base[: -len("/admin")]
        return base

    def _shopping_admin_get_token(self) -> str:
        """Fetch (once, cached on the instance) a Bearer token for 7780.

        Deliberately the shopping_admin module, not the shopping one: the
        latter falls back to ADMIN_AUTH_TOKEN in config/.server_env when a mint
        fails, and that key is written by the storefront (:7770) path. Grading
        with a storefront token would read the wrong catalog database and
        report confident, wrong results rather than an error.
        """
        if getattr(self, "_shopping_admin_token", None):
            return self._shopping_admin_token
        from config.init_tokens.refresh_shopping_admin_tokens import refresh_tokens
        self._shopping_admin_token = refresh_tokens(
            base_url=self._shopping_admin_api_base()
        )
        return self._shopping_admin_token

    def _shopping_admin_get_product_by_id(self, product_id) -> dict:
        """Fetch a full product record by numeric product id via REST search.

        Magento's `GET /V1/products/{sku}` is keyed by SKU, not id, so we
        search on `entity_id` and take the first item.
        """
        import requests as _requests

        base = self._shopping_admin_api_base()
        token = self._shopping_admin_get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = (
            f"{base}/rest/V1/products"
            f"?searchCriteria[filter_groups][0][filters][0][field]=entity_id"
            f"&searchCriteria[filter_groups][0][filters][0][value]={product_id}"
            f"&searchCriteria[filter_groups][0][filters][0][conditionType]=eq"
            f"&searchCriteria[pageSize]=1"
        )
        resp = _requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return {}
        items = resp.json().get("items", [])
        return items[0] if items else {}

    def _shopping_admin_get_product_attr_by_id(
        self, product_id, attr_name: str
    ) -> str:
        """
        Return the current value of `attr_name` for a product identified by
        numeric id.  Checks top-level product fields first, then falls back
        to `custom_attributes` (where non-stock attributes like "sale" live).

        Price is formatted as `"NN.NN"` (two decimals) because the WebArena
        ground-truth references match the admin form's rendered string
        (e.g. "62.10", not "62.1").

        Used by: func:shopping_admin_get_product_attr_by_id('<id>', '<attr>')
        """
        prod = self._shopping_admin_get_product_by_id(product_id)
        if not prod:
            return ""
        if attr_name in prod:
            val = prod[attr_name]
            if attr_name == "price" and val is not None:
                try:
                    return f"{float(val):.2f}"
                except (TypeError, ValueError):
                    pass
            return "" if val is None else str(val)
        for attr in prod.get("custom_attributes", []) or []:
            if attr.get("attribute_code") == attr_name:
                v = attr.get("value")
                return "" if v is None else str(v)
        return ""

    # --- P4: order status ---------------------------------------------------
    #
    # Magento's `.status` is single-l "canceled"; WebArena ground truth is
    # "Canceled". _evaluate_string_match's exact_match is case-insensitive
    # substring after S1, so returning the raw API value works.
    def _shopping_admin_get_order_status(self, order_id) -> str:
        """Return the `.status` field for an order via `GET /V1/orders/{id}`.

        Used by: func:shopping_admin_get_order_status('<order_id>')
        """
        import requests as _requests

        base = self._shopping_admin_api_base()
        token = self._shopping_admin_get_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = _requests.get(
            f"{base}/rest/V1/orders/{order_id}", headers=headers, timeout=15
        )
        if resp.status_code != 200:
            return ""
        return str(resp.json().get("status") or "")

    # --- P2: newest comment on an order ------------------------------------
    def _shopping_admin_get_order_last_comment(self, order_id) -> str:
        """Return the newest `comment` on an order via
        `GET /V1/orders/{id}/comments`, sorting by `created_at` DESC.

        Used by: func:shopping_admin_get_order_last_comment('<order_id>')
        """
        import requests as _requests

        base = self._shopping_admin_api_base()
        token = self._shopping_admin_get_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = _requests.get(
            f"{base}/rest/V1/orders/{order_id}/comments",
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            return ""
        items = resp.json().get("items", []) or []
        if not items:
            return ""
        # `id` is a monotonic autoincrement, so max(id) is the newest comment
        # even when created_at collides at second resolution.
        newest = max(items, key=lambda c: c.get("entity_id", c.get("id", 0)))
        return str(newest.get("comment") or "")

    # --- P3: shipment tracking numbers -------------------------------------
    _CARRIER_TITLES = {
        "fedex": "Federal Express",
        "dhl": "DHL",
        "ups": "United Parcel Service",
        "usps": "United States Postal Service",
    }

    def _shopping_admin_get_order_shipment_tracks(self, order_id) -> str:
        """Return one line per shipment track for an order, in the form the
        WebArena ground truth expects:

            Tracking number {track_number} for {carrier_display} assigned

        `carrier_display` prefers the shipping module's default title (a
        stable canonical form — 'Federal Express' for fedex, 'DHL' for dhl,
        'United Parcel Service' for ups, 'United States Postal Service' for
        usps), falls back to the track's `title` field, then to `carrier_code`.
        We also emit the raw title/code line so must_include matches whichever
        form the reference uses.

        Used by: func:shopping_admin_get_order_shipment_tracks('<order_id>')
        """
        import requests as _requests

        base = self._shopping_admin_api_base()
        token = self._shopping_admin_get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = (
            f"{base}/rest/V1/shipments"
            f"?searchCriteria[filter_groups][0][filters][0][field]=order_id"
            f"&searchCriteria[filter_groups][0][filters][0][value]={order_id}"
            f"&searchCriteria[filter_groups][0][filters][0][conditionType]=eq"
        )
        resp = _requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return ""
        lines: List[str] = []
        for shipment in resp.json().get("items", []) or []:
            for track in shipment.get("tracks", []) or []:
                track_number = track.get("track_number") or ""
                title = track.get("title") or ""
                code = (track.get("carrier_code") or "").lower()
                canonical = self._CARRIER_TITLES.get(code, "")
                for display in {canonical, title, code}:
                    if display and track_number:
                        lines.append(
                            f"Tracking number {track_number} for {display} assigned"
                        )
        return "\n".join(lines)

    # --- P5: CMS page title -------------------------------------------------
    def _shopping_admin_get_cms_page_title(self, page_id) -> str:
        """Return the `.title` of a CMS page via `GET /V1/cmsPage/{id}`.

        Used by: func:shopping_admin_get_cms_page_title('<page_id>')
        """
        import requests as _requests

        base = self._shopping_admin_api_base()
        token = self._shopping_admin_get_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = _requests.get(
            f"{base}/rest/V1/cmsPage/{page_id}", headers=headers, timeout=15
        )
        if resp.status_code != 200:
            return ""
        return str(resp.json().get("title") or "")

    # --- P6: review deleted? ------------------------------------------------
    def _shopping_admin_review_exists(self, review_id) -> str:
        """Return `"yes"` if a review with `review_id` still exists, `"no"` if
        it has been deleted (404) or the search returns zero rows.

        The old locator navigated to `/review/product/edit/id/{N}` and looked
        for a "Rating isn't Available" sentinel; after successful deletion the
        admin redirects to the review index and the sentinel never appears.
        Reversing the polarity — check that the review is *gone* — is a much
        cleaner signal, so the accompanying task-file edits change
        `required_contents` to `exact_match: 'no'`.

        Used by: func:shopping_admin_review_exists('<review_id>')
        """
        import requests as _requests

        base = self._shopping_admin_api_base()
        token = self._shopping_admin_get_token()
        headers = {"Authorization": f"Bearer {token}"}
        # Try the direct GET first (Magento 2.4.4+).
        resp = _requests.get(
            f"{base}/rest/V1/reviews/{review_id}", headers=headers, timeout=15
        )
        if resp.status_code == 200:
            return "yes"
        if resp.status_code == 404:
            return "no"
        # Fall back to a searchCriteria lookup — some Magento builds don't
        # expose the singular GET.
        search_url = (
            f"{base}/rest/V1/reviews"
            f"?searchCriteria[filter_groups][0][filters][0][field]=review_id"
            f"&searchCriteria[filter_groups][0][filters][0][value]={review_id}"
            f"&searchCriteria[filter_groups][0][filters][0][conditionType]=eq"
            f"&searchCriteria[pageSize]=1"
        )
        resp = _requests.get(search_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return ""
        return "yes" if (resp.json().get("items") or []) else "no"

    def _shopping_admin_get_configurable_children_by_id(self, product_id) -> str:
        """
        Return a newline-joined list of `sku` and `name` for each child of
        a configurable product identified by numeric id.  Emitting both
        makes must_include checks robust whether the ground truth cites a
        compound name ("Phoebe Zipper Sweatshirt-S-Brown") or a bare SKU.

        Used by: func:shopping_admin_get_configurable_children_by_id('<id>')
        """
        import requests as _requests

        parent = self._shopping_admin_get_product_by_id(product_id)
        parent_sku = parent.get("sku", "")
        if not parent_sku:
            return ""
        base = self._shopping_admin_api_base()
        token = self._shopping_admin_get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{base}/rest/V1/configurable-products/{parent_sku}/children"
        resp = _requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return ""
        lines: List[str] = []
        for child in resp.json() or []:
            if child.get("sku"):
                lines.append(child["sku"])
            if child.get("name"):
                lines.append(child["name"])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Content checking
    # ------------------------------------------------------------------

    def _check_contents(
        self,
        content: str,
        required_contents: Dict[str, Any],
    ):
        """
        Check extracted content against required_contents spec.

        Placeholder tokens like __GITLAB__ and __REDDIT__ in required_contents
        values are expanded to their real base URLs before comparison, so ground
        truth entries such as ``"__GITLAB__/user/repo"`` correctly match the
        actual URL that appears on the page.

        Returns:
            (passed: bool, missing: list, excluded_found: list)
        """
        missing: List[str] = []
        excluded_found: List[str] = []
        content_lower = content.lower()

        # exact_match: full string equality (case-insensitive)
        exact = required_contents.get("exact_match")
        if exact is not None:
            if content.strip().lower() != str(exact).strip().lower():
                missing.append(f"exact_match: {exact!r}")

        # must_include: all items must appear somewhere in content.
        # A nested list item means OR — at least one alternative must match.
        for item in required_contents.get("must_include", []):
            if isinstance(item, list):
                # OR group: at least one alternative must be present
                if not any(
                    self._resolve_placeholder(str(alt)).lower() in content_lower
                    for alt in item
                ):
                    missing.append(item)
            else:
                resolved_item = self._resolve_placeholder(str(item))
                if resolved_item.lower() not in content_lower:
                    missing.append(item)

        # must_exclude: none of these may appear in content
        for item in required_contents.get("must_exclude", []):
            resolved_item = self._resolve_placeholder(str(item))
            if resolved_item.lower() in content_lower:
                excluded_found.append(item)

        passed = not missing and not excluded_found
        return passed, missing, excluded_found

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fail_check(
        entry: Dict[str, Any],
        resolved_url: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "passed": False,
            "raw_url": entry.get("url"),
            "resolved_url": resolved_url,
            "locator": entry.get("locator"),
            "required_contents": entry.get("required_contents"),
            "extracted_content": None,
            "missing": [],
            "excluded_found": [],
            "error": error,
        }
