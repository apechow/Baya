"""
Execution agent for the Claude-Code-codegen eval pipeline.

agent/baya_code_agent/codegen/run_claude_task.sh launches the Claude Code CLI to write a Python
implementation script into generated/<site>/task_<id>/, but that session is
explicitly instructed not to run the live, state-changing workflow itself —
it only writes and locally mock-validates the code. This module finds that
generated script and actually executes it against the live site, and the
answer it produces for the task is the result the script itself printed: the
text inside the script's FINAL ANSWER block (see run_claude_task.sh's "FINAL
ANSWER" prompt section), not its trace output.
"""

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, NamedTuple, Optional


class ScriptExecutionError(Exception):
    """Raised when the generated script fails to run to completion."""

    def __init__(self, message: str, captured_output: str = "") -> None:
        super().__init__(message)
        self.captured_output = captured_output


class ScriptRun(NamedTuple):
    """Outcome of running a generated implementation script.

    answer      — the task's answer: the script's FINAL ANSWER block, or (for
                  scripts generated before that contract existed) its
                  trace-stripped stdout.
    raw_output  — everything the script wrote to stdout+stderr.
    has_marker  — whether answer came from a FINAL ANSWER block. False means
                  the fallback was used and the answer still carries trace
                  noise; re-run codegen (--regenerate-code) to fix.
    """

    answer: str
    raw_output: str
    has_marker: bool


# The block generated scripts are instructed to print last, e.g.
#   ===== FINAL ANSWER =====
#   Anglebert Dinkherhump, Dibbins
#   ===== END FINAL ANSWER =====
_ANSWER_BLOCK_RE = re.compile(
    r"^[ \t]*=+[ \t]*FINAL ANSWER[ \t]*=+[ \t]*$\n"
    r"(.*?)"
    r"^[ \t]*=+[ \t]*END FINAL ANSWER[ \t]*=+[ \t]*$",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)

_HTTP_TRACE_RE = re.compile(r"^\s*\[HTTP\]")


def _strip_http_trace_lines(text: str) -> str:
    """Drop the `[HTTP] ...` request/response trace lines that
    run_claude_task.sh's prompt instructs every generated script to print for
    each outbound API call, so the remaining text reads as the script's
    actual prose answer instead of being diluted by request/response noise.
    """
    lines = [line for line in text.splitlines() if not _HTTP_TRACE_RE.match(line)]
    return "\n".join(lines).strip()


def extract_answer(captured: str) -> Optional[str]:
    """Return the text of the script's last FINAL ANSWER block, or None if it
    printed no such block.

    The last block wins: a script that prints intermediate blocks (or whose
    own trace output happens to echo an earlier one) still ends with the real
    answer.
    """
    matches = _ANSWER_BLOCK_RE.findall(captured)
    if not matches:
        return None
    return matches[-1].strip()


class BayaCodeExecutionAgent:
    """Finds and runs the implementation script Claude Code generated for a
    task, capturing its output.

    Usage:
        agent = BayaCodeExecutionAgent()
        script_path = agent.find_script(task_dir)
        answer = await agent.run(script_path, task_dir)
    """

    @staticmethod
    def find_script(task_dir: Path) -> Path:
        """Return the implementation script for the task rooted at task_dir.

        run_claude_task.sh's prompt fixes the layout, so the path is
        deterministic: a task_dir named task_<id> holds task_<id>.py (with its
        test_task_<id>.py counterpart alongside).

        Raises FileNotFoundError if task_dir does not exist or does not contain
        that file — the latter meaning codegen ignored the naming contract.
        """
        if not task_dir.is_dir():
            raise FileNotFoundError(f"Task directory does not exist: {task_dir}")

        script_path = task_dir / f"{task_dir.name}.py"
        if not script_path.is_file():
            raise FileNotFoundError(
                f"No implementation script at {script_path} "
                f"(run_claude_task.sh requires the file be named {script_path.name})"
            )
        return script_path

    async def run(
        self,
        script_path: Path,
        task_dir: Path,
        timeout: int = 120,
        python_bin: str = "./venv/bin/python",
        env: Optional[Dict[str, str]] = None,
    ) -> ScriptRun:
        """Execute script_path live, capturing combined stdout+stderr.

        Appends the run to <task_dir>/python_output.log (the same convention
        run_claude_task.sh itself uses for any live runs it performs during
        its own session), and returns a ScriptRun whose `answer` is the result
        the script printed in its FINAL ANSWER block — that text, not the
        surrounding trace output, is what gets graded. Scripts generated
        before that contract existed print no such block; for those, `answer`
        falls back to the trace-stripped stdout and `has_marker` is False.

        Raises ScriptExecutionError on non-zero exit or timeout; the exception
        carries the raw captured output for debugging (empty on timeout, since
        the process is killed before output can be drained).

        env: extra environment variables merged over the current process's
        environment (e.g. <SITE>_DOMAIN, GITLAB_TOKEN for a specific
        multi-docker worker) — see run_claude_task.sh's "BASE URL RESOLUTION"
        prompt contract, which generated scripts are instructed to honor.
        """
        repo_root = Path(__file__).resolve().parents[2]
        merged_env = {**os.environ, **(env or {})}

        proc = await asyncio.create_subprocess_exec(
            python_bin, str(script_path),
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=merged_env,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass  # process already exited between the timeout firing and our kill
            self._append_log(task_dir, script_path, "(timed out)", None)
            raise ScriptExecutionError(f"{script_path.name} timed out after {timeout}s")

        captured = stdout.decode(errors="replace")
        self._append_log(task_dir, script_path, captured, proc.returncode)

        if proc.returncode != 0:
            raise ScriptExecutionError(
                f"{script_path.name} exited with code {proc.returncode}",
                captured_output=captured,
            )

        answer = extract_answer(captured)
        if answer is not None:
            return ScriptRun(answer=answer, raw_output=captured, has_marker=True)
        return ScriptRun(
            answer=_strip_http_trace_lines(captured),
            raw_output=captured,
            has_marker=False,
        )

    @staticmethod
    def _append_log(task_dir: Path, script_path: Path, captured: str, returncode: Optional[int]) -> None:
        log_path = task_dir / "python_output.log"
        header = (
            f"\n===== claude_code_agent execution: "
            f"{datetime.now(timezone.utc).isoformat()} "
            f"script={script_path.name} exit={returncode} =====\n"
        )
        with open(log_path, "a") as f:
            f.write(header)
            f.write(captured)
            if not captured.endswith("\n"):
                f.write("\n")
