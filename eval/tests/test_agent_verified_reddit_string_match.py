# eval/tests/test_agent_verified_reddit_string_match.py
#
# Integration tests: run the agent on every task in
# reddit_verified_string_match.json (string_match eval type).
#
# Run all tasks:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_string_match.py -v
#
# Smoke test (first 5 tasks):
#   python3 -m pytest eval/tests/test_agent_verified_reddit_string_match.py --task-limit 5 -v -s
#
# Single task by ID:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_string_match.py --task-id 389 -v -s
#
# Multiple tasks by ID:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_string_match.py --task-id 389,412 -v -s
#
# Save results to a JSON log:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_string_match.py -v --output reddit_string_match_results.json
#
# Force-reset Reddit state before every task:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_string_match.py -v --force-reset
#
# Use multi-docker worker pool:
#   python3 -m pytest eval/tests/test_agent_verified_reddit_string_match.py -v --multi-docker

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agent.baya_json_agent.auth import StaticAuth
from config.init_tokens.refresh_reddit_session import refresh_session as _refresh_reddit_session
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

TASK_FILE = Path(__file__).parent / "test_files" / "reddit_verified_string_match.json"
LOGS_DIR = Path(__file__).parent / "logs"


@asynccontextmanager
async def _local_session(reddit_url: str):
    """Stub worker session for a single local Reddit instance."""
    yield {"worker_id": "local", "reddit_url": reddit_url}


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
    site = task.get("sites", ["reddit"])[0]
    return f"task_{task['task_id']}_{site}"


def pytest_generate_tests(metafunc):
    if "task" in metafunc.fixturenames:
        tasks = _get_tasks(metafunc.config)
        metafunc.parametrize("task", tasks, ids=[_task_id(t) for t in tasks])


@pytest.fixture(scope="session", autouse=True)
def _inject_reddit_token(agent_runner, request):
    """
    Fetch a fresh Reddit PHPSESSID once at session start and inject it into
    the shared agent runner as a StaticAuth cookie header.

    The Postmill Reddit clone uses cookie-based session auth (PHPSESSID).
    All API calls also need the X-Experimental-API header to access the
    JSON API endpoints used by the MCP server.
    """
    # Two URLs in play. Session refresh has to hit Postmill (where the login
    # form lives); the agent's tool calls have to hit the FastAPI MCP server
    # (which exposes /list_forums, /create_post, …). Don't conflate them.
    postmill_url = (
        request.config.getoption("--base-url", default=None)
        or _SERVER_URLS["reddit"]
    )
    mcp_url = _SERVER_URLS["reddit_extra"]
    if agent_runner._agent is None or agent_runner._agent.execution_agent is None:
        raise RuntimeError(
            "execution_agent is not initialised — cannot inject Reddit token. "
            "Ensure _init_agent() completed successfully before this fixture runs."
        )
    print(f"\nRefreshing Reddit session from {postmill_url} ...")
    phpsessid = _refresh_reddit_session(base_url=postmill_url)
    agent_runner._agent.execution_agent.auth = StaticAuth({
        "Cookie": f"PHPSESSID={phpsessid}",
        "X-Experimental-API": "1",
    })
    agent_runner.server = "reddit"
    agent_runner.base_url = mcp_url
    print(f"  Reddit PHPSESSID injected (prefix={phpsessid[:8]}...); agent target {mcp_url}")


def _make_failure_message(
    task: Dict[str, Any],
    agent_result: Optional[Dict[str, Any]],
) -> str:
    ev = task.get("eval", {})
    ra = ev.get("reference_answers", {})
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
    base_url = request.config.getoption("--base-url", default=None) or _SERVER_URLS["reddit"]

    output_name = request.config.getoption("--output", default=None)
    if not output_name:
        output_name = "reddit_string_match_results.json"
    out_path = LOGS_DIR / output_name
    detailed_out_path = LOGS_DIR / (Path(output_name).stem + "_detailed.jsonl")

    if multi_docker:
        n_workers = _workers_new.num_workers()
        single_phpsessid = None
    else:
        n_workers = 1
        print(f"\nRefreshing Reddit session from {base_url} ...")
        single_phpsessid = _refresh_reddit_session(base_url=base_url)
        print(f"  PHPSESSID refreshed (prefix={single_phpsessid[:8]}...)")

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
                            server="reddit",
                            acquire_lock=acquire_lock,
                            read_only=task.get("read_only", True),
                        )
                    else:
                        worker_ctx = _local_session(base_url)

                    async with worker_ctx as w:
                        runner = AgentRunner(headless=True, enable_reset=False, force_reset=False)
                        runner.server = "reddit"
                        runner.base_url = w["reddit_url"]
                        # Uniform with the other reddit suites; unused here (no
                        # program_html grading) but keeps the attribute consistent.
                        runner.reddit_base_url = w["reddit_url"]

                        await runner._init_agent()

                        if runner._agent.execution_agent is not None:
                            phpsessid = (
                                await asyncio.to_thread(_refresh_reddit_session, base_url=w["reddit_url"])
                                if multi_docker else single_phpsessid
                            )
                            runner._agent.execution_agent.auth = StaticAuth({
                                "Cookie": f"PHPSESSID={phpsessid}",
                                "X-Experimental-API": "1",
                            })
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
