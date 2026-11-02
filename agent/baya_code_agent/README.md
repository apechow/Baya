# Baya Code Agent

The `agent/baya_code_agent/` package executes generated Python scripts against benchmark websites and extracts their final answers. It is the shared execution layer for the Claude Code, Codex, and Antigravity evaluation runners. All three runners use `BayaCodeExecutionAgent`.

Code generation and grading live outside this package. Unlike Baya's plan → execute agent in `agent/`, this executor runs an existing Python artifact rather than building an execution plan or calling an LLM itself.

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Empty package marker; import execution classes directly from `agent.baya_code_agent.execution_agent`. |
| `execution_agent.py` | Script lookup, asynchronous subprocess execution, combined output capture, execution logging, answer extraction, and error/result types. |
| `README.md` | Package overview, execution contract, and integration notes. |

## Execution interface

`execution_agent.py` exposes:

| Name | Purpose |
|---|---|
| `BayaCodeExecutionAgent` | Finds and executes a generated implementation script. |
| `ScriptRun` | Named tuple containing `answer`, `raw_output`, and `has_marker`. |
| `ScriptExecutionError` | Raised for a non-zero script exit or execution timeout. Includes a `captured_output` attribute. |
| `extract_answer(captured)` | Returns the stripped contents of the last complete FINAL ANSWER block, or `None` if no block is present. |

### Finding a script

`BayaCodeExecutionAgent.find_script(task_dir)` expects a directory named `task_<id>` containing `task_<id>.py`. It returns that script's path or raises `FileNotFoundError` if the directory or script is missing. The conventional full layout is:

```text
generated/<site>/task_<id>/
├── task_<id>.py
├── test_task_<id>.py     # optional codegen artifact; not executed by this package
└── python_output.log    # appended by the executor
```

The lookup uses the task directory's name; it does not search for alternative filenames or require a test file.

### Running a script

```python
import asyncio
import sys
from pathlib import Path

from agent.baya_code_agent.execution_agent import BayaCodeExecutionAgent

async def main():
    # Run this example from the repository root, with a generated script present.
    task_dir = Path("generated/gitlab/task_1").resolve()
    executor = BayaCodeExecutionAgent()
    script = executor.find_script(task_dir)
    result = await executor.run(
        script,
        task_dir,
        python_bin=sys.executable,
        timeout=120,
        env={"GITLAB_DOMAIN": "http://localhost:8023"},
    )
    print(result.answer)

asyncio.run(main())
```

`run(script_path, task_dir, timeout=120, python_bin="./venv/bin/python", env=None)` launches the script with the repository root as its working directory. Pass absolute paths when calling from another directory. The default interpreter requires a `venv/` at the repository root; callers can supply another interpreter as shown above.

The executor merges `env` over the current process environment and captures stdout and stderr together. Callers can provide site URLs, tokens, and companion-service URLs for the assigned benchmark instance. The generated script must honor those variables; the executor does not rewrite its URLs or obtain credentials.

### Answer contract

Generated scripts should print their answer inside this block:

```text
===== FINAL ANSWER =====
<the answer>
===== END FINAL ANSWER =====
```

`extract_answer` uses the last complete matching block. Matching is case-insensitive and permits variations in horizontal spacing and the number of equals signs. On success, `ScriptRun.has_marker` is `True`, and `raw_output` retains the full captured output.

If no complete block exists, `run()` returns captured output with lines beginning with `[HTTP]` removed as the fallback answer, and sets `has_marker=False`. This fallback includes other stdout/stderr text and can contain diagnostic noise. Marker presence alone does not validate the answer's correctness; grading belongs to the evaluation harness.

### Logging and failures

Each completed subprocess run appends its combined output to `task_dir/python_output.log`, preceded by a UTC timestamp, script filename, and exit code. The log header currently retains the legacy label `claude_code_agent execution`.

A non-zero exit raises `ScriptExecutionError` with the captured output after logging it. A timeout kills the direct subprocess, records `(timed out)`, and raises `ScriptExecutionError` with empty `captured_output`; partial process output is not retained on this path. Missing interpreter and subprocess startup errors propagate directly. The task directory must already exist and be writable for logging.

## Evaluation integration and code generation

| Runner | Local codegen driver |
|---|---|
| `eval/claude_code_agent_runner.py` | `agent/baya_code_agent/codegen/run_claude_task.sh` |
| `eval/codex_agent_runner.py` | `agent/baya_code_agent/codegen/run_codex_task.sh` |
| `eval/antigravity_agent_runner.py` | `agent/baya_code_agent/codegen/run_antigravity_task.sh` |

All three runners import this package and currently support `gitlab`, `reddit`, `shopping`, and `shopping_admin`. They orchestrate cached-script reuse or generation, inject runtime environment variables, execute the script, and pass its answer to the inherited benchmark grading. Retry/replan behavior is controlled by the runners and harness, not by this executor.

The local codegen workflow is designed to write and mock-validate scripts separately from live execution. Its prompts, sandbox setup, CLI authentication, slots, and codegen logs belong to the drivers in `codegen/`; this package does not enforce that codegen boundary or sandbox the generated script's live execution.

Runtime codegen drivers and their sandbox configuration now live in this package and are included in the public repository. The ignored root `scripts/` directory contains local utilities only. Full codegen evaluation still requires Bash 4 or newer, the relevant CLI, authentication, Linux sandbox tools, and the configured Python environment.

To add another codegen backend, integrate its driver with an evaluation runner and preserve the task filename and answer contracts above. To add a site, update the relevant runner's supported-site checks, codegen guidance/configuration, credentials, and benchmark environment. There is no site whitelist inside this execution module.

See [evaluation documentation](../../eval/README.md) for the harness and [configuration](../../config/README.md) for site and provider setup.

## Codegen and sandbox files

| Directory | Files and purpose |
|---|---|
| `codegen/` | `run_claude_task.sh`, `run_codex_task.sh`, and `run_antigravity_task.sh` orchestrate generation. `build_site_prompt.sh` builds site instructions from `invariant_rules.md`; `lookup_task.py`, `lookup_identity.py`, and `lookup_api_hints.py` supply context. `build_endpoint_catalog.py`, `query_schema.py`, and `schema_lib.py` render schema information. `__init__.py` marks the package. |
| `sandbox/` | `codex_sandbox.sh` and `antigravity_sandbox.sh` wrap generation in an OS sandbox. `claude_settings/` contains `gitlab_settings.sh`, `reddit_settings.sh`, `shopping_settings.sh`, and `shopping_admin_settings.sh` for site-specific permissions. |
| `tests/` | `schema_lib_check.py` compares codegen schema rendering with the JSON planner; `__init__.py` marks the package. Run `python -m agent.baya_code_agent.tests.schema_lib_check` from the repository root. |
| `run_claude_task.design.md` | Design notes for the Claude Code generation workflow. |

Shared configuration and LLM adapters live in `agent/common/` and `agent/providers/`.
