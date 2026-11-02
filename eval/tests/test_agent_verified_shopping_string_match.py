# eval/tests/test_agent_shopping_string_match.py
#
# Integration tests: run the agent on every task in
# shopping_verified_string_match.json (130 verified string_match tasks).
#
# Tasks run concurrently up to num_workers() at a time when --multi-docker
# is set; otherwise a single worker is used.
#
# Run all 130 tasks:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_string_match.py -v --server shopping
#
# Smoke test (first 5 tasks):
#   python3 -m pytest eval/tests/test_agent_verified_shopping_string_match.py --task-limit 5 -v -s --server shopping
#
# Single task by ID:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_string_match.py -k "task_21" -v -s --server shopping
#
# Multiple tasks by ID:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_string_match.py --task-id 21,42 -v -s --server shopping
#
# Save results to a JSON log:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_string_match.py -v --server shopping --output shopping_string_match_results.json
#
# Force-reset shopping state before every write task:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_string_match.py -v --server shopping --force-reset
#
# Use multi-docker worker pool:
#   python3 -m pytest eval/tests/test_agent_verified_shopping_string_match.py -v --server shopping --multi-docker
#
# Enable agent trace (print curl commands and raw responses):
#   python3 -m pytest eval/tests/test_agent_verified_shopping_string_match.py -v --server shopping --agent-trace

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agent.baya_json_agent.auth import RefreshableAuth
from config.init_tokens.refresh_shopping_tokens import (
    refresh_tokens as _refresh_shopping_admin_tokens,
    write_admin_token_to_env as _persist_admin_token,
)
from config.servers import SERVER_URLS as _SERVER_URLS
from eval.docker import workers_new as _workers_new
from eval.run_pte_benchmark import AgentRunner
from eval.tests.agent_test_utils import (
    build_detailed_entry,
    extract_agent_details,
    flush_detailed_jsonl,
    get_model_id,
)

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TASK_FILE = Path(__file__).parent / "test_files" / "shopping_verified_string_match.json"
LOGS_DIR = Path(__file__).parent / "logs"


@asynccontextmanager
async def _local_session(shopping_url: str):
    """Stub worker session for a single local shopping instance."""
    yield {"worker_id": "local", "shopping_url": shopping_url}


def _load_tasks() -> List[Dict[str, Any]]:
    with open(TASK_FILE) as f:
        return json.load(f)


def _get_tasks(config=None) -> List[Dict[str, Any]]:
    tasks = _load_tasks()
    if config is not None:
        task_id_opt = config.getoption("--task-id", default=None)
        if task_id_opt:
            ids = {int(x.strip()) for x in task_id_opt.split(",")}
            tasks = [t for t in tasks if t["task_id"] in ids]
        else:
            resume = config.getoption("--resume", default=False)
            output_name = config.getoption("--output", default=None)
            if resume and output_name:
                out_path = LOGS_DIR / output_name
                if out_path.exists():
                    prior = json.loads(out_path.read_text())
                    done_ids = {r["task_id"] for r in prior.get("results", [])}
                    before = len(tasks)
                    tasks = [t for t in tasks if t["task_id"] not in done_ids]
                    print(f"\n--resume: skipping {before - len(tasks)} already-completed tasks, {len(tasks)} remaining")
            limit = config.getoption("--task-limit", default=None)
            if limit is not None:
                tasks = tasks[:limit]
    return tasks


ALL_TASKS: List[Dict[str, Any]] = _load_tasks()


def _task_id(task: Dict[str, Any]) -> str:
    site = task.get("sites", ["shopping"])[0]
    return f"task_{task['task_id']}_{site}"


def pytest_generate_tests(metafunc):
    if "task" in metafunc.fixturenames:
        tasks = _get_tasks(metafunc.config)
        metafunc.parametrize("task", tasks, ids=[_task_id(t) for t in tasks])


def _make_failure_message(
    task: Dict[str, Any],
    agent_result: Optional[Dict[str, Any]],
) -> str:
    ev = task.get("eval", {})
    ra = ev.get("reference_answers") or {}  # program_html tasks carry null here
    lines = [
        f"Task {task['task_id']} FAILED",
        f"  intent       : {task['intent']}",
        f"  sites        : {task.get('sites', [])}",
        f"  eval_types   : {ev.get('eval_types', [])}",
    ]
    if ra.get("must_include"):
        lines.append(f"  must_include : {ra['must_include']}")
    if ra.get("must_exclude"):
        lines.append(f"  must_exclude : {ra['must_exclude']}")
    if ra.get("exact_match"):
        lines.append(f"  exact_match  : {ra['exact_match']!r}")
    if ra.get("fuzzy_match"):
        lines.append(f"  fuzzy_match  : {ra['fuzzy_match']!r}")
    if ev.get("string_note"):
        lines.append(f"  string_note  : {ev['string_note']}")
    if agent_result is not None:
        answer = agent_result.get("answer", "")
        snippet = str(answer)[:400] if answer else "(empty)"
        lines.append(f"  agent_answer : {snippet!r}")
        final_url = agent_result.get("final_url")
        if final_url:
            lines.append(f"  final_url    : {final_url}")
        must_include = ra.get("must_include", [])
        if must_include and answer:
            answer_lower = str(answer).lower()
            missing = [item for item in must_include
                       if isinstance(item, str) and item.lower() not in answer_lower]
            if missing:
                lines.append(f"  missing items: {missing}")
    return "\n".join(lines)


def _flush_result(out_path: Path, entry: dict) -> None:
    """Append a single completed task result to the output file immediately."""
    import os
    import tempfile
    from datetime import datetime, timezone

    out_path.parent.mkdir(exist_ok=True)

    summary = {"results": []}
    if out_path.exists():
        try:
            summary = json.loads(out_path.read_text())
        except Exception:
            pass

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


def test_agent_produces_correct_answer(
    session_event_loop,
    acquire_lock,
    result_log,
    request,
) -> None:
    tasks = _get_tasks(request.config)
    multi_docker = request.config.getoption("--multi-docker", default=False)
    base_url = request.config.getoption("--base-url", default=None) or _SERVER_URLS["shopping"]

    output_name = request.config.getoption("--output", default=None)
    if not output_name:
        output_name = "shopping_string_match_results.json"
    out_path = LOGS_DIR / output_name
    detailed_out_path = LOGS_DIR / (Path(output_name).stem + "_detailed.jsonl")

    if multi_docker:
        n_workers = _workers_new.num_workers()
        single_admin_token = None
    else:
        n_workers = 1
        print(f"\nRefreshing shopping auth tokens from {base_url} ...")
        single_admin_token = _refresh_shopping_admin_tokens(base_url=base_url)
        os.environ["ADMIN_AUTH_TOKEN"] = single_admin_token
        # Persist the fresh token so the program_html evaluator (which reads
        # ADMIN_AUTH_TOKEN from config/.server_env) doesn't fall through to a
        # stale cached value and get 401s on its lookup calls.
        _persist_admin_token(single_admin_token)
        print(f"  Admin token refreshed (length={len(single_admin_token)})")

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
                        runner = AgentRunner(headless=True, enable_reset=False, force_reset=False)
                        runner.server = "shopping"
                        runner.base_url = w["shopping_url"]

                        await runner._init_agent()

                        if runner._agent.execution_agent is not None:
                            if multi_docker:
                                admin_token = await asyncio.to_thread(
                                    _refresh_shopping_admin_tokens, base_url=w["shopping_url"]
                                )
                                await asyncio.to_thread(_persist_admin_token, admin_token)
                            else:
                                admin_token = single_admin_token

                            _base = w["shopping_url"]
                            runner._agent.execution_agent.auth = RefreshableAuth(
                                initial_token=admin_token,
                                refresh_fn=lambda: _refresh_shopping_admin_tokens(base_url=_base),
                            )
                            runner._agent.execution_agent.task_id = str(task["task_id"])

                        start_time = datetime.now(timezone.utc)
                        passed, agent_result, error, _html_detail = await runner.run_agent_on_task(task)
                        end_time = datetime.now(timezone.utc)

                        async with remaining_lock:
                            nonlocal remaining
                            remaining -= 1
                            outcome = "PASS" if passed and not error else "FAIL"
                            print(f"\n[{remaining} tasks remaining] Task {task['task_id']} done ({outcome})")

                        details = extract_agent_details(runner)
                        result = {
                            "task": task,
                            "passed": passed,
                            "agent_result": agent_result,
                            "error": error,
                            "worker_id": w["worker_id"],
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
                        "worker_id": None,
                        "costs": [],
                        "tokens": None,
                        "start_time": _now,
                        "end_time": _now,
                    }

                entry = {
                    "task_id":    task["task_id"],
                    "intent":     task.get("intent", ""),
                    "sites":      task.get("sites", []),
                    "eval_types": task.get("eval", {}).get("eval_types", []),
                    "passed":     result["passed"] and not result["error"],
                    "answer":     result["agent_result"].get("answer") if result["agent_result"] else None,
                    "error":      result["error"],
                }
                det_entry = build_detailed_entry(
                    task=result["task"],
                    agent_result=result["agent_result"],
                    error=result["error"],
                    correct=result["passed"] and not result["error"],
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

        result_log.append({
            "task_id":    task["task_id"],
            "intent":     task.get("intent", ""),
            "sites":      task.get("sites", []),
            "eval_types": task.get("eval", {}).get("eval_types", []),
            "passed":     passed and not error,
            "answer":     r["agent_result"].get("answer") if r["agent_result"] else None,
            "error":      error,
        })

        if error:
            failures.append(f"Task {task['task_id']}: agent returned an error: {error}")
        elif not passed:
            failures.append(_make_failure_message(task, r["agent_result"]))

    if interrupted:
        print(f"\nResults saved to {out_path} — re-run with --resume to continue.")
        pytest.exit("Interrupted by user", returncode=1)

    total = len(results)
    passed_count = sum(1 for r in results if r["passed"] and not r["error"])
    failed_count = total - passed_count
    print(f"\nResults: {passed_count}/{total} passed, {failed_count}/{total} failed")

    if failures:
        pytest.fail("\n\n".join(failures))
