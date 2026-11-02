# eval/tests/test_agent_verified_all_gitlab.py
#
# Integration tests: run the agent on every GitLab task in
# raw_webarena_tasks_all_gitlab.json (186 tasks, both string_match and
# program_html eval types).
#
# Tasks run concurrently up to num_workers() at a time, matching the
# parallelism of scripts/run_tasks_batch_new.py.
#
# Run all 186 tasks:
#   python3 -m pytest eval/tests/test_agent_verified_all_gitlab.py -v
#
# Smoke test (first 5 tasks):
#   python3 -m pytest eval/tests/test_agent_verified_all_gitlab.py --task-limit 5 -v -s
#
# Single task by ID:
#   python3 -m pytest eval/tests/test_agent_verified_all_gitlab.py --task-id 389 -v -s
#
# Multiple tasks by ID:
#   python3 -m pytest eval/tests/test_agent_verified_all_gitlab.py --task-id 389,412,500 -v -s
#
# Save results to a JSON log:
#   python3 -m pytest eval/tests/test_agent_verified_all_gitlab.py -v --output gitlab_results.json
#
# Force-reset GitLab state before every task:
#   python3 -m pytest eval/tests/test_agent_verified_all_gitlab.py -v --force-reset
#
# Plug in a custom agent runner:

#   python3 -m pytest eval/tests/test_agent_verified_all_gitlab.py \
#       --agent-runner my_agent_runner.MyAgentRunner -v -s

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agent.baya_json_agent.auth import StaticAuth
from config.servers import SERVER_URLS as _SERVER_URLS
from eval.docker import workers_new as _workers_new
from eval.run_pte_benchmark import AgentRunner
from eval.tests.agent_test_utils import (
    build_detailed_entry,
    extract_agent_details,
    flush_detailed_jsonl,
    get_model_id,
    task_status,
)


@asynccontextmanager
async def _local_session(gitlab_url: str, glpat=None):
    """Stub worker session for a single local GitLab instance."""
    yield {"worker_id": "local", "gitlab_url": gitlab_url, "glpat": glpat}


async def _mint_local_glpat(base_url: str) -> Optional[str]:
    """Mint one GitLab token for the whole single-instance run.

    Without this the execution agent falls back to GITLAB_TOKEN in
    config/.server_env, which goes stale whenever the instance is rebuilt and
    makes every request 401. A PAT survives everything the eval does to a
    running instance (the state reset is REST-only and never touches tokens),
    so one token minted here stays valid for every task in the run.
    """
    from config.init_tokens.refresh_gitlab_token import refresh_token

    print(f"  Minting one GitLab token for this run ({base_url})...")
    try:
        token = await asyncio.to_thread(refresh_token, base_url=base_url)
    except Exception as exc:
        print(
            f"  WARNING: could not mint a GitLab token ({exc}) — continuing with "
            f"whatever GITLAB_TOKEN is in config/.server_env."
        )
        return None
    print("  GitLab token minted; reusing it for every task in this run")
    return token

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

TASK_FILE = Path(__file__).parent / "test_files" / "gitlab_verified_string_match.json"
TASK_FILE2 = Path(__file__).parent / "test_files" / "gitlab_verified_program_html.json"

LOGS_DIR = Path(__file__).parent / "logs"


def _load_completed_ids(output_name: Optional[str]) -> set:
    """
    Return the set of task_ids already saved in the output file.
    Used by --resume to skip tasks that were completed in a prior run.
    """
    if not output_name:
        return set()
    out_path = LOGS_DIR / output_name
    if not out_path.exists():
        return set()
    try:
        data = json.loads(out_path.read_text())
        return {r["task_id"] for r in data.get("results", [])}
    except Exception:
        return set()


def _load_tasks(config=None) -> List[Dict[str, Any]]:
    tasks = json.loads(TASK_FILE.read_text()) + json.loads(TASK_FILE2.read_text())
    if config is not None:
        task_id = config.getoption("--task-id", default=None)
        if task_id is not None:
            ids = {int(x.strip()) for x in task_id.split(",")}
            tasks = [t for t in tasks if t.get("task_id") in ids]
        else:
            resume = config.getoption("--resume", default=False)
            if resume:
                output_name = config.getoption("--output", default=None)
                completed = _load_completed_ids(output_name)
                if completed:
                    print(f"\n[resume] Skipping {len(completed)} already-completed tasks: {sorted(completed)}")
                    tasks = [t for t in tasks if t.get("task_id") not in completed]
            limit = config.getoption("--task-limit", default=None)
            if limit is not None:
                tasks = tasks[:limit]
    return tasks


def _flush_results(output_name: Optional[str], result_log: List[Dict[str, Any]], interrupted: bool = False) -> None:
    """Write current result_log snapshot to the output file (atomic via temp file)."""
    if not output_name:
        return
    LOGS_DIR.mkdir(exist_ok=True)
    out_path = LOGS_DIR / output_name
    summary = {
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "model": get_model_id(),
        "interrupted": interrupted,
        "total": len(result_log),
        "passed": sum(1 for e in result_log if e.get("passed")),
        "failed": sum(1 for e in result_log if not e.get("passed")),
        "results": list(result_log),
    }
    # Write atomically via a temp file so a kill mid-write doesn't corrupt data.
    tmp_path = out_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(summary, indent=2, default=str))
    tmp_path.replace(out_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_failure_message(
    task: Dict[str, Any],
    agent_result: Optional[Dict[str, Any]],
    html_detail: Optional[Dict[str, Any]],
) -> str:
    ev = task.get("eval", {})
    lines = [
        f"Task {task['task_id']} FAILED",
        f"  intent     : {task['intent']}",
        f"  eval_types : {ev.get('eval_types', [])}",
    ]

    ra = ev.get("reference_answers") or {}
    if ra.get("must_include"):
        lines.append(f"  must_include : {ra['must_include']}")
    if ra.get("exact_match") is not None:
        lines.append(f"  exact_match  : {ra['exact_match']!r}")
    if ra.get("fuzzy_match") is not None:
        lines.append(f"  fuzzy_match  : {ra['fuzzy_match']!r}")
    if ev.get("string_note"):
        lines.append(f"  string_note  : {ev['string_note']}")
    if agent_result:
        lines.append(f"  agent_answer : {str(agent_result.get('answer', ''))[:300]!r}")

    if html_detail:
        top_error = html_detail.get("error")
        checks = html_detail.get("checks", [])
        if top_error and not checks:
            lines.append(f"  html_error   : {top_error}")
        for i, chk in enumerate(checks, 1):
            status = "PASS" if chk["passed"] else "FAIL"
            lines.append(f"  check {i} [{status}]: {chk.get('raw_url', '')}")
            if not chk["passed"]:
                if chk.get("missing"):
                    lines.append(f"    missing         : {chk['missing']}")
                if chk.get("excluded_found"):
                    lines.append(f"    excluded_found  : {chk['excluded_found']}")
                if chk.get("error"):
                    lines.append(f"    error           : {chk['error']}")
                if chk.get("extracted_content") is not None:
                    lines.append(f"    content_snippet : {str(chk['extracted_content'])[:300]!r}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_agent_accomplishes_gitlab_tasks(
    session_event_loop,
    acquire_lock,
    result_log,
    request,
) -> None:
    tasks = _load_tasks(request.config)
    force_reset = request.config.getoption("--force-reset", default=False)
    multi_docker = request.config.getoption("--multi-docker", default=False)
    base_url = request.config.getoption("--base-url") or _SERVER_URLS["gitlab"]
    output_name = request.config.getoption("--output", default=None)
    if not output_name:
        # Always save logs — auto-generate a timestamped filename when --output
        # is not explicitly provided so no run is ever lost.
        output_name = datetime.now(timezone.utc).strftime("gitlab_%Y%m%d_%H%M%S.json")
    detailed_out_path = LOGS_DIR / (Path(output_name).stem + "_detailed.jsonl")

    if multi_docker:
        n_workers = _workers_new.num_workers()
    else:
        n_workers = 1

    print(f"\nRunning {len(tasks)} tasks with {n_workers} workers")

    async def run_all() -> tuple:
        sem = asyncio.Semaphore(n_workers)
        remaining = len(tasks)
        # Lock protecting result_log mutations and incremental file flushes.
        record_lock = asyncio.Lock()
        failures: List[str] = []

        # Single-instance GitLab: mint one token up front and reuse it for every
        # task. The multi-docker path can't share a token this way — each worker
        # is a separate GitLab instance with its own database — so it mints per
        # instance inside worker_session instead.
        local_glpat: Optional[str] = None
        if not multi_docker and tasks:
            local_glpat = await _mint_local_glpat(base_url)

        async def run_one(task: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                try:
                    if multi_docker:
                        worker_ctx = _workers_new.worker_session(
                            str(task["task_id"]),
                            server="gitlab",
                            acquire_lock=acquire_lock,
                            read_only=True,
                        )
                    else:
                        worker_ctx = _local_session(base_url, glpat=local_glpat)

                    async with worker_ctx as w:
                        runner = AgentRunner(
                            headless=True,
                            enable_reset=True,
                            force_reset=force_reset,
                            gitlab_base_url=w["gitlab_url"],
                        )
                        runner.server = "gitlab"
                        runner.base_url = w["gitlab_url"]

                        await runner._init_agent()

                        if runner._agent.execution_agent is not None:
                            if w["glpat"]:
                                runner._agent.execution_agent.auth = StaticAuth(
                                    {"PRIVATE-TOKEN": w["glpat"]}
                                )
                            runner._agent.execution_agent.task_id = str(task["task_id"])

                        start_time = datetime.now(timezone.utc)
                        passed, agent_result, error, html_detail = await runner.run_agent_on_task(task)
                        end_time = datetime.now(timezone.utc)

                        details = extract_agent_details(runner)
                        status = task_status(passed, error, details["plan_steps"])

                        result = {
                            "task": task,
                            "passed": passed,
                            "agent_result": agent_result,
                            "error": error,
                            "html_detail": html_detail,
                            "plan": details["plan_steps"],
                            "parsed_outputs": details["parsed_outputs"],
                            "execution": details["raw_execution"],
                            "planning_log": details["planning_log"],
                            "worker_id": w["worker_id"],
                            "status": status,
                            "costs": details.get("costs"),
                            "tokens": details.get("tokens"),
                            "start_time": start_time,
                            "end_time": end_time,
                        }
                except Exception as e:
                    _now = datetime.now(timezone.utc)
                    result = {
                        "task": task,
                        "passed": False,
                        "agent_result": None,
                        "error": str(e),
                        "html_detail": None,
                        "plan": None,
                        "parsed_outputs": None,
                        "execution": None,
                        "worker_id": None,
                        "status": "failed",
                        "costs": [],
                        "tokens": None,
                        "start_time": _now,
                        "end_time": _now,
                    }

                # Record result and flush to disk immediately after each task.
                async with record_lock:
                    nonlocal remaining
                    remaining -= 1
                    task_obj = result["task"]
                    r_passed = result["passed"]
                    r_error = result["error"]
                    r_agent = result["agent_result"]

                    entry = {
                        "task_id":        task_obj["task_id"],
                        "intent":         task_obj.get("intent", ""),
                        "eval_types":     task_obj.get("eval", {}).get("eval_types", []),
                        "passed":         r_passed and not r_error,
                        "status":         result.get("status"),
                        "answer":         r_agent.get("answer") if r_agent else None,
                        "error":          r_error,
                        "worker_id":      result.get("worker_id"),
                        "plan":           result.get("plan"),
                        "plan_step_count": len(result["plan"]) if result.get("plan") else None,
                        "execution":      result.get("execution"),
                        "parsed_outputs": result.get("parsed_outputs"),
                        "planning_log":   result.get("planning_log"),
                    }
                    det_entry = build_detailed_entry(
                        task=task_obj,
                        agent_result=r_agent,
                        error=r_error,
                        correct=r_passed and not r_error,
                        start_time=result["start_time"],
                        end_time=result["end_time"],
                        eval_output_dir=str(LOGS_DIR / output_name),
                        costs=result.get("costs"),
                        tokens=result.get("tokens"),
                    )
                    flush_detailed_jsonl(detailed_out_path, det_entry)

                    result_log.append(entry)
                    _flush_results(output_name, result_log, interrupted=False)

                    outcome = "PASS" if r_passed and not r_error else "FAIL"
                    print(f"\n[{remaining} tasks remaining] Task {task_obj['task_id']} done ({outcome})")

                    if r_error:
                        failures.append(f"Task {task_obj['task_id']}: agent error: {r_error}")
                    elif not r_passed:
                        failures.append(_make_failure_message(task_obj, r_agent, result["html_detail"]))

                return result

        futures = [asyncio.ensure_future(run_one(t)) for t in tasks]
        try:
            all_results = list(await asyncio.gather(*futures))
            return all_results, failures, False
        except BaseException:
            print("\nInterrupted — cancelling remaining tasks and releasing workers...")
            for f in futures:
                f.cancel()
            all_results = await asyncio.gather(*futures, return_exceptions=True)
            partial = [r for r in all_results if isinstance(r, dict)]
            return partial, failures, True

    interrupted = False
    failures: List[str] = []
    try:
        _, failures, interrupted = session_event_loop.run_until_complete(run_all())
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt — collecting partial results...")
        for task in asyncio.all_tasks(session_event_loop):
            task.cancel()
        session_event_loop.run_until_complete(
            asyncio.gather(*asyncio.all_tasks(session_event_loop), return_exceptions=True)
        )
        interrupted = True

    if interrupted:
        _flush_results(output_name, result_log, interrupted=True)
        n = len(result_log)
        if output_name and n:
            print(f"\n📄 Partial results ({n} tasks) written to {LOGS_DIR / output_name}")
        pytest.exit("Interrupted by user", returncode=1)

    # Final flush (marks interrupted=False).
    _flush_results(output_name, result_log, interrupted=False)
    print(f"\n📄 Results saved to {LOGS_DIR / output_name}")

    passed_count = sum(1 for e in result_log if e.get("passed"))
    total = len(result_log)
    print(f"Results: {passed_count}/{total} passed, {total - passed_count}/{total} failed")

    if failures:
        pytest.fail("\n\n".join(failures))
