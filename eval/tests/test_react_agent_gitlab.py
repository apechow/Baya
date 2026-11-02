# eval/tests/test_react_agent_gitlab.py
#
# Integration tests: run the ReactAgent (ReAct loop) on GitLab tasks.
# Mirrors test_agent_verified_all_gitlab.py but uses ReactAgentRunner.
#
# Smoke test (first 5 tasks):
#   python3 -m pytest eval/tests/test_react_agent_gitlab.py --task-limit 5 -v -s
#
# Single task by ID:
#   python3 -m pytest eval/tests/test_react_agent_gitlab.py --task-id 389 -v -s
#
# Multiple tasks by ID:
#   python3 -m pytest eval/tests/test_react_agent_gitlab.py --task-id 389,412,500 -v -s
#
# Save results:
#   python3 -m pytest eval/tests/test_react_agent_gitlab.py --output react_gitlab.json -v
#
# Multi-docker (parallel workers):
#   python3 -m pytest eval/tests/test_react_agent_gitlab.py --multi-docker -v
#
# Force-reset GitLab state before every task:
#   python3 -m pytest eval/tests/test_react_agent_gitlab.py --force-reset -v

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from config.servers import SERVER_URLS as _SERVER_URLS
from eval.docker import workers_new as _workers_new
from eval.tests.agent_test_utils import (
    build_detailed_entry,
    flush_detailed_jsonl,
    get_model_id,
    task_status,
)
from react_agent.react_web.react_agent_runner import ReactAgentRunner

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

TASK_FILE = Path(__file__).parent / "test_files" / "gitlab_verified_string_match.json"
TASK_FILE2 = Path(__file__).parent / "test_files" / "gitlab_verified_program_html.json"

LOGS_DIR = Path(__file__).parent / "logs"


@asynccontextmanager
async def _local_session(gitlab_url: str, glpat=None):
    yield {"worker_id": "local", "gitlab_url": gitlab_url, "glpat": glpat}


def _load_completed_ids(output_name: Optional[str]) -> set:
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
    if not output_name:
        return
    LOGS_DIR.mkdir(exist_ok=True)
    out_path = LOGS_DIR / output_name
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": get_model_id(),
        "interrupted": interrupted,
        "total": len(result_log),
        "passed": sum(1 for e in result_log if e.get("passed")),
        "failed": sum(1 for e in result_log if not e.get("passed")),
        "results": list(result_log),
    }
    tmp_path = out_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(summary, indent=2))
    tmp_path.replace(out_path)


# ---------------------------------------------------------------------------
# Failure message helper
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

def test_react_agent_accomplishes_gitlab_tasks(
    session_event_loop,
    acquire_lock,
    request,
) -> None:
    result_log: List[Dict[str, Any]] = []
    tasks = _load_tasks(request.config)
    force_reset = request.config.getoption("--force-reset", default=False)
    multi_docker = request.config.getoption("--multi-docker", default=False)
    base_url = request.config.getoption("--base-url") or _SERVER_URLS["gitlab"]
    output_name = request.config.getoption("--output", default=None)
    if not output_name:
        output_name = datetime.now(timezone.utc).strftime("react_gitlab_%Y%m%d_%H%M%S.json")
    detailed_out_path = LOGS_DIR / (Path(output_name).stem + "_detailed.jsonl")

    if multi_docker:
        n_workers = _workers_new.num_workers()
    else:
        n_workers = 1

    print(f"\nRunning {len(tasks)} tasks with {n_workers} workers (ReactAgent)")

    async def run_all() -> tuple:
        sem = asyncio.Semaphore(n_workers)
        remaining = len(tasks)
        record_lock = asyncio.Lock()
        failures: List[str] = []

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
                        worker_ctx = _local_session(base_url, glpat=None)

                    async with worker_ctx as w:
                        runner = ReactAgentRunner(
                            headless=True,
                            enable_reset=True,
                            force_reset=force_reset,
                            gitlab_base_url=w["gitlab_url"],
                            max_iterations=30,
                        )
                        runner.server = "gitlab"
                        runner.base_url = w["gitlab_url"]
                        if w.get("glpat"):
                            runner.glpat = w["glpat"]

                        await runner._init_agent()

                        start_time = datetime.now(timezone.utc)
                        passed, agent_result, error, html_detail = await runner.run_agent_on_task(task)
                        end_time = datetime.now(timezone.utc)

                        plan_steps = runner._last_steps
                        status = task_status(passed, error, plan_steps)
                        llm = getattr(getattr(runner, "_react_agent", None), "llm", None)
                        task_cost = getattr(llm, "total_cost", None)
                        task_tokens = getattr(llm, "token_usage", None)

                        result = {
                            "task": task,
                            "passed": passed,
                            "agent_result": agent_result,
                            "error": error,
                            "html_detail": html_detail,
                            "plan": plan_steps,
                            "parsed_outputs": None,
                            "execution": None,
                            "planning_log": None,
                            "worker_id": w["worker_id"],
                            "status": status,
                            "costs": [task_cost] if task_cost is not None else [],
                            "tokens": task_tokens,
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
                        "worker_id": None,
                        "status": "failed",
                        "costs": [],
                        "tokens": None,
                        "start_time": _now,
                        "end_time": _now,
                    }

                async with record_lock:
                    nonlocal remaining
                    remaining -= 1
                    task_obj = result["task"]
                    r_passed = result["passed"]
                    r_error = result["error"]
                    r_agent = result["agent_result"]

                    entry = {
                        "task_id":         task_obj["task_id"],
                        "intent":          task_obj.get("intent", ""),
                        "eval_types":      task_obj.get("eval", {}).get("eval_types", []),
                        "passed":          r_passed and not r_error,
                        "status":          result.get("status"),
                        "answer":          r_agent.get("answer") if r_agent else None,
                        "error":           r_error,
                        "worker_id":       result.get("worker_id"),
                        "plan":            result.get("plan"),
                        "plan_step_count": len(result["plan"]) if result.get("plan") else None,
                        "execution":       result.get("execution"),
                        "parsed_outputs":  result.get("parsed_outputs"),
                        "planning_log":    result.get("planning_log"),
                    }
                    result_log.append(entry)
                    _flush_results(output_name, result_log, interrupted=False)

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

                    outcome = "PASS" if r_passed and not r_error else "FAIL"
                    m = det_entry["metrics"]
                    print(
                        f"\n[{remaining} tasks remaining] Task {task_obj['task_id']} done ({outcome}) "
                        f"| {m['duration_seconds']:.1f}s | tokens in={m['input_tokens']} out={m['output_tokens']} "
                        f"({m['n_llm_calls']} calls)"
                    )

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

    _flush_results(output_name, result_log, interrupted=False)
    print(f"\n📄 Results saved to {LOGS_DIR / output_name}")

    passed_count = sum(1 for e in result_log if e.get("passed"))
    total = len(result_log)
    print(f"Results: {passed_count}/{total} passed, {total - passed_count}/{total} failed")

    if failures:
        pytest.fail("\n\n".join(failures))
