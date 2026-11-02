"""Configuration helpers for reusable admin credentials."""

from __future__ import annotations

import os

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin1234"


def get_default_admin_credentials() -> tuple[str, str]:
    """
    Return the username/password pair to use for Luma admin interactions.

    Environment variables override the baked-in defaults:
    - Username: SHOPPING_ADMIN_USER
    - Password: SHOPPING_ADMIN_PASS
    """
    username = os.getenv("SHOPPING_ADMIN_USER") or DEFAULT_ADMIN_USERNAME
    password = os.getenv("SHOPPING_ADMIN_PASS") or DEFAULT_ADMIN_PASSWORD
    return username, password
