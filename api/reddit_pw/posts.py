"""Reddit post management helpers."""

import re
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

from playwright.sync_api import Page, TimeoutError

from .constants import (
    base_url,
    submit_url,
    Selectors,
    get_user_profile_url,
    get_forum_url,
)


@dataclass
class Post:
    """Representation of a Reddit post."""

    id: str
    title: str
    body: str
    author: str
    subreddit: str
    url: str
    created_at: Optional[datetime] = None
    score: int = 0
    link_url: Optional[str] = None


@dataclass
class VotePostResult:
    """Result of voting on a post."""

    success: bool
    error_message: Optional[str] = None


@dataclass
class EditPostResult:
    """Result of editing a post."""

    success: bool
    post_url: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class CreatePostResult:
    """Result of attempting to create a post."""

    success: bool
    post_url: Optional[str]
    post_id: Optional[str] = None
    already_existed: bool = False
    error_message: Optional[str] = None


@dataclass
class DeletePostResult:
    """Result of attempting to delete a post."""

    success: bool
    error_message: Optional[str] = None


def create_post(
    page: Page,
    forum: str,
    title: str,
    body: str,
    username: str,
    url: Optional[str] = None,
    base: Optional[str] = None,
) -> CreatePostResult:
    """
    Create a new Reddit post with title and body text (or a link post with url).

    Checks if a post with the same title already exists for the user before creating.

    Args:
        page: Playwright Page instance
        forum: Forum/subreddit name to post in (e.g., "AskReddit")
        title: Title of the post
        body: Body text of the post
        username: Username creating the post (used to check for duplicates)
        url: Optional URL for link posts (image reposts, etc.)

    Returns:
        CreatePostResult with success status, URL, and any error message
    """
    # First check if a post with the same title already exists (idempotency).
    # Normalize dashes to handle EM DASH (–) vs HYPHEN (-) mismatches.
    def _norm(s: str) -> str:
        return s.replace("\u2013", "-").replace("\u2014", "-").lower().strip()

    domain = base_url(base)
    profile_url = get_user_profile_url(username, base)
    page.goto(profile_url, wait_until="networkidle")

    title_norm = _norm(title)
    # Strip any "r/" / "f/" prefix before forum comparison
    forum_for_check = (forum or "").strip()
    for prefix in ("r/", "f/"):
        if forum_for_check.startswith(prefix):
            forum_for_check = forum_for_check[len(prefix):]
    forum_for_check = forum_for_check.lower()

    # Check submission links on the profile for a title+forum match
    for lnk in page.query_selector_all("a[href*='/f/']"):
        link_text = lnk.inner_text().strip()
        if link_text and _norm(link_text) == title_norm:
            href = lnk.get_attribute("href") or ""
            # Only treat as duplicate when the existing post is in the SAME forum
            import re as _re
            forum_m = _re.search(r"/f/([^/]+)/", href)
            if forum_m and forum_m.group(1).lower() != forum_for_check:
                continue  # same title but different forum — not a duplicate
            if href and "/" in href:
                existing_url = f"{domain}{href}"
                return CreatePostResult(
                    success=True,
                    post_url=existing_url,
                    already_existed=True,
                    error_message=f"A post with title '{title}' already exists"
                )

    # Strip any "r/" / "f/" prefix the planner may add.
    forum_clean = forum.strip() if forum else forum
    for prefix in ("r/", "f/"):
        if forum_clean and forum_clean.startswith(prefix):
            forum_clean = forum_clean[len(prefix):]

    # Navigate to submit page
    page.goto(submit_url(base), wait_until="networkidle")

    # Wait for form
    try:
        page.wait_for_selector(Selectors.POST_TITLE_INPUT, timeout=10000)
    except TimeoutError:
        return CreatePostResult(
            success=False,
            post_url=None,
            error_message="Post creation form not found"
        )

    # Fill in post details
    page.fill(Selectors.POST_TITLE_INPUT, title)
    if url:
        # Ensure the URL radio button is selected (not the image-upload radio).
        # If the image radio is checked from a prior navigation the URL input is
        # hidden and page.fill() would throw; clicking the URL radio first makes
        # the URL input visible and editable in all form states.
        try:
            page.click("#submission_mediaType_url", timeout=3000)
        except Exception:
            pass
        try:
            page.fill(Selectors.POST_URL_INPUT, url)
        except Exception:
            pass
    page.fill(Selectors.POST_BODY_INPUT, body)

    # Select the forum by driving the underlying <select> directly.
    # Options use numeric IDs as values and have a data-name attribute with
    # the forum slug (e.g. data-name="books"). Subscribed forums include a
    # heart emoji in their textContent ("books ❤️"), so text matching fails.
    # Match on data-name (case-insensitive) as the primary strategy.
    select_result = page.evaluate(
        """(forumName) => {
            const select = document.querySelector('#submission_forum');
            if (!select) return {ok: false, reason: 'select #submission_forum not found'};
            const target = forumName.toLowerCase();
            const opt =
                Array.from(select.options).find(o => (o.getAttribute('data-name') || '').toLowerCase() === target) ||
                Array.from(select.options).find(o => o.value === forumName) ||
                Array.from(select.options).find(o => o.value.toLowerCase() === target) ||
                Array.from(select.options).find(o => (o.textContent || '').trim().toLowerCase().replace(/[^a-z0-9_]/g, '') === target.replace(/[^a-z0-9_]/g, ''));
            if (!opt) return {ok: false, reason: 'forum not in options', available: Array.from(select.options).map(o => o.getAttribute('data-name') || o.value).slice(0, 8)};
            select.value = opt.value;
            select.dispatchEvent(new Event('change', {bubbles: true}));
            return {ok: true, selected: opt.getAttribute('data-name') || opt.value};
        }""",
        forum_clean,
    )
    if not select_result.get("ok"):
        return CreatePostResult(
            success=False,
            post_url=None,
            error_message=(
                f"Failed to select forum '{forum_clean}': {select_result.get('reason')}"
                + (f" (first options: {select_result.get('available')})" if select_result.get('available') else "")
            ),
        )

    # If Select2 left a dropdown open (e.g. user code clicked the container
    # before this function was reached), close it so it can't intercept the
    # submit button.
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
    except Exception:
        pass

    # Submit - use force=True as a last resort if the dropdown still intercepts
    try:
        page.click(Selectors.POST_SUBMIT_BUTTON, timeout=5000)
    except Exception:
        # If normal click fails (overlay still blocking), force the click
        try:
            page.locator(Selectors.POST_SUBMIT_BUTTON).dispatch_event("click")
        except Exception:
            pass
    page.wait_for_load_state("networkidle")

    # Give a bit more time for navigation to complete
    page.wait_for_timeout(500)

    # Check if we're still on submit page (creation failed)
    if page.url.strip("/") == submit_url(base).strip("/"):
        # Try to find any error messages on the page
        error_text = ""
        error_selectors = [
            ".alert-danger",
            ".error",
            ".form-error",
            ".invalid-feedback",
            '[class*="error"]',
        ]
        for selector in error_selectors:
            error_elements = page.query_selector_all(selector)
            for el in error_elements:
                text = el.inner_text().strip()
                if text and text not in error_text:
                    error_text += text + "; "

        if error_text:
            return CreatePostResult(
                success=False,
                post_url=None,
                error_message=f"Failed to create post: {error_text.strip('; ')}"
            )
        return CreatePostResult(
            success=False,
            post_url=None,
            error_message="Failed to create post - still on submit page"
        )

    # Extract post ID from URL if possible
    post_id = None
    url_parts = page.url.rstrip("/").split("/")
    if len(url_parts) > 0:
        post_id = url_parts[-1]

    return CreatePostResult(
        success=True,
        post_url=page.url,
        post_id=post_id,
        already_existed=False,
        error_message=None
    )


def create_post_with_title_and_text(
    page: Page,
    forum: str,
    title: str,
    body: str,
    username: str,
    base: Optional[str] = None,
) -> CreatePostResult:
    """Convenience alias for create_post."""
    return create_post(page, forum, title, body, username, base=base)


def delete_post(page: Page, post_url: str, base: Optional[str] = None) -> DeletePostResult:
    """
    Delete a post by its URL.

    Args:
        page: Playwright Page instance
        post_url: Full URL of the post to delete

    Returns:
        DeletePostResult with success status and any error message
    """
    page.goto(post_url, wait_until="networkidle")

    # Set up dialog handler to accept confirmation (use once to avoid multiple handlers)
    def handle_dialog(dialog):
        dialog.accept()
    page.once("dialog", handle_dialog)

    try:
        page.wait_for_selector(Selectors.DELETE_BUTTON, state="visible", timeout=2000)
        button = page.query_selector(Selectors.DELETE_BUTTON)
        if button:
            button.click()
            page.wait_for_timeout(1000)
            return DeletePostResult(success=True)
        else:
            return DeletePostResult(
                success=False,
                error_message="Delete button not found"
            )
    except TimeoutError:
        return DeletePostResult(
            success=False,
            error_message="Delete button not found or timeout"
        )
    except Exception as e:
        return DeletePostResult(
            success=False,
            error_message=f"Error deleting post: {str(e)}"
        )


def delete_post_by_url(page: Page, url: str, base: Optional[str] = None) -> DeletePostResult:
    """Alias for delete_post."""
    return delete_post(page, url, base=base)


def delete_all_posts_by_username(
    page: Page,
    username: str,
    max_attempts: int = 5,
    base: Optional[str] = None,
) -> int:
    """
    Delete all posts by a user.

    Args:
        page: Playwright Page instance
        username: Username whose posts should be deleted
        max_attempts: Maximum number of posts to attempt to delete

    Returns:
        Number of posts deleted
    """
    profile_url = get_user_profile_url(username, base)
    page.goto(profile_url, wait_until="networkidle")

    # Set up dialog handler to accept confirmation
    page.on("dialog", lambda dialog: dialog.accept())

    deleted_count = 0
    attempt = 0

    while attempt < max_attempts:
        try:
            page.wait_for_selector(Selectors.DELETE_BUTTON, state="visible", timeout=3000)
            button = page.query_selector(Selectors.DELETE_BUTTON)
            if not button:
                break

            button.click()
            page.wait_for_timeout(1000)
            deleted_count += 1
            attempt += 1
        except TimeoutError:
            break
        except Exception as e:
            print(f"Error during deletion: {e}")
            break

    return deleted_count


def vote_post(page: Page, post_url: str, direction: str, base: Optional[str] = None) -> VotePostResult:
    """
    Vote on a submission (upvote or downvote). Idempotent.

    Postmill's vote buttons toggle: clicking the up button on an already-upvoted
    post un-votes it. Naive click-and-return therefore silently flips the vote
    off whenever residual state from a prior run is present. We read the
    submission__vote form's class to see the current state, click only when
    needed, and then re-read the class to confirm the desired state took.
    """
    is_up = direction in ("up", "1", 1)
    desired_class = "vote--user-upvoted" if is_up else "vote--user-downvoted"
    selector = "button.vote__up" if is_up else "button.vote__down"

    try:
        page.goto(post_url, wait_until="networkidle")
        form = page.wait_for_selector("div.submission__vote form", timeout=5000)

        current_class = form.get_attribute("class") or ""
        if desired_class in current_class:
            return VotePostResult(success=True)

        # Postmill submits the vote through a Stimulus "vote" controller: the
        # click dispatches an async fetch POST to /sv/{id} a tick LATER. A plain
        # wait_for_load_state("networkidle") after the click can observe an
        # already-idle network and return before that fetch even starts, so the
        # subsequent reload aborts the in-flight vote and it never persists.
        # Register the response waiter BEFORE clicking so we deterministically
        # wait for the server to record the vote (and can detect a failure).
        try:
            with page.expect_response(
                lambda r: "/sv/" in r.url and r.request.method == "POST",
                timeout=8000,
            ) as resp_info:
                page.click(selector, timeout=5000)
            resp = resp_info.value
            if resp.status >= 400:
                return VotePostResult(
                    success=False,
                    error_message=f"Vote POST to {resp.url} returned HTTP {resp.status}",
                )
        except TimeoutError:
            # No /sv/ POST observed (e.g. JS disabled → native form navigation);
            # fall back to settling the network before reloading.
            page.wait_for_load_state("networkidle")

        # Reload to confirm the vote persisted server-side (guards against
        # optimistic DOM updates where the class flips client-side but the
        # server write fails silently, e.g. under rapid sequential voting).
        page.reload(wait_until="networkidle")
        form = page.query_selector("div.submission__vote form")
        new_class = (form.get_attribute("class") or "") if form else ""
        if desired_class not in new_class:
            return VotePostResult(
                success=False,
                error_message=(
                    f"Vote did not persist on {post_url}: form class "
                    f"{new_class!r} missing {desired_class!r} after reload"
                ),
            )
        return VotePostResult(success=True)
    except TimeoutError:
        return VotePostResult(success=False, error_message=f"Vote button '{selector}' not found on {post_url}")
    except Exception as e:
        return VotePostResult(success=False, error_message=str(e))


def edit_post(
    page: Page,
    post_url: str,
    new_body: Optional[str] = None,
    new_title: Optional[str] = None,
    append: bool = False,
    base: Optional[str] = None,
) -> EditPostResult:
    """
    Edit a post's body.

    Args:
        page: Playwright Page instance
        post_url: Full URL of the post (e.g. http://host/f/books/59421/slug)
        new_body: Body text to set. Replaces the existing body unless append=True.
        new_title: Ignored — Postmill does not allow title edits after posting.
        append: If True, new_body is appended to the existing body; if False (default), replaces it.

    Returns:
        EditPostResult with success status and post URL
    """
    m = re.search(r'/f/([^/]+)/(\d+)', post_url)
    if not m:
        return EditPostResult(success=False, error_message=f"Cannot parse forum/post_id from URL: {post_url}")

    forum, post_id = m.group(1), m.group(2)
    domain = base_url(base)
    edit_url = f"{domain}/f/{forum}/{post_id}/-/edit"
    response = page.goto(edit_url, wait_until="domcontentloaded")

    if response and response.status == 403:
        return EditPostResult(success=False, error_message="Access denied: you can only edit your own posts")

    if "/edit" not in page.url:
        return EditPostResult(success=False, error_message=f"Edit page not accessible (redirected to {page.url})")

    try:
        page.wait_for_selector(Selectors.POST_BODY_INPUT, timeout=5000)
    except TimeoutError:
        return EditPostResult(success=False, error_message="Edit form not found")

    if new_body is not None:
        if append:
            existing_body = page.input_value(Selectors.POST_BODY_INPUT)
            body_to_write = (existing_body + "\n\n" + new_body) if existing_body.strip() else new_body
        else:
            body_to_write = new_body
        page.fill(Selectors.POST_BODY_INPUT, body_to_write)

    page.click(Selectors.EDIT_POST_SUBMIT_BUTTON)
    page.wait_for_load_state("domcontentloaded")

    if "/edit" in page.url:
        return EditPostResult(success=False, error_message="Still on edit page after submit — validation may have failed")

    return EditPostResult(success=True, post_url=page.url)


def get_post(page: Page, forum: str, post_id: str, base: Optional[str] = None) -> Optional[Post]:
    """
    Fetch a single post by forum name and post ID.

    Args:
        page: Playwright Page instance
        forum: Forum/subreddit name (e.g. "MachineLearning")
        post_id: Numeric post ID (from the post URL: /f/{forum}/{post_id}/...)

    Returns:
        Post object, or None if the post doesn't exist or is inaccessible
    """
    domain = base_url(base)
    url = f"{domain}/f/{forum}/{post_id}"
    page.goto(url, wait_until="networkidle")

    article = page.query_selector("article.submission")
    if not article:
        return None

    title_el = article.query_selector("h1.submission__title a")
    title = title_el.inner_text().strip() if title_el else ""
    title_href = (title_el.get_attribute("href") or "") if title_el else ""

    # Determine full URL and link_url (link posts have external href on submission__link)
    link_el = article.query_selector("a.submission__link")
    link_href = (link_el.get_attribute("href") or "") if link_el else ""
    is_external = link_href.startswith("http") and not link_href.startswith(domain)
    is_local_image = "/submission_images/" in link_href
    if is_external or is_local_image:
        link_url = link_href
        post_url = f"{domain}{title_href}" if title_href.startswith("/") else page.url
    else:
        link_url = None
        post_url = f"{domain}{title_href}" if title_href.startswith("/") else page.url

    author_el = article.query_selector("a.submission__submitter strong")
    author = author_el.inner_text().strip() if author_el else ""

    forum_el = article.query_selector("a.submission__forum strong")
    subreddit = forum_el.inner_text().strip() if forum_el else forum

    body_el = article.query_selector(".submission__body")
    body = body_el.inner_text().strip() if body_el else ""

    vote_form = article.query_selector("form.vote")
    score_str = (vote_form.get_attribute("data-vote-score-value") or "0") if vote_form else "0"
    try:
        score = int(score_str)
    except ValueError:
        score = 0

    return Post(
        id=post_id,
        title=title,
        body=body,
        author=author,
        subreddit=subreddit,
        url=post_url,
        link_url=link_url,
        score=score,
    )


def search_posts(
    page: Page,
    query: str,
    limit: int = 25,
    base: Optional[str] = None,
) -> List[Post]:
    """
    Full-text search across all posts (titles, bodies) using the site's search page.

    Args:
        page: Playwright Page instance
        query: Search query string
        limit: Maximum number of results to return (default: 25)

    Returns:
        List of Post objects matching the query
    """
    from urllib.parse import quote_plus
    domain = base_url(base)
    url = f"{domain}/search?q={quote_plus(query)}"
    page.goto(url, wait_until="domcontentloaded")

    posts: List[Post] = []
    articles = page.query_selector_all("article.submission")

    for article in articles:
        if len(posts) >= limit:
            break

        title_link = article.query_selector("a.submission__link")
        if not title_link:
            continue

        # Replace <mark> highlight tags with spaces before stripping so adjacent
        # highlighted words (e.g. <mark>bald</mark><mark>eagle</mark>) don't merge
        raw_html = title_link.inner_html()
        raw_html = re.sub(r'<mark[^>]*>', ' ', raw_html)   # space before each highlighted word
        raw_html = re.sub(r'</mark>', '', raw_html)         # closing tag needs no space
        title = re.sub(r'<[^>]+>', '', raw_html)
        title = re.sub(r'\s+', ' ', title).strip()
        title_href = title_link.get_attribute("href") or ""
        if not title_href:
            continue

        postmill_url = None
        link_url = None
        comments_link = article.query_selector("nav.submission__nav a[href*='/f/']")
        if comments_link:
            comments_href = comments_link.get_attribute("href") or ""
            postmill_url = comments_href if comments_href.startswith("http") else f"{domain}{comments_href}"

        if title_href.startswith("http") and postmill_url:
            link_url = title_href
        elif title_href.startswith("/f/"):
            postmill_url = postmill_url or f"{domain}{title_href}"
        else:
            postmill_url = postmill_url or (title_href if title_href.startswith("http") else f"{domain}{title_href}")

        pm = re.search(r'/f/([^/]+)/(\d+)', postmill_url or "")
        if pm:
            post_id = pm.group(2)
            subreddit = pm.group(1)
        else:
            vote_form_action = article.query_selector("form[action^='/sv/']")
            action = vote_form_action.get_attribute("action") if vote_form_action else ""
            id_m = re.search(r'/sv/(\d+)', action or "")
            post_id = id_m.group(1) if id_m else ""
            subreddit = ""

        author_el = article.query_selector("a.submission__submitter strong")
        author = author_el.inner_text().strip() if author_el else ""

        body_el = article.query_selector(".submission__body")
        body = body_el.inner_text().strip() if body_el else ""

        vote_form = article.query_selector("form.vote[data-vote-route-value='submission_vote']")
        score_str = vote_form.get_attribute("data-vote-score-value") if vote_form else "0"
        try:
            score = int(score_str or 0)
        except ValueError:
            score = 0

        posts.append(Post(
            id=post_id,
            title=title,
            body=body,
            author=author,
            subreddit=subreddit,
            url=postmill_url or "",
            link_url=link_url,
            score=score,
        ))

    return posts


def get_forum_posts(
    page: Page,
    forum_name: str,
    sort: str = "hot",
    limit: int = 25,
    base: Optional[str] = None,
) -> List[Post]:
    """
    Get posts from a forum, optionally sorted.

    Args:
        page: Playwright Page instance
        forum_name: Name of the forum (e.g. "books")
        sort: Sort order — "hot", "new", "top", "controversial", "active" (default: "hot")
        limit: Maximum number of posts to return (default: 25)

    Returns:
        List of Post objects (up to limit)
    """
    # Normalize natural-language sort aliases that agents commonly emit.
    # "ever" / "all" / "all_time" all mean all-time top in Reddit parlance.
    _aliases = {"ever": "top", "all": "top", "all_time": "top", "all-time": "top", "best": "top"}
    sort = _aliases.get(sort.lower(), sort.lower())
    sort_map = {"hot": "", "new": "/new", "top": "/top", "controversial": "/controversial", "active": "/active"}
    suffix = sort_map.get(sort, f"/{sort}")
    domain = base_url(base)
    url = f"{domain}/f/{forum_name}{suffix}"
    page.goto(url, wait_until="networkidle")

    posts: List[Post] = []
    articles = page.query_selector_all("article.submission")

    for article in articles:
        if len(posts) >= limit:
            break

        title_link = article.query_selector("a.submission__link")
        if not title_link:
            continue

        title = title_link.inner_text().strip()
        title_href = title_link.get_attribute("href") or ""
        if not title_href:
            continue

        # The Postmill submission URL is in the comments nav link (/f/{forum}/{id}/slug).
        # For link posts, submission__link points to the external URL — use the comments
        # link as the canonical Postmill URL and store the external URL as link_url.
        postmill_url = None
        link_url = None
        comments_link = article.query_selector("nav.submission__nav a[href*='/f/']")
        if comments_link:
            comments_href = comments_link.get_attribute("href") or ""
            postmill_url = comments_href if comments_href.startswith("http") else f"{domain}{comments_href}"

        if title_href.startswith("http") and postmill_url:
            # Link post: external URL is the link, comments link is the Postmill URL
            link_url = title_href
        elif title_href.startswith("/f/"):
            # Text post: submission__link IS the Postmill URL
            postmill_url = postmill_url or f"{domain}{title_href}"
        else:
            postmill_url = postmill_url or (title_href if title_href.startswith("http") else f"{domain}{title_href}")

        # Extract post_id from the Postmill URL (or vote form action /sv/{id} as fallback)
        pm = re.search(r'/f/([^/]+)/(\d+)', postmill_url or "")
        if pm:
            post_id = pm.group(2)
            subreddit = pm.group(1)
        else:
            vote_form_action = article.query_selector("form[action^='/sv/']")
            action = vote_form_action.get_attribute("action") if vote_form_action else ""
            id_m = re.search(r'/sv/(\d+)', action or "")
            post_id = id_m.group(1) if id_m else ""
            subreddit = forum_name

        # Author
        author_el = article.query_selector("a.submission__submitter strong")
        author = author_el.inner_text().strip() if author_el else ""

        # Body (text posts only — link posts have no body)
        body_el = article.query_selector(".submission__body")
        body = body_el.inner_text().strip() if body_el else ""

        # Score from vote form data attribute
        vote_form = article.query_selector("form.vote[data-vote-route-value='submission_vote']")
        score_str = vote_form.get_attribute("data-vote-score-value") if vote_form else "0"
        try:
            score = int(score_str or 0)
        except ValueError:
            score = 0

        # Timestamp
        time_el = article.query_selector("time[datetime]")
        created_at = None
        if time_el:
            dt_str = time_el.get_attribute("datetime") or ""
            try:
                from datetime import timezone
                created_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except Exception:
                pass

        posts.append(Post(
            id=post_id,
            title=title,
            body=body,
            author=author,
            subreddit=subreddit,
            url=postmill_url or "",
            link_url=link_url,
            created_at=created_at,
            score=score,
        ))

    return posts


def get_posts_by_username(page: Page, username: str, limit: int = 50, base: Optional[str] = None) -> List[Post]:
    """
    Get submissions by a user from their /submissions profile page.

    Paginates through all cursor pages until `limit` posts are collected or
    there are no more pages.  Postmill uses <link rel="next"> in <head> to
    point to the next page; it is resolved via JavaScript because Playwright
    locators don't search <head>.

    Args:
        page: Playwright Page instance
        username: Username to get posts for
        limit: Max posts to return (default 50)

    Returns:
        List of Post objects
    """
    posts: List[Post] = []
    domain = base_url(base)
    next_url: str | None = f"{domain}/user/{username}/submissions"

    while next_url and len(posts) < limit:
        page.goto(next_url, wait_until="networkidle")
        articles = page.query_selector_all("article.submission")

        for article in articles:
            if len(posts) >= limit:
                break

            title_link = article.query_selector("a.submission__link")
            if not title_link:
                continue

            title = title_link.inner_text().strip()
            title_href = title_link.get_attribute("href") or ""
            if not title_href:
                continue

            postmill_url = None
            link_url = None
            comments_link = article.query_selector("nav.submission__nav a[href*='/f/']")
            if comments_link:
                comments_href = comments_link.get_attribute("href") or ""
                postmill_url = comments_href if comments_href.startswith("http") else f"{domain}{comments_href}"

            if title_href.startswith("http") and postmill_url:
                link_url = title_href
            elif title_href.startswith("/f/"):
                postmill_url = postmill_url or f"{domain}{title_href}"
            else:
                postmill_url = postmill_url or (title_href if title_href.startswith("http") else f"{domain}{title_href}")

            pm = re.search(r'/f/([^/]+)/(\d+)', postmill_url or "")
            if pm:
                post_id = pm.group(2)
                subreddit = pm.group(1)
            else:
                vote_form_action = article.query_selector("form[action^='/sv/']")
                action = vote_form_action.get_attribute("action") if vote_form_action else ""
                id_m = re.search(r'/sv/(\d+)', action or "")
                post_id = id_m.group(1) if id_m else ""
                subreddit = ""

            body_el = article.query_selector(".submission__body")
            body = body_el.inner_text().strip() if body_el else ""

            vote_form = article.query_selector("form.vote[data-vote-route-value='submission_vote']")
            score_str = vote_form.get_attribute("data-vote-score-value") if vote_form else "0"
            try:
                score = int(score_str or 0)
            except ValueError:
                score = 0

            time_el = article.query_selector("time[datetime]")
            created_at = None
            if time_el:
                dt_str = time_el.get_attribute("datetime") or ""
                try:
                    from datetime import timezone
                    created_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                except Exception:
                    pass

            # Skip posts that don't actually exist on this instance. Postmill can
            # have user-submission records pointing to post IDs that were never
            # imported (or were later deleted), causing 404s when the agent tries
            # to interact with them.
            if postmill_url:
                try:
                    resp = page.request.get(postmill_url)
                    if resp.status == 404:
                        continue
                except Exception:
                    pass

            posts.append(Post(
                id=post_id,
                title=title,
                body=body,
                author=username,
                subreddit=subreddit,
                url=postmill_url or "",
                link_url=link_url,
                created_at=created_at,
                score=score,
            ))

        # Follow the pagination cursor. <link rel="next"> is in <head>, so
        # use JS to reach it rather than a Playwright locator (which only
        # searches <body>).
        next_url = page.evaluate(
            "document.querySelector('link[rel=\"next\"]')?.href || null"
        )

    return posts
