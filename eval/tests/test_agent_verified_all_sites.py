# eval/tests/test_agent_verified_all_sites.py
#
# Integration tests: run every verified task for a single site
# (string_match + program_html) through the Claude-Code-codegen pipeline
# (ClaudeCodeAgentRunner — see eval/claude_code_agent_runner.py): for each
# task, agent/baya_code_agent/codegen/run_claude_task.sh has Claude Code write an implementation
# script into generated/<site>/task_<id>/ (reused on later runs unless
# --regenerate-code is passed), then claude_code_agent/baya_json_agent/execution_agent.py
# executes that script live against the site. The answer that gets graded is
# the result the script itself prints — the text inside its FINAL ANSWER block
# — so e.g. task 21's answer is whatever generated/shopping/task_21/task_21.py
# produced on that live run, not the Claude Code session's prose report.
#
# Evaluation (string_match / program_html) and the gitlab state-reset step
# are unchanged — inherited from BaseAgentRunner exactly as in the older
# LLM-planner/executor pipeline.
#
# gitlab, reddit, shopping and shopping_admin are all supported.
#
# Tasks tagged with more than one site (none exist today, but the schema
# allows it) are filtered out — only tasks whose "sites" list is exactly
# [site] are run.
#
# --site is required. One of: gitlab, reddit, shopping, shopping_admin.
#
# Run all tasks for a site:
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site gitlab -v
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site reddit -v
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site shopping -v
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site shopping_admin -v
#
# reddit note: reddit is the one site whose generated scripts do not call the site
# itself. api/schemas/reddit_api_schema.json documents a companion FastAPI server
# (api/servers/reddit.py, port 7791, exported as REDDIT_EXTRA_DOMAIN) that drives a
# headless browser, because Postmill has no API of its own. Two consequences:
#   - That one server process resolves base_url per request (api/reddit_pw/constants.py)
#     and caches a session per instance, so --multi-docker IS supported: each worker's
#     writes AND its program_html grading (the generated scripts pass base_url; the
#     login step _login_for_task in eval/run_pte_benchmark.py pins /session to the
#     worker via reddit_base_url) target that worker's own Postmill instance. Its
#     between-task reset is the orchestrator's clean-image recreate on release.
#   - Run initialize.py first. Both the generated scripts and the program_html login
#     step (_login_for_task in eval/run_pte_benchmark.py) need port 7791 up.
# reddit also has no per-task undo, and all 95 of its program_html tasks are writes,
# so each write task is followed by a whole-container restore from a clean image
# (eval/reddit_state_reset.py, ~15-20s). Disable with --no-reddit-restore; results
# after a failed restore are contaminated and the run says so.
#
# shopping_admin note: its program_html tasks mutate the admin database and no
# state reset exists for it, so a single-instance run is one-shot — several
# tasks are relative ("reduce the price by $5") and fail on a second pass
# against already-modified data. Restore the database between full runs, or use
# --multi-docker --restart-on-release.
#
# Smoke test (first 5 tasks):
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site shopping --task-limit 5 -v -s
#
# Single task by ID (reuses generated/shopping/task_21 if it already exists):
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site shopping --task-id 21 -v -s
#
# Force a fresh Claude Code codegen session instead of reusing generated code:
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site gitlab --task-id 45 -v -s --regenerate-code
#
# Start from a given task and run every task from there onward (in loaded order:
# string_match tasks then program_html). Use this to pick a run back up after it
# died partway — e.g. on an API quota limit — without re-running earlier tasks:
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site shopping --start 368 -v -s
#
# Replan-and-retry (--max-attempts, default 3): when a generated script fails to
# execute — non-zero exit or timeout — Claude Code is re-run in replan mode
# (agent/baya_code_agent/codegen/run_claude_task.sh <site> <id> replan). That session reads the task's
# own python_output.log and claude_output.log, diagnoses why the run failed, and
# repairs task_<id>.py; the repaired script is then run again, up to --max-attempts
# runs in total. Only execution failures are retried: a task that printed an answer
# is never re-run, even if that answer graded wrong, since the replan cannot see the
# reference answer and retrying would only fit the grader. The pre-task state reset
# runs before the first attempt only. Each replan costs a full codegen session
# (~2.5 min), so a pathological task can take ~45 min at the default. Disable with:
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site gitlab --max-attempts 1 -v
#
# Worker release (--multi-docker only): the docker instance is NOT restarted on
# release by default — each task's worker is handed back still running, which
# avoids the slow gitlab container restart. Tasks therefore inherit whatever
# state earlier tasks wrote; pass --restart-on-release to restore the restart
# when running state-modifying tasks:
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site gitlab --multi-docker -v --restart-on-release
#
# Save results to a JSON log:
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site gitlab -v --output gitlab_results.json
#
# Resume an interrupted run:
#   python3 -m pytest eval/tests/test_agent_verified_all_sites.py --site shopping -v --output shopping.json --resume

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Must run before the repo-internal imports below so `config`/`eval` resolve
# regardless of how this file is invoked (bare `pytest ...`, `python3 -m
# pytest ...`, etc.) rather than relying on `-m`'s cwd-insertion side effect.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from config.servers import SERVER_URLS as _SERVER_URLS
from eval.antigravity_agent_runner import AntigravityAgentRunner
from eval.claude_code_agent_runner import ClaudeCodeAgentRunner
from eval.codex_agent_runner import CodexAgentRunner
from eval.docker import workers_new as _workers_new
from eval.tests.agent_test_utils import (
    build_detailed_entry,
    extract_agent_details,
    flush_detailed_jsonl,
    get_model_id,
    task_status,
)

# Results land under the repo-root generated/ tree, alongside each task's
# generated code (generated/<site>/task_<id>/), rather than eval/tests/logs/.
LOGS_DIR = PROJECT_ROOT / "generated"
TEST_FILES_DIR = Path(__file__).parent / "test_files"

# The repo's own Claude Code settings. agent/baya_code_agent/codegen/run_claude_task.sh cd's to the
# repo root before launching the CLI, so the CLI treats PTE as its project
# directory and may write permission decisions back into this file. It is
# gitignored (.gitignore: `.claude/`), so nothing else would ever notice or
# undo that — hence the snapshot/restore around the run.
CLAUDE_LOCAL_SETTINGS = PROJECT_ROOT / ".claude" / "settings.local.json"

# Sites with a agent/baya_code_agent/sandbox/claude_settings/<site>_settings.sh generator, which is
# what run_claude_task.sh requires. All four sites have one.
#
# enable_reset is True only for gitlab: eval/gitlab_state_reset.py is the only
# resetter with the pre-task reset_for_task(task) shape BaseAgentRunner's Step 0
# wants — and that step hardcodes GitLabStateReset anyway (see
# eval/run_pte_benchmark.py:375). Neither shopping nor shopping_admin has a
# resetter at all, and all 155 shopping_admin tasks carry require_reset: false.
#
# reddit does have one (eval/reddit_state_reset.py), but it exposes
# restore_checkpoint(): a whole-container restore run AFTER a write task, not a
# per-task undo run before it. So reddit stays enable_reset: False here and gets
# its reset as a post-task hook in run_one below. All 106 reddit tasks carry
# require_reset: false.
#
# That matters more for shopping_admin than for shopping, because its 66
# program_html tasks all mutate the admin database and many are phrased
# relatively ("reduce the price by $5", task 458). Those are not idempotent, so
# a single-instance run is effectively one-shot — a repeat needs the database
# restored first. Under --multi-docker, pass --restart-on-release to give each
# task clean state; the default hands the worker back still running.
SITE_CONFIGS = {
    "gitlab": {"enable_reset": True},
    "reddit": {"enable_reset": False},
    "shopping": {"enable_reset": False},
    "shopping_admin": {"enable_reset": False},
}


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

def _site_logs_dir(site: str) -> Path:
    """Per-site output directory: results for --site gitlab land in generated/gitlab/."""
    return LOGS_DIR / site


def _task_files(site: str) -> List[Path]:
    return [
        TEST_FILES_DIR / f"{site}_verified_string_match.json",
        TEST_FILES_DIR / f"{site}_verified_program_html.json",
    ]


def _load_completed_ids(output_name: Optional[str], site: str) -> set:
    """
    Return the set of task_ids already saved in the output file.
    Used by --resume to skip tasks that were completed in a prior run.
    """
    if not output_name:
        return set()
    out_path = _site_logs_dir(site) / output_name
    if not out_path.exists():
        return set()
    try:
        data = json.loads(out_path.read_text())
        return {r["task_id"] for r in data.get("results", [])}
    except Exception:
        return set()


def _load_tasks(site: str, config=None) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    for path in _task_files(site):
        tasks.extend(json.loads(path.read_text()))

    # Defensive single-site filter: only run tasks scoped to exactly this
    # site, excluding any multi-site task (none exist today, but the schema
    # allows "sites" to contain more than one entry).
    tasks = [t for t in tasks if (t.get("sites") or []) == [site]]

    if config is not None:
        task_id = config.getoption("--task-id", default=None)
        if task_id is not None:
            ids = {int(x.strip()) for x in task_id.split(",")}
            tasks = [t for t in tasks if t.get("task_id") in ids]
        else:
            # --start: keep every task from the given task_id onward in loaded
            # order. Lets a run that died partway (e.g. an API quota limit) pick
            # back up at the first task that didn't complete, without re-running
            # the ones before it. Applied before --resume/--task-limit so they
            # compose (e.g. --start 368 --task-limit 10 runs 368..+10).
            start = config.getoption("--start", default=None)
            if start is not None:
                ordered_ids = [t.get("task_id") for t in tasks]
                if start in ordered_ids:
                    tasks = tasks[ordered_ids.index(start):]
                    print(f"\n[start] Starting from task {start} ({len(tasks)} tasks from here onward)")
                else:
                    print(f"\n[start] WARNING: no task with id {start} for site {site!r}; running all tasks")
            resume = config.getoption("--resume", default=False)
            if resume:
                output_name = config.getoption("--output", default=None)
                completed = _load_completed_ids(output_name, site)
                if completed:
                    print(f"\n[resume] Skipping {len(completed)} already-completed tasks: {sorted(completed)}")
                    tasks = [t for t in tasks if t.get("task_id") not in completed]
            limit = config.getoption("--task-limit", default=None)
            if limit is not None:
                tasks = tasks[:limit]
    return tasks


def _flush_results(output_name: Optional[str], result_log: List[Dict[str, Any]], site: str, interrupted: bool = False) -> None:
    """Write current result_log snapshot to the output file (atomic via temp file)."""
    if not output_name:
        return
    logs_dir = _site_logs_dir(site)
    logs_dir.mkdir(parents=True, exist_ok=True)
    out_path = logs_dir / output_name
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
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

def _read_claude_local_settings() -> Optional[bytes]:
    """Snapshot .claude/settings.local.json; None if it does not exist."""
    try:
        return CLAUDE_LOCAL_SETTINGS.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        print(f"\n[settings] WARNING: could not read {CLAUDE_LOCAL_SETTINGS}: {exc}")
        return None


def _restore_claude_local_settings(original: Optional[bytes]) -> None:
    """Put .claude/settings.local.json back the way the run found it.

    A no-op when the run left it untouched, so a clean run stays silent. The
    write goes through a temp file then replace, like _flush_results, so a kill
    mid-restore cannot truncate the file. Never raises: a failed restore must
    not mask the run's own result.
    """
    try:
        current = _read_claude_local_settings()
        if current == original:
            return
        if original is None:
            CLAUDE_LOCAL_SETTINGS.unlink()
            print(f"\n[settings] Removed {CLAUDE_LOCAL_SETTINGS} (created during this run)")
        else:
            CLAUDE_LOCAL_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = CLAUDE_LOCAL_SETTINGS.with_suffix(".json.tmp")
            tmp_path.write_bytes(original)
            tmp_path.replace(CLAUDE_LOCAL_SETTINGS)
            print(f"\n[settings] Restored {CLAUDE_LOCAL_SETTINGS} to its pre-run contents")
    except OSError as exc:
        print(f"\n[settings] WARNING: could not restore {CLAUDE_LOCAL_SETTINGS}: {exc}")


@asynccontextmanager
async def _local_session(url: str, glpat: Optional[str] = None):
    """Stub worker session for a single local site instance (non-multi-docker).

    `glpat` is minted once for the whole run (see _mint_local_glpat) and handed
    to every task, rather than re-minted per task as the multi-docker path
    historically did.
    """
    yield {"worker_id": "local", "url": url, "glpat": glpat}


async def _mint_local_glpat(base_url: str) -> Optional[str]:
    """Mint one GitLab token for the whole single-instance run.

    A PAT survives everything the eval does to a running instance (the state
    reset in eval/gitlab_state_reset.py is REST-only and never touches tokens),
    so one token minted here stays valid for every task in the run. Returns None
    on failure: the generated scripts then fall back to refreshing their own
    token, which is exactly the behaviour before this existed.
    """
    from config.init_tokens.refresh_gitlab_token import refresh_token

    print(f"  Minting one GitLab token for this run ({base_url})...")
    try:
        token = await asyncio.to_thread(refresh_token, base_url=base_url)
    except Exception as exc:
        print(
            f"  WARNING: could not mint a GitLab token ({exc}) — continuing without one. "
            f"Each generated script will refresh its own."
        )
        return None
    print("  GitLab token minted; reusing it for every task in this run")
    return token


def _expected_answer(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The task's reference answer, in the shape the grader actually checks.

    string_match tasks carry it in eval.reference_answers. program_html tasks
    have reference_answers=None and keep their expectation inside each check's
    required_contents, so fall back to those — otherwise every program_html row
    would log an empty expectation.
    """
    ev = task.get("eval") or {}
    ra = ev.get("reference_answers") or {}
    expected = {
        k: ra[k]
        for k in ("exact_match", "fuzzy_match", "must_include", "must_exclude")
        if ra.get(k) is not None
    }
    if expected:
        return expected
    checks = [c.get("required_contents") for c in (ev.get("program_html") or [])]
    checks = [c for c in checks if c]
    return {"program_html": checks} if checks else None


# reddit tasks carry no read_only field, so whether a task mutates site state has
# to be derived. Every program_html task does (they are graded on what they
# changed). Among the 11 string_match tasks only these two templates write:
# 25 ("Like all submissions created by ... " — task 723) and 1510 ("DisLike all
# submissions ..." — task 726). Templates 17 and 33 are pure reads and so skip the
# ~20s restore.
_REDDIT_WRITE_STRING_MATCH_TEMPLATES = {25, 1510}


def _reddit_task_writes(task: Dict[str, Any]) -> bool:
    """True when a reddit task mutates site state and needs a post-task restore."""
    if "program_html" in (task.get("eval") or {}).get("eval_types", []):
        return True
    return task.get("intent_template_id") in _REDDIT_WRITE_STRING_MATCH_TEMPLATES


def _fmt_value(value: Any, limit: int = 300) -> str:
    """One-line, length-capped rendering of an answer or expected answer."""
    if value is None:
        return "(none)"
    if isinstance(value, str):
        text = " ".join(value.split())
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    if not text:
        return "(empty)"
    return text if len(text) <= limit else text[:limit] + f"... (+{len(text) - limit} chars)"


def _print_result_line(entry: Dict[str, Any], prefix: str = "") -> None:
    """Print one task's outcome as passed / answer / expected answer."""
    outcome = "PASS" if entry.get("passed") else "FAIL"
    print(f"{prefix}[{outcome}] task {entry['task_id']}")
    print(f"{prefix}    passed   : {bool(entry.get('passed'))}")
    print(f"{prefix}    answer   : {_fmt_value(entry.get('answer'))}")
    print(f"{prefix}    expected : {_fmt_value(entry.get('expected_answer'))}")
    if entry.get("error"):
        print(f"{prefix}    error    : {_fmt_value(entry['error'], limit=200)}")


def _make_failure_message(
    task: Dict[str, Any],
    agent_result: Optional[Dict[str, Any]],
    html_detail: Optional[Dict[str, Any]],
) -> str:
    ev = task.get("eval", {})
    lines = [
        f"Task {task['task_id']} FAILED",
        f"  intent     : {task['intent']}",
        f"  sites      : {task.get('sites', [])}",
        f"  eval_types : {ev.get('eval_types', [])}",
    ]

    ra = ev.get("reference_answers") or {}
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
        if agent_result.get("script"):
            lines.append(f"  script       : {agent_result['script']}")
        lines.append(f"  agent_answer : {str(agent_result.get('answer', ''))[:300]!r}")
        if agent_result.get("script_output"):
            tail = str(agent_result["script_output"])[-500:]
            lines.append("  script_output (tail):")
            lines.extend(f"    {ln}" for ln in tail.splitlines())

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

def test_agent_accomplishes_site_tasks(
    session_event_loop,
    acquire_lock,
    result_log,
    request,
) -> None:
    site = request.config.getoption("--site", default=None)
    if not site:
        pytest.fail(
            "eval/tests/test_agent_verified_all_sites.py requires --site "
            f"(one of: {', '.join(SITE_CONFIGS)}). "
            "Example: pytest eval/tests/test_agent_verified_all_sites.py --site shopping"
        )
    if site not in SITE_CONFIGS:
        pytest.fail(
            f"Unknown or unsupported --site {site!r}. One of: {', '.join(SITE_CONFIGS)}."
        )

    enable_reset = SITE_CONFIGS[site]["enable_reset"]
    multi_docker = request.config.getoption("--multi-docker", default=False)
    # reddit + multi-docker is now supported: the companion API server
    # (api/servers/reddit.py) resolves base_url per request (api/reddit_pw/constants.py),
    # so each worker's writes and grading target that worker's own Postmill instance.
    # Its between-task reset is the orchestrator's clean-image recreate on release
    # (release --server reddit → compose_recreate_service), driven per write task
    # below via force_restart (see run_one).
    # Off by default: skip the docker instance restart on release (see
    # --restart-on-release). Only meaningful under --multi-docker, since the
    # single-instance path never releases a worker at all.
    restart_on_release = request.config.getoption("--restart-on-release", default=False)

    tasks = _load_tasks(site, request.config)
    force_reset = request.config.getoption("--force-reset", default=False)
    regenerate_code = request.config.getoption("--regenerate-code", default=False)
    max_attempts = max(1, request.config.getoption("--max-attempts", default=3))
    debug = request.config.getoption("--agent-trace", default=False)
    # reddit's reset is a POST-task whole-container restore, not BaseAgentRunner's
    # pre-task Step 0 (see SITE_CONFIGS above). Single-instance only:
    # reddit_state_reset.py addresses one fixed container name and ports 9999/9998.
    reddit_restore = (
        site == "reddit"
        and not multi_docker
        and not request.config.getoption("--no-reddit-restore", default=False)
    )
    _restore_reddit_checkpoint = None
    if reddit_restore:
        # Imported lazily so a gitlab/shopping run never loads a reddit-only module.
        from eval.reddit_state_reset import restore_checkpoint as _restore_reddit_checkpoint
    output_name = request.config.getoption("--output", default=None)
    if not output_name:
        # Always save logs — auto-generate a timestamped filename when --output
        # is not explicitly provided so no run is ever lost.
        output_name = datetime.now(timezone.utc).strftime(f"{site}_%Y%m%d_%H%M%S.json")
    # Per-site output directory (logs/<site>/). Created up front because
    # flush_detailed_jsonl (agent_test_utils.py) mkdir's only the leaf without
    # parents=True, so logs/<site> must already exist when it writes.
    site_logs_dir = _site_logs_dir(site)
    site_logs_dir.mkdir(parents=True, exist_ok=True)
    detailed_out_path = site_logs_dir / (Path(output_name).stem + "_detailed.jsonl")

    # Codegen is worker-agnostic (see ClaudeCodeAgentRunner._run_codegen) and
    # cached per generated/<site>/task_<id>/, so only live *execution* needs
    # a worker — safe to parallelize across the orchestrator's pool exactly
    # like the other eval/tests/*.py files do.
    n_workers = _workers_new.num_workers() if multi_docker else 1

    # run_claude_task.sh runs each codegen session in one of a fixed pool of
    # staging directories (its settings file has to stay byte-identical across
    # tasks for the prompt cache to hit, so it cannot be named after the task).
    # A pool smaller than the worker count makes runs queue for a slot instead
    # of running, so size it from n_workers here rather than leaving the
    # script's default to guess. setdefault so an explicit export still wins.
    os.environ.setdefault("PTE_CLAUDE_SLOTS", str(n_workers))
    os.environ.setdefault("PTE_ANTIGRAVITY_SLOTS", str(n_workers))
    os.environ.setdefault("PTE_CODEX_SLOTS", str(n_workers))

    # Resolve agent runner class (supporting --agent-runner antigravity / claude / codex / dotted.path or config.yaml)
    runner_path = request.config.getoption("--agent-runner", default=None)
    if runner_path == "antigravity":
        runner_cls = AntigravityAgentRunner
    elif runner_path == "claude":
        runner_cls = ClaudeCodeAgentRunner
    elif runner_path == "codex":
        runner_cls = CodexAgentRunner
    elif runner_path:
        from eval.tests.conftest import _load_runner_class
        runner_cls = _load_runner_class(runner_path)
    else:
        try:
            import yaml
            cfg = yaml.safe_load((PROJECT_ROOT / "config" / "config.yaml").read_text())
            provider = cfg.get("agent_llm_provider")
            if provider in ("google", "antigravity"):
                runner_cls = AntigravityAgentRunner
            elif provider in ("openrouter", "deepseek", "openai"):
                runner_cls = CodexAgentRunner
            else:
                runner_cls = ClaudeCodeAgentRunner
        except Exception:
            runner_cls = ClaudeCodeAgentRunner

    print(f"\nRunning {len(tasks)} tasks with {n_workers} workers using {runner_cls.__name__}")

    async def run_all() -> tuple:
        sem = asyncio.Semaphore(n_workers)
        remaining = len(tasks)
        # Lock protecting result_log mutations and incremental file flushes.
        record_lock = asyncio.Lock()
        failures: List[str] = []

        # Cleared on the first restore failure so a broken ssh target or a missing
        # clean image costs one timeout, not one per task for the rest of the run.
        reddit_restore_ok = reddit_restore

        # Single-instance gitlab: mint one token up front and reuse it for every
        # task, instead of letting each task pay its own browser login. The
        # multi-docker path can't share a token this way — each worker is a
        # separate GitLab instance with its own database — so it mints per
        # instance inside worker_session instead.
        local_glpat: Optional[str] = None
        if not multi_docker and site == "gitlab" and tasks:
            local_glpat = await _mint_local_glpat(
                request.config.getoption("--base-url") or _SERVER_URLS[site]
            )

        async def run_one(task: Dict[str, Any]) -> Dict[str, Any]:
            # Declared up front, not at the assignment below: a nonlocal statement
            # has to precede every use of the name in its function, and the reddit
            # restore block reads this flag before it can fail and clear it.
            nonlocal reddit_restore_ok
            async with sem:
                try:
                    if multi_docker:
                        if site == "reddit":
                            # Postmill has no per-task REST undo; its between-task
                            # reset is the orchestrator's clean-image recreate on
                            # release (release --server reddit → compose_recreate_
                            # service). Drive it per task: a write releases with
                            # --server reddit (reset), a pure read releases
                            # --read-only (no reset). reddit tasks carry no
                            # read_only field, so derive it from _reddit_task_writes;
                            # force_restart=None keeps that per-task logic whatever
                            # --restart-on-release is set to.
                            ro = not _reddit_task_writes(task)
                            fr = None
                        else:
                            ro = task.get("read_only", False)
                            # False => release without restarting the instance;
                            # None => keep the per-task read_only behaviour.
                            fr = None if restart_on_release else False
                        worker_ctx = _workers_new.worker_session(
                            str(task["task_id"]), server=site,
                            acquire_lock=acquire_lock,
                            read_only=ro,
                            force_restart=fr,
                        )
                    else:
                        base_url = request.config.getoption("--base-url") or _SERVER_URLS[site]
                        worker_ctx = _local_session(base_url, glpat=local_glpat)

                    async with worker_ctx as w:
                        site_url = w.get(f"{site}_url") or w.get("url")

                        runner = runner_cls(
                            headless=True,
                            enable_reset=enable_reset,
                            force_reset=force_reset,
                            gitlab_base_url=site_url if site == "gitlab" else _SERVER_URLS["gitlab"],
                            regenerate_code=regenerate_code,
                            debug=debug,
                        )
                        runner.server = site
                        runner.base_url = site_url
                        if site == "reddit":
                            # program_html grading/login target this worker's Postmill
                            # instance via the dedicated attribute (see
                            # BaseAgentRunner._reddit_program_html_base).
                            runner.reddit_base_url = site_url
                        if w.get("glpat"):
                            runner.site_glpat = w["glpat"]

                        await runner._init_agent()

                        start_time = datetime.now(timezone.utc)
                        # Per-task reset: retry state is set inside the loop
                        # below on each retry, so clear it here to be sure no
                        # value from a prior task in this run leaks into this
                        # task's first attempt.
                        runner.replan = False
                        runner.codegen_correction = None
                        attempt = 1
                        while True:
                            passed, agent_result, error, html_detail = (
                                await runner.run_agent_on_task(
                                    task,
                                    # Reset once, before the first attempt only:
                                    # a retry deliberately inherits whatever the
                                    # failed run left behind.
                                    skip_reset=attempt > 1,
                                )
                            )
                            if passed or attempt >= max_attempts:
                                break
                            # `passed` is checked first, so a program_html task
                            # whose script made its change and then crashed is
                            # already out of this loop — its answer never
                            # mattered, and retrying would re-apply the change.
                            # A task that produced an answer is done even if that
                            # answer graded wrong: replanning against a reference
                            # the session cannot see would just be fitting the
                            # grader.
                            failure_kind = (agent_result or {}).get("failure_kind")
                            if failure_kind not in (
                                "script_execution",
                                "codegen",
                                "sandbox_write_block",
                            ):
                                break

                            attempt += 1
                            runner.replan_attempt = attempt
                            runner.replan_max_attempts = max_attempts
                            if failure_kind == "script_execution":
                                # There is a script, and its logs say why it
                                # failed — replan mode stages those in and asks
                                # for a repair.
                                runner.replan = True
                                runner.codegen_correction = None
                                print(
                                    f"\n[replan] Task {task['task_id']} attempt {attempt}/{max_attempts} "
                                    f"failed to execute ({error}) — replanning from its logs"
                                )
                            elif failure_kind == "sandbox_write_block":
                                # The prior session died at the sandbox
                                # write-boundary (it reached outside its slot),
                                # not on a bug — so there is nothing to repair.
                                # Regenerate from scratch, but WITH a corrective
                                # hint, so it does not walk into the same
                                # deterministic read-only wall every attempt.
                                runner.replan = False
                                runner.codegen_correction = "sandbox_write_block"
                                print(
                                    f"\n[retry] Task {task['task_id']} attempt {attempt}/{max_attempts} "
                                    f"hit the sandbox write-boundary ({error}) — regenerating with corrective guidance"
                                )
                            else:
                                # A codegen failure leaves nothing to diagnose
                                # from, so this is a fresh attempt rather than a
                                # replan — the replan prompt would otherwise
                                # instruct the session to repair an
                                # implementation that does not exist. Worth
                                # retrying at all because the common case is
                                # transient: a session that started, burned one
                                # turn and returned nothing. A permanently broken
                                # setup (not signed in, say) will now consume all
                                # max_attempts before failing, which is the price
                                # of not giving up on the transient case.
                                runner.replan = False
                                runner.codegen_correction = None
                                print(
                                    f"\n[retry] Task {task['task_id']} attempt {attempt}/{max_attempts} "
                                    f"codegen produced nothing ({error}) — regenerating from scratch"
                                )
                        end_time = datetime.now(timezone.utc)

                        # reddit's post-task state reset. Placement is deliberate:
                        #  - AFTER the replan loop, so it runs exactly once per
                        #    task. Restoring between attempts would undo the
                        #    attempt's writes, and it would contradict the loop's
                        #    own contract (skip_reset=attempt > 1 — a retry
                        #    deliberately inherits whatever the failed run left
                        #    behind). A replan only ever fires on a script that
                        #    failed to EXECUTE anyway.
                        #  - AFTER run_agent_on_task returned, which is after
                        #    program_html grading — that runs inside it — so the
                        #    checks still see the state the script produced.
                        #  - BEFORE extract_agent_details and the result recording,
                        #    so nothing in the recorded row depends on whether the
                        #    restore worked.
                        #  - In its own try/except: a failed restore must not turn a
                        #    graded task into an error row.
                        if reddit_restore_ok and _reddit_task_writes(task):
                            print("  [reset] Restoring Postmill clean image...", flush=True)
                            try:
                                # mcp_url is required here, not optional: the
                                # restored database has no record of the old
                                # PHPSESSID, and both the next task's script and the
                                # program_html login step (_login_for_task in
                                # eval/run_pte_benchmark.py) read their session from
                                # that server.
                                await asyncio.to_thread(
                                    _restore_reddit_checkpoint, _SERVER_URLS["reddit_extra"]
                                )
                                print("  [reset] Postmill restored.", flush=True)
                            except Exception as reset_err:
                                reddit_restore_ok = False
                                print(
                                    f"\n  [reset] WARNING: restore failed ({reset_err}). "
                                    f"Disabling further restores for this run — every "
                                    f"remaining write task will inherit contaminated "
                                    f"state, so results after this point are suspect.",
                                    flush=True,
                                )

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
                            "attempts": attempt,
                            # start_time spans every attempt, so end_time - start_time
                            # is the task's true wall cost including replans.
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
                        "attempts": None,
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
                        "sites":          task_obj.get("sites", []),
                        "eval_types":     task_obj.get("eval", {}).get("eval_types", []),
                        "passed":         r_passed and not r_error,
                        "status":         result.get("status"),
                        # The graded answer: what the generated script printed
                        # as its result (see claude_code_agent/baya_json_agent/execution_agent.py).
                        "answer":         r_agent.get("answer") if r_agent else None,
                        # What it was graded against, so a result row is
                        # readable without cross-referencing the task file.
                        "expected_answer": _expected_answer(task_obj),
                        "script":         r_agent.get("script") if r_agent else None,
                        "script_output":  r_agent.get("script_output") if r_agent else None,
                        "error":          r_error,
                        "failure_kind":   r_agent.get("failure_kind") if r_agent else None,
                        # How many times the script was run. >1 means earlier
                        # attempts failed to execute and were replanned.
                        "attempts":       result.get("attempts"),
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
                        eval_output_dir=str(site_logs_dir / output_name),
                        costs=result.get("costs"),
                        tokens=result.get("tokens"),
                    )
                    flush_detailed_jsonl(detailed_out_path, det_entry)

                    result_log.append(entry)
                    _flush_results(output_name, result_log, site, interrupted=False)

                    print(f"\n[{remaining} tasks remaining] Task {task_obj['task_id']} done")
                    _print_result_line(entry)

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
            print("\nInterrupted — cancelling remaining tasks...")
            for f in futures:
                f.cancel()
            all_results = await asyncio.gather(*futures, return_exceptions=True)
            partial = [r for r in all_results if isinstance(r, dict)]
            return partial, failures, True

    # Snapshot before the first codegen session, restore in the finally below,
    # so whatever the claude CLI writes into the repo's own settings during this
    # run is undone. The finally lives in the test body rather than a fixture
    # because pytest.exit and pytest.fail below both raise from inside this
    # function — a finally here is guaranteed to run before either propagates.
    claude_settings_snapshot = _read_claude_local_settings()

    interrupted = False
    failures: List[str] = []
    try:
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
            _flush_results(output_name, result_log, site, interrupted=True)
            n = len(result_log)
            if output_name and n:
                print(f"\n📄 Partial results ({n} tasks) written to {site_logs_dir / output_name}")
            pytest.exit("Interrupted by user", returncode=1)

        # Final flush (marks interrupted=False).
        _flush_results(output_name, result_log, site, interrupted=False)
        print(f"\n📄 Results saved to {site_logs_dir / output_name}")

        passed_count = sum(1 for e in result_log if e.get("passed"))
        total = len(result_log)

        # Per-task recap: passed / answer / expected answer, in task_id order
        # (parallel workers finish out of order, so result_log is not sorted).
        print("\n===== Per-task results =====")
        for entry in sorted(result_log, key=lambda e: e.get("task_id") or 0):
            _print_result_line(entry)
        print("============================")

        print(f"Results: {passed_count}/{total} passed, {total - passed_count}/{total} failed")

        if failures:
            pytest.fail("\n\n".join(failures))
    finally:
        _restore_claude_local_settings(claude_settings_snapshot)

