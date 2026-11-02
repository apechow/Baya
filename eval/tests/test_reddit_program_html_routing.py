# eval/tests/test_reddit_program_html_routing.py
#
# Static (no browser, no network) unit tests for the reddit program_html
# instance-routing fix. program_html grading/login must target THIS task's
# Postmill site instance (the worker under --multi-docker), carried on the
# dedicated `reddit_base_url` attribute — never the overloaded `base_url`,
# which some suites point at the companion API server (reddit_extra).
#
# Run:
#   python3 -m pytest eval/tests/test_reddit_program_html_routing.py -v

from config.servers import SERVER_URLS
from eval.run_pte_benchmark import AgentRunner


def _runner() -> AgentRunner:
    # enable_reset=False avoids constructing GitLabStateReset; _init_agent is a
    # separate call, so construction does no I/O.
    return AgentRunner(headless=True, enable_reset=False)


def test_reddit_base_defaults_to_canonical_when_unset():
    r = _runner()
    assert r._reddit_program_html_base() == SERVER_URLS["reddit"].rstrip("/")


def test_reddit_base_uses_worker_url_when_set():
    r = _runner()
    r.reddit_base_url = "http://localhost:20034/"  # worker 3
    assert r._reddit_program_html_base() == "http://localhost:20034"


def test_reddit_base_ignores_overloaded_base_url():
    # Even if base_url is (wrongly) the companion API server, grading must not
    # follow it — the old `_cand != reddit_extra` guard is gone.
    r = _runner()
    r.server = "reddit"
    r.base_url = SERVER_URLS["reddit_extra"]
    r.reddit_base_url = "http://localhost:20024"  # worker 2
    assert r._reddit_program_html_base() == "http://localhost:20024"


def test_reddit_extra_never_becomes_the_graded_instance():
    # Regression guard for the original bug: reddit_extra is an API server, not
    # a Postmill HTML site, and must never be what DOM grading navigates.
    r = _runner()
    r.reddit_base_url = "http://localhost:20044"  # worker 4
    assert r._reddit_program_html_base() != SERVER_URLS["reddit_extra"].rstrip("/")
