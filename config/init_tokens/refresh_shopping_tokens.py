"""
Fetch a fresh ADMIN_AUTH_TOKEN from the shopping server.

Credentials (SHOPPING_ADMIN_USER / SHOPPING_ADMIN_PASS) are read from config/.env.
The token is returned to the caller and never written to disk — callers inject it
directly into the agent via StaticAuth.

Usage (standalone):
    python3 scripts/refresh_shopping_tokens.py

Importable:
    from scripts.refresh_shopping_tokens import refresh_tokens
    token = refresh_tokens()
    agent.execution_agent.auth = StaticAuth({"Authorization": f"Bearer {token}"})

Run this whenever you need to verify the token works, or use it programmatically
at eval startup (see TaskBatchRunner.initialize and the conftest agent_runner fixture).
"""

import re
from pathlib import Path

from dotenv import dotenv_values

from config.servers import SERVER_URLS as _SERVER_URLS

PROJECT_ROOT = Path(__file__).parent.parent.parent
ENV_FILE = PROJECT_ROOT / "config" / ".env"
SERVER_ENV = PROJECT_ROOT / "config" / ".server_env"

SHOPPING_BASE_URL = _SERVER_URLS["shopping"]


def _get_admin_token(base_url: str, username: str, password: str) -> str:
    import requests

    url = f"{base_url}/rest/V1/integration/admin/token"
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


def _existing_token(env_file: Path, key: str) -> str:
    """Read a token value from an env file without modifying it."""
    if not env_file.exists():
        return ""
    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            return stripped.split("=", 1)[1].strip()
    return ""


def write_admin_token_to_env(token: str, server_env_file: "str | Path" = SERVER_ENV) -> None:
    """
    Upsert ADMIN_AUTH_TOKEN=<token> into config/.server_env.

    Callers use this after refresh_tokens() so that the program_html evaluator,
    which reads its admin token from .server_env, sees the fresh value instead
    of the expired one cached on disk.
    """
    path = Path(server_env_file)
    key = "ADMIN_AUTH_TOKEN"
    new_line = f"{key}={token}"

    if path.exists():
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = new_line
                break
        else:
            lines.append(new_line)
        path.write_text("\n".join(lines) + "\n")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_line + "\n")


def refresh_tokens(
    base_url: str = SHOPPING_BASE_URL,
    credentials_file: "str | Path" = ENV_FILE,
    server_env_file: "str | Path" = SERVER_ENV,
) -> str:
    """
    Fetch a fresh admin token from the shopping server and return it.

    Admin credentials are read from SHOPPING_ADMIN_USER / SHOPPING_ADMIN_PASS
    in credentials_file (config/.env). Nothing is written to disk.

    On network failure:
    - If ADMIN_AUTH_TOKEN exists in server_env_file, warns and returns that as fallback.
    - If no token exists anywhere, re-raises the exception.
    """
    creds = dotenv_values(Path(credentials_file))

    username = creds.get("SHOPPING_ADMIN_USER", "").strip()
    password = creds.get("SHOPPING_ADMIN_PASS", "").strip()

    if not username or not password:
        raise ValueError(
            f"SHOPPING_ADMIN_USER and SHOPPING_ADMIN_PASS must be set in {credentials_file}"
        )

    try:
        token = _get_admin_token(base_url, username, password)
        print(f"  ✓ Admin token refreshed")
        return token
    except Exception as exc:
        existing = _existing_token(Path(server_env_file), "ADMIN_AUTH_TOKEN")
        if existing:
            print(f"  ⚠ Could not refresh admin token ({exc}); using existing token from {server_env_file}")
            return existing
        raise RuntimeError(
            f"Failed to get admin token and none found in {server_env_file}: {exc}"
        ) from exc


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch a fresh shopping admin Bearer token")
    parser.add_argument("--base-url", default=SHOPPING_BASE_URL, help=f"Shopping base URL (default: {SHOPPING_BASE_URL})")
    parser.add_argument("--credentials-file", default=str(ENV_FILE), help="Path to read admin credentials from (default: config/.env)")
    args = parser.parse_args()

    print(f"Fetching shopping admin token from {args.base_url} ...")
    token = refresh_tokens(base_url=args.base_url, credentials_file=args.credentials_file)
    print(f"  Token: {token[:8]}...{token[-4:]}")
    print("Done.")
