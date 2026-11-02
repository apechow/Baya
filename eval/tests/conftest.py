# tests/conftest.py
#
# Shared pytest configuration for the PTE test suite.
#
# Plugging in a custom agent
# ──────────────────────────
# Any agent implementation can be tested by subclassing BaseAgentRunner and
# passing the dotted import path via --agent-runner:
#
#   python3 -m pytest tests/ --agent-runner mymodule.MyAgentRunner -v
#
# The default is the built-in AgentRunner (PTE ToolCallAgent + MCP tools).
# See BaseAgentRunner in run_program_html_benchmark.py for the interface.

import asyncio
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.servers import SERVER_URLS as _SERVER_URLS
from eval.tests.agent_test_utils import get_model_id as _get_model_id


# ---------------------------------------------------------------------------
# CLI options
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--task-limit",
        type=int,
        default=None,
        metavar="N",
        help="Only run the first N tasks in parameterised agent tests.",
    )
    parser.addoption(
        "--site",
        type=str,
        default=None,
        metavar="SITE",
        help=(
            "Filter tasks to a specific site before applying --task-limit. "
            "One of: gitlab, reddit, shopping, shopping_admin."
        ),
    )
    parser.addoption(
        "--agent-runner",
        type=str,
        default=None,
        metavar="MODULE.CLASS",
        help=(
            "Dotted import path of a BaseAgentRunner subclass to use instead "
            "of the default AgentRunner. Example: mymodule.MyAgentRunner"
        ),
    )
    parser.addoption(
        "--output",
        type=str,
        default=None,
        metavar="FILENAME",
        help=(
            "Write test results as JSON to tests/logs/<FILENAME>. "
            "If omitted, no log file is written. "
            "Example: --output run_gitlab.json"
        ),
    )
    parser.addoption(
        "--server",
        type=str,
        default="gitlab",
        metavar="SERVER",
        help=(
            "Server the agent authenticates against. "
            "One of: gitlab, reddit, shopping, shopping_admin. Default: gitlab."
        ),
    )
    parser.addoption(
        "--base-url",
        type=str,
        default=None,
        metavar="URL",
        help=(
            "Base URL of the server the agent talks to. "
            "Defaults to the canonical URL for --server (e.g. shopping → "
            f"{_SERVER_URLS['shopping']}, gitlab → {_SERVER_URLS['gitlab']})."
        ),
    )
    parser.addoption(
        "--force-reset",
        action="store_true",
        default=False,
        help=(
            "Override require_reset to True for every task, regardless of the "
            "value in the task JSON. Useful for re-runs where prior runs may "
            "have left state (e.g. duplicate milestones, issues, or MRs) that "
            "would cause tasks to fail. Has no effect when --no-reset is set."
        ),
    )
    parser.addoption(
        "--task-id",
        type=str,
        default=None,
        help="Run only the task(s) with these numeric task_ids, comma-separated (e.g. --task-id 136 or --task-id 136,389,412).",
    )
    parser.addoption(
        "--start",
        type=int,
        default=None,
        metavar="TASK_ID",
        help=(
            "Start from the task with this numeric task_id and run every task from "
            "there onward, in loaded order (string_match tasks then program_html). "
            "Useful to resume a run that died partway (e.g. on an API quota limit) "
            "without re-running the tasks before it. Ignored when --task-id is set. "
            "Composes with --resume and --task-limit. Example: --start 368."
        ),
    )
    parser.addoption(
        "--multi-docker",
        action="store_true",
        default=False,
        help=(
            "Use the remote multi-docker worker pool via the SSH orchestrator. "
            "When False (default), tests run against a single local GitLab instance "
            f"at --base-url (default: {_SERVER_URLS['gitlab']})."
        ),
    )
    parser.addoption(
        "--resume",
        action="store_true",
        default=False,
        help=(
            "Resume a previously interrupted run. Reads the file specified by --output "
            "and skips any task_ids that already appear in it, so only incomplete tasks "
            "are re-run. Has no effect when --output is not set."
        ),
    )
    parser.addoption(
        "--agent-trace",
        action="store_true",
        default=False,
        help="Print curl commands and raw responses for each execution step.",
    )
    parser.addoption(
        "--regenerate-code",
        action="store_true",
        default=False,
        help=(
            "Force a fresh codegen session for every task (agent/baya_code_agent/codegen/run_claude_task.sh "
            "or agent/baya_code_agent/codegen/run_antigravity_task.sh, depending on the runner), even if "
            "generated/<site>/task_<id>/ already contains an implementation script. "
            "Used by ClaudeCodeAgentRunner and AntigravityAgentRunner."
        ),
    )
    parser.addoption(
        "--max-attempts",
        type=int,
        default=3,
        metavar="N",
        help=(
            "Maximum number of times a task's generated script is run (default: 3). "
            "When a run fails to execute — non-zero exit or timeout — the coding "
            "agent replans: it reads the task's own python_output.log and session log "
            "(claude_output.log or antigravity_output.log), repairs the script, and "
            "the script is run again. A codegen session that produced no script at "
            "all is instead retried from scratch, since there is nothing to repair. "
            "A task that produces an answer is never retried, whether or not that "
            "answer is graded correct. 1 disables both entirely. Each attempt is a "
            "full codegen session (~2.5-4 min), so N=3 can take ~45 min for a "
            "pathological task. Used by test_agent_verified_all_sites.py."
        ),
    )
    parser.addoption(
        "--restart-on-release",
        action="store_true",
        default=False,
        help=(
            "Restart the worker's docker instance when releasing it after each "
            "task. Off by default: releases pass force_restart=False so the "
            "instance is left running, which avoids the slow (and currently "
            "flaky) gitlab container restart. Turn this ON when running tasks "
            "that modify site state, since without a restart each task inherits "
            "whatever the previous one wrote. Used by "
            "test_agent_verified_all_sites.py."
        ),
    )
    parser.addoption(
        "--no-reddit-restore",
        action="store_true",
        default=False,
        help=(
            "Skip the post-task Postmill checkpoint restore on --site reddit "
            "single-instance runs. That restore (eval/reddit_state_reset.py) "
            "rebuilds the container from a clean image and costs ~15-20s per write "
            "task, but it is what keeps feed-position and duplicate-post "
            "contamination out of a full run: reddit has no per-task undo, and "
            "every program_html task is a write. Turn it off for single-task smoke "
            "tests, or when the ssh target it needs is unreachable. Used by "
            "test_agent_verified_all_sites.py."
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_runner_class(dotted_path: str):
    """
    Import and return a runner class from a dotted path like 'mymodule.MyClass'.
    Raises ValueError, ImportError, or AttributeError with helpful messages.
    """
    parts = dotted_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError(
            f"--agent-runner must be a dotted MODULE.CLASS path, got: {dotted_path!r}"
        )
    module_path, class_name = parts
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls


@pytest.fixture(scope="session", autouse=True)
def reddit_startup_cleanup():
    yield


# ---------------------------------------------------------------------------
# Session-scoped fixtures — shared across all three agent test files
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def session_event_loop():
    """
    Session-scoped asyncio event loop shared across all tests.

    Using a single loop for the whole test session lets the agent runner
    initialise its connections once and reuse them across every test,
    rather than creating and tearing down new connections per test.
    Named ``session_event_loop`` (not ``event_loop``) to avoid conflicting
    with pytest-asyncio's own loop fixture if that plugin is installed.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.run_until_complete(asyncio.sleep(0))
    loop.close()


@pytest.fixture(scope="session")
def agent_runner(request, session_event_loop):
    """
    Initialise the agent runner exactly once for the entire test session.

    By default uses AgentRunner (PTE ToolCallAgent + MCP tools). Pass
    --agent-runner MODULE.CLASS to swap in any BaseAgentRunner subclass.

    Configuration:
    - headless=True      — no visible browser during evaluation.
    - enable_reset=True  — GitLab state is reset before each GitLab task.
    """
    runner_path = request.config.getoption("--agent-runner", default=None)

    if runner_path:
        runner_cls = _load_runner_class(runner_path)
    else:
        from eval.run_pte_benchmark import AgentRunner
        runner_cls = AgentRunner

    force_reset = request.config.getoption("--force-reset", default=False)
    debug = request.config.getoption("--agent-trace", default=False)
    server = request.config.getoption("--server", default="gitlab")
    base_url = request.config.getoption("--base-url") or _SERVER_URLS.get(server, _SERVER_URLS["gitlab"])
    runner = runner_cls(headless=True, enable_reset=True, force_reset=force_reset,
                        gitlab_base_url=base_url, debug=debug)
    runner.server = server
    runner.base_url = base_url

    session_event_loop.run_until_complete(runner._init_agent())
    return runner


@pytest.fixture(scope="session")
def acquire_lock():
    """
    Session-scoped asyncio.Lock used to serialize concurrent worker acquire
    calls when multiple coroutines run in the same event loop.
    """
    return asyncio.Lock()


@pytest.fixture(scope="session")
def result_log(request):
    """
    Session-scoped list that accumulates per-task result dicts.

    Tests append to this list; at session teardown the entries are written
    to tests/logs/<filename> when --output is provided.

    Each entry shape:
        {
            "task_id":    int,
            "intent":     str,
            "sites":      list[str],
            "eval_types": list[str],
            "passed":     bool,
            "answer":     str | None,
            "error":      str | None,
        }
    """
    entries: List[Dict[str, Any]] = []
    yield entries

    output_name = request.config.getoption("--output", default=None)
    if not output_name:
        # Derive a prefix from the test file(s) passed on the command line so
        # that auto-saved logs are easy to identify (e.g. "test_agent_shopping_
        # string_match_20260531_194331.json").  Fall back to "run" if no
        # specific file was given.
        stems = [
            Path(arg).stem
            for arg in request.config.args
            if arg.endswith(".py") and Path(arg).exists()
        ]
        prefix = stems[0] if stems else "run"
        output_name = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)

    out_path = logs_dir / output_name

    # When --resume is set, merge new entries with any prior results so that
    # tasks skipped by _load_tasks are preserved in the output file.
    resume = request.config.getoption("--resume", default=False)
    if resume and out_path.exists():
        try:
            prior = json.loads(out_path.read_text()).get("results", [])
            new_ids = {e["task_id"] for e in entries}
            entries = [e for e in prior if e["task_id"] not in new_ids] + entries
        except Exception:
            pass

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": _get_model_id(),
        "total": len(entries),
        "passed": sum(1 for e in entries if e["passed"]),
        "failed": sum(1 for e in entries if not e["passed"]),
        "results": entries,
    }
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\n📄 Results written to {out_path}")
