"""Reddit forum/subreddit management helpers."""

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page, TimeoutError

from .constants import (
    base_url,
    create_forum_url,
    Selectors,
    get_user_profile_url,
    get_forum_url,
)


@dataclass
class Forum:
    """Representation of a Reddit forum/subreddit."""

    name: str
    title: str
    description: str
    sidebar: str
    url: str
    creator: Optional[str] = None


@dataclass
class CreateForumResult:
    """Result of attempting to create a forum."""

    success: bool
    forum_url: Optional[str]
    forum_name: Optional[str] = None
    already_existed: bool = False
    error_message: Optional[str] = None


@dataclass
class SubscribeForumResult:
    """Result of subscribing or unsubscribing from a forum."""

    success: bool
    already_subscribed: bool = False
    error_message: Optional[str] = None


def create_forum(
    page: Page,
    forum_name: str,
    forum_title: str,
    forum_description: str,
    forum_sidebar: str,
    username: str,
    base: Optional[str] = None,
) -> CreateForumResult:
    """
    Create a new Reddit forum/subreddit.

    Checks if a forum with the same name already exists before creating.

    Args:
        page: Playwright Page instance
        forum_name: Unique name for the forum (used in URL)
        forum_title: Display title for the forum
        forum_description: Description of the forum
        forum_sidebar: Sidebar content (rules, guidelines, etc.)
        username: Username creating the forum (used to check for duplicates)

    Returns:
        CreateForumResult with success status, URL, and any error message
    """
    # First check if a forum with this exact name already exists by navigating to it
    forum_url_check = get_forum_url(forum_name, base)
    page.goto(forum_url_check, wait_until="networkidle")
    content_lower = page.content().lower()
    if "not found" not in content_lower and "404" not in content_lower and page.url.rstrip("/") == forum_url_check.rstrip("/"):
        return CreateForumResult(
            success=True,
            forum_url=forum_url_check,
            forum_name=forum_name,
            already_existed=True,
            error_message=f"Forum '{forum_name}' already exists"
        )

    # Navigate to create forum page
    page.goto(create_forum_url(base), wait_until="networkidle")

    # Wait for form
    try:
        page.wait_for_selector(Selectors.FORUM_NAME_INPUT, timeout=10000)
    except TimeoutError:
        return CreateForumResult(
            success=False,
            forum_url=None,
            error_message="Forum creation form not found"
        )

    # Fill in forum details
    page.fill(Selectors.FORUM_NAME_INPUT, forum_name)
    page.fill(Selectors.FORUM_TITLE_INPUT, forum_title)
    page.fill(Selectors.FORUM_DESCRIPTION_INPUT, forum_description)
    page.fill(Selectors.FORUM_SIDEBAR_INPUT, forum_sidebar)

    # Submit - using sync pattern
    page.click(Selectors.FORUM_SUBMIT_BUTTON)
    page.wait_for_load_state("networkidle")

    # Check if we're still on create forum page (creation failed)
    if page.url.strip("/") == create_forum_url(base).strip("/"):
        return CreateForumResult(
            success=False,
            forum_url=None,
            error_message="Failed to create forum - still on creation page"
        )

    return CreateForumResult(
        success=True,
        forum_url=page.url,
        forum_name=forum_name,
        already_existed=False,
        error_message=None
    )


def get_forum_info(page: Page, forum_name: str, base: Optional[str] = None) -> Optional[Forum]:
    """
    Get information about a forum.

    Args:
        page: Playwright Page instance
        forum_name: Name of the forum to get info for

    Returns:
        Forum object if found, None otherwise
    """
    forum_url = get_forum_url(forum_name, base)
    page.goto(forum_url, wait_until="networkidle")

    # Check if forum exists (not a 404 page)
    if "not found" in page.content().lower() or "404" in page.content():
        return None

    # Extract forum info from page
    # Actual selectors will depend on the Reddit instance's HTML structure
    title_elem = page.query_selector("h1, .forum-title, .subreddit-title")
    title = title_elem.inner_text().strip() if title_elem else forum_name

    description_elem = page.query_selector(".forum-description, .subreddit-description, .description")
    description = description_elem.inner_text().strip() if description_elem else ""

    sidebar_elem = page.query_selector(".sidebar, .forum-sidebar, aside")
    sidebar = sidebar_elem.inner_text().strip() if sidebar_elem else ""

    return Forum(
        name=forum_name,
        title=title,
        description=description,
        sidebar=sidebar,
        url=page.url,
    )


def subscribe_to_forum(page: Page, forum_name: str, base: Optional[str] = None) -> SubscribeForumResult:
    """
    Subscribe to a forum. Idempotent and verifies persistence.

    Postmill renders subscribe/unsubscribe as two distinct forms whose action is
    `/f/{name}/subscribe` or `/f/{name}/unsubscribe`. We target those forms by
    action URL rather than by button class — the `subscribe-button` class names
    don't reliably match real Postmill markup. Verifies by re-reading the page
    for the unsubscribe form after click.
    """
    forum_url = get_forum_url(forum_name, base)
    page.goto(forum_url, wait_until="networkidle")

    if page.query_selector("form[action*='/unsubscribe'] button"):
        return SubscribeForumResult(success=True, already_subscribed=True)

    subscribe_btn = "form[action*='/subscribe'] button"
    try:
        page.wait_for_selector(subscribe_btn, timeout=5000)
        page.click(subscribe_btn)
        page.wait_for_load_state("networkidle")

        if not page.query_selector("form[action*='/unsubscribe'] button"):
            return SubscribeForumResult(
                success=False,
                error_message=(
                    f"Subscribe to /f/{forum_name} did not persist: "
                    f"unsubscribe form absent after click"
                ),
            )
        return SubscribeForumResult(success=True)
    except TimeoutError:
        return SubscribeForumResult(success=False, error_message=f"Subscribe button not found on /f/{forum_name}")
    except Exception as e:
        return SubscribeForumResult(success=False, error_message=str(e))


def forum_exists(page: Page, forum_name: str, base: Optional[str] = None) -> bool:
    """
    Check if a forum exists.

    Args:
        page: Playwright Page instance
        forum_name: Name of the forum to check

    Returns:
        True if forum exists, False otherwise
    """
    forum_url = get_forum_url(forum_name, base)
    page.goto(forum_url, wait_until="networkidle")

    # Check if we got a 404 or similar
    content = page.content().lower()
    if "not found" in content or "404" in content or "doesn't exist" in content:
        return False

    return True
