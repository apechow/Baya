# eval/tests/test_agent_shopping_program_html.py
#
# Integration tests: run the agent on every shopping program_html task in
# shopping_program_html_verified.json (51 tasks).
#
# Excluded task IDs (not in this file): 118, 528-532
#   528-532: malformed/superseded by 653-657 (inconsistent price vs SKU checks)
#   118:     open-ended navigation task with ambiguous eval
#
# 585-589 (rate a product) are INCLUDED — func:shopping_get_sku_latest_review_rating/author
#   locators are fully implemented in ProgramHtmlEvaluator.
#
# Tasks run concurrently up to num_workers() at a time when --multi-docker
# is set; otherwise a single worker is used.
#
# Run all 57 tasks:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_program_html.py -v
#
# Smoke test (first 5 tasks):
#   python3 -m pytest eval/tests/test_agent_verified_shopping_program_html.py --task-limit 5 -v -s
#
# Single task by ID:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_program_html.py --task-id 465 -v -s
#
# Multiple tasks by ID:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_program_html.py --task-id 465,521,571 -v -s
#
# Save results to a JSON log:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_program_html.py -v --output shopping_program_html_results.json
#
# Force-reset shopping state before every write task:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_program_html.py -v --force-reset
#
# Use multi-docker worker pool:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_program_html.py -v --multi-docker
#
# Plug in a custom agent runner:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_program_html.py \
#       --agent-runner my_agent_runner.MyAgentRunner -v -s
#
# Enable agent trace (print curl commands and raw responses):
#   python3 -m pytest eval/tests/test_agent_verified_shopping_program_html.py --agent-trace -v -s

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agent.baya_json_agent.auth import RefreshableAuth
from config.servers import SERVER_URLS as _SERVER_URLS
from config.init_tokens.refresh_shopping_tokens import (
    refresh_tokens as _refresh_shopping_admin_tokens,
    write_admin_token_to_env as _persist_admin_token,
)
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
async def _local_session(shopping_url: str):
    """Stub worker session for a single local shopping instance."""
    yield {"worker_id": "local", "shopping_url": shopping_url}


PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _build_log_entry(r: dict) -> dict:
    """Convert a run_one() result dict into the flat log-entry shape."""
    task = r["task"]
    return {
        "task_id":         task["task_id"],
        "intent":          task.get("intent", ""),
        "eval_types":      task.get("eval", {}).get("eval_types", []),
        "read_only":       task.get("read_only"),
        "passed":          bool(r["passed"]) and not r["error"],
        "status":          r.get("status"),
        "answer":          r["agent_result"].get("answer") if r["agent_result"] else None,
        "error":           r["error"],
        "worker_id":       r.get("worker_id"),
        "plan":            r.get("plan"),
        "plan_step_count": len(r["plan"]) if r.get("plan") else None,
        "execution":       r.get("execution"),
        "parsed_outputs":  r.get("parsed_outputs"),
        "planning_log":    r.get("planning_log"),
    }


def _flush_result(out_path: Path, entry: dict) -> None:
    """Append a single completed task result to the output file immediately.

    Reads the existing file (if any), upserts the entry by task_id, and
    writes back atomically via a temp file so a crash mid-write never
    corrupts the log.
    """
    from datetime import datetime, timezone
    import tempfile, os

    out_path.parent.mkdir(exist_ok=True)

    # Load existing summary or start fresh
    summary = {"results": []}
    if out_path.exists():
        try:
            summary = json.loads(out_path.read_text())
        except Exception:
            pass

    # Upsert: replace existing entry for this task_id, or append
    results = summary.get("results", [])
    results = [r for r in results if r.get("task_id") != entry["task_id"]]
    results.append(entry)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": get_model_id(),
        "total":  len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "failed": sum(1 for r in results if not r.get("passed")),
        "results": results,
    }

    # Atomic write: write to a sibling temp file then rename
    tmp_fd, tmp_path = tempfile.mkstemp(dir=out_path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(summary, f, indent=2)
        os.replace(tmp_path, out_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

TASK_FILE = Path(__file__).parent / "test_files" / "shopping_verified_program_html.json"


def _load_tasks(config=None) -> List[Dict[str, Any]]:
    tasks = json.loads(TASK_FILE.read_text())
    if config is not None:
        task_id = config.getoption("--task-id", default=None)
        if task_id is not None:
            ids = {int(x.strip()) for x in task_id.split(",")}
            tasks = [t for t in tasks if t.get("task_id") in ids]
        else:
            limit = config.getoption("--task-limit", default=None)
            if limit is not None:
                tasks = tasks[:limit]

        # --resume: skip tasks that already have results in the output file
        resume = config.getoption("--resume", default=False)
        output_name = config.getoption("--output", default=None)
        if resume and output_name:
            logs_dir = Path(__file__).parent / "logs"
            out_path = logs_dir / output_name
            if out_path.exists():
                prior = json.loads(out_path.read_text())
                done_ids = {r["task_id"] for r in prior.get("results", [])}
                before = len(tasks)
                tasks = [t for t in tasks if t["task_id"] not in done_ids]
                print(f"\n--resume: skipping {before - len(tasks)} already-completed tasks, {len(tasks)} remaining")
    return tasks


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
        f"  read_only  : {task.get('read_only')}",
    ]

    ra = ev.get("reference_answers") or {}
    if ra:
        if ra.get("must_include"):
            lines.append(f"  must_include : {ra['must_include']}")
        if ra.get("must_exclude"):
            lines.append(f"  must_exclude : {ra['must_exclude']}")
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

def test_agent_accomplishes_shopping_tasks(
    session_event_loop,
    acquire_lock,
    result_log,
    request,
) -> None:
    tasks = _load_tasks(request.config)
    force_reset = request.config.getoption("--force-reset", default=False)
    multi_docker = request.config.getoption("--multi-docker", default=False)
    base_url = request.config.getoption("--base-url") or _SERVER_URLS["shopping"]
    debug = request.config.getoption("--agent-trace", default=False)

    # Resolve output path once — _flush_result writes here after every task.
    # Always save results: use --output filename if provided, otherwise default.
    output_name = request.config.getoption("--output", default=None)
    if not output_name:
        output_name = "shopping_program_html_results.json"
    _logs_dir = Path(__file__).parent / "logs"
    out_path = _logs_dir / output_name
    detailed_out_path = _logs_dir / (Path(output_name).stem + "_detailed.jsonl")

    if multi_docker:
        n_workers = _workers_new.num_workers()
    else:
        n_workers = 1

    single_admin_token: Optional[str] = None
    if not multi_docker:
        print("Refreshing shopping auth tokens...")
        single_admin_token = _refresh_shopping_admin_tokens(base_url=base_url)
        # Persist the fresh token so the program_html evaluator (which reads
        # ADMIN_AUTH_TOKEN from config/.server_env) doesn't fall through to a
        # stale cached value and get 401s on its review-lookup calls.
        _persist_admin_token(single_admin_token)

    print(f"\nRunning {len(tasks)} tasks with {n_workers} workers")
    print(f"Results written incrementally to {out_path} — safe to Ctrl+C and resume with --resume")

    async def run_all() -> list:
        sem = asyncio.Semaphore(n_workers)
        write_lock = asyncio.Lock()
        remaining = len(tasks)
        remaining_lock = asyncio.Lock()

        async def run_one(task: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                try:
                    if multi_docker:
                        worker_ctx = _workers_new.worker_session(
                            str(task["task_id"]),
                            server="shopping",
                            acquire_lock=acquire_lock,
                            read_only=task.get("read_only", True),
                        )
                    else:
                        worker_ctx = _local_session(base_url)

                    async with worker_ctx as w:
                        runner = AgentRunner(headless=True, enable_reset=False, force_reset=False, debug=debug)
                        runner.server = "shopping"
                        runner.base_url = w["shopping_url"]

                        await runner._init_agent()

                        if runner._agent.execution_agent is not None:
                            if multi_docker:
                                admin_token = _refresh_shopping_admin_tokens(base_url=runner.base_url)
                                _persist_admin_token(admin_token)
                            else:
                                admin_token = single_admin_token

                            _base = runner.base_url
                            runner._agent.execution_agent.auth = RefreshableAuth(
                                initial_token=admin_token,
                                refresh_fn=lambda: _refresh_shopping_admin_tokens(base_url=_base),
                            )
                            runner._agent.execution_agent.task_id = str(task["task_id"])

                        run_task = task
                        if force_reset and not task.get("read_only", False):
                            run_task = {**task, "require_reset": True}

                        start_time = datetime.now(timezone.utc)
                        passed, agent_result, error, html_detail = await runner.run_agent_on_task(run_task)
                        end_time = datetime.now(timezone.utc)

                        async with remaining_lock:
                            nonlocal remaining
                            remaining -= 1
                            print(f"\n[{remaining} tasks remaining] Task {task['task_id']} done ({'PASS' if passed and not error else 'FAIL'})")

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

                # Flush to disk immediately — safe under the write_lock so
                # concurrent workers don't interleave writes.
                entry = _build_log_entry(result)
                det_entry = build_detailed_entry(
                    task=result["task"],
                    agent_result=result["agent_result"],
                    error=result["error"],
                    correct=bool(result["passed"]) and not result["error"],
                    start_time=result["start_time"],
                    end_time=result["end_time"],
                    eval_output_dir=str(out_path),
                    costs=result.get("costs"),
                    tokens=result.get("tokens"),
                )
                async with write_lock:
                    await asyncio.get_event_loop().run_in_executor(
                        None, _flush_result, out_path, entry
                    )
                    await asyncio.get_event_loop().run_in_executor(
                        None, flush_detailed_jsonl, detailed_out_path, det_entry
                    )

                return result

        futures = [asyncio.ensure_future(run_one(t)) for t in tasks]
        try:
            return list(await asyncio.gather(*futures))
        except BaseException:
            print("\nInterrupted — cancelling remaining tasks...")
            for f in futures:
                f.cancel()
            all_results = await asyncio.gather(*futures, return_exceptions=True)
            # Return only tasks that completed before the interrupt;
            # the rest are already on disk from prior flushes.
            return [r for r in all_results if isinstance(r, dict)]

    try:
        results = session_event_loop.run_until_complete(run_all())
        interrupted = False
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt — completed tasks already saved to disk.")
        interrupted = True
        results = []

    failures = []
    for r in results:
        task = r["task"]
        passed = r["passed"]
        error = r["error"]

        result_log.append(_build_log_entry(r))

        if error:
            failures.append(f"Task {task['task_id']}: agent error: {error}")
        elif not passed:
            failures.append(_make_failure_message(task, r["agent_result"], r["html_detail"]))

    if interrupted:
        print(f"\n📄 Results saved to {out_path} — re-run with --resume to continue.")
        pytest.exit("Interrupted by user", returncode=1)

    total = len(results)
    passed_count = sum(1 for r in results if r["passed"] and not r["error"])
    failed_count = total - passed_count
    print(f"\nResults: {passed_count}/{total} passed, {failed_count}/{total} failed")

    if failures:
        pytest.fail("\n\n".join(failures))
