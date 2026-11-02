"""Reddit comment operations helpers."""

import re
import requests as _http
from dataclasses import dataclass
from typing import List, Optional

from playwright.sync_api import Page, TimeoutError

from .constants import (
    Selectors,
    base_url,
    get_post_url,
)


@dataclass
class Comment:
    """Representation of a Reddit comment."""

    id: str
    body: str
    author: str
    post_id: str
    subreddit: str
    url: str
    score: int = 0


@dataclass
class ReplyToCommentResult:
    """Result of replying to a comment."""

    success: bool
    comment_url: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class CommentResult:
    """Result of attempting to post a comment."""

    success: bool
    comment_url: Optional[str]
    error_message: Optional[str] = None


@dataclass
class DeleteCommentResult:
    """Result of attempting to delete comments."""

    success: bool
    deleted_count: int = 0
    error_message: Optional[str] = None


def comment_on_post(
    page: Page,
    subreddit: str,
    post_id: str,
    comment_text: str,
    base: Optional[str] = None,
) -> CommentResult:
    """
    Post a comment on a Reddit post.

    Args:
        page: Playwright Page instance
        subreddit: Name of the subreddit (e.g., "AskReddit")
        post_id: Numeric ID of the post to comment on (e.g., "2", not the slug)
        comment_text: Text content of the comment

    Returns:
        CommentResult with success status, URL, and any error message
    """
    post_url = get_post_url(subreddit, post_id, base)
    page.goto(post_url, wait_until="networkidle")

    # Get the dynamic comment input selector
    comment_input_selector = Selectors.get_comment_input(post_id)

    # Wait for comment form
    try:
        page.wait_for_selector(comment_input_selector, timeout=10000)
    except TimeoutError:
        return CommentResult(
            success=False,
            comment_url=None,
            error_message="Comment form not found on post"
        )

    # Fill in comment
    page.fill(comment_input_selector, comment_text)

    # Submit - using sync pattern
    page.click(Selectors.COMMENT_SUBMIT_BUTTON)
    page.wait_for_load_state("networkidle")

    # Check if we're still on the same URL (might have an anchor added)
    current_url = page.url

    # If URL changed significantly or stayed same, consider success
    # Reddit typically stays on same page after commenting
    if post_url.split("#")[0] in current_url.split("#")[0]:
        return CommentResult(
            success=True,
            comment_url=current_url,
            error_message=None
        )

    return CommentResult(
        success=False,
        comment_url=None,
        error_message=f"Comment may have failed - ended up at {current_url}"
    )


def comment_on_post_by_url(
    page: Page,
    post_url: str,
    comment_text: str,
    base: Optional[str] = None,
) -> CommentResult:
    """
    Post a comment on a Reddit post using its full URL.

    Extracts the numeric post ID from the URL to build the comment selector.

    Args:
        page: Playwright Page instance
        post_url: Full URL of the post (e.g., http://localhost:9999/f/AskReddit/2/post-title)
        comment_text: Text content of the comment

    Returns:
        CommentResult with success status, URL, and any error message
    """
    page.goto(post_url, wait_until="networkidle")

    # Extract the numeric post ID from URL: /f/{subreddit}/{post_id}/...
    # URL format: http://domain/f/SubredditName/123/post-slug
    url_parts = post_url.rstrip("/").split("/")
    post_id = None

    # Find the part after /f/{subreddit}/ which is the numeric ID
    try:
        f_index = url_parts.index("f")
        if f_index + 2 < len(url_parts):
            potential_id = url_parts[f_index + 2]
            # Check if it's numeric
            if potential_id.isdigit():
                post_id = potential_id
    except (ValueError, IndexError):
        pass

    if not post_id:
        return CommentResult(
            success=False,
            comment_url=None,
            error_message=f"Could not extract post ID from URL: {post_url}"
        )

    # Get the dynamic comment input selector
    comment_input_selector = Selectors.get_comment_input(post_id)

    # Wait for comment form
    try:
        page.wait_for_selector(comment_input_selector, timeout=10000)
    except TimeoutError:
        return CommentResult(
            success=False,
            comment_url=None,
            error_message=f"Comment form not found on post (looking for {comment_input_selector})"
        )

    # Fill in comment
    page.fill(comment_input_selector, comment_text)

    # Submit - using sync pattern
    page.click(Selectors.COMMENT_SUBMIT_BUTTON)
    page.wait_for_load_state("networkidle")

    # Check if we're still on the same URL (might have an anchor added)
    current_url = page.url

    return CommentResult(
        success=True,
        comment_url=current_url,
        error_message=None
    )


def delete_all_comments_on_post(
    page: Page,
    post_url: str,
    max_attempts: int = 5,
    base: Optional[str] = None,
) -> DeleteCommentResult:
    """
    Delete all comments by the current user on a specific post.

    Args:
        page: Playwright Page instance
        post_url: Full URL of the post
        max_attempts: Maximum number of comments to attempt to delete

    Returns:
        DeleteCommentResult with success status and count of deleted comments
    """
    page.goto(post_url, wait_until="networkidle")

    # Set up dialog handler to accept confirmation
    def handle_dialog(dialog):
        dialog.accept()
    page.on("dialog", handle_dialog)

    deleted_count = 0
    attempt = 0

    try:
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
                return DeleteCommentResult(
                    success=deleted_count > 0,
                    deleted_count=deleted_count,
                    error_message=f"Error after deleting {deleted_count} comments: {str(e)}"
                )

        return DeleteCommentResult(
            success=True,
            deleted_count=deleted_count,
            error_message=None if deleted_count > 0 else "No comments found to delete"
        )
    finally:
        # Remove the dialog handler to avoid conflicts with other code
        page.remove_listener("dialog", handle_dialog)


def delete_all_comments_on_post_by_user(
    page: Page,
    url: str,
    max_attempts: int = 5,
    base: Optional[str] = None,
) -> DeleteCommentResult:
    """Alias for delete_all_comments_on_post for compatibility with reddit_editor.py."""
    return delete_all_comments_on_post(page, url, max_attempts, base=base)


def get_comments_on_post(
    page: Page,
    subreddit: str,
    post_id: str,
    base: Optional[str] = None,
) -> List[Comment]:
    """
    Get all comments on a post, including their net vote score.

    Args:
        page: Playwright Page instance
        subreddit: Name of the subreddit
        post_id: ID of the post

    Returns:
        List of Comment objects (score > 0 means more upvotes, score < 0 means more downvotes)
    """
    domain = base_url(base)
    post_url = get_post_url(subreddit, post_id, base)
    page.goto(post_url, wait_until="networkidle")

    comments: List[Comment] = []

    # Comments use <article class="comment" id="comment_{id}"> (underscore separator)
    comment_elements = page.query_selector_all("article.comment[id^='comment_']")

    for elem in comment_elements:
        article_id = elem.get_attribute("id") or ""
        comment_id = article_id.replace("comment_", "") if article_id else ""

        # Body: .comment__body contains the markdown-rendered text
        body_elem = elem.query_selector(".comment__body")
        body = body_elem.inner_text().strip() if body_elem else ""

        # Author: first user link inside the comment header
        author_elem = elem.query_selector("a[href*='/user/'] strong")
        author = author_elem.inner_text().strip() if author_elem else ""

        # Score from the vote form data attribute
        vote_form = elem.query_selector(f"form[data-vote-id-value='{comment_id}']")
        score_str = vote_form.get_attribute("data-vote-score-value") if vote_form else "0"
        try:
            score = int(score_str or 0)
        except ValueError:
            score = 0

        comments.append(Comment(
            id=comment_id,
            body=body,
            author=author,
            post_id=post_id,
            subreddit=subreddit,
            url=f"{domain}/f/{subreddit}/{post_id}/-/comment/{comment_id}",
            score=score,
        ))

    return comments


def reply_to_comment(
    page: Page,
    post_url: str,
    comment_id: str,
    reply_text: str,
    base: Optional[str] = None,
) -> ReplyToCommentResult:
    """
    Reply to a specific comment on a post.

    Uses the Postmill form API directly (fetches the CSRF-token form, then
    POSTs the reply) so no UI-selector guessing is required.

    Args:
        page: Playwright Page instance (provides the authenticated session cookie)
        post_url: Full URL of the post containing the comment
        comment_id: Numeric ID of the comment to reply to
        reply_text: Text content of the reply

    Returns:
        ReplyToCommentResult with success status and URL
    """
    domain = base_url(base)

    # Extract forum + post_id from post_url: …/f/{forum}/{post_id}/…
    url_parts = post_url.rstrip("/").split("/")
    try:
        f_idx = url_parts.index("f")
        forum = url_parts[f_idx + 1]
        post_id = url_parts[f_idx + 2]
    except (ValueError, IndexError):
        return ReplyToCommentResult(
            success=False,
            error_message=f"Could not parse forum/post_id from URL: {post_url}",
        )

    # Pull PHPSESSID from the browser context so we can reuse it in requests
    cookies = page.context.cookies()
    phpsessid = next((c["value"] for c in cookies if c["name"] == "PHPSESSID"), None)
    if not phpsessid:
        return ReplyToCommentResult(
            success=False,
            error_message="No PHPSESSID cookie found in browser context",
        )

    session = _http.Session()
    session.cookies.set("PHPSESSID", phpsessid)

    # Fetch the inline reply form — it carries the CSRF token and the form action URL
    form_url = f"{domain}/comment_form/{forum}/{post_id}/{comment_id}"
    form_resp = session.get(form_url)
    if form_resp.status_code != 200:
        return ReplyToCommentResult(
            success=False,
            error_message=f"Failed to fetch comment form (HTTP {form_resp.status_code})",
        )

    form_html = form_resp.text

    # Extract form action
    action_m = re.search(r'<form[^>]+action="([^"]+)"', form_html)
    if not action_m:
        return ReplyToCommentResult(
            success=False,
            error_message="Form action not found in reply form HTML",
        )
    form_action = action_m.group(1)

    # Collect hidden inputs (includes CSRF token) and select defaults
    post_data: dict = {}
    for tag in re.finditer(r'<input([^>]*)>', form_html):
        attrs = tag.group(1)
        t_m = re.search(r'type="([^"]+)"', attrs)
        if not t_m or t_m.group(1) != "hidden":
            continue
        name_m = re.search(r'name="([^"]+)"', attrs)
        val_m = re.search(r'value="([^"]*)"', attrs)
        if name_m:
            post_data[name_m.group(1)] = val_m.group(1) if val_m else ""
    for sel in re.finditer(r'<select[^>]+name="([^"]+)"[^>]*>(.*?)</select>', form_html, re.DOTALL):
        opt_m = re.search(r'<option[^>]+value="([^"]*)"', sel.group(2))
        post_data[sel.group(1)] = opt_m.group(1) if opt_m else ""

    # Locate the comment body field (e.g. "reply_to_comment_1235250[comment]")
    body_field_m = re.search(r'name="([^"]*\[comment\][^"]*)"', form_html)
    body_field = (
        body_field_m.group(1) if body_field_m
        else f"reply_to_comment_{comment_id}[comment]"
    )
    post_data[body_field] = reply_text

    # POST the reply; a successful submission redirects (302) to the new comment
    reply_url = (domain + form_action) if form_action.startswith("/") else form_action
    resp = session.post(reply_url, data=post_data, allow_redirects=False)

    if resp.status_code in (301, 302):
        loc = resp.headers.get("Location", "")
        comment_url = (domain + loc) if loc.startswith("/") else loc
        return ReplyToCommentResult(success=True, comment_url=comment_url)

    return ReplyToCommentResult(
        success=False,
        error_message=f"Reply submission returned HTTP {resp.status_code} (expected 302 redirect)",
    )
