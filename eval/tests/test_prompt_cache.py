# eval/tests/test_prompt_cache.py
#
# Guards the two invariants Anthropic prompt caching depends on in the ReAct agent.
# Both fail silently in production — a broken breakpoint or a drifting system prompt costs
# ~10x on input tokens with no error and no behavioural change — so they need a test.
#
# Unlike the other files here, this one hits no server and makes no LLM call.

import copy
import os

import pytest

from react_agent.react_web.codeact_agent.codeact_agent import (
    IPythonRunCellObservation,
    _apply_cache_control,
)
from react_agent.react_web.react_agent_runner import _SimpleState

N_STEPS = 6


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _StubLLM:
    """Records every request verbatim and returns a canned ipython action."""

    supports_prompt_cache = True

    def __init__(self):
        self.requests = []
        self.n = 0

    def completion(self, messages, stop=None, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        self.n += 1
        return _Resp(
            f"Let me check step {self.n}.\n"
            f"<execute_ipython>\nprint({self.n})\n</execute_ipython>"
        )


# site -> the os.environ key ReactAgentRunner._run_task writes for it (_SITE_TO_ENV there).
SITE_ENV = {
    "gitlab": "GITLAB",
    "shopping": "SHOPPING",
    "shopping_admin": "SHOPPING_ADMIN",
    "reddit": "REDDIT",
}


@pytest.fixture(params=sorted(SITE_ENV))
def requests(request, monkeypatch):
    """Drive the real CodeActAgent.step() for N_STEPS, flipping the shared site URL midway.

    The flip mimics a concurrent task in another thread overwriting os.environ (see
    ReactAgentRunner._run_task), which is what run_webarena_verified.py does under
    --multi-docker.

    Parametrised over every site because each resolves its own API schemas and base-URL lines,
    so each has its own opportunity to drift.
    """
    from react_agent.react_web.codeact_agent.codeact_agent import CodeActAgent

    site = request.param
    env_key = SITE_ENV[site]
    for key in set(SITE_ENV.values()) | {"MAP"}:
        monkeypatch.setenv(key, f"http://{key.lower()}.invalid")
    monkeypatch.setenv(env_key, "http://localhost:20046")

    agent = CodeActAgent(llm=None)
    stub = _StubLLM()
    agent.llm = stub
    state = _SimpleState(
        task_prompt=f"{site} instance: http://localhost:20046\nTask: do the thing",
        max_iterations=30,
        sites=[site],
    )

    for i in range(N_STEPS):
        if i == 2:
            os.environ[env_key] = "http://localhost:20016"
        action = agent.step(state)
        state.iteration += 1
        obs = IPythonRunCellObservation()
        obs.content = f"output-{i}"
        state.history.append((action, obs))

    assert len(stub.requests) == N_STEPS
    return stub.requests


def test_system_prompt_is_byte_stable_across_env_flip(requests):
    """The snapshot must survive a concurrent task rewriting os.environ mid-task.

    Without it the API 'Base URL:' lines change ~15% into a ~98k-char prompt, which both feeds
    the agent another worker's GitLab (a separate database) and invalidates the cached prefix.
    """
    distinct = {r[0]["content"] for r in requests}
    assert len(distinct) == 1, f"system prompt drifted into {len(distinct)} versions"


def test_breakpoints_never_land_on_step_varying_messages(requests):
    """step() appends the axtree and turn counter to the newest message every call.

    A breakpoint there would rebuild the entry every step and never read one.
    """
    for i, req in enumerate(requests, 1):
        marked = [j for j, m in enumerate(req) if "cache_control" in m]
        assert 0 in marked, f"step {i}: system prompt not marked"
        assert len(marked) <= 2, f"step {i}: {len(marked)} breakpoints, design allows 2"
        for j in marked:
            content = req[j]["content"]
            assert "ENVIRONMENT REMINDER" not in content, (
                f"step {i}: breakpoint {j} sits on a step-varying message"
            )
            assert content.strip(), (
                f"step {i}: breakpoint {j} sits on empty content, which litellm drops"
            )


def test_cached_prefix_would_actually_be_read(requests):
    """Model Anthropic's read: the longest previously-written prefix that still matches.

    This is the assertion that catches a breakpoint placed one message too far forward — it
    still looks reasonable, but every read misses.
    """
    written = []
    for i, req in enumerate(requests, 1):
        current = [(m["role"], m["content"]) for m in req]
        best = max(
            (len(e) for e in written if len(e) <= len(current) and list(e) == current[: len(e)]),
            default=0,
        )
        covered = sum(len(c) for _, c in current[:best])
        total = sum(len(c) for _, c in current)
        if i > 1:
            assert covered > 0, f"step {i}: no cached prefix matches — breakpoints are useless"
            assert covered / total > 0.5, (
                f"step {i}: only {100 * covered / total:.1f}% of input cacheable"
            )
        for j, m in enumerate(req):
            if "cache_control" in m:
                written.append(tuple(current[: j + 1]))


def test_disabled_by_env_flag(monkeypatch):
    """PROMPT_CACHE=false must produce a plain payload, so the A/B comparison is honest."""
    import react_agent.react_web.codeact_agent.codeact_agent as ca

    monkeypatch.setattr(ca, "PROMPT_CACHE", False)
    monkeypatch.setenv("GITLAB", "http://localhost:20046")
    for key in ("SHOPPING", "SHOPPING_ADMIN", "REDDIT", "MAP"):
        monkeypatch.setenv(key, f"http://{key.lower()}.invalid")

    agent = ca.CodeActAgent(llm=None)
    stub = _StubLLM()
    agent.llm = stub
    state = _SimpleState(task_prompt="Task: do the thing", max_iterations=30, sites=["gitlab"])
    agent.step(state)

    assert stub.requests, "step() made no LLM call"
    marked = [i for i, m in enumerate(stub.requests[0]) if "cache_control" in m]
    assert marked == [], f"cache_control leaked through with the flag off: {marked}"


def test_non_anthropic_models_are_not_marked(monkeypatch):
    """cache_control is Anthropic-specific; OpenAI/Gemini payloads must stay clean."""
    from react_agent.react_web.codeact_agent.codeact_agent import _ConfigLLM

    assert _ConfigLLM("anthropic", "claude-sonnet-4-6").supports_prompt_cache is True
    assert _ConfigLLM("openai", "gpt-5.4").supports_prompt_cache is False
    assert _ConfigLLM("google", "gemini-3.5-flash").supports_prompt_cache is False


def test_empty_assistant_message_is_skipped():
    """litellm drops an assistant block with empty content, taking its cache_control with it.

    Reachable via InterleavingResponseParser returning MessageAction('') for an empty completion.
    """
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "o"},
    ]
    out = _apply_cache_control(messages, skip={3})
    marked = [i for i, m in enumerate(out) if "cache_control" in m]
    assert marked == [0, 1], f"expected fallback past the empty message, got {marked}"
    assert all("cache_control" not in m for m in messages), "caller's list was mutated"
