"""
Fetch a fresh customer token for the shopping test user (Emma Lopez).

Credentials are read from SHOPPING_USER / SHOPPING_PASS in config/.env,
defaulting to emma.lopez@gmail.com / Password.123.

Usage (standalone):
    python3 config/init_tokens/refresh_shopping_customer_token.py

Importable:
    from config.init_tokens.refresh_shopping_customer_token import refresh_customer_token
    token = refresh_customer_token()
    agent.execution_agent.auth = StaticAuth({"Authorization": f"Bearer {token}"})
"""

from pathlib import Path

from dotenv import dotenv_values

from config.servers import SERVER_URLS as _SERVER_URLS

PROJECT_ROOT = Path(__file__).parent.parent.parent
ENV_FILE = PROJECT_ROOT / "config" / ".env"

SHOPPING_BASE_URL = _SERVER_URLS["shopping"]

DEFAULT_USER = "emma.lopez@gmail.com"
DEFAULT_PASS = "Password.123"


def _get_customer_token(base_url: str, username: str, password: str) -> str:
    import requests

    url = f"{base_url}/rest/V1/integration/customer/token"
    resp = requests.post(
        url,
        json={"username": username, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()
    if not isinstance(token, str) or not token:
        raise ValueError(f"Unexpected token response from {url}: {token!r}")
    return token


def refresh_customer_token(
    base_url: str = SHOPPING_BASE_URL,
    credentials_file: "str | Path" = ENV_FILE,
) -> str:
    """
    Fetch a fresh customer token for the shopping test user and return it.

    Credentials are read from SHOPPING_USER / SHOPPING_PASS in credentials_file,
    falling back to the hardcoded defaults for Emma Lopez.
    """
    creds = dotenv_values(Path(credentials_file)) if Path(credentials_file).exists() else {}

    username = creds.get("SHOPPING_USER", DEFAULT_USER).strip() or DEFAULT_USER
    password = creds.get("SHOPPING_PASS", DEFAULT_PASS).strip() or DEFAULT_PASS

    token = _get_customer_token(base_url, username, password)
    print(f"  ✓ Customer token refreshed for {username}")
    return token


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch a fresh shopping customer Bearer token")
    parser.add_argument("--base-url", default=SHOPPING_BASE_URL)
    args = parser.parse_args()

    print(f"Fetching shopping customer token from {args.base_url} ...")
    token = refresh_customer_token(base_url=args.base_url)
    print(f"  Token: {token[:8]}...{token[-4:]}")
    print("Done.")
