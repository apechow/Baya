"""Shared constants for the reddit_pw package.

The Postmill base URL is resolved at call time rather than baked in at import.
Under ``--multi-docker`` every eval worker owns a separate Postmill instance, so
a value frozen when the module first loads sends browser helpers to the wrong
server — and a check that reads the wrong instance grades stale data instead of
what the task just wrote.

Resolution order, highest priority first:

  1. an explicit ``base`` argument passed to one of the ``*_url()`` helpers
     (how the eval harness / API server pins a task to its worker),
  2. the ``REDDIT_DOMAIN`` environment variable — what the harness already
     exports for generated scripts (see ``ClaudeCodeAgentRunner``),
  3. ``config/servers.py`` ``SERVER_URLS["reddit"]``, the single-instance
     default.

The legacy uppercase names (``REDDIT_DOMAIN``, ``SUBMIT_URL``, ...) still work
and go through the same chain, but they resolve at the moment of *access*: a
module doing ``from .constants import SUBMIT_URL`` snapshots the value at import
time. Anything that has to follow a per-task URL must call the ``*_url()``
helper (or pass ``base=`` into a ``get_*_url`` helper).
"""

from __future__ import annotations

import os
from typing import Optional

from config.servers import SERVER_URLS as _SERVER_URLS

#: Single-instance fallback, used when nothing else specifies a Postmill URL.
DEFAULT_BASE_URL = _SERVER_URLS["reddit"]

#: Path suffix for each legacy ``*_URL`` constant, keyed by that name.
_PATHS = {
    "LOGIN_URL": "/login",
    "REGISTRATION_URL": "/registration",
    "SUBMIT_URL": "/submit",
    "CREATE_FORUM_URL": "/create_forum",
    "MESSAGES_URL": "/messages",
}


def base_url(base: Optional[str] = None) -> str:
    """Return the Postmill base URL, without a trailing slash.

    ``base`` wins when given, then ``$REDDIT_DOMAIN``, then the configured
    single-instance default.
    """
    if base:
        return base.rstrip("/")
    env_base = os.environ.get("REDDIT_DOMAIN", "").strip()
    if env_base:
        return env_base.rstrip("/")
    return DEFAULT_BASE_URL.rstrip("/")


# ── Static path helpers ───────────────────────────────────────────────────────

def login_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["LOGIN_URL"]


def registration_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["REGISTRATION_URL"]


def submit_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["SUBMIT_URL"]


def create_forum_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["CREATE_FORUM_URL"]


def messages_url(base: Optional[str] = None) -> str:
    return base_url(base) + _PATHS["MESSAGES_URL"]


# ── Parameterised path helpers ────────────────────────────────────────────────

def get_user_profile_url(username: str, base: Optional[str] = None) -> str:
    """Get the profile URL for a user."""
    return f"{base_url(base)}/user/{username}"


def get_user_account_url(username: str, base: Optional[str] = None) -> str:
    """Get the account settings URL for a user."""
    return f"{base_url(base)}/user/{username}/account"


def get_user_block_list_url(username: str, base: Optional[str] = None) -> str:
    """Get the block list URL for a user."""
    return f"{base_url(base)}/user/{username}/block_list"


def get_block_user_url(username_to_block: str, base: Optional[str] = None) -> str:
    """Get the URL to block a specific user."""
    return f"{base_url(base)}/user/{username_to_block}/block_user"


def get_compose_message_url(recipient_username: str, base: Optional[str] = None) -> str:
    """Get the URL to compose a message to a user."""
    return f"{base_url(base)}/user/{recipient_username}/compose_message"


def get_forum_url(forum_name: str, base: Optional[str] = None) -> str:
    """Get the URL for a forum/subreddit."""
    return f"{base_url(base)}/f/{forum_name}"


def get_post_url(forum_name: str, post_id: str, base: Optional[str] = None) -> str:
    """Get the URL for a specific post."""
    return f"{base_url(base)}/f/{forum_name}/{post_id}"


def __getattr__(name: str) -> str:
    """Resolve the legacy uppercase constants on access (PEP 562).

    Keeps ``from .constants import REDDIT_DOMAIN, SUBMIT_URL, ...`` working — each
    resolves through :func:`base_url` at the moment of access, so single-instance
    imports see today's default and nothing else changes for them.
    """
    if name == "REDDIT_DOMAIN":
        return base_url()
    if name in _PATHS:
        return base_url() + _PATHS[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(list(globals()) + ["REDDIT_DOMAIN"] + list(_PATHS)))


# Common selectors used across modules
class Selectors:
    """CSS selectors for Reddit UI elements."""

    # Login form
    LOGIN_USERNAME_INPUT = "input#login-username"
    LOGIN_PASSWORD_INPUT = "input#login-password"
    LOGIN_SUBMIT_BUTTON = 'button[type="submit"]'

    # Registration form
    REGISTER_USERNAME_INPUT = "input#user_username"
    REGISTER_PASSWORD_FIRST = "input#user_password_first"
    REGISTER_PASSWORD_SECOND = "input#user_password_second"
    REGISTER_SUBMIT_BUTTON = 'button:has-text("Sign up")'

    # Post creation form
    POST_TITLE_INPUT = "#submission_title"
    POST_BODY_INPUT = "#submission_body"
    POST_URL_INPUT = "#submission_url"
    POST_FORUM_SELECT = "#submission_forum"
    POST_SUBMIT_BUTTON = 'button:has-text("Create submission")'
    EDIT_POST_SUBMIT_BUTTON = 'button:has-text("Edit submission")'

    # Forum creation form
    FORUM_NAME_INPUT = "#forum_name"
    FORUM_TITLE_INPUT = "#forum_title"
    FORUM_DESCRIPTION_INPUT = "#forum_description"
    FORUM_SIDEBAR_INPUT = "#forum_sidebar"
    FORUM_SUBMIT_BUTTON = 'button:has-text("Create forum")'

    # Comment form (post_id is dynamic)
    @staticmethod
    def get_comment_input(post_id: str) -> str:
        """Get the selector for the comment input field."""
        return f"#reply_to_submission_{post_id}_comment"

    COMMENT_SUBMIT_BUTTON = 'button:has-text("Post")'

    # Message form
    MESSAGE_SUBJECT_INPUT = "#message_subject"
    MESSAGE_BODY_INPUT = "#message_body"
    MESSAGE_SUBMIT_BUTTON = 'button:has-text("Send")'
    MESSAGE_THREAD_LINKS = 'a[href*="messages/thread"]'

    # Account settings
    ACCOUNT_EMAIL_INPUT = "#user_email"
    ACCOUNT_SAVE_BUTTON = 'button:has-text("Save changes")'

    # Block user
    BLOCK_SUBMIT_BUTTON = 'button:has-text("Block")'

    # Delete button (used across various pages)
    DELETE_BUTTON = 'button:has-text("Delete")'

    # Error messages
    ALREADY_USED_ERROR = "This value is already used"
