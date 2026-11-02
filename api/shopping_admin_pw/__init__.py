"""Magento Luma Admin helpers split into focused modules."""

from . import constants
from .constants import (
    admin_base_url,
    admin_bestsellers_url,
    admin_customer_totals_url,
    admin_login_url,
    admin_orders_report_url,
    admin_products_url,
    admin_search_terms_url,
)
from .config import (
    DEFAULT_ADMIN_USERNAME,
    DEFAULT_ADMIN_PASSWORD,
    get_default_admin_credentials,
)
from .login import LoginResult, login_admin
from .products import fuzzy_search_products
from .reports import get_bestsellers_report, get_customer_totals_report, get_orders_report
from .search_terms import get_search_terms


def __getattr__(name: str) -> str:
    """Delegate the legacy uppercase URL constants to `constants`.

    Re-exporting them eagerly would snapshot the admin URL at package import,
    which defeats the whole point of resolving it per call.
    """
    if name == "ADMIN_BASE_URL" or name in constants._PATHS:
        return getattr(constants, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ADMIN_BASE_URL",
    "ADMIN_LOGIN_URL",
    "admin_base_url",
    "admin_bestsellers_url",
    "admin_customer_totals_url",
    "admin_login_url",
    "admin_orders_report_url",
    "admin_products_url",
    "admin_search_terms_url",
    "DEFAULT_ADMIN_USERNAME",
    "DEFAULT_ADMIN_PASSWORD",
    "get_default_admin_credentials",
    "LoginResult",
    "login_admin",
    "fuzzy_search_products",
    "get_bestsellers_report",
    "get_customer_totals_report",
    "get_orders_report",
    "get_search_terms",
]
