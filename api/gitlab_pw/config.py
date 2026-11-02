"""Configuration helpers for reusable GitLab credentials."""

from __future__ import annotations

import os
from typing import Tuple

# Override these via environment variables for your own account.
DEFAULT_GITLAB_USERNAME = "byteblaze"
DEFAULT_GITLAB_PASSWORD = "hello1234"

USERNAME_ENV_VARS = (
    "GITLAB_USERNAME",
    "WEBARENA_GITLAB_USERNAME",
)
PASSWORD_ENV_VARS = (
    "GITLAB_PASSWORD",
    "WEBARENA_GITLAB_PASSWORD",
)


def _first_env(keys: Tuple[str, ...]) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return None


def get_default_gitlab_credentials() -> tuple[str, str]:
    """
    Return the username/password pair to use for GitLab interactions.

    Environment variables override the baked-in defaults:
    - Username: GITLAB_USERNAME or WEBARENA_GITLAB_USERNAME
    - Password: GITLAB_PASSWORD or WEBARENA_GITLAB_PASSWORD
    """
    username = _first_env(USERNAME_ENV_VARS) or DEFAULT_GITLAB_USERNAME
    password = _first_env(PASSWORD_ENV_VARS) or DEFAULT_GITLAB_PASSWORD
    return username, password
