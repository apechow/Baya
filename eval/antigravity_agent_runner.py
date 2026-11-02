# eval/antigravity_agent_runner.py
#
# BaseAgentRunner subclass that drives tasks through the Antigravity-codegen
# pipeline (agent/baya_code_agent/codegen/run_antigravity_task.sh) instead of the LLM planner/executor
# or Claude Code pipeline.

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from agent.baya_code_agent.execution_agent import BayaCodeExecutionAgent, ScriptExecutionError
from config.servers import SERVER_URLS as _SERVER_URLS
from eval.run_pte_benchmark import BaseAgentRunner

SUPPORTED_SITES = {"gitlab", "shopping", "shopping_admin", "reddit"}

ROOT = Path(__file__).resolve().parent.parent


class AntigravityAgentRunner(BaseAgentRunner):
    """Drives a task through Antigravity CLI codegen + live script execution.

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
        self.replan = False
        self.replan_attempt = 0
        self.replan_max_attempts = 0
        # When set, a corrective hint passed to the codegen session via the
        # PTE_CODEGEN_CORRECTION env var (e.g. "sandbox_write_block"). Unlike
        # replan, this drives a from-scratch regeneration -- there is no prior
        # script to repair -- but with guidance so the retry does not repeat the
        # same fatal move. The retry loop sets and clears it per attempt.
        self.codegen_correction: Optional[str] = None
        self._executor = BayaCodeExecutionAgent()

    async def _init_agent(self) -> None:
        script = ROOT / "agent" / "baya_code_agent" / "codegen" / "run_antigravity_task.sh"
        if not script.is_file() or not os.access(script, os.X_OK):
            raise RuntimeError(f"agent/baya_code_agent/codegen/run_antigravity_task.sh not found or not executable: {script}")

    async def _run_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        sites = task.get("sites") or []
        site = sites[0] if sites else None
        if site not in SUPPORTED_SITES:
            return {
                "success": False,
                "error": (
                    f"site {site!r} not supported by AntigravityAgentRunner yet "
                    f"(only {sorted(SUPPORTED_SITES)} are supported)"
                ),
                "final_url": None,
                "answer": None,
                "failure_kind": "unsupported_site",
            }

        task_id = task["task_id"]
        task_dir = ROOT / "generated" / site / f"task_{task_id}"

        script_path = None
        if not self.regenerate_code and not self.replan:
            try:
                script_path = self._executor.find_script(task_dir)
                if self.debug:
                    print(f"[AntigravityAgentRunner] task {task_id}: reusing {script_path}")
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
                    "failure_kind": (
                        "sandbox_write_block"
                        if self._is_sandbox_write_block(site, task_id)
                        else "codegen"
                    ),
                }

        base_url = getattr(self, "base_url", None) or _SERVER_URLS[site]
        env = {f"{site.upper()}_DOMAIN": base_url}
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
                f"[AntigravityAgentRunner] task {task_id}: {script_path.name} printed no "
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
        """Run agent/baya_code_agent/codegen/run_antigravity_task.sh for (site, task_id)."""
        cmd = [str(ROOT / "agent" / "baya_code_agent" / "codegen" / "run_antigravity_task.sh"), site, str(task_id)]
        if replan:
            if attempt and max_attempts:
                cmd.append(f"replan:{attempt}:{max_attempts}")
            else:
                cmd.append("replan")
        # Inherit the environment (explicitly, so the correction hint can ride
        # alongside it). run_antigravity_task.sh reads PTE_CODEGEN_CORRECTION to
        # append a targeted correction section to the codegen prompt.
        env = dict(os.environ)
        if self.codegen_correction:
            env["PTE_CODEGEN_CORRECTION"] = self.codegen_correction
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(ROOT),
            env=env,
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
                    f"run_antigravity_task.sh exited with code {proc.returncode} "
                    f"for site={site} task_id={task_id}: {tail}"
                ),
                "final_url": None,
                "answer": None,
                "failure_kind": (
                    "sandbox_write_block"
                    if self._is_sandbox_write_block(site, task_id)
                    else "codegen"
                ),
            }

        session_error = self._codegen_session_error(site, task_id)
        if session_error is None:
            return None

        # A session error does not by itself mean the codegen failed. Observed:
        # the session wrote task_<id>.py and test_task_<id>.py, ran the tests and
        # passed them, and only then hit a retryable 429 from the model provider
        # on its closing message -- which lands as status=ERROR. Throwing that
        # script away would discard finished work over a transient stop.
        #
        # So the artifact decides. If a usable implementation exists the run
        # continues and the error is recorded as a warning; if not, the session
        # error is the diagnosis, which is far more useful than the caller's
        # generic "produced no usable implementation script".
        try:
            self._executor.find_script(ROOT / "generated" / site / f"task_{task_id}")
        except (FileNotFoundError, RuntimeError):
            return {
                "success": False,
                "error": (
                    f"codegen session failed for site={site} task_id={task_id} "
                    f"and produced no implementation: {session_error}"
                ),
                "final_url": None,
                "answer": None,
                "failure_kind": (
                    "sandbox_write_block"
                    if self._is_sandbox_write_block(site, task_id)
                    else "codegen"
                ),
            }

        print(
            f"[AntigravityAgentRunner] task {task_id}: codegen reported an error but "
            f"produced a usable script; continuing. ({session_error})"
        )
        return None

    @staticmethod
    def _is_sandbox_write_block(site: str, task_id: Any) -> bool:
        """True if the last codegen session died at the sandbox write-boundary.

        The agy engine's own CODE_ACTION step (not a model tool call, so the
        mkdir hook can't catch it) hit a path outside the writable slot, which
        the sandbox remounts read-only -- a fatal, deterministic EROFS. The
        read-only remount (antigravity_sandbox.sh) is the only source of a
        read-only-filesystem error in this pipeline, so that string is a
        reliable signature. Blindly regenerating repeats it identically; the
        caller uses this to retry WITH a corrective hint instead.
        """
        result_path = ROOT / "generated" / site / f"task_{task_id}" / "antigravity_result.jsonl"
        try:
            lines = [ln for ln in result_path.read_text().splitlines() if ln.strip()]
            if not lines:
                return False
            record = json.loads(lines[-1])
        except (OSError, ValueError):
            return False
        return "read-only file system" in str(record.get("error") or "").lower()

    @staticmethod
    def _codegen_session_error(site: str, task_id: Any) -> Optional[str]:
        """Return a description of the last codegen session's failure, or None.

        Reads only antigravity_result.jsonl. It deliberately does NOT fall back to
        claude_result.jsonl: a task previously attempted through the Claude runner
        leaves one behind, and reading it would report -- or worse, clear -- an
        error belonging to a different session entirely.

        `status` alone is not a sufficient signal. agy reports status SUCCESS for a
        session that did nothing: a recorded run has status SUCCESS, num_turns 1
        and response "" because every tool call was auto-denied in headless mode.
        run_antigravity_task.sh now folds all of that into an `is_error` field it
        writes into the record, so the shell and this layer cannot disagree; the
        checks below re-derive it for records written before that change.
        """
        result_path = ROOT / "generated" / site / f"task_{task_id}" / "antigravity_result.jsonl"
        try:
            lines = [ln for ln in result_path.read_text().splitlines() if ln.strip()]
            if not lines:
                return None
            record = json.loads(lines[-1])
        except (OSError, ValueError):
            return None

        status = record.get("status")
        error = record.get("error")
        response = record.get("response") or ""
        exit_code = record.get("run_exit_code")

        reasons = []
        if error:
            reasons.append(f"error={str(error)[:400]!r}")
        if status is not None and status != "SUCCESS":
            reasons.append(f"status={status!r}")
        if isinstance(exit_code, int) and exit_code != 0:
            reasons.append(f"run_exit_code={exit_code}")
        if not str(response).strip():
            # The most common Gemini failure mode: the session started, burned a
            # turn and returned nothing. Worth naming explicitly, because the
            # alternative is the caller reporting the far vaguer "codegen
            # completed but produced no usable implementation script".
            reasons.append(f"empty response (num_turns={record.get('num_turns')})")

        if not reasons and not record.get("is_error"):
            return None
        if not reasons:
            reasons.append("is_error=True")
        return f"{'; '.join(reasons)} (see {result_path.relative_to(ROOT)})"
