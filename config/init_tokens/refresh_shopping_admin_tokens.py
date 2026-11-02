"""
Fetch a fresh admin Bearer token from the shopping_admin (Luma Admin) server.

This is the shopping_admin counterpart of refresh_shopping_tokens.py, and it
exists because the two Magento instances are NOT interchangeable: the storefront
on :7770 and the Luma admin panel on :7780 are separate installs with separate
catalog databases. A token minted by one is useless against the other, and
worse, using it reads or writes the wrong catalog.

refresh_shopping_tokens.refresh_tokens() defaults its base_url to
SERVER_URLS["shopping"] -- :7770 -- so calling it without an explicit base_url
silently authenticates against the storefront. The old eval harness avoided that
by always passing base_url itself (see eval/tests/
test_agent_verified_all_shopping_admin.py). In the Claude-Code codegen pipeline
the *generated script* resolves its own auth, so the safe default has to live in
the module rather than in every caller: hence this file, whose default is
already :7780.

The module name also matters. Codegen sessions run sandboxed with
`denyRead: [$ROOT]` and `Bash(find *)` denied, so they cannot discover a module
by listing the tree -- they can only import a name they can guess from the site
name. `refresh_<site>_tokens.py` is that convention, and this file completes it
for shopping_admin.

Credentials (SHOPPING_ADMIN_USER / SHOPPING_ADMIN_PASS) are read from
config/.env, shared with the shopping module. The token is returned to the
caller and never written to disk.

Usage (standalone):
    ./venv/bin/python -m config.init_tokens.refresh_shopping_admin_tokens

Importable:
    from config.init_tokens.refresh_shopping_admin_tokens import refresh_tokens
    token = refresh_tokens(base_url=base_url)
    headers = {"Authorization": f"Bearer {token}"}
"""

from pathlib import Path

from dotenv import dotenv_values

from config.servers import SERVER_URLS as _SERVER_URLS

PROJECT_ROOT = Path(__file__).parent.parent.parent
ENV_FILE = PROJECT_ROOT / "config" / ".env"

SHOPPING_ADMIN_BASE_URL = _SERVER_URLS["shopping_admin"]


# Deliberately self-contained: this duplicates ~15 lines of refresh_shopping_
# tokens.py rather than importing them. Importing would drag the storefront
# module -- and its :7770 base_url default -- into every generated shopping_admin
# script, one attribute lookup away from the function they are meant to call.
# Fifteen duplicated lines are cheaper than that footgun, and they keep the two
# instances as independent in the code as they are in reality.
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


def refresh_tokens(
    base_url: str = SHOPPING_ADMIN_BASE_URL,
    credentials_file: "str | Path" = ENV_FILE,
) -> str:
    """
    Fetch a fresh admin token from the shopping_admin server and return it.

    Admin credentials are read from SHOPPING_ADMIN_USER / SHOPPING_ADMIN_PASS in
    credentials_file (config/.env). Nothing is written to disk.

    `base_url` is the BARE instance root (e.g. http://localhost:7780), not the
    /admin UI path: Magento serves REST from <root>/rest/V1/..., so a base_url
    carrying an /admin suffix produces 404s. Under --multi-docker, pass the
    worker's own admin URL.

    Unlike the shopping module, this deliberately does NOT fall back to
    ADMIN_AUTH_TOKEN in config/.server_env. That key is written by the shopping
    (:7770) path, and returning a storefront token here would authenticate
    against the wrong catalog database -- failing later, further away, and much
    more confusingly than an exception raised right here. A failure to mint is
    reported, not papered over.
    """
    creds = dotenv_values(Path(credentials_file))

    username = creds.get("SHOPPING_ADMIN_USER", "").strip()
    password = creds.get("SHOPPING_ADMIN_PASS", "").strip()

    if not username or not password:
        raise ValueError(
            f"SHOPPING_ADMIN_USER and SHOPPING_ADMIN_PASS must be set in {credentials_file}"
        )

    token = _get_admin_token(base_url, username, password)
    print("  ✓ Shopping admin token refreshed")
    return token


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch a fresh shopping_admin (Luma Admin) Bearer token"
    )
    parser.add_argument(
        "--base-url",
        default=SHOPPING_ADMIN_BASE_URL,
        help=f"Shopping admin base URL (default: {SHOPPING_ADMIN_BASE_URL})",
    )
    parser.add_argument(
        "--credentials-file",
        default=str(ENV_FILE),
        help=f"Path to read admin credentials from (default: {ENV_FILE})",
    )
    args = parser.parse_args()

    print(f"Fetching shopping admin token from {args.base_url} ...")
    token = refresh_tokens(
        base_url=args.base_url, credentials_file=args.credentials_file
    )
    print(f"  Token: {token[:8]}...{token[-4:]}")
    print("Done.")
