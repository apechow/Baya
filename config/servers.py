"""
Server definitions: base URLs and user context for all WebArena sites.

Import from here rather than hardcoding URLs or reading user_info.json:
    from config.servers import SERVERS, SERVER_URLS

Multi-docker mode ignores SERVER_URLS — the worker pool provides per-task
URLs dynamically. These values are the single-server defaults.
"""

SERVERS: dict = {
    "gitlab": {
        "url":          "http://127.0.0.1:8023",
        "label":        "GitLab",
        "username_env": "GITLAB_USERNAME",
    },
    "reddit": {
        "url":          "http://127.0.0.1:9999",
        "label":        "Reddit",
        "username_env": "REDDIT_USERNAME",
    },
    # Both Magento instances must be addressed as "localhost", not "127.0.0.1".
    # Magento canonicalises every request to its configured base URL, and that
    # 302 *drops the query string*: a request to
    #   http://127.0.0.1:7770/catalogsearch/result/?q=laundry+detergent
    # redirects to http://localhost:7770/catalogsearch/result/ (no ?q=), which
    # in turn redirects to the homepage. Clients that follow redirects then
    # scrape the homepage's featured-product grid and mistake it for search
    # results — see /fuzzy_search in api/servers/shopping_extra.py.
    "shopping": {
        "url":          "http://localhost:7770",
        "label":        "Shopping",
        "username_env": "SHOPPING_USERNAME",
    },
    "shopping_admin": {
        "url":          "http://localhost:7780",
        "label":        "Luma Admin",
        "username_env": None,
    },
    "wikipedia": {
        "url":          "http://127.0.0.1:8889",
        "label":        "Wikipedia",
        "username_env": None,
    },
    "shopping_extra": {
        "url":          "http://127.0.0.1:7790",
        "label":        "Shopping Extra",
        "username_env": None,
    },
    "reddit_extra": {
        "url":          "http://127.0.0.1:7791",
        "label":        "Reddit Extra",
        "username_env": None,
    },
    "gitlab_extra": {
        "url":          "http://127.0.0.1:7792",
        "label":        "GitLab Extra",
        "username_env": None,
    },
    "shopping_admin_extra": {
        "url":          "http://127.0.0.1:7793",
        "label":        "Shopping Admin Extra",
        "username_env": None,
    },
}

# Convenience alias used throughout the codebase
SERVER_URLS: dict = {k: v["url"] for k, v in SERVERS.items()}
