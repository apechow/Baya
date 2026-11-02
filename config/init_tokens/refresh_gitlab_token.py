"""
Refresh the GITLAB_TOKEN in config/.server_env.

Usage:
    python3 config/init_tokens/refresh_gitlab_token.py

Logs in as byteblaze, creates a new PAT named 'benchmark-runner',
and overwrites GITLAB_TOKEN in config/.server_env.

Run this whenever the agent gets HTTP 401 from GitLab.
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright

from config.servers import SERVER_URLS as _SERVER_URLS

SERVER_ENV = PROJECT_ROOT / "config" / ".server_env"

GITLAB_URL = _SERVER_URLS["gitlab"]
USERNAME = "byteblaze"
PASSWORD = "hello1234"
TOKEN_NAME = "benchmark-runner"


def refresh_token(base_url: str = GITLAB_URL) -> str:
    """
    Log in as byteblaze, POST to /-/profile/personal_access_tokens, and
    capture the new token from the JSON response (the 'new_token' key).
    GitLab masks the token in the DOM so we intercept the network response.

    base_url: the GitLab instance to target — defaults to the single-server
    convention (config.servers.SERVER_URLS["gitlab"]), but callers targeting
    a specific multi-docker worker should pass that worker's URL explicitly.
    """
    token_holder: dict = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Login — use the same origin as ACCESS_TOKENS_URL
        page.goto(f"{base_url}/users/sign_in", wait_until="networkidle")
        page.fill("#user_login", USERNAME)
        page.fill("#user_password", PASSWORD)
        page.locator('button[type="submit"]').click()
        page.wait_for_load_state("networkidle")
        print(f"  Logged in: {page.url}")

        # Navigate to PAT creation page
        page.goto(f"{base_url}/-/profile/personal_access_tokens", wait_until="domcontentloaded")
        page.wait_for_selector("#personal_access_token_name", timeout=15000)
        page.fill("#personal_access_token_name", TOKEN_NAME)
        page.locator("label[for='personal_access_token_scopes_api']").click()

        # Intercept the POST response to read the token before the DOM masks it
        with page.expect_response(
            lambda r: "personal_access_tokens" in r.url and r.request.method == "POST",
            timeout=15000,
        ) as resp_info:
            page.locator("input[type='submit'], button[type='submit']").last.click()

        body = resp_info.value.json()
        token_holder["token"] = body.get("new_token") or body.get("token", "")
        browser.close()

    token = token_holder.get("token", "")
    if not token or not token.startswith("glpat-"):
        raise RuntimeError(f"Unexpected token value in response: {token!r}")
    return token


# def update_server_env(token: str) -> None:
#     content = SERVER_ENV.read_text() if SERVER_ENV.exists() else ""
#     if re.search(r"^GITLAB_TOKEN=", content, re.MULTILINE):
#         content = re.sub(r"^GITLAB_TOKEN=.*$", f"GITLAB_TOKEN={token}", content, flags=re.MULTILINE)
#     else:
#         content = content.rstrip("\n") + f"\nGITLAB_TOKEN={token}\n"
#     SERVER_ENV.write_text(content)
#     print(f"  Updated {SERVER_ENV}")


if __name__ == "__main__":
    print("Refreshing GitLab token...")
    token = refresh_token()
    print(f"  New token: {token}")
    # update_server_env(token)
    # print("Done.")
