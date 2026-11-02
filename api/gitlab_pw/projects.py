"""GitLab project management helpers."""

import json as _json
import re as _re
import urllib.request as _urlreq
import urllib.parse as _urlparse
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError

from .constants import (
    GITLAB_DOMAIN,
    NEW_PROJECT_URL,
    DASHBOARD_PROJECTS_URL,
    Selectors,
    get_project_settings_url,
)

# ---------------------------------------------------------------------------
# GitLab API helpers (used for fork — more reliable than the Vue SPA UI)
# ---------------------------------------------------------------------------

def _gitlab_api_request(method: str, path: str, body: Optional[dict] = None, token: Optional[str] = None) -> dict:
    """Make a GitLab API request and return the parsed JSON response."""
    url = f"{GITLAB_DOMAIN}/api/v4{path}"
    data = _json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = _urlreq.Request(url, data=data, headers=headers, method=method)
    try:
        with _urlreq.urlopen(req, timeout=15) as resp:
            return _json.loads(resp.read())
    except Exception as exc:
        return {"_error": str(exc)}


def _get_oauth_token(page: Page) -> Optional[str]:
    """Obtain an OAuth token for the currently logged-in GitLab user via the page cookies."""
    # Extract user credentials from stored config and get a fresh OAuth token.
    from .config import get_default_gitlab_credentials
    username, password = get_default_gitlab_credentials()
    body = {
        "grant_type": "password",
        "username": username,
        "password": password,
    }
    result = _gitlab_api_request("POST", "/oauth/token".replace("/api/v4", ""), body=body)
    # The path above has a bug — fix: call directly
    url = f"{GITLAB_DOMAIN}/oauth/token"
    data = _json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    req = _urlreq.Request(url, data=data, headers=headers, method="POST")
    try:
        with _urlreq.urlopen(req, timeout=15) as resp:
            resp_data = _json.loads(resp.read())
            return resp_data.get("access_token")
    except Exception:
        return None


def _api_fork_project(token: str, source_project_id: int, target_namespace_id: int) -> dict:
    """Fork a project via GitLab API. Returns the response dict."""
    return _gitlab_api_request(
        "POST",
        f"/projects/{source_project_id}/fork",
        body={"namespace_id": target_namespace_id},
        token=token,
    )


def _api_find_project(token: str, namespace: str, project: str) -> Optional[dict]:
    """Find a project by namespace/project path via GitLab API. Returns project dict or None."""
    path = _urlparse.quote(f"{namespace}/{project}", safe="")
    result = _gitlab_api_request("GET", f"/projects/{path}", token=token)
    if "id" in result:
        return result
    return None


def _api_get_user_namespace_id(token: str) -> Optional[int]:
    """Get the namespace ID of the currently authenticated user."""
    result = _gitlab_api_request("GET", "/user", token=token)
    return result.get("namespace_id") or result.get("id")


def _api_wait_for_import(token: str, project_id: int, timeout_s: int = 30) -> bool:
    """Wait for a forked project's import to finish. Returns True on success."""
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = _gitlab_api_request("GET", f"/projects/{project_id}", token=token)
        status = result.get("import_status", "")
        if status in ("finished", "none", ""):
            return True
        if status == "failed":
            return False
        time.sleep(2)
    return False


@dataclass
class Project:
    """Representation of a GitLab project."""

    name: str
    slug: str
    namespace: str
    url: str
    visibility: Optional[str] = None  # "private", "internal", "public"


@dataclass
class CreateProjectResult:
    """Result of attempting to create a project."""

    success: bool
    project_slug: Optional[str]
    project_url: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class DeleteProjectResult:
    """Result of attempting to delete a project."""

    success: bool
    error_message: Optional[str] = None


@dataclass
class ForkProjectResult:
    """Result of attempting to fork a project."""

    success: bool
    fork_url: Optional[str] = None
    error_message: Optional[str] = None


def create_private_project(
    page: Page,
    project_name: str,
    namespace_name: Optional[str] = None,
    timeout_for_dropdown: int = 5000,
) -> CreateProjectResult:
    """
    Create a new private project in GitLab.

    FIXED: Uses sync pattern instead of expect_navigation context manager.

    Args:
        page: Playwright Page instance
        project_name: Name for the new project
        namespace_name: Optional namespace (group/user) to create under.
                       If not provided, uses the default prefilled namespace.
        timeout_for_dropdown: Timeout in ms for namespace dropdown to load

    Returns:
        CreateProjectResult with success status, slug, and any error message
    """
    page.goto(NEW_PROJECT_URL, wait_until="networkidle")

    # Fill project name
    name_locator = page.get_by_label("Project name")
    try:
        name_locator.wait_for(state="visible", timeout=10000)
    except TimeoutError:
        return CreateProjectResult(
            success=False,
            project_slug=None,
            error_message="Project name field not found"
        )

    name_locator.fill(project_name)

    # Wait for JS-driven URL slug generation
    page.wait_for_timeout(2000)

    # Handle namespace selection if specified
    if namespace_name:
        result = _select_namespace(page, namespace_name, timeout_for_dropdown)
        if result:  # result is an error message
            return CreateProjectResult(
                success=False,
                project_slug=None,
                error_message=result
            )

    # Select private visibility
    try:
        page.locator("#blank-project-pane").get_by_text(
            "PrivateProject access must be"
        ).check()
    except Exception:
        # Try alternative selector
        try:
            page.locator("label[for='project_visibility_level_0']").check()
        except Exception:
            pass  # Continue anyway, may have different visibility options

    # Submit - FIXED: sync pattern
    page.get_by_role("button", name="Create project").click()
    page.wait_for_load_state("networkidle")

    # Check if we got a URL collision error
    if page.url.endswith("projects"):
        return _handle_project_creation_error(page, project_name, namespace_name)

    # Check for error messages
    error_container = page.locator(Selectors.PROJECT_ERROR_CONTAINER)
    if error_container.count() > 0:
        errors = error_container.locator("ul li").all_inner_texts()
        error_message = "; ".join(errors)

        # Check if project already exists
        if "taken" in error_message.lower():
            return CreateProjectResult(
                success=True,
                project_slug=project_name,
                project_url=None,
                error_message=f"Project may already exist: {error_message}"
            )

        return CreateProjectResult(
            success=False,
            project_slug=None,
            error_message=error_message
        )

    # Extract project slug from final URL
    final_url = page.url
    parsed = urlparse(final_url)
    project_slug = parsed.path.strip("/").split("/")[-1]

    return CreateProjectResult(
        success=True,
        project_slug=project_slug,
        project_url=final_url,
        error_message=None
    )


def _cleanup_stuck_fork(page: Page, username: str, project_slug: str) -> None:
    """
    If a fork at {username}/{project_slug} exists but shows 'Page Not Found'
    (i.e. stuck in an initializing state), delete it via the settings UI so
    a fresh fork can be created.
    """
    fork_url = f"{GITLAB_DOMAIN}/{username}/{project_slug}"
    try:
        page.goto(fork_url, wait_until="networkidle", timeout=10000)
    except Exception:
        return

    # If the page loaded fine (not 404), the fork is alive — don't delete it.
    pnf = page.locator(Selectors.PAGE_NOT_FOUND)
    if pnf.count() == 0:
        return
    text = pnf.text_content() or ""
    if "Page Not Found" not in text:
        return

    # Fork page shows 404 but the fork may exist in a stuck state.
    # Try to delete it via the settings page.
    settings_url = f"{GITLAB_DOMAIN}/{username}/{project_slug}/-/edit"
    try:
        page.goto(settings_url, wait_until="networkidle", timeout=10000)
    except Exception:
        return

    # If settings page also 404s, there's nothing to clean up.
    pnf2 = page.locator(Selectors.PAGE_NOT_FOUND)
    if pnf2.count() > 0:
        return

    # Use the delete_project flow
    try:
        delete_project(page, username, project_slug)
    except Exception:
        pass


def fork_project(
    page: Page,
    source_namespace: str,
    source_project: str,
) -> ForkProjectResult:
    """
    Fork an existing GitLab project into the currently logged-in user's namespace.

    Navigates to the GitLab fork UI at
    /{source_namespace}/{source_project}/-/forks/new, selects the user's
    own namespace from the dropdown, and submits the form.

    Args:
        page: Playwright Page instance (must be logged in)
        source_namespace: Namespace of the project to fork (username or group)
        source_project: Name of the project to fork

    Returns:
        ForkProjectResult with success status and the fork URL
    """
    fork_url = f"{GITLAB_DOMAIN}/{source_namespace}/{source_project}/-/forks/new"
    # Use a try/except here because the page may be in a bad state (e.g. chrome-error://)
    # from a previous navigation. Retry once if the first goto fails.
    try:
        page.goto(fork_url, wait_until="networkidle", timeout=15000)
    except Exception:
        # Recover: navigate to a neutral page first, then retry
        try:
            page.goto(GITLAB_DOMAIN, wait_until="networkidle", timeout=10000)
        except Exception:
            pass
        try:
            page.goto(fork_url, wait_until="networkidle", timeout=15000)
        except Exception:
            return ForkProjectResult(
                success=False,
                error_message=f"Could not navigate to fork page: {fork_url}"
            )

    # Check for page not found
    not_found = page.locator(Selectors.PAGE_NOT_FOUND)
    if not_found.count() > 0:
        text = not_found.text_content() or ""
        if "Page Not Found" in text or "404" in text:
            return ForkProjectResult(
                success=False,
                error_message=f"Source project not found: {fork_url}"
            )

    # GitLab's Vue-based fork form (14+) has a namespace dropdown.
    # The toggle button opens a list; each namespace is a
    # button[data-qa-selector="select_namespace_dropdown_item"].
    # We open the dropdown, pick the first item (the current user's
    # personal namespace), then click "Fork project".
    #
    # IMPORTANT: This is a Vue SPA — after clicking the fork submit button,
    # the page URL does NOT change (the fork is submitted via AJAX to
    # /api/v4/projects/{id}/fork). We must navigate to the expected fork
    # URL directly to confirm success.

    NAMESPACE_DROPDOWN_TOGGLE = (
        # Vue dropdown toggle — the btn-group that shows the current namespace
        "div[role='group'].btn-group button:first-child, "
        "button[data-testid='select-namespace-dropdown'], "
        ".dropdown-menu-toggle"
    )
    NAMESPACE_ITEM = "button[data-qa-selector='select_namespace_dropdown_item']"
    # Verified selector: data-testid='submit-button' (NOT type='submit' which doesn't exist)
    FORK_SUBMIT = "button[data-testid='submit-button']"

    # Wait for the fork form to appear
    try:
        page.wait_for_selector("input#fork-name, input[id='fork-name']", timeout=10000)
    except TimeoutError:
        return ForkProjectResult(
            success=False,
            error_message="Fork form not found on page — project may not exist or already forked"
        )

    # Open the namespace dropdown if present
    try:
        toggle = page.locator(NAMESPACE_DROPDOWN_TOGGLE).first
        if toggle.is_visible():
            toggle.click()
            page.wait_for_timeout(500)
    except Exception:
        pass  # Dropdown may already be open or not needed

    # Click the first namespace item (current user's personal namespace)
    try:
        page.wait_for_selector(NAMESPACE_ITEM, timeout=5000)
        page.locator(NAMESPACE_ITEM).first.click()
        page.wait_for_timeout(300)
    except TimeoutError:
        # Namespace may be pre-selected; continue to submit
        pass

    # Read the fork name from the input (GitLab may slugify it)
    fork_slug = source_project
    try:
        fork_name_input = page.locator("input#fork-name, input[id='fork-name']").first
        fork_slug = fork_name_input.input_value() or source_project
    except Exception:
        pass

    # Determine the logged-in username BEFORE submitting (while we're still on the fork form)
    logged_in_user = "byteblaze"  # default
    try:
        user_link = page.locator("a[data-testid='user-menu-toggle'], .header-user-dropdown-toggle a").first
        if user_link.count() > 0:
            href = user_link.get_attribute("href") or ""
            if href.startswith("/"):
                candidate = href.strip("/").split("/")[0]
                if candidate:
                    logged_in_user = candidate
    except Exception:
        pass

    # Before submitting: if the fork already exists in byteblaze's namespace
    # but is stuck (e.g. from a previous failed run), delete it first.
    _cleanup_stuck_fork(page, logged_in_user, fork_slug)

    # Submit the fork form via the verified submit button
    try:
        page.goto(fork_url, wait_until="networkidle", timeout=15000)
    except Exception:
        pass
    # Re-read fork slug after re-navigation (in case page state changed)
    try:
        fork_name_input2 = page.locator("input#fork-name, input[id='fork-name']").first
        fork_slug = fork_name_input2.input_value() or fork_slug
    except Exception:
        pass
    # Re-open namespace dropdown if needed after re-navigation
    try:
        toggle2 = page.locator(NAMESPACE_DROPDOWN_TOGGLE).first
        if toggle2.is_visible():
            toggle2.click()
            page.wait_for_timeout(500)
    except Exception:
        pass
    try:
        page.wait_for_selector(NAMESPACE_ITEM, timeout=3000)
        page.locator(NAMESPACE_ITEM).first.click()
        page.wait_for_timeout(300)
    except Exception:
        pass
    try:
        page.wait_for_selector(FORK_SUBMIT, timeout=5000)
        page.locator(FORK_SUBMIT).click()
    except TimeoutError:
        return ForkProjectResult(
            success=False,
            error_message="Fork submit button not found"
        )

    # The page URL stays on /-/forks/new (Vue SPA — fork submitted via AJAX to
    # /api/v4/projects/{id}/fork). The Vue app may also trigger a page navigation
    # that results in a chrome-error page or a redirect. Either way, wait a moment
    # and then navigate directly to the expected fork URL.
    #
    # Poll up to 15 attempts × 1 s = 15 s for the fork to materialise.
    expected_fork_url = f"{GITLAB_DOMAIN}/{logged_in_user}/{fork_slug}"
    # The fork URL prefix we expect the browser to land at (allows sub-paths like /-/import)
    expected_fork_prefix = expected_fork_url.rstrip("/") + "/"

    def _is_valid_fork_url(current_url: str, base_ns: str, base_proj: str) -> bool:
        """Return True if current_url looks like the expected fork URL (case-insensitive)."""
        base = f"{GITLAB_DOMAIN}/{base_ns}/{base_proj}".lower()
        return current_url.lower().startswith(base)

    def _page_has_content(pg: Page) -> bool:
        """Return True if the current page is NOT a 'Page Not Found' error."""
        try:
            container = pg.locator(Selectors.PAGE_NOT_FOUND)
            if container.count() > 0:
                text = container.text_content() or ""
                if "Page Not Found" in text:
                    return False
        except Exception:
            pass
        return True

    for _attempt in range(15):
        page.wait_for_timeout(1000)
        try:
            page.goto(expected_fork_url, wait_until="networkidle", timeout=10000)
        except Exception:
            # ERR_ABORTED can happen while the fork is being submitted; check if
            # we're already on a valid fork URL despite the exception.
            if _is_valid_fork_url(page.url, logged_in_user, fork_slug):
                if _page_has_content(page):
                    return ForkProjectResult(success=True, fork_url=page.url)
            continue
        # Success: we're on the fork's project page or its /-/import sub-path.
        # Do NOT use _is_page_not_found here for /-/import — it temporarily shows
        # "Page Not Found" h3 while the fork is initializing.
        if _is_valid_fork_url(page.url, logged_in_user, fork_slug):
            # If we're on the base URL and it shows 404, the fork is stuck.
            # Check if we're on the import sub-path (that's OK even with 404 text).
            current_is_import = "/-/import" in page.url
            if current_is_import or _page_has_content(page):
                return ForkProjectResult(success=True, fork_url=page.url)
            # Page shows 404 at the base URL — fork is stuck. Try to clean up
            # and re-fork via the GitLab delete-project UI, then re-navigate.
            # For now: keep waiting (the import may take longer).

    # Last-ditch: try original source_project slug in case fork_slug was slugified
    if fork_slug.lower() != source_project.lower():
        alt_url = f"{GITLAB_DOMAIN}/{logged_in_user}/{source_project}"
        try:
            page.goto(alt_url, wait_until="networkidle", timeout=10000)
            if _is_valid_fork_url(page.url, logged_in_user, source_project):
                if _page_has_content(page):
                    return ForkProjectResult(success=True, fork_url=page.url)
        except Exception:
            pass

    # Final fallback: check if the fork exists via the GitLab explore page
    # (sometimes the fork page is not immediately accessible but the fork exists)
    explore_url = f"{GITLAB_DOMAIN}/explore/projects?search={fork_slug}"
    try:
        page.goto(explore_url, wait_until="networkidle", timeout=10000)
        links = page.locator(f"ul.projects-list a.text-plain[href*='/{logged_in_user}/{fork_slug}']")
        if links.count() > 0:
            fork_href = links.first.get_attribute("href") or ""
            if fork_href:
                return ForkProjectResult(success=True, fork_url=f"{GITLAB_DOMAIN}{fork_href}")
    except Exception:
        pass

    return ForkProjectResult(
        success=False,
        error_message=f"Fork timed out; fork not found at {expected_fork_url} after 15s"
    )


def delete_project(
    page: Page,
    namespace: str,
    project: str,
) -> DeleteProjectResult:
    """
    Delete a project from GitLab.

    FIXED: Uses sync pattern instead of expect_navigation context manager.

    Args:
        page: Playwright Page instance
        namespace: Project namespace
        project: Project name

    Returns:
        DeleteProjectResult with success status and any error message
    """
    settings_url = get_project_settings_url(namespace, project)
    confirmation_phrase = f"{namespace}/{project}"

    return _delete_project_or_group(
        page=page,
        url=settings_url,
        confirmation_phrase=confirmation_phrase,
        delete_section_id=Selectors.DELETE_PROJECT_SECTION.lstrip("#"),
        trigger_button_text="Delete project",
        confirm_button_text="Yes, delete project",
        success_url=DASHBOARD_PROJECTS_URL,
    )


def _select_namespace(
    page: Page,
    namespace_name: str,
    timeout: int,
) -> Optional[str]:
    """
    Select a namespace from the dropdown if available.

    Returns None on success, error message on failure.
    """
    dropdown_button = page.locator(Selectors.NAMESPACE_DROPDOWN_BUTTON)

    if dropdown_button.is_visible():
        # Multiple namespaces available
        dropdown_button.click()
        page.wait_for_timeout(timeout)

        group_item = page.locator(
            f"li.gl-dropdown-item >> text={namespace_name}"
        )

        if not group_item.is_visible():
            return f"Namespace '{namespace_name}' not found in dropdown"

        group_item.click()
        return None

    # Single namespace - check if it matches
    prefilled_element = page.query_selector(".input-group-prepend.static-namespace")
    if prefilled_element:
        prefilled_url = prefilled_element.get_attribute("title") or ""
        parsed = urlparse(prefilled_url)
        prefilled_namespace = parsed.path.strip("/").split("/")[-1]

        if prefilled_namespace != namespace_name:
            return (
                f"Namespace '{namespace_name}' requested but page shows "
                f"'{prefilled_namespace}'. Create the namespace first."
            )

    return None


def _handle_project_creation_error(
    page: Page,
    project_name: str,
    namespace_name: Optional[str],
) -> CreateProjectResult:
    """Handle errors during project creation."""
    error_container = page.locator(Selectors.PROJECT_ERROR_CONTAINER)
    if error_container.count() > 0:
        errors = error_container.locator("ul li").all_inner_texts()
        error_message = "; ".join(errors)

        if "taken" in error_message.lower():
            return CreateProjectResult(
                success=True,
                project_slug=project_name,
                error_message=f"Project may already exist: {error_message}"
            )

        return CreateProjectResult(
            success=False,
            project_slug=None,
            error_message=error_message
        )

    return CreateProjectResult(
        success=False,
        project_slug=None,
        error_message=f"Failed to create project; ended up at {page.url}"
    )


def _delete_project_or_group(
    page: Page,
    url: str,
    confirmation_phrase: str,
    delete_section_id: str,
    trigger_button_text: str,
    confirm_button_text: str,
    success_url: str,
) -> DeleteProjectResult:
    """
    Shared deletion logic for projects and groups.

    FIXED: Uses sync pattern instead of expect_navigation context manager.
    """
    page.goto(url, wait_until="networkidle")

    # Check for page not found
    if _is_page_not_found(page):
        # Already deleted
        return DeleteProjectResult(success=True)

    # Expand the advanced settings section
    try:
        expand_selector = f"section#{delete_section_id} {Selectors.EXPAND_BUTTON}"
        page.click(expand_selector)
    except Exception:
        return DeleteProjectResult(
            success=False,
            error_message="Could not expand settings section"
        )

    # Click delete trigger button
    delete_button = f'button:has-text("{trigger_button_text}")'
    try:
        page.wait_for_selector(delete_button, timeout=3000)
        page.locator(delete_button).click()
    except TimeoutError:
        return DeleteProjectResult(
            success=False,
            error_message=f"Delete button '{trigger_button_text}' not found"
        )

    # Fill confirmation input
    try:
        page.wait_for_selector(Selectors.CONFIRM_NAME_INPUT, timeout=3000)
        page.locator(Selectors.CONFIRM_NAME_INPUT).fill(confirmation_phrase)
    except TimeoutError:
        return DeleteProjectResult(
            success=False,
            error_message="Confirmation input not found"
        )

    # Confirm deletion - FIXED: sync pattern
    confirm_button = f'button:has-text("{confirm_button_text}")'
    page.locator(confirm_button).click()
    page.wait_for_load_state("networkidle")

    # Verify deletion
    if page.url == success_url:
        alert = page.locator(Selectors.ALERT_BODY)
        if alert.count() > 0:
            text = alert.text_content() or ""
            if "being deleted" in text or "deleted" in text.lower():
                return DeleteProjectResult(success=True)

    # Check if we're on a different success page
    if "/dashboard" in page.url or page.url.rstrip("/") == GITLAB_DOMAIN.rstrip("/"):
        return DeleteProjectResult(success=True)

    return DeleteProjectResult(
        success=False,
        error_message=f"Deletion may have failed; ended up at {page.url}"
    )


def _is_page_not_found(page: Page) -> bool:
    """Check if the current page shows a 'Page Not Found' error."""
    container = page.locator(Selectors.PAGE_NOT_FOUND)
    if container.count() > 0:
        text = container.text_content()
        if text and "Page Not Found" in text:
            return True
    return False
