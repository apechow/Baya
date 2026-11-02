# eval/tests/agent_test_utils.py
#
# Shared utilities for agent integration tests (gitlab, shopping, and future servers).

import json
import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional


def new_token_counter() -> Dict[str, int]:
    """Return a zeroed token-usage counter in the shape written to the detailed log.

    `cache_read` / `cache_creation` are prompt-cache counters.  They are *subsets* of `input`,
    not additions to it: litellm folds cache tokens into prompt_tokens for Anthropic, so summing
    them onto `input` or `total` would double-count.
    """
    return {
        "input": 0, "output": 0, "total": 0, "llm_calls": 0,
        "cache_read": 0, "cache_creation": 0,
    }


def add_token_usage(target: Dict[str, int], usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Add one agent's token usage into `target` in place; ignores None/malformed input."""
    if not isinstance(usage, dict):
        return target
    for key in ("input", "output", "total", "llm_calls", "cache_read", "cache_creation"):
        val = usage.get(key)
        if isinstance(val, (int, float)):
            target[key] += int(val)
    return target


def serialize_plan(plan) -> list:
    """Serialize a plan to a JSON-serializable list of step dicts."""
    return [
        {
            "step_id": step.step_id,
            "tool_name": step.tool_name.value if hasattr(step.tool_name, "value") else str(step.tool_name),
            "arguments": [
                {"name": a.name, "value": a.value, "value_type": a.value_type}
                for a in (step.arguments or [])
            ],
            "depends_on": step.depends_on or [],
            "hints": step.hints or "",
        }
        for step in plan
    ]


def extract_agent_details(runner) -> Dict[str, Any]:
    """
    Extract plan, execution, and logging details from an AgentRunner instance.

    Returns a dict with keys:
        plan_steps      — serialized plan steps, or None
        parsed_outputs  — step_outputs from execution context, or None
        raw_execution   — last_raw_outputs from execution agent, or None
        planning_log    — last_run_log from planning agent, or None
        costs           — per-LLM-call costs from the planning agent
        tokens          — token usage summed over planning + execution agents
    """
    plan_steps = None
    parsed_outputs = None
    raw_execution = None
    planning_log = None

    costs: list = []
    tokens = new_token_counter()
    _agent = getattr(runner, "_agent", None)
    if _agent is not None:
        pr = getattr(_agent, "last_plan_response", None)
        if pr is not None:
            plan_steps = serialize_plan(pr.plan)
        pa = getattr(_agent, "planning_agent", None)
        if pa is not None:
            planning_log = getattr(pa, "last_run_log", None)
            costs = getattr(pa, "last_run_costs", [])
            add_token_usage(tokens, getattr(pa, "last_run_tokens", None))
        ea = getattr(_agent, "execution_agent", None)
        if ea is not None:
            raw_execution = getattr(ea, "last_raw_outputs", None)
            ctx = getattr(ea, "last_ctx", None)
            if ctx is not None:
                parsed_outputs = getattr(ctx, "step_outputs", None)
            add_token_usage(tokens, getattr(ea, "last_run_tokens", None))

    return {
        "plan_steps": plan_steps,
        "parsed_outputs": parsed_outputs,
        "raw_execution": raw_execution,
        "planning_log": planning_log,
        "costs": costs,
        "tokens": tokens,
    }


def task_status(passed: bool, error: Optional[str], plan_steps: Optional[list]) -> str:
    """Derive a status string consistent across all server test files."""
    if error:
        return "failed" if plan_steps is None else "execution_failed"
    if passed:
        return "success"
    return "execution_failed"


@lru_cache(maxsize=1)
def get_model_id() -> str:
    """Return agent_llm_model from config.yaml (same field ModelProvider reads)."""
    try:
        from agent.common.configurator import Configurator
        return str(Configurator().data.agent_llm_model)
    except Exception:
        return "unknown"


@lru_cache(maxsize=1)
def get_git_commit() -> str:
    """Return the current HEAD commit hash."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def build_detailed_entry(
    task: Dict[str, Any],
    agent_result: Optional[Dict[str, Any]],
    error: Optional[str],
    correct: bool,
    start_time: datetime,
    end_time: datetime,
    eval_output_dir: str,
    costs: Optional[List] = None,
    tokens: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a lightweight JSONL entry in the OpenHands/WebArena result format."""
    model_id = get_model_id()
    raw_answer = (agent_result or {}).get("answer") or ""
    # Preserve the full answer, stripping only surrounding whitespace and any
    # trailing period(s). Gold answers never carry a trailing period, so this is
    # neutral for substring/must_include grading and fixes exact-match grading
    # (e.g. "Shawn Allen." -> "Shawn Allen"). Do NOT split on "." — that
    # truncated answers with internal periods (emails, decimals, URLs, names
    # like "Andrew M. Whalen").
    cleaned = raw_answer.strip().rstrip(".").strip() if raw_answer else None
    cleaned = cleaned or None
    valid_costs = [c for c in (costs or []) if c is not None]
    token_usage = add_token_usage(new_token_counter(), tokens)
    duration_seconds = round((end_time - start_time).total_seconds(), 2)
    return {
        "task_id": task["task_id"],
        "raw": cleaned,
        "answer_id": str(task["answer_id"]) if task.get("answer_id") is not None else "None",
        "model_id": model_id,
        "metadata": {
            "agent_class": "AgentRunner",
            "model_name": model_id,
            "max_iterations": None,
            "eval_output_dir": eval_output_dir,
            "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "git_commit": get_git_commit(),
        },
        "metrics": {
            "accumulated_cost": sum(valid_costs) if valid_costs else None,
            "costs": costs or [],
            # Wall-clock seconds for the whole task (agent run + evaluation).
            "duration_seconds": duration_seconds,
            # Cumulative LLM token usage for this task. Zeros mean the runner
            # reported no usage (e.g. the task errored before any LLM call).
            "input_tokens": token_usage["input"],
            "output_tokens": token_usage["output"],
            "total_tokens": token_usage["total"],
            "n_llm_calls": token_usage["llm_calls"],
            # Prompt-cache tokens, both subsets of input_tokens (see new_token_counter).
            # cache_read_tokens staying at 0 on an Anthropic run means caching is not hitting.
            "cache_read_tokens": token_usage["cache_read"],
            "cache_creation_tokens": token_usage["cache_creation"],
        },
        "error": error,
        "correct": correct,
        "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def flush_detailed_jsonl(out_path: Path, entry: Dict[str, Any]) -> None:
    """Append a single task result as one JSON line to the detailed JSONL log."""
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# PTE task files — site → the verified task files carrying real eval criteria
# ---------------------------------------------------------------------------

TEST_FILES_DIR = Path(__file__).parent / "test_files"

PTE_TEST_FILES: Dict[str, List[str]] = {
    "gitlab": [
        "gitlab_verified_string_match.json",
        "gitlab_verified_program_html.json",
    ],
    "shopping": [
        "shopping_verified_string_match.json",
        "shopping_verified_program_html.json",
    ],
    "shopping_admin": [
        "shopping_admin_verified_string_match.json",
        "shopping_admin_verified_program_html.json",
    ],
    "reddit": [
        "reddit_verified_string_match.json",
        "reddit_verified_program_html.json",
    ],
}


def load_pte_task_ids(site: str) -> set:
    """Return the task_ids that have a PTE counterpart for `site`.

    Unions the string_match and program_html files for that site; a task may
    appear in both, so this is smaller than the sum of their lengths.
    """
    ids: set = set()
    for fname in PTE_TEST_FILES.get(site, []):
        fp = TEST_FILES_DIR / fname
        if fp.exists():
            for t in json.loads(fp.read_text()):
                ids.add(int(t["task_id"]))
        else:
            print(f"⚠️  PTE file not found: {fp}")
    return ids
