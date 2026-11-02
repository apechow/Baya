"""
Reddit API server — FastAPI wrapper around api/reddit_pw Playwright functions.

Exposes Playwright-based actions as simple HTTP endpoints so the agent can call
them without worrying about CSRF scraping, Select2 dropdowns, or AJAX form loading.

Run on port 7791:
    python3 api/servers/reddit.py

Or launched automatically by initialize.py / run_tasks_batch_new.py.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import os
import threading
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from playwright.sync_api import sync_playwright

from config.init_tokens.refresh_reddit_session import refresh_session
from api.reddit_pw.constants import base_url as resolve_base_url
from api.reddit_pw import (
    vote_post,
    edit_post,
    get_forum_posts,
    get_post,
    search_posts,
    get_posts_by_username,
    subscribe_to_forum,
    reply_to_comment,
    update_biography,
    create_post,
    get_comments_on_post,
    comment_on_post_by_url,
    create_forum,
    send_message,
    get_messages,
    delete_post,
    update_email,
    get_blocked_users,
)

app = FastAPI(
    title="Reddit API",
    description="Playwright-based actions for the WebArena Reddit (Postmill) site.",
    version="1.0.0",
)


# ── Auth helper ───────────────────────────────────────────────────────────────

# One Postmill session per instance. This process serves every eval worker, and
# under --multi-docker each worker is a different Postmill container reached at a
# different base URL. A single process-wide PHPSESSID would authenticate against
# whichever instance minted it and then be reused for the others — a silent
# wrong-instance write. So the cache is keyed by resolved base URL, and every
# entry is validated before use: a worker's container is recreated in place on
# reset (same URL), so a stale cookie must be detected and re-minted — there is
# no external signal to invalidate it.

_SESSION_CACHE: Dict[str, str] = {}
_SESSION_LOCK = threading.Lock()


def _is_session_valid(phpsessid: str, reddit_base: str) -> bool:
    """Return True if the PHPSESSID is still authenticated on this instance.

    Hits {reddit_base}/submit (requires login) with allow_redirects=False. A
    redirect to /login means the session has expired; a 200 or any non-login
    redirect means it is still active.
    """
    import requests as _http
    try:
        s = _http.Session()
        s.cookies.set("PHPSESSID", phpsessid)
        r = s.get(f"{reddit_base}/submit", allow_redirects=False, timeout=5)
        location = r.headers.get("Location", "")
        return "/login" not in location
    except Exception:
        return False


def _get_cached_phpsessid(reddit_base: str, force_refresh: bool = False) -> str:
    """Return a valid PHPSESSID for ``reddit_base``, caching one per instance.

    Validates the cached session before returning it — if it has expired (e.g.
    the instance was reset and its DB no longer knows the old session), a fresh
    login against that instance is performed transparently. The network
    validation/login runs outside the lock (the lock only guards the dict), so
    concurrent requests for different instances never block each other.
    """
    if not force_refresh:
        with _SESSION_LOCK:
            cached = _SESSION_CACHE.get(reddit_base)
        if cached and _is_session_valid(cached, reddit_base):
            return cached
    phpsessid = refresh_session(base_url=reddit_base)
    with _SESSION_LOCK:
        _SESSION_CACHE[reddit_base] = phpsessid
    return phpsessid


def _make_browser_page(playwright, reddit_base: str, force_refresh: bool = False):
    """Create a browser page authenticated against the given Postmill instance."""
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    phpsessid = _get_cached_phpsessid(reddit_base, force_refresh=force_refresh)
    # Cookie for the instance's own host, plus localhost/127.0.0.1 (agent URLs may
    # use either; single-docker uses the default host).
    hosts = {"localhost", "127.0.0.1"}
    parsed_host = urlparse(reddit_base).hostname
    if parsed_host:
        hosts.add(parsed_host)
    for domain in hosts:
        context.add_cookies([{
            "name": "PHPSESSID",
            "value": phpsessid,
            "domain": domain,
            "path": "/",
        }])
    page = context.new_page()
    return browser, page


# ── Request / Response models ─────────────────────────────────────────────────

class VotePostRequest(BaseModel):
    # Accept post_url (full URL) OR post_id + optional forum (agent may pass either)
    post_url: Optional[str] = None
    post_id: Optional[str] = None
    forum: Optional[str] = None
    # Accept direction ("up"/"down") OR vote ("1"/"-1"/1/-1) — allow int or str
    direction: Optional[Union[str, int]] = None
    vote: Optional[Union[str, int]] = None
    base_url: Optional[str] = None

class VotePostResponse(BaseModel):
    success: bool
    error_message: Optional[str] = None


class SubscribeForumRequest(BaseModel):
    forum_name: Optional[str] = None
    body: Optional[str] = None  # agent sometimes sends the name as "body" when resolving a reference
    base_url: Optional[str] = None

class SubscribeForumResponse(BaseModel):
    success: bool
    already_subscribed: bool = False
    error_message: Optional[str] = None


class UpdateBiographyRequest(BaseModel):
    username: str
    biography: str
    base_url: Optional[str] = None

class UpdateBiographyResponse(BaseModel):
    success: bool
    error_message: Optional[str] = None


class ReplyToCommentRequest(BaseModel):
    # post_url + comment_id (explicit) OR comment_url (full comment permalink — both derived automatically)
    post_url: Optional[str] = None
    comment_id: Optional[str] = None
    comment_url: Optional[str] = None  # e.g. http://host/f/books/59421/-/comment/1235250
    reply_text: Optional[str] = None   # also accepted as "text"
    text: Optional[str] = None         # alias for reply_text
    base_url: Optional[str] = None

class ReplyToCommentResponse(BaseModel):
    success: bool
    comment_url: Optional[str] = None
    error_message: Optional[str] = None


class EditPostRequest(BaseModel):
    post_url: str
    new_body: Optional[str] = None
    new_title: Optional[str] = None
    append: bool = False
    base_url: Optional[str] = None

class EditPostResponse(BaseModel):
    success: bool
    post_url: Optional[str] = None
    error_message: Optional[str] = None


class GetPostResponse(BaseModel):
    id: str
    title: str
    body: str
    author: str
    subreddit: str
    url: str
    score: int
    link_url: Optional[str] = None
    error_message: Optional[str] = None


class CreatePostRequest(BaseModel):
    forum: str
    title: str
    body: str = ""
    url: Optional[str] = None  # for link/image posts
    base_url: Optional[str] = None

class CreatePostResponse(BaseModel):
    success: bool
    post_url: Optional[str] = None
    already_existed: bool = False
    error_message: Optional[str] = None


class PostInfo(BaseModel):
    id: str
    title: str
    body: str = ""
    author: str
    subreddit: str
    url: str
    score: int
    link_url: Optional[str] = None

class GetForumPostsResponse(BaseModel):
    posts: List[PostInfo]


class CommentInfo(BaseModel):
    id: str
    body: str
    author: str
    url: str
    score: int

class GetCommentsResponse(BaseModel):
    comments: List[CommentInfo]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/vote_post", response_model=VotePostResponse)
def vote_post_endpoint(payload: VotePostRequest) -> VotePostResponse:
    """
    Upvote or downvote a submission.
    direction: "up" to upvote, "down" to downvote.
    post_url: full URL of the submission page.
    """
    reddit_base = resolve_base_url(payload.base_url)
    # Resolve URL: accept post_url directly, or construct from post_id + forum
    url = payload.post_url
    # If post_url is a JSON-stringified dict (e.g. loop_item was a post object not a string),
    # extract the 'url' or 'post_url' field from it.
    if url and url.strip().startswith("{"):
        import json as _json
        try:
            obj = _json.loads(url)
            if isinstance(obj, dict):
                url = obj.get("url") or obj.get("post_url") or url
        except Exception:
            pass
    if not url and payload.post_id:
        forum = payload.forum or "books"
        url = f"{reddit_base}/f/{forum}/{payload.post_id}"
    if not url:
        return VotePostResponse(success=False, error_message="Provide post_url or post_id")

    # Resolve direction: accept "up"/"down" or "1"/"-1"/1/-1
    raw = str(payload.direction or payload.vote or "up").strip()
    direction = "up" if raw in ("up", "1", "upvote") else "down"

    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            result = vote_post(page, url, direction, base=reddit_base)
            return VotePostResponse(success=result.success, error_message=result.error_message)
        finally:
            browser.close()


@app.post("/subscribe_forum", response_model=SubscribeForumResponse)
def subscribe_forum_endpoint(payload: SubscribeForumRequest) -> SubscribeForumResponse:
    """
    Subscribe to a forum (subreddit). Idempotent — returns already_subscribed=true if already done.
    forum_name: the forum to subscribe to (also accepted as "body" if the agent passes a bare string reference).
    """
    reddit_base = resolve_base_url(payload.base_url)
    forum_name = payload.forum_name or payload.body
    if not forum_name:
        return SubscribeForumResponse(success=False, error_message="forum_name is required")
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            result = subscribe_to_forum(page, forum_name, base=reddit_base)
            return SubscribeForumResponse(
                success=result.success,
                already_subscribed=result.already_subscribed,
                error_message=result.error_message,
            )
        finally:
            browser.close()


@app.post("/update_biography", response_model=UpdateBiographyResponse)
def update_biography_endpoint(payload: UpdateBiographyRequest) -> UpdateBiographyResponse:
    """
    Update the biography of the logged-in user. Markdown is supported.
    username must be the currently logged-in user's username.
    """
    reddit_base = resolve_base_url(payload.base_url)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            result = update_biography(page, payload.username, payload.biography, base=reddit_base)
            return UpdateBiographyResponse(success=result.success, error_message=result.error_message)
        finally:
            browser.close()


@app.post("/reply_to_comment", response_model=ReplyToCommentResponse)
def reply_to_comment_endpoint(payload: ReplyToCommentRequest) -> ReplyToCommentResponse:
    """
    Reply to a specific comment on a post.
    Pass EITHER:
      - comment_url (full comment permalink from /comments response, e.g. http://host/f/books/59421/-/comment/1235250)
      OR
      - post_url + comment_id separately
    reply_text (or alias "text"): the reply body.
    """
    reddit_base = resolve_base_url(payload.base_url)
    # Resolve reply text — accept "text" as alias
    reply_text = payload.reply_text or payload.text
    if not reply_text:
        return ReplyToCommentResponse(success=False, error_message="reply_text (or text) is required")

    # Derive post_url + comment_id from comment_url if provided
    post_url = payload.post_url
    comment_id = payload.comment_id
    if payload.comment_url and (not post_url or not comment_id):
        # comment_url format: http://host/f/{forum}/{post_id}/-/comment/{comment_id}
        parts = payload.comment_url.rstrip("/").split("/")
        try:
            c_idx = parts.index("comment")
            comment_id = comment_id or parts[c_idx + 1]
            # post_url = everything before /-/comment/...
            post_url = post_url or "/".join(parts[:c_idx - 1])
        except (ValueError, IndexError):
            return ReplyToCommentResponse(success=False, error_message=f"Cannot parse comment_url: {payload.comment_url}")

    # Validate we have what we need
    if not comment_id or not comment_id.strip() or comment_id.strip().lower() in ("null", "none", ""):
        return ReplyToCommentResponse(success=False, error_message="comment_id is required and must be a valid numeric ID")
    if not post_url:
        return ReplyToCommentResponse(success=False, error_message="post_url (or comment_url) is required")

    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            result = reply_to_comment(page, post_url, comment_id.strip(), reply_text, base=reddit_base)
            return ReplyToCommentResponse(
                success=result.success,
                comment_url=result.comment_url,
                error_message=result.error_message,
            )
        finally:
            browser.close()


@app.post("/edit_post", response_model=EditPostResponse)
def edit_post_endpoint(payload: EditPostRequest) -> EditPostResponse:
    """
    Edit a post's title and/or body.
    post_url: full URL of the submission.
    new_body / new_title: pass only the fields you want to change.
    """
    reddit_base = resolve_base_url(payload.base_url)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            result = edit_post(page, payload.post_url, payload.new_body, payload.new_title, payload.append, base=reddit_base)
            return EditPostResponse(
                success=result.success,
                post_url=result.post_url,
                error_message=result.error_message,
            )
        finally:
            browser.close()


@app.post("/create_post", response_model=CreatePostResponse)
def create_post_endpoint(payload: CreatePostRequest) -> CreatePostResponse:
    """
    Create a new submission. Checks for duplicate titles (idempotent).
    forum: forum name (e.g. "books"). body: post body text. url: optional link URL for link posts.
    """
    reddit_base = resolve_base_url(payload.base_url)
    # Get the username from the current session to check for duplicates
    from dotenv import dotenv_values
    from pathlib import Path
    creds = dotenv_values(Path(__file__).parent.parent.parent / "config" / ".env")
    username = creds.get("REDDIT_USERNAME", "").strip()

    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            result = create_post(page, payload.forum, payload.title, payload.body, username, payload.url, base=reddit_base)
            return CreatePostResponse(
                success=result.success,
                post_url=result.post_url,
                already_existed=result.already_existed,
                error_message=result.error_message,
            )
        finally:
            browser.close()


def _all_forum_names(reddit_base: str) -> list[str]:
    """Fetch the canonical list of forum names from /forums/all."""
    import re as _re
    import requests as _http
    phpsessid = _get_cached_phpsessid(reddit_base)
    s = _http.Session()
    s.cookies.set("PHPSESSID", phpsessid)
    r = s.get(f"{reddit_base}/forums/all")
    return sorted(set(_re.findall(r'/f/([A-Za-z0-9_]+)', r.text)))


@app.get("/session")
def session_endpoint(base_url: Optional[str] = None) -> dict:
    """Return the current PHPSESSID the server is using (refreshing if expired)."""
    reddit_base = resolve_base_url(base_url)
    return {"phpsessid": _get_cached_phpsessid(reddit_base)}


@app.get("/list_forums")
def list_forums_endpoint(base_url: Optional[str] = None) -> dict:
    """
    Return the names of all forums (subreddits) on this site.
    Use this to discover the exact forum name before calling /create_post or /forum_posts.
    """
    reddit_base = resolve_base_url(base_url)
    return {"forums": [{"name": f} for f in _all_forum_names(reddit_base)]}


@app.get("/find_forum_by_name")
def find_forum_by_name_endpoint(name: str, base_url: Optional[str] = None) -> dict:
    """
    Resolve a user-supplied forum name to its canonical form.

    Postmill forum names are case-sensitive (the URL `/f/books` is distinct
    from `/f/Books`), but task intents often use casual casing ("Books",
    "r/Books"). This endpoint matches by exact name first, then
    case-insensitive, and returns the canonical name to pass to /create_post,
    /forum_posts, /vote_post, etc.

    Returns:
        {"name": <canonical_name>, "exists": true, "url": "/f/<name>"} on match,
        {"name": null, "exists": false, "candidates": [...]} when no match.
        `candidates` is a short list of forums whose name contains the query
        as a substring (case-insensitive) — useful for fuzzy intents.
    """
    reddit_base = resolve_base_url(base_url)
    query = (name or "").strip()
    for prefix in ("r/", "f/"):
        if query.startswith(prefix):
            query = query[len(prefix):]

    forums = _all_forum_names(reddit_base)
    if not query:
        return {"name": None, "exists": False, "candidates": []}

    exact = next((f for f in forums if f == query), None)
    if exact is None:
        target = query.lower()
        exact = next((f for f in forums if f.lower() == target), None)

    if exact is not None:
        return {"name": exact, "exists": True, "url": f"/f/{exact}"}

    # Normalized matching for the common casual variants the planner emits:
    #   "relationships"     → strip trailing 's' → "relationship" → in "relationship_advice"
    #   "deep learning"     → strip whitespace  → "deeplearning"
    #   "long-distance"     → strip dashes      → "longdistance"
    #   "Worcester"         → lowercase         → "worcester" → prefix of "worcesterma"
    _DIGIT_WORDS = {
        "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
        "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    }

    def _norm(s: str) -> str:
        # Replace standalone digits with their English word so that
        # "explain like im 5" normalises to match "explainlikeimfive".
        parts = []
        for ch in s.lower():
            parts.append(_DIGIT_WORDS.get(ch, ch) if ch.isdigit() else ch)
        s = "".join(c for c in "".join(parts) if c.isalnum())
        # English plural heuristic — only strip for words long enough that
        # the 's' is unlikely to be load-bearing
        if len(s) > 4 and s.endswith("s"):
            s = s[:-1]
        return s

    nq = _norm(query)
    if nq:
        # Score forums by match quality: exact normalized > substring either way
        exact_norm = next((f for f in forums if _norm(f) == nq), None)
        if exact_norm is not None:
            return {
                "name": exact_norm,
                "exists": True,
                "url": f"/f/{exact_norm}",
                "fuzzy_match": True,
                "matched_via": "normalized_exact",
            }

        substr = [f for f in forums if nq in _norm(f) or _norm(f) in nq]
        if len(substr) == 1:
            only = substr[0]
            # Only auto-select when the query is a sub-string of the forum name
            # (nq ⊆ _norm(f)).  The reverse case — forum name is a sub-string of
            # the query (e.g. "technology" inside "futuretechnology") — is too
            # ambiguous: a better match might exist via difflib (e.g. "Futurology").
            if nq in _norm(only):
                return {
                    "name": only,
                    "exists": True,
                    "url": f"/f/{only}",
                    "fuzzy_match": True,
                    "matched_via": "normalized_substring",
                }
            # forum ⊆ query — fall through to difflib; keep `only` as a fallback
            substr_fallback = only
        else:
            substr_fallback = None
        if len(substr) > 1:
            # Tiebreak: prefer the candidate whose normalized name matches an
            # individual word in the original query (multi-word queries like
            # "gaming consoles" contain the exact forum name as a token).
            import re as _re
            query_words = [w for w in _re.split(r"[^a-z0-9]+", query.lower()) if w and len(w) >= 2]
            word_set = set(query_words)
            word_matches = [f for f in substr if _norm(f) in word_set or f.lower() in word_set]
            if len(word_matches) == 1:
                only = word_matches[0]
                return {"name": only, "exists": True, "url": f"/f/{only}", "fuzzy_match": True, "matched_via": "word_tiebreak"}
            if len(word_matches) > 1:
                # Multiple query-word matches: prefer the one whose matching word
                # appears LAST in the query (last token = most specific term).
                positions = {}
                for f in word_matches:
                    f_key = _norm(f) if _norm(f) in word_set else f.lower()
                    if f_key in query_words:
                        positions[f] = max(i for i, w in enumerate(query_words) if w == f_key)
                best = max(positions, key=lambda f: positions[f]) if positions else word_matches[0]
                return {"name": best, "exists": True, "url": f"/f/{best}", "fuzzy_match": True, "matched_via": "word_tiebreak_last"}
            # No individual-word match: populate name with first candidate so that
            # plan references like {step_1.result.name} resolve to something usable.
            return {"name": substr[0], "exists": True, "url": f"/f/{substr[0]}", "candidates": substr[:8], "fuzzy_match": True, "matched_via": "candidate_fallback"}

        # Fuzzy close-match fallback using difflib (handles e.g. "games" → "gaming").
        import difflib
        import re as _re
        norm_forum_map = {_norm(f): f for f in forums}
        close = difflib.get_close_matches(nq, list(norm_forum_map.keys()), n=3, cutoff=0.6)
        if len(close) == 1:
            only = norm_forum_map[close[0]]
            return {
                "name": only,
                "exists": True,
                "url": f"/f/{only}",
                "fuzzy_match": True,
                "matched_via": "difflib_close",
            }
        if close:
            # Tie/near-tie tiebreak: "future technology" scores the same against
            # "futurology" and "technology". Prefer a candidate whose normalized
            # name starts with a prefix of the first content word of the query.
            _STOPWORDS = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "my"}
            content_words = [
                w for w in _re.split(r"[^a-z0-9]+", query.lower())
                if w and w not in _STOPWORDS and len(w) >= 3
            ]
            if content_words:
                first = content_words[0]
                prefix = first[:min(4, len(first))]
                prefix_matches = [c for c in close if c.startswith(prefix)]
                if len(prefix_matches) == 1:
                    only = norm_forum_map[prefix_matches[0]]
                    return {
                        "name": only,
                        "exists": True,
                        "url": f"/f/{only}",
                        "fuzzy_match": True,
                        "matched_via": "difflib_close_prefix_tiebreak",
                    }
            best_close = norm_forum_map[close[0]]
            return {"name": best_close, "exists": True, "url": f"/f/{best_close}", "candidates": [norm_forum_map[c] for c in close], "fuzzy_match": True, "matched_via": "difflib_candidate_fallback"}

        # Last resort: the forum-name-in-query single match we deferred earlier.
        if substr_fallback:
            return {
                "name": substr_fallback,
                "exists": True,
                "url": f"/f/{substr_fallback}",
                "fuzzy_match": True,
                "matched_via": "normalized_substring_fallback",
            }

    # Word-by-word fallback for multi-word queries where the full query didn't
    # match.  Split into content words and try each individually (reverse order
    # so the most specific/rightmost term is tried first).
    import re as _re
    _WBW_STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "my",
                 "is", "are", "by", "at", "it", "its", "be", "as", "was"}
    content_words = [
        w for w in _re.split(r"[^a-z0-9]+", query.lower())
        if w and len(w) >= 3 and w not in _WBW_STOP and w != query.lower()
    ]
    if len(content_words) > 1:
        for word in reversed(content_words):
            word_norm = _norm(word)
            if not word_norm:
                continue
            # Exact or normalized exact
            m = next((f for f in forums if f.lower() == word or _norm(f) == word_norm), None)
            if m:
                return {"name": m, "exists": True, "url": f"/f/{m}", "fuzzy_match": True, "matched_via": f"word_fallback:{word}"}
            # Substring: only when the word covers a significant portion of the forum name
            for f in forums:
                fn = _norm(f)
                if fn and word_norm in fn and len(word_norm) / len(fn) >= 0.5:
                    return {"name": f, "exists": True, "url": f"/f/{f}", "fuzzy_match": True, "matched_via": f"word_fallback_substr:{word}"}

    # Final fallback: original case-insensitive substring against raw names
    target = query.lower()
    candidates = [f for f in forums if target in f.lower()][:8]
    return {"name": None, "exists": False, "candidates": candidates}


@app.get("/forum_posts", response_model=GetForumPostsResponse)
def get_forum_posts_endpoint(
    forum: str,
    sort: str = "hot",
    limit: int = 25,
    base_url: Optional[str] = None,
) -> GetForumPostsResponse:
    """
    List posts in a forum with their scores.
    sort: hot (default), new, top (all-time highest score), controversial, active. Use "top" for tasks that say "ever", "all-time", or "highest score".
    limit: max posts to return (default 25, max 50).
    """
    reddit_base = resolve_base_url(base_url)
    limit = min(limit, 50)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            posts = get_forum_posts(page, forum, sort, limit, base=reddit_base)
            return GetForumPostsResponse(posts=[
                PostInfo(
                    id=p.id, title=p.title, body=p.body, author=p.author,
                    subreddit=p.subreddit, url=p.url, score=p.score,
                    link_url=p.link_url,
                )
                for p in posts
            ])
        finally:
            browser.close()


@app.get("/post", response_model=GetPostResponse)
def get_post_endpoint(forum: str, post_id: str, base_url: Optional[str] = None) -> GetPostResponse:
    """
    Fetch a single post by forum name and post ID.
    Returns the full body text, score, author, and link URL.
    forum: forum name (e.g. "MachineLearning").
    post_id: numeric post ID from the post URL (/f/{forum}/{post_id}/...).
    """
    reddit_base = resolve_base_url(base_url)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            post = get_post(page, forum, post_id, base=reddit_base)
            if post is None:
                return GetPostResponse(
                    id=post_id, title="", body="", author="", subreddit=forum,
                    url="", score=0, error_message="Post not found",
                )
            return GetPostResponse(
                id=post.id,
                title=post.title,
                body=post.body,
                author=post.author,
                subreddit=post.subreddit,
                url=post.url,
                score=post.score,
                link_url=post.link_url,
            )
        finally:
            browser.close()


@app.get("/search", response_model=GetForumPostsResponse)
def search_endpoint(
    q: str,
    limit: int = 25,
    base_url: Optional[str] = None,
) -> GetForumPostsResponse:
    """
    Full-text search across all posts by title and body content.
    Returns posts whose title or body contains the query string (fuzzy match).
    Use this instead of /forum_posts when you need to find a specific post by
    a keyword or partial title rather than listing a forum's posts.
    q: search query (e.g. "Bald Eagle").
    limit: max posts to return (default 25, max 50).
    """
    reddit_base = resolve_base_url(base_url)
    limit = min(limit, 50)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            posts = search_posts(page, q, limit, base=reddit_base)
            return GetForumPostsResponse(posts=[
                PostInfo(
                    id=post.id, title=post.title, body=post.body, author=post.author,
                    subreddit=post.subreddit, url=post.url, score=post.score,
                    link_url=post.link_url,
                )
                for post in posts
            ])
        finally:
            browser.close()


@app.get("/user_posts", response_model=GetForumPostsResponse)
def get_user_posts_endpoint(
    username: str,
    limit: int = 50,
    forum: Optional[str] = None,
    base_url: Optional[str] = None,
) -> GetForumPostsResponse:
    """
    List all submissions by a user from their profile /submissions page.
    Use this when you need to find and act on all posts by a specific user,
    regardless of which forum they posted in.
    limit: max posts to return (default 50).
    forum: optional forum name to filter results (case-insensitive substring match on subreddit).
    """
    # When filtering by forum, we must scan beyond the first `limit` posts
    # because the user may have many posts in other forums before reaching
    # the target one. Fetch up to 500 to ensure we cover most users.
    reddit_base = resolve_base_url(base_url)
    fetch_limit = min(limit, 50) if not forum else 500
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            posts = get_posts_by_username(page, username, fetch_limit, base=reddit_base)
            if forum:
                forum_lower = forum.lower()
                posts = [p for p in posts if p.subreddit and p.subreddit.lower() == forum_lower]
                posts = posts[:limit]
            return GetForumPostsResponse(posts=[
                PostInfo(
                    id=p.id, title=p.title, body=p.body, author=p.author,
                    subreddit=p.subreddit, url=p.url, score=p.score,
                    link_url=p.link_url,
                )
                for p in posts
            ])
        finally:
            browser.close()


@app.get("/comments", response_model=GetCommentsResponse)
def get_comments_endpoint(forum: str, post_id: str, base_url: Optional[str] = None) -> GetCommentsResponse:
    """
    Get all comments on a post with their net vote scores.
    score > 0 means more upvotes; score < 0 means more downvotes than upvotes.
    """
    reddit_base = resolve_base_url(base_url)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            comments = get_comments_on_post(page, forum, post_id, base=reddit_base)
            return GetCommentsResponse(comments=[
                CommentInfo(id=c.id, body=c.body, author=c.author, url=c.url, score=c.score)
                for c in comments
            ])
        finally:
            browser.close()


class CreateCommentRequest(BaseModel):
    post_url: str
    comment_text: str
    base_url: Optional[str] = None

class CreateCommentResponse(BaseModel):
    success: bool
    comment_url: Optional[str] = None
    error_message: Optional[str] = None


class CreateForumRequest(BaseModel):
    forum_name: str
    forum_title: str
    forum_description: str
    forum_sidebar: str
    base_url: Optional[str] = None

class CreateForumResponse(BaseModel):
    success: bool
    forum_url: Optional[str] = None
    forum_name: Optional[str] = None
    already_existed: bool = False
    error_message: Optional[str] = None


@app.post("/create_comment", response_model=CreateCommentResponse)
def create_comment_endpoint(payload: CreateCommentRequest) -> CreateCommentResponse:
    """
    Post a top-level comment on a submission.
    post_url: full URL of the submission page.
    """
    reddit_base = resolve_base_url(payload.base_url)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            result = comment_on_post_by_url(page, payload.post_url, payload.comment_text, base=reddit_base)
            return CreateCommentResponse(
                success=result.success,
                comment_url=result.comment_url,
                error_message=result.error_message,
            )
        finally:
            browser.close()


@app.post("/create_forum", response_model=CreateForumResponse)
def create_forum_endpoint(payload: CreateForumRequest) -> CreateForumResponse:
    """
    Create a new forum (subreddit). Checks if the forum already exists (idempotent).
    forum_name is the URL-safe identifier; forum_title is the display name.
    Both forum_description and forum_sidebar are required.
    """
    reddit_base = resolve_base_url(payload.base_url)
    from dotenv import dotenv_values
    from pathlib import Path
    creds = dotenv_values(Path(__file__).parent.parent.parent / "config" / ".env")
    username = creds.get("REDDIT_USERNAME", "").strip()

    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            result = create_forum(
                page, payload.forum_name, payload.forum_title,
                payload.forum_description, payload.forum_sidebar, username,
                base=reddit_base,
            )
            return CreateForumResponse(
                success=result.success,
                forum_url=result.forum_url,
                forum_name=result.forum_name,
                already_existed=result.already_existed,
                error_message=result.error_message,
            )
        finally:
            browser.close()


class SendMessageRequest(BaseModel):
    recipient_username: str
    subject: str = ""
    message_body: str
    base_url: Optional[str] = None

class SendMessageResponse(BaseModel):
    success: bool
    error_message: Optional[str] = None


class MessageInfo(BaseModel):
    subject: str
    sender: str
    body: str

class GetMessagesResponse(BaseModel):
    messages: List[MessageInfo]


class DeletePostRequest(BaseModel):
    post_url: str
    base_url: Optional[str] = None

class DeletePostResponse(BaseModel):
    success: bool
    error_message: Optional[str] = None


class UpdateEmailRequest(BaseModel):
    username: str
    new_email: str
    base_url: Optional[str] = None

class UpdateEmailResponse(BaseModel):
    success: bool
    error_message: Optional[str] = None


class GetBlockListResponse(BaseModel):
    blocked_users: List[str]


@app.post("/send_message", response_model=SendMessageResponse)
def send_message_endpoint(payload: SendMessageRequest) -> SendMessageResponse:
    """Send a private message (DM) to another user."""
    reddit_base = resolve_base_url(payload.base_url)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            result = send_message(page, payload.recipient_username, payload.subject, payload.message_body, base=reddit_base)
            return SendMessageResponse(success=result.success, error_message=result.error_message)
        finally:
            browser.close()


@app.get("/get_messages", response_model=GetMessagesResponse)
def get_messages_endpoint(base_url: Optional[str] = None) -> GetMessagesResponse:
    """Read the inbox — returns subject, sender, and body for each message."""
    reddit_base = resolve_base_url(base_url)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            msgs = get_messages(page, base=reddit_base)
            return GetMessagesResponse(messages=[
                MessageInfo(subject=m.subject, sender=m.sender, body=m.body)
                for m in msgs
            ])
        finally:
            browser.close()


@app.post("/delete_post", response_model=DeletePostResponse)
def delete_post_endpoint(payload: DeletePostRequest) -> DeletePostResponse:
    """Delete a post by its URL (must be owned by the logged-in user)."""
    reddit_base = resolve_base_url(payload.base_url)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            result = delete_post(page, payload.post_url, base=reddit_base)
            return DeletePostResponse(success=result.success, error_message=result.error_message)
        finally:
            browser.close()


@app.post("/update_email", response_model=UpdateEmailResponse)
def update_email_endpoint(payload: UpdateEmailRequest) -> UpdateEmailResponse:
    """Change the logged-in user's email address in account settings."""
    reddit_base = resolve_base_url(payload.base_url)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            result = update_email(page, payload.username, payload.new_email, base=reddit_base)
            return UpdateEmailResponse(success=result.success, error_message=result.error_message)
        finally:
            browser.close()


@app.get("/get_block_list", response_model=GetBlockListResponse)
def get_block_list_endpoint(username: str, base_url: Optional[str] = None) -> GetBlockListResponse:
    """Return the list of usernames blocked by the given user."""
    reddit_base = resolve_base_url(base_url)
    with sync_playwright() as p:
        browser, page = _make_browser_page(p, reddit_base)
        try:
            users = get_blocked_users(page, username, base=reddit_base)
            return GetBlockListResponse(blocked_users=users)
        finally:
            browser.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7791)
