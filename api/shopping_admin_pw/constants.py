"""Shared constants for the shopping_admin_pw package.

The Luma admin base URL is resolved at call time rather than baked in at import,
mirroring ``api/shopping_pw/constants.py``. Under ``--multi-docker`` every eval
worker owns a separate Magento backend, so a value frozen when the module first
loads sends browser helpers to the wrong server — and a report read from the
wrong instance grades stale data instead of what the task just wrote.

Resolution order, highest priority first:

  1. an explicit ``base`` argument passed to one of the ``*_url()`` helpers
     (how the eval harness pins a task to its worker),
  2. the ``SHOPPING_ADMIN_DOMAIN`` environment variable,
  3. ``config/servers.py`` ``SERVER_URLS["shopping_admin"]``, the
     single-instance default.

The legacy uppercase names (``ADMIN_BASE_URL``, ``ADMIN_LOGIN_URL``) still work
and go through the same chain, but they resolve at the moment of *access*: a
module doing ``from .constants import ADMIN_LOGIN_URL`` snapshots the value at
import time. Anything that has to follow a per-task URL must call the
``*_url()`` helper.
"""

from __future__ import annotations

import os
from typing import Optional

from config.servers import SERVER_URLS as _SERVER_URLS

#: Single-instance fallback, used when nothing else specifies an admin backend.
DEFAULT_ADMIN_BASE_URL = _SERVER_URLS["shopping_admin"]

#: Path suffix for each legacy ``*_URL`` constant, keyed by that name.
_PATHS = {
    "ADMIN_LOGIN_URL": "/admin",
    "ADMIN_PRODUCTS_URL": "/admin/catalog/product/",
    "ADMIN_SEARCH_TERMS_URL": "/admin/search/term/index/?limit=200",
    "ADMIN_BESTSELLERS_URL": "/admin/reports/report_sales/bestsellers/",
    "ADMIN_ORDERS_REPORT_URL": "/admin/reports/report_sales/sales/",
    "ADMIN_CUSTOMER_TOTALS_URL": "/admin/reports/report_customer/totals/",
}


def admin_base_url(base: Optional[str] = None) -> str:
    """Return the Luma admin base URL, without a trailing slash.

    `base` wins when given, then ``$SHOPPING_ADMIN_DOMAIN``, then the
    configured single-instance default.
    """
    if base:
        return base.rstrip("/")
    env_base = os.environ.get("SHOPPING_ADMIN_DOMAIN", "").strip()
    if env_base:
        return env_base.rstrip("/")
    return DEFAULT_ADMIN_BASE_URL.rstrip("/")


def admin_login_url(base: Optional[str] = None) -> str:
    return admin_base_url(base) + _PATHS["ADMIN_LOGIN_URL"]


def admin_products_url(base: Optional[str] = None) -> str:
    return admin_base_url(base) + _PATHS["ADMIN_PRODUCTS_URL"]


def admin_search_terms_url(base: Optional[str] = None) -> str:
    return admin_base_url(base) + _PATHS["ADMIN_SEARCH_TERMS_URL"]


def admin_bestsellers_url(base: Optional[str] = None) -> str:
    return admin_base_url(base) + _PATHS["ADMIN_BESTSELLERS_URL"]


def admin_orders_report_url(base: Optional[str] = None) -> str:
    return admin_base_url(base) + _PATHS["ADMIN_ORDERS_REPORT_URL"]


def admin_customer_totals_url(base: Optional[str] = None) -> str:
    return admin_base_url(base) + _PATHS["ADMIN_CUSTOMER_TOTALS_URL"]


def __getattr__(name: str) -> str:
    """Resolve the legacy uppercase constants on access (PEP 562)."""
    if name == "ADMIN_BASE_URL":
        return admin_base_url()
    if name in _PATHS:
        return admin_base_url() + _PATHS[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(list(globals()) + ["ADMIN_BASE_URL"] + list(_PATHS)))
