import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from typing import Optional

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from playwright.sync_api import sync_playwright

from api.gitlab_pw import get_default_gitlab_credentials, login_user, get_rss_token, reset_rss_token
from api.gitlab_pw.constants import base_url as resolve_base_url

app = FastAPI(
    title="GitLab Extra API",
    description="Custom endpoints for GitLab that aren't covered by the REST API (e.g. RSS/feed token).",
    version="1.0.0",
)


def _make_browser_page(playwright, gitlab_base):
    """Create a browser page authenticated as the default GitLab user.

    `gitlab_base` pins which GitLab instance to log into. This process serves
    every eval worker, so without it a multi-docker run authenticates against
    the instance this server happened to start with — and reads a token from
    an instance the task is not graded on.
    """
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    username, password = get_default_gitlab_credentials()
    try:
        login_result = login_user(page, username, password, base_url=gitlab_base)
    except Exception as exc:
        # A caller-supplied base_url that is unreachable or not a GitLab surfaces
        # here as a Playwright error. Close the browser and convert it, so the
        # endpoint answers with the documented success=false body instead of a
        # 500 whose payload no generated script can parse.
        browser.close()
        raise RuntimeError(f"Could not reach GitLab at {gitlab_base}: {exc}") from exc
    if not login_result.success:
        browser.close()
        raise RuntimeError(f"Login failed: {login_result.error_message or 'Unknown error'}")
    return browser, page


class RssTokenResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    error_message: Optional[str] = None


@app.get("/rss_token", response_model=RssTokenResponse)
def rss_token(base_url: Optional[str] = None) -> RssTokenResponse:
    """Read the current user's RSS/feed token from their profile page.

    `base_url` pins which GitLab instance to read, exactly as it does for
    /reset_rss_token. This process serves every eval worker, so without it a
    multi-docker run reports the token of the shared default instance rather
    than the one the task is graded on.
    """
    gitlab_base = resolve_base_url(base_url)
    with sync_playwright() as p:
        try:
            browser, page = _make_browser_page(p, gitlab_base)
        except RuntimeError as exc:
            return RssTokenResponse(success=False, error_message=str(exc))
        try:
            result = get_rss_token(page, base_url=gitlab_base)
            return RssTokenResponse(success=result.success, token=result.token, error_message=result.error_message)
        finally:
            browser.close()


@app.post("/reset_rss_token", response_model=RssTokenResponse)
def reset_rss_token_endpoint(base_url: Optional[str] = None) -> RssTokenResponse:
    """Reset (regenerate) the current user's RSS/feed token and return the new value.

    `base_url` pins which GitLab instance to act on. Omitting it under
    multi-docker rotates the token on the shared default instance, leaving the
    graded instance untouched.
    """
    gitlab_base = resolve_base_url(base_url)
    with sync_playwright() as p:
        try:
            browser, page = _make_browser_page(p, gitlab_base)
        except RuntimeError as exc:
            return RssTokenResponse(success=False, error_message=str(exc))
        try:
            result = reset_rss_token(page, base_url=gitlab_base)
            return RssTokenResponse(success=result.success, token=result.token, error_message=result.error_message)
        finally:
            browser.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7792)
