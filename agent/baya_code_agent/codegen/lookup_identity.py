#!/usr/bin/env python3
"""Resolve the account-identity prompt section for a given site.

The codegen prompt (agent/baya_code_agent/codegen/run_claude_task.sh) has to tell the generated
implementation script *who* the authenticated user is, so first-person tasks
("my orders", "issues assigned to me") resolve to the right account. That
identity is NOT in the task intent, and it is deliberately unreadable from
inside the codegen sandbox (config/.env is on the deny list), so it must be
injected into the prompt from here instead of discovered by the session --
otherwise the model fills the gap with whatever ambient identity it can see
(the operator's own email/git config), which is the bug this fixes.

The username for each site comes from the same single source the rest of the
eval uses: config.servers.SERVERS[site]["username_env"] names the env var, and
config/.env (with config/.env.example as a fallback) holds its value. Sites
without a username_env (shopping_admin, wikipedia, ...) have no first-person
customer identity and print nothing, so the caller injects no section.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Per-site guidance on how to *use* the identity. {username} is filled from
# config. A site absent from this map gets no identity section even if it has a
# username_env, so adding a new site here is a deliberate opt-in.
SITE_TEMPLATES = {
    "gitlab": (
        "ACCOUNT IDENTITY (required)\n"
        "\n"
        "The authenticated GitLab user for this task is \"{username}\". Any "
        "first-person or implicitly-self-scoped phrasing in the task -- \"my "
        "projects\", \"issues assigned to me\", \"repositories I own\", \"my "
        "to-do list\", \"merge requests I opened\" -- refers to this user. The "
        "auth token (see AUTHENTICATION) already authenticates AS \"{username}\", "
        "so resolve \"me\"/\"my\" through the current-user endpoints and "
        "assigned/authored filters (e.g. the /user endpoint, or scope=assigned_to_me "
        "/ author_username={username}) rather than hardcoding a numeric id. Never "
        "substitute a different username, and never infer the identity from the "
        "environment, git config, or whoever is running this session."
    ),
    "shopping": (
        "ACCOUNT IDENTITY (required)\n"
        "\n"
        "The logged-in storefront customer for this task is \"{username}\". Any "
        "first-person phrasing -- \"my orders\", \"my cart\", \"my account\", "
        "\"my wishlist\", \"my last order\", \"reorder\" -- refers to this "
        "customer. You are holding an ADMIN token (not a customer token), so "
        "there is NO implicit \"current customer\" the token resolves to: look "
        "this customer up explicitly by their email \"{username}\" via "
        "GET /V1/customers/search (searchCriteria filter field=email, "
        "conditionType=eq), take the numeric customer id from the result, and "
        "drive every customer-scoped operation through the customer-keyed admin "
        "endpoints with that id. Never substitute a different identity, and never "
        "infer it from the environment, git config, or whoever is running this "
        "session."
    ),
    "reddit": (
        "ACCOUNT IDENTITY (required)\n"
        "\n"
        "The logged-in Postmill user for this task is \"{username}\". Any "
        "first-person phrasing -- \"my post\", \"my submission\", \"my bio\", "
        "\"my forum\", \"posts I created\", \"edit my comment\" -- refers to this "
        "user. The Reddit API server is already authenticated AS \"{username}\", "
        "so every write it performs (create_post, update_biography, vote_post, "
        "subscribe_forum, ...) is attributed to them automatically and takes no "
        "username argument. For READS, resolve \"my\" explicitly: "
        "GET /user_posts?username={username} lists this user's posts, and "
        "/u/{username} is their profile page. Never substitute a different "
        "username, and never infer the identity from the environment, git config, "
        "or whoever is running this session."
    ),
}


def _read_env_var(name: str) -> str | None:
    """Return the value of `name` from config/.env, falling back to .env.example.

    A minimal dotenv reader -- we only need one bare KEY=VALUE identity line and
    must not import anything that would load secrets. The values in question
    (byteblaze, emma.lopez@gmail.com) are public WebArena identities, not
    secrets.
    """
    for fname in (".env", ".env.example"):
        env_path = ROOT / "config" / fname
        try:
            lines = env_path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    return None


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <site>", file=sys.stderr)
        sys.exit(2)
    site = sys.argv[1]

    sys.path.insert(0, str(ROOT))
    from config.servers import SERVERS

    server = SERVERS.get(site)
    if server is None:
        print(f"Error: unknown site '{site}'", file=sys.stderr)
        sys.exit(1)

    username_env = server.get("username_env")
    template = SITE_TEMPLATES.get(site)
    # No first-person identity for this site (no username_env, or not opted into
    # SITE_TEMPLATES): print nothing so the caller injects no identity section.
    if not username_env or template is None:
        return

    username = _read_env_var(username_env)
    if not username:
        print(
            f"Error: {username_env} not set in config/.env (needed for the "
            f"{site} account identity)",
            file=sys.stderr,
        )
        sys.exit(1)

    print(template.format(username=username))


if __name__ == "__main__":
    main()
