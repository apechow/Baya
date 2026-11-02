"""Configuration helpers for reusable storefront credentials."""

from __future__ import annotations

import os
from typing import Tuple

# Override these via environment variables for your own account.
DEFAULT_CUSTOMER_EMAIL = "emma.lopez@gmail.com"
DEFAULT_CUSTOMER_PASSWORD = "Password.123"
DEFAULT_CUSTOMER_FIRST_NAME = "Emma"
DEFAULT_CUSTOMER_LAST_NAME = "Lopez"

EMAIL_ENV_VARS = (
    "SHOPPING_CUSTOMER_EMAIL",
    "WEBARENA_SHOPPING_EMAIL",
)
PASSWORD_ENV_VARS = (
    "SHOPPING_CUSTOMER_PASSWORD",
    "WEBARENA_SHOPPING_PASSWORD",
)


def _first_env(keys: Tuple[str, ...]) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return None


def get_default_customer_credentials() -> tuple[str, str]:
    """
    Return the email/password pair to use for storefront interactions.

    Environment variables override the baked-in defaults:
    - Email: SHOPPING_CUSTOMER_EMAIL or WEBARENA_SHOPPING_EMAIL
    - Password: SHOPPING_CUSTOMER_PASSWORD or WEBARENA_SHOPPING_PASSWORD
    """
    # email = _first_env(EMAIL_ENV_VARS) or DEFAULT_CUSTOMER_EMAIL
    # password = _first_env(PASSWORD_ENV_VARS) or DEFAULT_CUSTOMER_PASSWORD
    email = DEFAULT_CUSTOMER_EMAIL
    password = DEFAULT_CUSTOMER_PASSWORD
    return email, password
