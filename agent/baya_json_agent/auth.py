"""
Auth providers for ExecutionAgent.

All tokens are read from a .env-style file (e.g. config/.server_env).
Each server gets its own AuthProvider that knows how to format its tokens
into the correct curl headers. AuthRegistry maps server names to providers
and provides a factory that wires up all known servers from a single env file.

Adding a new server:
  1. Add its token key(s) to .server_env
  2. Register it in AuthRegistry.build_default()
"""

import base64
import json
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional

from dotenv import dotenv_values


# ──────────────────────────────────────────────────────────────────────────────
# Base
# ──────────────────────────────────────────────────────────────────────────────

class AuthProvider(ABC):
    """Supplies HTTP headers needed for authentication."""

    @abstractmethod
    def get_headers(self, url: str = "") -> Dict[str, str]:
        """Return {header_name: header_value} to inject into every request.

        The optional ``url`` argument allows URL-aware providers (e.g.
        RoutingAuth) to select the correct token for each request.
        Implementations that don't need it can safely ignore it.
        """


# ──────────────────────────────────────────────────────────────────────────────
# Concrete providers
# ──────────────────────────────────────────────────────────────────────────────

class HeaderAuth(AuthProvider):
    """
    Single token from .server_env → one request header.

    Examples:
        # GitLab PAT
        HeaderAuth(env, "GRAPHQL_TOKEN", "PRIVATE-TOKEN")

        # Shopping Bearer token
        HeaderAuth(env, "CUSTOMER_AUTH_TOKEN", "Authorization", prefix="Bearer ")
    """

    def __init__(
        self,
        env: Dict[str, str],
        env_key: str,
        header: str,
        prefix: str = "",
    ) -> None:
        token = env.get(env_key, "").strip()
        if not token:
            raise ValueError(f"Token key {env_key!r} not found or empty in env file")
        self._headers = {header: f"{prefix}{token}"}

    def get_headers(self, url: str = "") -> Dict[str, str]:
        return dict(self._headers)


class CookieAuth(AuthProvider):
    """
    Multiple tokens from .server_env → one "Cookie: k=v; k=v" header.

    cookie_map is an ordered dict of {cookie_name: env_key}, e.g.:
        {"token": "REDDIT_TOKEN", "phpbb3_session": "REDDIT_PHPBB_SESSION"}
    → Cookie: token=xxx; phpbb3_session=yyy

    Missing keys are skipped with a warning rather than raising, so partial
    configs still work during development.
    """

    def __init__(
        self,
        env: Dict[str, str],
        cookie_map: Dict[str, str],
    ) -> None:
        parts = []
        for cookie_name, env_key in cookie_map.items():
            value = env.get(env_key, "").strip()
            if value:
                parts.append(f"{cookie_name}={value}")
            else:
                print(f"[CookieAuth] Warning: {env_key!r} not found in env file — skipping")
        if not parts:
            raise ValueError("CookieAuth: no cookie values resolved from env file")
        self._headers = {"Cookie": "; ".join(parts)}

    def get_headers(self, url: str = "") -> Dict[str, str]:
        return dict(self._headers)


class MultiAuth(AuthProvider):
    """
    Combines multiple AuthProviders into one.
    Use when a server needs both cookies AND a separate auth header.

    If two providers set the same header name, the last one wins.
    """

    def __init__(self, *providers: AuthProvider) -> None:
        self._providers = providers

    def get_headers(self, url: str = "") -> Dict[str, str]:
        merged: Dict[str, str] = {}
        for provider in self._providers:
            merged.update(provider.get_headers(url=url))
        return merged


class StaticAuth(AuthProvider):
    """Pass headers directly — useful for tests or one-off scripts."""

    def __init__(self, headers: Dict[str, str]) -> None:
        self._headers = headers

    def get_headers(self, url: str = "") -> Dict[str, str]:
        return dict(self._headers)


class RoutingAuth(AuthProvider):
    """
    URL-aware auth that selects a token based on the request URL.

    A default provider handles all requests; override providers are checked
    first — the first pattern whose substring appears in the URL wins.

    Example (shopping eval — admin token for catalog/orders, customer token
    for cart/wishlist/customer-me endpoints):

        RoutingAuth(
            default=StaticAuth({"Authorization": f"Bearer {admin_token}"}),
            overrides=[
                ("/carts/mine",    StaticAuth({"Authorization": f"Bearer {customer_token}"})),
                ("/customers/me",  StaticAuth({"Authorization": f"Bearer {customer_token}"})),
                (":7790/",         StaticAuth({"Authorization": f"Bearer {customer_token}"})),
            ],
        )
    """

    def __init__(
        self,
        default: AuthProvider,
        overrides: "list[tuple[str, AuthProvider]]",
    ) -> None:
        self._default = default
        self._overrides = overrides  # [(url_substring, provider), ...]

    def get_headers(self, url: str = "") -> Dict[str, str]:
        for pattern, provider in self._overrides:
            if pattern in url:
                return provider.get_headers(url=url)
        return self._default.get_headers(url=url)


class RefreshableAuth(AuthProvider):
    """
    Auth provider that automatically refreshes the token before it expires.

    Decodes the JWT ``exp`` claim to determine expiry. On every call to
    ``get_headers()``, if the token is within ``buffer_seconds`` of expiry
    a fresh token is fetched via ``refresh_fn`` before returning headers.
    Thread-safe via an internal lock.

    Example::

        auth = RefreshableAuth(
            initial_token=admin_token,
            refresh_fn=lambda: refresh_tokens(base_url="http://127.0.0.1:7770"),
        )
    """

    def __init__(
        self,
        initial_token: str,
        refresh_fn: Callable[[], str],
        header: str = "Authorization",
        prefix: str = "Bearer ",
        buffer_seconds: int = 300,
    ) -> None:
        self._token = initial_token
        self._refresh_fn = refresh_fn
        self._header = header
        self._prefix = prefix
        self._buffer = buffer_seconds
        self._lock = threading.Lock()

    @staticmethod
    def _jwt_exp(token: str) -> Optional[float]:
        """Extract the ``exp`` claim from a JWT without verifying the signature."""
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None
            payload = parts[1] + "=="  # restore base64 padding
            decoded = base64.urlsafe_b64decode(payload)
            claims = json.loads(decoded)
            exp = claims.get("exp")
            return float(exp) if exp else None
        except Exception:
            return None

    def _refresh_if_needed(self) -> None:
        exp = self._jwt_exp(self._token)
        if exp is None:
            return  # non-JWT token — can't determine expiry, leave as-is
        remaining = exp - time.time()
        if remaining < self._buffer:
            print(f"  ↻ Token expires in {remaining:.0f}s — refreshing...")
            self._token = self._refresh_fn()
            print("  ✓ Token refreshed")

    def get_headers(self, url: str = "") -> Dict[str, str]:
        with self._lock:
            self._refresh_if_needed()
            return {self._header: f"{self._prefix}{self._token}"}


# ──────────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────────

class AuthRegistry:
    """
    Maps server names to their AuthProvider.

    Usage:
        registry = AuthRegistry.build_default("config/.server_env")
        auth = registry.get("gitlab")
        agent = ExecutionAgent(base_url="...", auth=auth)
    """

    def __init__(self) -> None:
        self._registry: Dict[str, AuthProvider] = {}

    def register(self, server: str, provider: AuthProvider) -> None:
        self._registry[server] = provider

    def get(self, server: str) -> AuthProvider:
        if server not in self._registry:
            raise KeyError(
                f"No auth registered for server {server!r}. "
                f"Available: {list(self._registry.keys())}"
            )
        return self._registry[server]

    def __contains__(self, server: str) -> bool:
        return server in self._registry

    @classmethod
    def build_default(cls, env_file: str) -> "AuthRegistry":
        """
        Factory: reads env_file once and registers all known servers.

        To add a new server, add its token key(s) to .server_env and
        add a register() call below.
        """
        env = dotenv_values(env_file)
        registry = cls()

        # GitLab — PRIVATE-TOKEN header
        if env.get("GITLAB_TOKEN"):
            registry.register(
                "gitlab",
                HeaderAuth(env, "GITLAB_TOKEN", "PRIVATE-TOKEN"),
            )

        # Shopping — token expires ~1h so it is always fetched fresh at runtime and
        # injected via StaticAuth. Register a no-op placeholder so agent.initialize()
        # accepts these server names without requiring stale tokens in .server_env.
        registry.register("shopping", StaticAuth({}))
        registry.register("shopping_admin", StaticAuth({}))

        # Shopping Extra — no auth required (public FastAPI endpoints)
        registry.register("shopping_extra", StaticAuth({}))

        # Shopping Admin Extra — no auth required (public FastAPI endpoints)
        registry.register("shopping_admin_extra", StaticAuth({}))

        # Reddit Extra — no auth required (handles auth internally via Playwright)
        registry.register("reddit_extra", StaticAuth({}))

        # GitLab Extra — no auth required (handles login internally via Playwright)
        registry.register("gitlab_extra", StaticAuth({}))

        # Reddit — session fetched fresh at runtime by run_tasks_batch_new and injected
        # via StaticAuth per task. Register a placeholder unconditionally so
        # agent.initialize() passes without needing REDDIT_PHPSESSID in .server_env.
        registry.register("reddit", StaticAuth({}))

        return registry
