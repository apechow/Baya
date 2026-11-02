# eval/tests/test_codeact_step.py
#
# Behavioural tests for CodeActAgent.step() that need no server and no LLM call.

import os

import pytest

from react_agent.react_web.codeact_agent.codeact_agent import (
    BrowseInteractiveAction,
    BrowserOutputObservation,
    CodeActAgent,
)
from react_agent.react_web.react_agent_runner import _SimpleState


class _StubLLM:
    supports_prompt_cache = False

    def completion(self, messages, stop=None, **kwargs):
        class _M:
            content = "ok\n<execute_ipython>\nprint(1)\n</execute_ipython>"

        class _C:
            message = _M()

        class _R:
            choices = [_C()]

        return _R()


@pytest.fixture(autouse=True)
def _site_env(monkeypatch):
    for key in ("GITLAB", "SHOPPING", "SHOPPING_ADMIN", "REDDIT", "MAP"):
        monkeypatch.setenv(key, f"http://{key.lower()}.invalid")


def _run(n_errors: int, n_steps: int):
    """Seed `n_errors` failed browser actions into history, then take `n_steps` steps.

    Returns the 1-based step at which the agent bailed out, or None if it never did.
    """
    agent = CodeActAgent(llm=None)
    agent.llm = _StubLLM()
    state = _SimpleState(task_prompt="t", max_iterations=30, sites=["gitlab"])

    for _ in range(n_errors):
        action = BrowseInteractiveAction(browser_actions='click("1")')
        obs = BrowserOutputObservation()
        obs.error = True
        obs.last_browser_action = 'click("1")'
        obs.axtree_object = {}
        obs.extra_element_properties = {}
        obs.content = "err"
        state.history.append((action, obs))

    for step in range(1, n_steps + 1):
        action = agent.step(state)
        if "Too many errors" in str(getattr(action, "content", "")):
            return step
        state.iteration += 1
    return None


@pytest.mark.parametrize("n_errors", [1, 3, 9, 10])
def test_browser_errors_below_threshold_never_bail_out(n_errors):
    """The error count must reflect history, not how many times history has been replayed.

    step() rebuilds its message list by walking the whole history every call, so counting onto
    an instance attribute made the total grow as steps x errors: 3 real errors tripped the
    >10 bail-out on step 4 and the task died with "Too many errors encountered" having only
    ever failed 3 browser actions.
    """
    assert _run(n_errors, n_steps=12) is None


def test_browser_errors_above_threshold_still_bail_out():
    """The guard must still fire — 11 genuine errors exceeds the limit of 10."""
    assert _run(11, n_steps=3) == 1
