"""Shared constants for the shopping_pw package.

The storefront base URL is resolved at call time rather than baked in at import.
Under ``--multi-docker`` every eval worker owns a separate Magento instance, so
a value frozen when the module first loads sends browser helpers to the wrong
server — and a check that reads the wrong instance grades stale data instead of
what the task just wrote.

Resolution order, highest priority first:

  1. an explicit ``base`` argument passed to one of the ``*_url()`` helpers
     (how the eval harness pins a task to its worker),
  2. the ``SHOPPING_DOMAIN`` environment variable — what the harness already
     exports for generated scripts (see ``ClaudeCodeAgentRunner._run_script``),
  3. ``config/servers.py`` ``SERVER_URLS["shopping"]``, the single-instance
     default.

The legacy uppercase names (``BASE_URL``, ``CART_URL``, ...) still work and go
through the same chain, but they resolve at the moment of *access*: a module
doing ``from .constants import CART_URL`` snapshots the value at import time.
Anything that has to follow a per-task URL must call the ``*_url()`` helper.
"""

from __future__ import annotations

import os
from typing import Optional

from config.servers import SERVER_URLS as _SERVER_URLS

#: Single-instance fallback, used when nothing else specifies a storefront.
DEFAULT_BASE_URL = _SERVER_URLS["shopping"]

#: Path suffix for each legacy ``*_URL`` constant, keyed by that name.
_PATHS = {
    "CART_URL": "/checkout/cart/",
    "CHECKOUT_URL": "/checkout/",
    "WISHLIST_URL": "/wishlist/",
    "ACCOUNT_EDIT_URL": "/customer/account/edit/",
    "ADDRESS_BOOK_URL": "/customer/address/",
    "ORDER_HISTORY_URL": "/sales/order/history/",
    "LOGIN_URL": "/customer/account/login/",
    "ADVANCED_SEARCH_URL": "/catalogsearch/advanced/",
    "SEARCH_TERMS_URL": "/search/term/popular/",
}


def base_url(base: Optional[str] = None) -> str:
    """Return the storefront base URL, without a trailing slash.

    `base` wins when given, then ``$SHOPPING_DOMAIN``, then the configured
    single-instance default.
    """
    if base:
        return base.rstrip("/")
    env_base = os.environ.get("SHOPPING_DOMAIN", "").strip()
    if env_base:
        return env_base.rstrip("/")
    return DEFAULT_BASE_URL.rstrip("/")


def cart_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["CART_URL"]


def checkout_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["CHECKOUT_URL"]


def wishlist_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["WISHLIST_URL"]


def account_edit_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["ACCOUNT_EDIT_URL"]


def address_book_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["ADDRESS_BOOK_URL"]


def order_history_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["ORDER_HISTORY_URL"]


def login_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["LOGIN_URL"]


def advanced_search_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["ADVANCED_SEARCH_URL"]


def search_terms_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["SEARCH_TERMS_URL"]


def __getattr__(name: str) -> str:
    """Resolve the legacy uppercase constants on access (PEP 562)."""
    if name == "BASE_URL":
        return base_url()
    if name in _PATHS:
        return base_url() + _PATHS[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(list(globals()) + ["BASE_URL"] + list(_PATHS)))
