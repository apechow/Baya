"""
Fetch a fresh PHPSESSID from the Reddit (Postmill) server.

Credentials (REDDIT_USERNAME / REDDIT_PASSWORD) are read from config/.env.
The session ID is returned to the caller; optionally write it to
config/.server_env as REDDIT_PHPSESSID for single-server dev runs.

Usage (standalone):
    python3 config/init_tokens/refresh_reddit_session.py
    python3 config/init_tokens/refresh_reddit_session.py --write   # also saves to .server_env

Importable:
    from config.init_tokens.refresh_reddit_session import refresh_session
    phpsessid = refresh_session()
    agent.execution_agent.auth = StaticAuth({
        "Cookie": f"PHPSESSID={phpsessid}",
        "X-Experimental-API": "1",
    })

Run this whenever the agent gets HTTP 401/403 from Reddit.
"""

import re
from pathlib import Path

from dotenv import dotenv_values

from config.servers import SERVER_URLS as _SERVER_URLS

PROJECT_ROOT = Path(__file__).parent.parent.parent
ENV_FILE = PROJECT_ROOT / "config" / ".env"
SERVER_ENV = PROJECT_ROOT / "config" / ".server_env"

REDDIT_BASE_URL = _SERVER_URLS["reddit"]


def _get_phpsessid(base_url: str, username: str, password: str) -> str:
    """
    Three-step login required by Postmill:
      1. GET /login  (follow cookie-check redirect) → obtain session cookie + CSRF token
      2. POST /login_check with _username, _password, _csrf_token + session cookie
      3. Successful login redirects to /  (not back to /login); extract new PHPSESSID
    """
    import re as _re
    import requests

    session = requests.Session()

    # Step 1: GET login page (follows _cookie_check redirect automatically)
    login_page = session.get(f"{base_url}/login", timeout=30)
    csrf_match = _re.search(r'name="_csrf_token"\s+value="([^"]+)"', login_page.text)
    if not csrf_match:
        raise ValueError(f"CSRF token not found on login page (status {login_page.status_code})")
    csrf_token = csrf_match.group(1)

    # Step 2: POST credentials + CSRF token
    resp = session.post(
        f"{base_url}/login_check",
        data={"_username": username, "_password": password, "_csrf_token": csrf_token},
        allow_redirects=False,
        timeout=30,
    )

    # Step 3: successful login → Location: / (not /login)
    location = resp.headers.get("Location", "")
    if "/login" in location:
        raise ValueError(
            f"Login failed — server redirected back to login page. "
            f"Check REDDIT_USERNAME / REDDIT_PASSWORD in config/.env"
        )

    phpsessid = session.cookies.get("PHPSESSID")
    if not phpsessid:
        raise ValueError(f"No PHPSESSID after login (status {resp.status_code})")
    return phpsessid


def _existing_session(env_file: Path) -> str:
    if not env_file.exists():
        return ""
    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("REDDIT_PHPSESSID="):
            return stripped.split("=", 1)[1].strip()
    return ""


def refresh_session(
    base_url: str = REDDIT_BASE_URL,
    credentials_file: "str | Path" = ENV_FILE,
    server_env_file: "str | Path" = SERVER_ENV,
) -> str:
    """
    Fetch a fresh PHPSESSID from the Reddit server and return it.

    Reads REDDIT_USERNAME / REDDIT_PASSWORD from credentials_file.
    On network failure, falls back to REDDIT_PHPSESSID in server_env_file.
    """
    creds = dotenv_values(Path(credentials_file))
    username = creds.get("REDDIT_USERNAME", "").strip()
    password = creds.get("REDDIT_PASSWORD", "").strip()

    if not username or not password:
        raise ValueError(
            f"REDDIT_USERNAME and REDDIT_PASSWORD must be set in {credentials_file}"
        )

    try:
        phpsessid = _get_phpsessid(base_url, username, password)
        print(f"  Reddit session refreshed (PHPSESSID: {phpsessid[:8]}...)")
        return phpsessid
    except Exception as exc:
        existing = _existing_session(Path(server_env_file))
        if existing:
            print(f"  Could not refresh Reddit session ({exc}); using existing from {server_env_file}")
            return existing
        raise RuntimeError(
            f"Failed to get Reddit session and none found in {server_env_file}: {exc}"
        ) from exc


def update_server_env(phpsessid: str, server_env_file: "str | Path" = SERVER_ENV) -> None:
    """Write REDDIT_PHPSESSID to .server_env, creating or updating the key."""
    path = Path(server_env_file)
    content = path.read_text() if path.exists() else ""
    if re.search(r"^REDDIT_PHPSESSID=", content, re.MULTILINE):
        content = re.sub(
            r"^REDDIT_PHPSESSID=.*$", f"REDDIT_PHPSESSID={phpsessid}",
            content, flags=re.MULTILINE,
        )
    else:
        content = content.rstrip("\n") + f"\nREDDIT_PHPSESSID={phpsessid}\n"
    path.write_text(content)
    print(f"  Updated {path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch a fresh Reddit PHPSESSID session cookie")
    parser.add_argument("--base-url", default=REDDIT_BASE_URL,
                        help=f"Reddit base URL (default: {REDDIT_BASE_URL})")
    parser.add_argument("--credentials-file", default=str(ENV_FILE),
                        help="Path to credentials file (default: config/.env)")
    parser.add_argument("--write", action="store_true",
                        help="Also write REDDIT_PHPSESSID to config/.server_env")
    args = parser.parse_args()

    print(f"Fetching Reddit session from {args.base_url} ...")
    phpsessid = refresh_session(base_url=args.base_url, credentials_file=args.credentials_file)
    print(f"  PHPSESSID: {phpsessid}")
    if args.write:
        update_server_env(phpsessid)
    print("Done.")
