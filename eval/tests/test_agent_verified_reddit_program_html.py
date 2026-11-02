# eval/tests/test_agent_verified_reddit_program_html.py
#
# Integration tests: run the agent on every Reddit program_html task in
# reddit_verified_program_html.json.
#
# Run all tasks:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_program_html.py -v
#
# Smoke test (first 5 tasks):
#   python3 -m pytest eval/tests/test_agent_verified_reddit_program_html.py --task-limit 5 -v -s
#
# Single task by ID:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_program_html.py --task-id 465 -v -s
#
# Multiple tasks by ID:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_program_html.py --task-id 465,521 -v -s
#
# Save results to a JSON log:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_program_html.py -v --output reddit_program_html_results.json
#
# Use multi-docker worker pool:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_program_html.py -v --multi-docker
#
# Resume a previous run:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_program_html.py -v --output reddit_program_html_results.json --resume
#
# Trigger WebArena Docker reset before each write task:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_program_html.py -v --force-reset

import asyncio
import json
import os
import re
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from dotenv import dotenv_values as _dotenv_values

import requests as _requests

from agent.baya_json_agent.auth import StaticAuth
from config.init_tokens.refresh_reddit_session import refresh_session as _refresh_reddit_session
from config.servers import SERVER_URLS as _SERVER_URLS
from eval.docker import workers_new as _workers_new
from eval.run_pte_benchmark import AgentRunner
from eval.tests.agent_test_utils import (
    build_detailed_entry,
    extract_agent_details,
    flush_detailed_jsonl,
    get_model_id,
    task_status,
)

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TASK_FILE = Path(__file__).parent / "test_files" / "reddit_verified_program_html.json"
LOGS_DIR = Path(__file__).parent / "logs"

_ENV_FILE = Path(__file__).parent.parent.parent / "config" / ".env"
_REDDIT_USERNAME: str = _dotenv_values(_ENV_FILE).get("REDDIT_USERNAME", "MarvelsGrantMan136")

# ---------------------------------------------------------------------------
# Task ordering
# ---------------------------------------------------------------------------

# Priority order for task execution (lower = runs first).
# Templates that require the agent to navigate to a post by FEED POSITION
# (newest/top/hottest) must run before any post-creation templates, or new
# posts created by earlier tasks will shift the feed and send the agent to
# the wrong post.
_TEMPLATE_PRIORITY: dict = {
    22: 0,      # Upvote newest post in forum — most feed-position-sensitive
    4: 1,       # Subscribe from hottest/top/newest/most-commented post page
    24: 2,      # Thumbs down top-N post (hardcoded URLs, not feed-sensitive)
    25: 2,       # Like all submissions by user (hardcoded)
    1510: 2,    # Dislike all submissions by user (hardcoded)
    23: 3,      # Reply to specific comment (hardcoded thread URL)
    6: 4,       # Change bio
    7: 5,       # Create forum
    27: 6,      # Edit own post
    # Post-creation templates — run last (they add new posts to feeds)
    3765: 10, 5: 10, 9: 10, 11: 10, 12: 10, 13: 10,
    15: 10, 16: 10, 19: 10, 6100: 10,
}

_VOTE_TEMPLATE_IDS: frozenset = frozenset({22, 24, 25, 1510})
_POST_CREATION_TEMPLATE_IDS: frozenset = frozenset({3765, 5, 9, 11, 12, 13, 15, 16, 19, 6100})

_IMPORT_CUTOFF = "2024-01-01"


def _task_priority(task: Dict[str, Any]) -> int:
    return _TEMPLATE_PRIORITY.get(task.get("intent_template_id"), 6)


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

def _load_tasks(config=None) -> List[Dict[str, Any]]:
    tasks = json.loads(TASK_FILE.read_text())
    if config is not None:
        task_id = config.getoption("--task-id", default=None)
        if task_id is not None:
            ids = {int(x.strip()) for x in task_id.split(",")}
            tasks = [t for t in tasks if t.get("task_id") in ids]
        else:
            limit = config.getoption("--task-limit", default=None)
            if limit is not None:
                tasks = tasks[:limit]

        resume = config.getoption("--resume", default=False)
        output_name = config.getoption("--output", default=None)
        if resume and output_name:
            out_path = LOGS_DIR / output_name
            if out_path.exists():
                prior = json.loads(out_path.read_text())
                done_ids = {r["task_id"] for r in prior.get("results", [])}
                before = len(tasks)
                tasks = [t for t in tasks if t["task_id"] not in done_ids]
                print(f"\n--resume: skipping {before - len(tasks)} already-completed tasks, {len(tasks)} remaining")

    # Sort by priority so feed-position-sensitive tasks run first.
    # Use task_id as a tiebreaker for stability.
    tasks.sort(key=lambda t: (_task_priority(t), t.get("task_id", 0)))
    return tasks


# ---------------------------------------------------------------------------
# Checkpoint restore — shared with the ReactAgent runner via eval.reddit_state_reset
# ---------------------------------------------------------------------------

from eval.reddit_state_reset import restore_checkpoint as _restore_checkpoint


# ---------------------------------------------------------------------------
# Session helper — borrow the MCP server's current session
# ---------------------------------------------------------------------------

def _mcp_phpsessid(mcp_url: str) -> str:
    """Return the PHPSESSID the MCP server is currently using."""
    r = _requests.get(f"{mcp_url}/session", timeout=5)
    r.raise_for_status()
    return r.json()["phpsessid"]


# ---------------------------------------------------------------------------
# Helpers — state restore
# ---------------------------------------------------------------------------

def _read_bio(base_url: str, phpsessid: str, username: str) -> str:
    """Return the current value of the user's biography field."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "PHPSESSID", "value": phpsessid, "url": base_url}])
        page = ctx.new_page()
        page.goto(f"{base_url}/user/{username}/edit_biography", wait_until="domcontentloaded")
        bio = page.input_value("#user_biography_biography")
        browser.close()
    return bio


def _restore_bio(base_url: str, phpsessid: str, username: str, bio_value: str) -> None:
    """Reset the user's biography to bio_value via Playwright form submission."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "PHPSESSID", "value": phpsessid, "url": base_url}])
        page = ctx.new_page()
        page.goto(f"{base_url}/user/{username}/edit_biography", wait_until="domcontentloaded")
        page.fill("#user_biography_biography", bio_value)
        page.locator("form[name='user_biography'] button").click()
        page.wait_for_load_state("networkidle")
        browser.close()
    print(f"  [undo] Bio reset to {bio_value!r}")


def _vote_urls_from_task(task: Dict[str, Any], base_url: str) -> List[str]:
    """Extract post URLs the task's eval expects to find votes on."""
    urls = []
    for check in (task.get("eval", {}) or {}).get("program_html", []) or []:
        url = check.get("url", "") or ""
        if not url.startswith("__REDDIT__/f/"):
            continue
        rc = check.get("required_contents", {}) or {}
        needles = []
        if rc.get("must_include"):
            needles += [str(s) for s in rc["must_include"]]
        if rc.get("exact_match"):
            needles.append(str(rc["exact_match"]))
        if any("vote--user-" in n for n in needles):
            full_url = url.replace("__REDDIT__", base_url.rstrip("/"))
            if full_url not in urls:
                urls.append(full_url)
    return urls


def _restore_votes_on_urls(base_url: str, phpsessid: str, urls: List[str]) -> None:
    """Retract any active vote on the given post URLs (idempotent if neutral)."""
    if not urls:
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "PHPSESSID", "value": phpsessid, "url": base_url}])
        page = ctx.new_page()
        retracted = 0
        for url in urls:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                retract = page.locator("div.submission__vote form button[value='0']")
                if retract.count() > 0:
                    retract.first.click()
                    page.wait_for_load_state("networkidle", timeout=8000)
                    retracted += 1
            except Exception as exc:
                print(f"  [undo] Warning: could not retract vote on {url}: {exc}")
        browser.close()
    if retracted:
        print(f"  [undo] Retracted {retracted} vote(s)")


def _forum_name_from_subscribe_task(task: Dict[str, Any]) -> Optional[str]:
    """Extract the forum name that a subscribe task subscribes to.

    Template 4 evals check the homepage sidebar's outerText for the forum
    name via must_include — the first must_include string is the forum name.
    """
    ph = (task.get("eval", {}) or {}).get("program_html", []) or []
    for check in ph:
        url = check.get("url", "") or ""
        if "__REDDIT__/" == url or url == "__REDDIT__":
            rc = check.get("required_contents", {}) or {}
            mi = rc.get("must_include") or []
            if mi:
                return str(mi[0])
    return None


def _unsubscribe_from_forums(base_url: str, phpsessid: str, forum_names: List[str]) -> None:
    """Unsubscribe from each listed forum (idempotent if already unsubscribed)."""
    if not forum_names:
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "PHPSESSID", "value": phpsessid, "url": base_url}])
        page = ctx.new_page()
        for forum in forum_names:
            try:
                page.goto(f"{base_url}/f/{forum}", wait_until="domcontentloaded", timeout=15000)
                unsub = page.locator("form[action*='/unsubscribe'] button")
                if unsub.count() > 0:
                    unsub.first.click()
                    page.wait_for_load_state("networkidle", timeout=8000)
                    print(f"  [undo] Unsubscribed from f/{forum}")
            except Exception as exc:
                print(f"  [undo] Warning: could not unsubscribe from {forum}: {exc}")
        browser.close()


def _delete_forum(base_url: str, phpsessid: str, forum_name: str) -> None:
    """Delete a forum via Postmill's moderator delete form."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "PHPSESSID", "value": phpsessid, "url": base_url}])
        page = ctx.new_page()
        try:
            page.goto(f"{base_url}/f/{forum_name}", wait_until="domcontentloaded", timeout=15000)
            content = page.content().lower()
            if "not found" in content or "404" in content:
                return
            page.goto(f"{base_url}/f/{forum_name}/moderation", wait_until="domcontentloaded", timeout=15000)
            delete_btn = page.locator("form[action*='/delete'] button")
            if delete_btn.count() == 0:
                page.goto(f"{base_url}/f/{forum_name}/edit", wait_until="domcontentloaded", timeout=15000)
                delete_btn = page.locator("form[action*='/delete'] button")
            if delete_btn.count() > 0:
                delete_btn.first.click()
                page.wait_for_load_state("networkidle", timeout=8000)
                print(f"  [undo] Deleted forum '{forum_name}'")
            else:
                print(f"  [undo] Warning: could not find delete button for forum '{forum_name}'")
        except Exception as exc:
            print(f"  [undo] Warning: could not delete forum '{forum_name}': {exc}")
        finally:
            browser.close()


def _delete_comment_in_thread(
    base_url: str, phpsessid: str, parent_post_url: str, text_fragment: str
) -> None:
    """Find and delete any comment by the logged-in user in parent_post_url
    whose body contains text_fragment.

    Postmill comment deletes are triggered by a JS confirm() dialog, not a
    form POST, so we set up a dialog handler then click the scoped Delete
    button within the matching comment article.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "PHPSESSID", "value": phpsessid, "url": base_url}])
        page = ctx.new_page()
        deleted = 0
        try:
            page.on("dialog", lambda d: d.accept())
            page.goto(parent_post_url, wait_until="domcontentloaded", timeout=15000)
            comments = page.locator("article.comment")
            for i in range(comments.count()):
                comment = comments.nth(i)
                body = comment.locator(".comment__body")
                if body.count() == 0:
                    continue
                if text_fragment.lower() not in (body.first.inner_text() or "").lower():
                    continue
                # Delete button only appears on the user's own comments.
                delete_btn = comment.locator('button:has-text("Delete")')
                if delete_btn.count() == 0:
                    continue
                try:
                    delete_btn.first.click()
                    page.wait_for_timeout(1000)
                    deleted += 1
                except Exception as exc:
                    print(f"  [undo] Warning: could not delete comment: {exc}")
            if deleted:
                print(f"  [undo] Deleted {deleted} comment(s) matching {text_fragment!r}")
        except Exception as exc:
            print(f"  [undo] Warning: comment cleanup failed for {parent_post_url}: {exc}")
        finally:
            browser.close()


def _edit_url_from_post_url(post_url: str) -> str:
    """Build the Postmill edit URL from any post URL.

    Postmill's edit route is /f/{forum}/{post_id}/-/edit — the slug is not
    part of the path.  Matches posts.py:edit_post() behaviour.
    """
    m = re.search(r"/f/([^/]+)/(\d+)", post_url)
    if m:
        return f"{post_url.split('/f/')[0]}/f/{m.group(1)}/{m.group(2)}/-/edit"
    return post_url.rstrip("/") + "/-/edit"


def _fetch_post_body(base_url: str, phpsessid: str, post_url: str) -> str:
    """Return the current body text of a post via its edit page."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "PHPSESSID", "value": phpsessid, "url": base_url}])
        page = ctx.new_page()
        try:
            edit_url = _edit_url_from_post_url(post_url)
            page.goto(edit_url, wait_until="domcontentloaded", timeout=15000)
            textarea = page.locator("#submission_body")
            if textarea.count() > 0:
                return textarea.first.input_value() or ""
            print(f"  [undo] Warning: #submission_body not found at {edit_url}")
        except Exception as exc:
            print(f"  [undo] Warning: could not fetch post body from {post_url}: {exc}")
        finally:
            browser.close()
    return ""


def _restore_post_body(base_url: str, phpsessid: str, post_url: str, body: str) -> None:
    """Restore a post's body to the given value via its edit form."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "PHPSESSID", "value": phpsessid, "url": base_url}])
        page = ctx.new_page()
        try:
            edit_url = _edit_url_from_post_url(post_url)
            page.goto(edit_url, wait_until="domcontentloaded", timeout=15000)
            textarea = page.locator("#submission_body")
            if textarea.count() == 0:
                print(f"  [undo] Warning: #submission_body not found at {edit_url}")
                return
            textarea.first.fill(body)
            # Postmill's edit submit button text matches Selectors.EDIT_POST_SUBMIT_BUTTON
            submit = page.locator('button:has-text("Edit submission")')
            if submit.count() > 0:
                submit.first.click()
                page.wait_for_load_state("domcontentloaded", timeout=8000)
                print(f"  [undo] Restored post body at {post_url}")
            else:
                print(f"  [undo] Warning: 'Edit submission' button not found at {edit_url}")
        except Exception as exc:
            print(f"  [undo] Warning: could not restore post body at {post_url}: {exc}")
        finally:
            browser.close()


def _delete_post_by_url(base_url: str, phpsessid: str, post_url: str) -> None:
    """Delete a specific post by its full URL using the Postmill CSRF delete form."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "PHPSESSID", "value": phpsessid, "url": base_url}])
        page = ctx.new_page()
        try:
            page.goto(post_url, wait_until="domcontentloaded", timeout=15000)
            html = page.content()
            token_m = re.search(
                r'action="([^"]*/-/delete)"[^>]*>.*?<input[^>]*name="token"\s+value="([^"]+)"',
                html,
                re.DOTALL,
            )
            if not token_m:
                print(f"  [undo] Warning: could not find delete form for post {post_url}")
                return
            action_path, token = token_m.group(1), token_m.group(2)
            delete_url = action_path if action_path.startswith("http") else base_url.rstrip("/") + action_path
            resp = page.request.post(delete_url, form={"token": token})
            if resp.status in (200, 302):
                print(f"  [undo] Deleted post {post_url}")
            else:
                print(f"  [undo] Warning: delete returned HTTP {resp.status} for {post_url}")
        except Exception as exc:
            print(f"  [undo] Warning: could not delete post {post_url}: {exc}")
        finally:
            browser.close()


def _delete_user_posts_matching(
    base_url: str, phpsessid: str, username: str, keywords: List[str]
) -> None:
    """Delete any post created by the user (after _IMPORT_CUTOFF) whose title
    or body contains ALL of the given keywords.

    Scans up to 3 pages of the user's submissions so that stale posts from
    a previously interrupted run are also found.  Stops early once a page
    contains no agent-created (post-cutoff) posts at all.
    """
    if not keywords:
        return
    from playwright.sync_api import sync_playwright
    from datetime import datetime, timezone as tz

    cutoff = datetime.fromisoformat(_IMPORT_CUTOFF).replace(tzinfo=tz.utc)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "PHPSESSID", "value": phpsessid, "url": base_url}])
        page = ctx.new_page()
        deleted = 0
        try:
            next_url: Optional[str] = f"{base_url}/user/{username}/submissions"
            pages_scanned = 0
            to_delete: List[str] = []

            while next_url and pages_scanned < 3:
                page.goto(next_url, wait_until="domcontentloaded", timeout=15000)
                articles = page.locator("article")
                found_any_new = False

                for i in range(articles.count()):
                    art = articles.nth(i)
                    time_el = art.locator("time[datetime]")
                    if time_el.count() > 0:
                        dt_str = time_el.first.get_attribute("datetime") or ""
                        try:
                            created = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                            if created <= cutoff:
                                continue
                        except ValueError:
                            pass
                    found_any_new = True
                    text = (art.inner_text() or "").lower()
                    if all(kw.lower() in text for kw in keywords):
                        links = art.locator("a[href^='/f/']")
                        for j in range(links.count()):
                            href = links.nth(j).get_attribute("href") or ""
                            if re.match(r"^/f/[^/]+/\d+/", href):
                                to_delete.append(href)
                                break

                pages_scanned += 1
                if not found_any_new:
                    break
                next_url = page.evaluate(
                    "document.querySelector('link[rel=\"next\"]')?.href || null"
                )

            for href in to_delete:
                try:
                    page.goto(f"{base_url}{href}", wait_until="domcontentloaded", timeout=15000)
                    html = page.content()
                    token_m = re.search(
                        r'action="[^"]*/-/delete"[^>]*>.*?<input[^>]*name="token"\s+value="([^"]+)"',
                        html,
                        re.DOTALL,
                    )
                    if not token_m:
                        print(f"  [undo] Warning: could not find delete CSRF token for post {href}")
                        continue
                    token = token_m.group(1)
                    post_path = re.sub(r"\?.*", "", href)
                    delete_url = f"{base_url}{post_path.rstrip('/')}/-/delete"
                    resp = page.request.post(delete_url, form={"token": token})
                    if resp.status in (200, 302):
                        deleted += 1
                except Exception as exc:
                    print(f"  [undo] Warning: could not delete post {href}: {exc}")

        except Exception as exc:
            print(f"  [undo] Warning: post cleanup failed: {exc}")
        finally:
            browser.close()

    if deleted:
        print(f"  [undo] Deleted {deleted} post(s) matching {keywords}")


# ---------------------------------------------------------------------------
# Startup stale-post sweep (--cleanup-stale)
# ---------------------------------------------------------------------------

# Forums the agent is expected to interact with across all tasks.
_AGENT_FORUMS = [
    "books", "television", "movies", "music", "science",
    "DIY", "Futurology", "deeplearning", "explainlikeimfive",
    "consoles", "nyc", "pittsburgh", "MachineLearning", "singularity",
    "relationship_advice", "headphones", "sports", "iphone",
    "connecticut", "funny", "earthporn", "wallstreetbets",
    "dataisbeautiful", "technology", "games",
]


def _delete_stale_test_posts(
    base_url: str, phpsessid: str, username: str = _REDDIT_USERNAME
) -> None:
    """Delete agent-created posts that survived from a previous interrupted run.

    Original WebArena dataset posts all pre-date _IMPORT_CUTOFF.  Any post
    with a created_at timestamp after that cutoff was produced by the agent
    and was not cleaned up (e.g. process killed mid-run).  Scans the hot
    feed of each agent forum and deletes matching posts via CSRF token POST.

    The username parameter is accepted for backward compatibility with
    conftest.py but is not used — authorship is determined by the presence
    of a delete form (only shown for your own posts).
    """
    from playwright.sync_api import sync_playwright

    cutoff = datetime.fromisoformat(_IMPORT_CUTOFF).replace(tzinfo=timezone.utc)
    deleted = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "PHPSESSID", "value": phpsessid, "url": base_url}])
        page = ctx.new_page()
        try:
            stale_hrefs: List[str] = []
            for forum in _AGENT_FORUMS:
                next_url: Optional[str] = f"{base_url}/f/{forum}"
                pages_scanned = 0
                while next_url and pages_scanned < 5:
                    page.goto(next_url, wait_until="domcontentloaded", timeout=15000)
                    articles = page.locator("article")
                    found_any_stale = False
                    for i in range(articles.count()):
                        art = articles.nth(i)
                        time_el = art.locator("time[datetime]")
                        if time_el.count() == 0:
                            continue
                        dt_str = time_el.first.get_attribute("datetime") or ""
                        try:
                            created = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        except ValueError:
                            continue
                        if created <= cutoff:
                            continue
                        found_any_stale = True
                        links = art.locator("a[href^='/f/']")
                        href = ""
                        for j in range(links.count()):
                            candidate = links.nth(j).get_attribute("href") or ""
                            if re.match(rf"^/f/{forum}/\d+", candidate):
                                href = candidate
                                break
                        if href and href not in stale_hrefs:
                            stale_hrefs.append(href)
                    pages_scanned += 1
                    if not found_any_stale:
                        break
                    next_url = page.evaluate(
                        "document.querySelector('link[rel=\"next\"]')?.href || null"
                    )

            for href in stale_hrefs:
                try:
                    page.goto(f"{base_url}{href}", wait_until="domcontentloaded", timeout=15000)
                    html = page.content()
                    token_m = re.search(
                        r'action="[^"]*/-/delete"[^>]*>\s*<input[^>]*name="token"\s+value="([^"]+)"',
                        html,
                        re.DOTALL,
                    )
                    if not token_m:
                        continue
                    token = token_m.group(1)
                    post_path = re.sub(r"\?.*", "", href)
                    delete_url = f"{base_url}{post_path.rstrip('/')}/-/delete"
                    resp = page.request.post(delete_url, form={"token": token})
                    if resp.status in (200, 302):
                        deleted += 1
                except Exception as exc:
                    print(f"  [startup] Warning: could not delete stale post {href}: {exc}")
        except Exception as exc:
            print(f"  [startup] Warning: stale-post cleanup failed: {exc}")
        finally:
            browser.close()
    if deleted:
        print(f"  [startup] Deleted {deleted} stale post(s) from previous run(s)", flush=True)


# ---------------------------------------------------------------------------
# Undo dispatcher
# ---------------------------------------------------------------------------

def _keywords_from_task(task: Dict[str, Any]) -> List[str]:
    """Extract must_include keywords from the first program_html eval check."""
    ph = (task.get("eval", {}) or {}).get("program_html", []) or []
    for check in ph:
        rc = check.get("required_contents", {}) or {}
        mi = rc.get("must_include") or []
        if mi:
            return [str(k) for k in mi]
        em = rc.get("exact_match")
        if em:
            return [str(em)]
    return []


async def _apply_undo(
    task: Dict[str, Any],
    passed: bool,
    pre_state: Dict[str, Any],
    base_url: str,
    phpsessid: str,
    original_bio: str,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Run the appropriate undo for a completed task.

    For vote and subscribe tasks the undo is unconditional — partial agent
    runs may have changed state even when the task is scored as failed.
    For create/edit/post tasks the undo is also unconditional because the
    helpers are idempotent (no-op when target doesn't exist).
    """
    tid = task.get("intent_template_id")
    task_id = task.get("task_id")

    if tid in _VOTE_TEMPLATE_IDS:
        # Retract every vote the eval was checking, regardless of pass/fail.
        vote_urls = _vote_urls_from_task(task, base_url)
        if vote_urls:
            await loop.run_in_executor(
                None, _restore_votes_on_urls, base_url, phpsessid, vote_urls
            )

    elif tid == 4:
        # Unsubscribe from the forum this task subscribed to.
        forum = _forum_name_from_subscribe_task(task)
        if forum:
            await loop.run_in_executor(
                None, _unsubscribe_from_forums, base_url, phpsessid, [forum]
            )

    elif tid == 6:
        # Restore bio to the value captured before the run started.
        await loop.run_in_executor(
            None, _restore_bio, base_url, phpsessid, _REDDIT_USERNAME, original_bio
        )

    elif tid == 7:
        # Delete the forum the agent just created.
        forum_name = (task.get("instantiation_dict") or {}).get("name", "")
        if forum_name:
            await loop.run_in_executor(
                None, _delete_forum, base_url, phpsessid, forum_name
            )

    elif tid == 23:
        # Delete the comment the agent posted so future runs don't get a
        # false positive from the comment already being present.
        ph = (task.get("eval", {}) or {}).get("program_html", []) or []
        if ph:
            eval_url = (ph[0].get("url") or "")
            rc = ph[0].get("required_contents") or {}
            mi = rc.get("must_include") or []
            em = rc.get("exact_match")
            text = str(mi[0]) if mi else (str(em) if em else "")

            if eval_url.startswith("__REDDIT__"):
                # Eval URL points to a specific comment thread; strip the
                # comment anchor to get the parent post URL.
                parent_url = re.sub(
                    r"/-/comment/\d+$",
                    "",
                    eval_url.replace("__REDDIT__", base_url.rstrip("/")),
                )
            else:
                # Eval URL is "last" (agent navigated to the post itself).
                # The parent post is the task's start_url.
                start = (task.get("start_url") or "").replace("__REDDIT__", base_url.rstrip("/"))
                parent_url = start

            if parent_url and text:
                await loop.run_in_executor(
                    None, _delete_comment_in_thread, base_url, phpsessid, parent_url, text
                )

    elif tid == 27:
        # Restore post body to the value captured before the task ran.
        ph = (task.get("eval", {}) or {}).get("program_html", []) or []
        if ph:
            post_url = (ph[0].get("url") or "").replace("__REDDIT__", base_url.rstrip("/"))
            original_body = pre_state.get("post_body", "")
            if post_url and original_body:
                await loop.run_in_executor(
                    None, _restore_post_body, base_url, phpsessid, post_url, original_body
                )

    elif tid in _POST_CREATION_TEMPLATE_IDS:
        # Delete the agent-created post. If the agent returned a post_url we
        # navigate there directly (reliable). Otherwise fall back to scanning
        # the user's submissions by keyword (slower, may miss if post is buried
        # below older high-scored posts in the default sort order).
        created_url = pre_state.get("created_url")
        if created_url:
            # Normalise placeholder to the real base URL.
            created_url = created_url.replace("__REDDIT__", base_url.rstrip("/"))
            if not created_url.startswith("http"):
                created_url = base_url.rstrip("/") + "/" + created_url.lstrip("/")
            await loop.run_in_executor(
                None,
                _delete_post_by_url,
                base_url, phpsessid, created_url,
            )
        else:
            keywords = _keywords_from_task(task)
            if keywords:
                await loop.run_in_executor(
                    None,
                    _delete_user_posts_matching,
                    base_url, phpsessid, _REDDIT_USERNAME, keywords,
                )

    else:
        print(f"  [undo] No undo registered for template_id={tid} (task {task_id})")


# ---------------------------------------------------------------------------
# Worker context
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _local_session(reddit_url: str):
    yield {"worker_id": "local", "reddit_url": reddit_url}


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _build_log_entry(r: dict) -> dict:
    task = r["task"]
    return {
        "task_id":         task["task_id"],
        "intent":          task.get("intent", ""),
        "eval_types":      task.get("eval", {}).get("eval_types", []),
        "read_only":       task.get("read_only"),
        "passed":          bool(r["passed"]) and not r["error"],
        "status":          r.get("status"),
        "answer":          r["agent_result"].get("answer") if r["agent_result"] else None,
        "error":           r["error"],
        "worker_id":       r.get("worker_id"),
        "plan":            r.get("plan"),
        "plan_step_count": len(r["plan"]) if r.get("plan") else None,
        "execution":       r.get("execution"),
        "parsed_outputs":  r.get("parsed_outputs"),
        "planning_log":    r.get("planning_log"),
    }


def _flush_result(out_path: Path, entry: dict) -> None:
    out_path.parent.mkdir(exist_ok=True)
    summary = {"results": []}
    if out_path.exists():
        try:
            summary = json.loads(out_path.read_text())
        except Exception:
            pass
    results = summary.get("results", [])
    results = [r for r in results if r.get("task_id") != entry["task_id"]]
    results.append(entry)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": get_model_id(),
        "total":  len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "failed": sum(1 for r in results if not r.get("passed")),
        "results": results,
    }
    tmp_fd, tmp_path = tempfile.mkstemp(dir=out_path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(summary, f, indent=2)
        os.replace(tmp_path, out_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _make_failure_message(
    task: Dict[str, Any],
    agent_result: Optional[Dict[str, Any]],
    html_detail: Optional[Dict[str, Any]],
) -> str:
    ev = task.get("eval", {})
    lines = [
        f"Task {task['task_id']} FAILED",
        f"  intent     : {task['intent']}",
        f"  eval_types : {ev.get('eval_types', [])}",
        f"  read_only  : {task.get('read_only')}",
    ]
    ra = ev.get("reference_answers") or {}
    if ra:
        if ra.get("must_include"):
            lines.append(f"  must_include : {ra['must_include']}")
        if ra.get("must_exclude"):
            lines.append(f"  must_exclude : {ra['must_exclude']}")
        if ra.get("exact_match") is not None:
            lines.append(f"  exact_match  : {ra['exact_match']!r}")
        if ra.get("fuzzy_match") is not None:
            lines.append(f"  fuzzy_match  : {ra['fuzzy_match']!r}")
    if ev.get("string_note"):
        lines.append(f"  string_note  : {ev['string_note']}")
    if agent_result:
        lines.append(f"  agent_answer : {str(agent_result.get('answer', ''))[:300]!r}")
    if html_detail:
        top_error = html_detail.get("error")
        checks = html_detail.get("checks", [])
        if top_error and not checks:
            lines.append(f"  html_error   : {top_error}")
        for i, chk in enumerate(checks, 1):
            status = "PASS" if chk["passed"] else "FAIL"
            lines.append(f"  check {i} [{status}]: {chk.get('raw_url', '')}")
            if not chk["passed"]:
                if chk.get("missing"):
                    lines.append(f"    missing         : {chk['missing']}")
                if chk.get("excluded_found"):
                    lines.append(f"    excluded_found  : {chk['excluded_found']}")
                if chk.get("error"):
                    lines.append(f"    error           : {chk['error']}")
                if chk.get("extracted_content") is not None:
                    lines.append(f"    content_snippet : {str(chk['extracted_content'])[:300]!r}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_agent_accomplishes_reddit_tasks(
    session_event_loop,
    acquire_lock,
    result_log,
    request,
) -> None:
    tasks = _load_tasks(request.config)
    force_reset = request.config.getoption("--force-reset", default=False)
    base_url = request.config.getoption("--base-url") or _SERVER_URLS["reddit"]

    output_name = request.config.getoption("--output", default=None)
    if not output_name:
        output_name = "reddit_program_html_results.json"
    out_path = LOGS_DIR / output_name
    detailed_out_path = LOGS_DIR / (Path(output_name).stem + "_detailed.jsonl")

    multi_docker = request.config.getoption("--multi-docker", default=False)

    mcp_url = _SERVER_URLS["reddit_extra"]

    if multi_docker:
        n_workers = _workers_new.num_workers()
    else:
        n_workers = 1

    print(f"\nRunning {len(tasks)} tasks with {n_workers} workers")
    print(f"Results written incrementally to {out_path} — safe to Ctrl+C and resume with --resume")

    async def run_all() -> list:
        sem = asyncio.Semaphore(n_workers)
        write_lock = asyncio.Lock()
        remaining = len(tasks)
        remaining_lock = asyncio.Lock()

        async def run_one(task: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal remaining
            async with sem:
                try:
                    if multi_docker:
                        worker_ctx = _workers_new.worker_session(
                            str(task["task_id"]),
                            server="reddit",
                            acquire_lock=acquire_lock,
                            read_only=task.get("read_only", True),
                        )
                    else:
                        worker_ctx = _local_session(base_url)

                    async with worker_ctx as w:
                        runner = AgentRunner(headless=True, enable_reset=False, force_reset=False)
                        runner.server = "reddit"
                        # Agent acts on this worker's Postmill instance (matches the
                        # string_match suite); reddit_base_url routes program_html
                        # grading + login to the SAME worker. w["reddit_url"] is the
                        # canonical :9999 in single-docker.
                        runner.base_url = w["reddit_url"]
                        runner.reddit_base_url = w["reddit_url"]

                        await runner._init_agent()

                        if runner._agent.execution_agent is not None:
                            if multi_docker:
                                task_phpsessid = await asyncio.to_thread(
                                    _refresh_reddit_session, base_url=w["reddit_url"]
                                )
                            else:
                                task_phpsessid = await asyncio.to_thread(_mcp_phpsessid, mcp_url)
                            runner._agent.execution_agent.auth = StaticAuth({
                                "Cookie": f"PHPSESSID={task_phpsessid}",
                                "X-Experimental-API": "1",
                            })
                            runner._agent.execution_agent.task_id = str(task["task_id"])

                        # --force-reset: trigger WebArena Docker reset before each write task.
                        run_task = task
                        if force_reset and not task.get("read_only", False):
                            run_task = {**task, "require_reset": True}

                        start_time = datetime.now(timezone.utc)
                        passed, agent_result, error, html_detail = await runner.run_agent_on_task(run_task)
                        end_time = datetime.now(timezone.utc)

                        # Restore Postmill from the clean commit image after eval
                        # completes. Skip for read-only tasks (they don't mutate
                        # state, so there's nothing to clean up) — this is the
                        # program_html suite, whose tasks are all writes, so in
                        # practice every task resets; the guard is defensive.
                        if not multi_docker and not task.get("read_only", False):
                            print(f"  [reset] Restoring clean image...", flush=True)
                            try:
                                await asyncio.to_thread(_restore_checkpoint, mcp_url)
                                print(f"  [reset] Postmill restored.", flush=True)
                            except Exception as _reset_err:
                                print(f"  [reset] WARNING: restore failed: {_reset_err}", flush=True)

                        async with remaining_lock:
                            remaining -= 1
                            outcome = "PASS" if passed and not error else "FAIL"
                            print(f"\n[{remaining} tasks remaining] Task {task['task_id']} done ({outcome})")

                        details = extract_agent_details(runner)
                        status = task_status(passed, error, details["plan_steps"])

                        result = {
                            "task": task,
                            "passed": passed,
                            "agent_result": agent_result,
                            "error": error,
                            "html_detail": html_detail,
                            "plan": details["plan_steps"],
                            "parsed_outputs": details["parsed_outputs"],
                            "execution": details["raw_execution"],
                            "planning_log": details["planning_log"],
                            "worker_id": w["worker_id"],
                            "status": status,
                            "costs": details.get("costs"),
                            "tokens": details.get("tokens"),
                            "start_time": start_time,
                            "end_time": end_time,
                        }

                except Exception as e:
                    _now = datetime.now(timezone.utc)
                    result = {
                        "task": task,
                        "passed": False,
                        "agent_result": None,
                        "error": str(e),
                        "html_detail": None,
                        "plan": None,
                        "parsed_outputs": None,
                        "execution": None,
                        "planning_log": None,
                        "worker_id": None,
                        "status": "failed",
                        "costs": [],
                        "tokens": None,
                        "start_time": _now,
                        "end_time": _now,
                    }

                entry = _build_log_entry(result)
                det_entry = build_detailed_entry(
                    task=result["task"],
                    agent_result=result["agent_result"],
                    error=result["error"],
                    correct=bool(result["passed"]) and not result["error"],
                    start_time=result["start_time"],
                    end_time=result["end_time"],
                    eval_output_dir=str(out_path),
                    costs=result.get("costs"),
                    tokens=result.get("tokens"),
                )
                async with write_lock:
                    await asyncio.get_event_loop().run_in_executor(
                        None, _flush_result, out_path, entry
                    )
                    await asyncio.get_event_loop().run_in_executor(
                        None, flush_detailed_jsonl, detailed_out_path, det_entry
                    )

                return result

        futures = [asyncio.ensure_future(run_one(t)) for t in tasks]
        try:
            return list(await asyncio.gather(*futures))
        except BaseException:
            print("\nInterrupted — cancelling remaining tasks...")
            for f in futures:
                f.cancel()
            all_results = await asyncio.gather(*futures, return_exceptions=True)
            return [r for r in all_results if isinstance(r, dict)]

    try:
        results = session_event_loop.run_until_complete(run_all())
        interrupted = False
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt — completed tasks already saved to disk.")
        interrupted = True
        results = []

    failures = []
    for r in results:
        task = r["task"]
        passed = r["passed"]
        error = r["error"]

        result_log.append(_build_log_entry(r))

        if error:
            failures.append(f"Task {task['task_id']}: agent error: {error}")
        elif not passed:
            failures.append(_make_failure_message(task, r["agent_result"], r["html_detail"]))

    if interrupted:
        print(f"\nResults saved to {out_path} — re-run with --resume to continue.")
        pytest.exit("Interrupted by user", returncode=1)

    total = len(results)
    passed_count = sum(1 for r in results if r["passed"] and not r["error"])
    failed_count = total - passed_count
    print(f"\nResults: {passed_count}/{total} passed, {failed_count}/{total} failed")

    if failures:
        pytest.fail("\n\n".join(failures))
