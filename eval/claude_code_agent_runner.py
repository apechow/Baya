# eval/claude_code_agent_runner.py
#
# BaseAgentRunner subclass that drives tasks through the Claude-Code-codegen
# pipeline (agent/baya_code_agent/codegen/run_claude_task.sh) instead of the LLM planner/executor
# (agent/baya_json_agent/planning_agent.py + agent/baya_json_agent/execution_agent.py).
#
# For each task:
#   1. Reuse the already-generated implementation script in
#      generated/<site>/task_<id>/ if one exists (regenerate_code=False,
#      the default) — otherwise run agent/baya_code_agent/codegen/run_claude_task.sh to have
#      Claude Code write one.
#   2. Execute that script live via agent.baya_code_agent.execution_agent. The
#      task's answer is the result that script prints — the text inside its
#      FINAL ANSWER block — not the agent's own prose and not the script's
#      trace output.
#
# All evaluation (string_match / url_match / program_html) and the gitlab
# state-reset step are inherited unchanged from BaseAgentRunner — this class
# only overrides how an answer is produced, not how it's judged. See
# eval/agent_runner.py for the general --agent-runner subclass pattern this
# follows.
#
# Supported sites are those with a agent/baya_code_agent/sandbox/claude_settings/<site>_settings.sh
# generator, since run_claude_task.sh hard-errors without one. All four sites
# have one.
#
# reddit is the one site whose generated scripts do not talk to the site itself:
# api/schemas/reddit_api_schema.json documents a companion FastAPI server
# (api/servers/reddit.py, host 127.0.0.1:7791) that drives a headless browser,
# because Postmill has no API of its own. So for reddit the REDDIT_EXTRA_DOMAIN
# exported below is the *primary* API target, not a supplement, and REDDIT_DOMAIN
# is the graded site the script only needs for URL semantics. That server resolves
# base_url per request (api/reddit_pw/constants.py) and caches a session per
# instance, so reddit DOES support --multi-docker: the script forwards REDDIT_DOMAIN
# (the worker's Postmill URL) as base_url on every call, and program_html grading
# targets the same worker via runner.reddit_base_url.

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from agent.baya_code_agent.execution_agent import BayaCodeExecutionAgent, ScriptExecutionError
from config.servers import SERVER_URLS as _SERVER_URLS
from eval.run_pte_benchmark import BaseAgentRunner

SUPPORTED_SITES = {"gitlab", "reddit", "shopping", "shopping_admin"}

ROOT = Path(__file__).resolve().parent.parent


class ClaudeCodeAgentRunner(BaseAgentRunner):
    """Drives a task through Claude Code codegen + live script execution.

    For multi-docker runs, set `self.base_url` (and, for gitlab,
    `self.site_glpat`) after construction — mirroring the `getattr(self,
    "base_url", ...)` convention used by other BaseAgentRunner subclasses —
    so `_run_task` targets the assigned worker instead of the single-instance
    default. Codegen itself stays worker-agnostic (see `_run_codegen`).
    """

    def __init__(
        self,
        headless: bool = True,
        enable_reset: bool = True,
        force_reset: bool = False,
        gitlab_base_url: str = _SERVER_URLS["gitlab"],
        regenerate_code: bool = False,
        codegen_timeout: int = 900,
        script_timeout: int = 120,
        debug: bool = False,
    ) -> None:
        super().__init__(
            headless=headless,
            enable_reset=enable_reset,
            force_reset=force_reset,
            gitlab_base_url=gitlab_base_url,
        )
        self.regenerate_code = regenerate_code
        self.codegen_timeout = codegen_timeout
        self.script_timeout = script_timeout
        self.debug = debug
        # Set by the caller between attempts (see eval/tests/
        # test_agent_verified_all_sites.py) after a generated script failed its
        # live run: the next codegen repairs the existing script from the task's
        # own logs instead of writing a fresh one, and the cached script is
        # never reused. Left False for a first attempt.
        self.replan = False
        # Set by the same caller: which attempt this is, and how many there are.
        # The codegen prompt only needs one thing from them -- whether this is
        # the LAST attempt, which is when a "not found" has to be reported as
        # the answer instead of as a failure. 0 means the caller did not say,
        # and is treated as "not final".
        self.replan_attempt = 0
        self.replan_max_attempts = 0
        self._executor = BayaCodeExecutionAgent()

    async def _init_agent(self) -> None:
        script = ROOT / "agent" / "baya_code_agent" / "codegen" / "run_claude_task.sh"
        if not script.is_file() or not os.access(script, os.X_OK):
            raise RuntimeError(f"agent/baya_code_agent/codegen/run_claude_task.sh not found or not executable: {script}")

    async def _run_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        sites = task.get("sites") or []
        site = sites[0] if sites else None
        if site not in SUPPORTED_SITES:
            return {
                "success": False,
                "error": (
                    f"site {site!r} not supported by ClaudeCodeAgentRunner yet "
                    f"(only {sorted(SUPPORTED_SITES)} have an agent/baya_code_agent/sandbox/claude_settings generator)"
                ),
                "final_url": None,
                "answer": None,
                "failure_kind": "unsupported_site",
            }

        task_id = task["task_id"]
        task_dir = ROOT / "generated" / site / f"task_{task_id}"

        script_path = None
        # On a replan the cached script is exactly the one that just failed, so
        # reusing it would re-run the same failure — always regenerate.
        if not self.regenerate_code and not self.replan:
            try:
                script_path = self._executor.find_script(task_dir)
                if self.debug:
                    print(f"[ClaudeCodeAgentRunner] task {task_id}: reusing {script_path}")
            except FileNotFoundError:
                script_path = None

        if script_path is None:
            codegen_failure = await self._run_codegen(
                site,
                task_id,
                replan=self.replan,
                attempt=self.replan_attempt,
                max_attempts=self.replan_max_attempts,
            )
            if codegen_failure is not None:
                return codegen_failure
            try:
                script_path = self._executor.find_script(task_dir)
            except (FileNotFoundError, RuntimeError) as e:
                return {
                    "success": False,
                    "error": f"codegen completed but produced no usable implementation script: {e}",
                    "final_url": None,
                    "answer": None,
                    "failure_kind": "codegen",
                }

        base_url = getattr(self, "base_url", None) or _SERVER_URLS[site]
        env = {f"{site.upper()}_DOMAIN": base_url}
        # The companion "extra" API server, if the site has one. There is a
        # single shared process per extra server (initialize.py starts them,
        # the worker pool does not), so this is always the configured default —
        # exporting it just makes the name official instead of leaving every
        # generated script to invent it. For reddit this one is not a supplement
        # but the only API the script has (see the module comment), so a reddit
        # script that never reads it cannot work.
        extra_site = f"{site}_extra"
        if extra_site in _SERVER_URLS:
            env[f"{extra_site.upper()}_DOMAIN"] = _SERVER_URLS[extra_site]
        glpat = getattr(self, "site_glpat", None)
        if site == "gitlab" and glpat:
            env["GITLAB_TOKEN"] = glpat

        try:
            run = await self._executor.run(
                script_path, task_dir, timeout=self.script_timeout, env=env
            )
        except ScriptExecutionError as e:
            # The script itself is at fault, which a replan can plausibly fix —
            # unlike a codegen/infra failure. script_output carries the script's
            # own error text (empty on timeout, since the process is killed
            # before its output can be drained); the same text is also appended
            # to <task_dir>/python_output.log, which is what the replan session
            # actually reads.
            return {
                "success": False,
                "error": str(e),
                "final_url": None,
                "answer": None,
                "failure_kind": "script_execution",
                "script": str(script_path.relative_to(ROOT)),
                "script_output": e.captured_output,
            }

        if not run.has_marker:
            print(
                f"[ClaudeCodeAgentRunner] task {task_id}: {script_path.name} printed no "
                f"FINAL ANSWER block — grading its whole stdout instead. Re-run with "
                f"--regenerate-code to regenerate it against the current prompt."
            )

        return {
            "answer": run.answer,
            "final_url": None,
            "script": str(script_path.relative_to(ROOT)),
            "script_output": run.raw_output,
        }

    async def _run_codegen(
        self,
        site: str,
        task_id: Any,
        replan: bool = False,
        attempt: int = 0,
        max_attempts: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Run agent/baya_code_agent/codegen/run_claude_task.sh for (site, task_id).

        Returns a failure dict on error, or None on success (the caller then
        re-resolves the generated script). When self.debug is set, the Claude
        Code session's own stdout/stderr stream live to the console instead
        of being captured — useful for smoke-testing a single task.

        replan=True passes the script's optional third argument, which swaps the
        prompt's "write this from scratch" framing for "the existing script
        failed its live run; read this task's logs and repair it". When the
        caller also knows the attempt numbers, they go in the same argument as
        replan:<attempt>:<max_attempts>, which additionally lets the prompt tell
        the session that this is its last chance to produce any answer at all.
        """
        cmd = [str(ROOT / "agent" / "baya_code_agent" / "codegen" / "run_claude_task.sh"), site, str(task_id)]
        if replan:
            if attempt and max_attempts:
                cmd.append(f"replan:{attempt}:{max_attempts}")
            else:
                cmd.append("replan")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(ROOT),
            stdout=None if self.debug else asyncio.subprocess.PIPE,
            stderr=None if self.debug else asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.codegen_timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass  # process already exited between the timeout firing and our kill
            return {
                "success": False,
                "error": f"codegen timed out after {self.codegen_timeout}s for site={site} task_id={task_id}",
                "final_url": None,
                "answer": None,
                "failure_kind": "codegen",
            }

        if proc.returncode != 0:
            tail = stdout.decode(errors="replace")[-2000:] if stdout else ""
            return {
                "success": False,
                "error": (
                    f"run_claude_task.sh exited with code {proc.returncode} "
                    f"for site={site} task_id={task_id}: {tail}"
                ),
                "final_url": None,
                "answer": None,
                "failure_kind": "codegen",
            }

        session_error = self._codegen_session_error(site, task_id)
        if session_error is not None:
            return {
                "success": False,
                "error": (
                    f"codegen session failed for site={site} task_id={task_id} "
                    f"despite exiting 0: {session_error}"
                ),
                "final_url": None,
                "answer": None,
                "failure_kind": "codegen",
            }
        return None

    @staticmethod
    def _codegen_session_error(site: str, task_id: Any) -> Optional[str]:
        """Return a description of the last codegen session's failure, or None.

        A zero exit code does not mean the session did its job: an interrupted
        stream leaves run_claude_task.sh exiting 0 while the session wrote
        nothing (observed as terminal_reason="aborted_streaming", is_error=True,
        result=None). Left undetected, a replan attempt that aborts this way
        re-runs byte-identical code and burns an attempt on an unchanged script.

        The last line of <task_dir>/claude_result.jsonl is this run's record.
        `is_error` is the trustworthy signal — `subtype` reads "success" even on
        a failed run — so it is what's checked here.

        Never raises: this is a diagnostic, and a missing or malformed file must
        not turn a working codegen into a reported failure.
        """
        result_path = ROOT / "generated" / site / f"task_{task_id}" / "claude_result.jsonl"
        try:
            # One JSON object per run, appended — parse the last non-empty line
            # rather than the whole file, which is not valid JSON once a second
            # run lands.
            lines = [ln for ln in result_path.read_text().splitlines() if ln.strip()]
            if not lines:
                return None
            record = json.loads(lines[-1])
        except (OSError, ValueError):
            return None

        if not record.get("is_error"):
            return None
        return (
            f"is_error=True terminal_reason={record.get('terminal_reason')!r} "
            f"api_error_status={record.get('api_error_status')!r} "
            f"(see {result_path.relative_to(ROOT)})"
        )
