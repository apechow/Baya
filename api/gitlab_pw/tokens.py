# In order to get the GLPAT token for a new gitlab instance, we need to use
# playwright to log in and create that token, then pass it into agent.

import threading

from playwright.sync_api import sync_playwright

from api.gitlab_pw import login_user, create_access_token
from api.gitlab_pw.config import get_default_gitlab_credentials
import api.gitlab_pw.settings as _glab_settings

# Serialize get_glpat calls: the function patches a module-level URL global in
# settings, so concurrent calls from different threads would overwrite each
# other's values.
_get_glpat_lock = threading.Lock()


def get_glpat(gitlab_url: str, token_name: str = "agent-token") -> str:
    """
    Launch a headless browser, log in to the given GitLab instance, and
    create a personal access token (GLPAT) with all scopes enabled.

    Args:
        gitlab_url: Base URL of the GitLab instance, e.g. "http://worker1:8023"
        token_name: Name to give the created token (default: "agent-token")

    Returns:
        The GLPAT token string (e.g. "glpat-xxxxxxxxxxxxxxxxxxxx").

    Raises:
        RuntimeError: if login or token creation fails.
    """
    with _get_glpat_lock:
        return _get_glpat_locked(gitlab_url, token_name)


def _get_glpat_locked(gitlab_url: str, token_name: str) -> str:
    # create_access_token still reads settings.ACCESS_TOKENS_URL (a module global
    # snapshotted at import time), so patch it to this worker's instance.
    # login_user takes an explicit base_url instead — see the call below.
    _glab_settings.ACCESS_TOKENS_URL = f"{gitlab_url}/-/profile/personal_access_tokens"

    username, password = get_default_gitlab_credentials()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            login_result = login_user(page, username, password, base_url=gitlab_url)
            
            if not login_result.success:
                raise RuntimeError(f"GitLab login failed: {login_result.error_message}")
            print("Log in successful")
            token_result = create_access_token(page, token_name=token_name)
            if not token_result.success:
                raise RuntimeError(f"GLPAT creation failed: {token_result.error_message}")

            return token_result.token
        finally:
            browser.close()
