# Baya Evaluation

The evaluation package runs agents on WebArena tasks and grades answers, final URLs, and website state. `run_pte_benchmark.py` provides the shared harness; its filename retains the earlier project name.

## Setup and running

Run commands from the repository root with the configured Python environment, provider keys, site credentials, benchmark websites, and required helper services available. See [configuration](../config/README.md), [agents](../agent/README.md), and the root [setup guide](../README.md).

Live benchmark runs can change website state and incur provider usage. The focused unit tests listed below do not require live benchmark tasks.

Several live entry points currently import the locally maintained worker-pool module unconditionally, including single-instance runs. That module is excluded from the public repository. Those entry points require the local orchestration dependency until their imports are made optional or the dependency is supplied separately.

The shared harness can be invoked directly with an explicit task file (the CLI's default task path is outdated):

```bash
python -m eval.run_pte_benchmark \
  --tasks eval/tests/test_files/gitlab_verified_program_html.json \
  --limit 1 --agent-only --output gitlab_results.json
```

For site-specific pytest benchmarks, the corresponding entry point and its local orchestration dependencies must be available:

```bash
python -m pytest eval/tests/test_agent_verified_all_gitlab.py --task-limit 1 -v -s
python -m pytest eval/tests/test_agent_verified_reddit_program_html.py --task-limit 1 -v -s
python -m pytest eval/tests/test_agent_verified_shopping_string_match.py --server shopping --task-limit 1 -v -s
```

The all-site codegen suite defaults to Claude Code and requires `--site`:

```bash
python -m pytest eval/tests/test_agent_verified_all_sites.py \
  --site gitlab --task-limit 1 -v -s

python -m pytest eval/tests/test_agent_verified_all_sites.py \
  --site shopping --task-limit 1 \
  --agent-runner eval.codex_agent_runner.CodexAgentRunner -v -s
```

Codegen drivers and sandbox settings are included under `agent/baya_code_agent/`. See its [README](../agent/baya_code_agent/README.md) for interpreter, CLI, and sandbox requirements.

## Top-level files

| File | Purpose |
|---|---|
| `README.md` | Evaluation overview and file reference. |
| `run_pte_benchmark.py` | `BaseAgentRunner`, the JSON `AgentRunner`, `BaselineRunner`, shared grading/login/reset behavior, and a standalone benchmark CLI. |
| `agent_runner.py` | Custom-runner example (`MyAgentRunner`) and integration notes; imports the JSON `Agent` but is not the shared harness implementation. |
| `claude_code_agent_runner.py` | Claude Code generation/cached-script orchestration using `BayaCodeExecutionAgent`. |
| `codex_agent_runner.py` | Codex generation/cached-script orchestration using the same executor. |
| `antigravity_agent_runner.py` | Antigravity generation/cached-script orchestration using the same executor. |
| `program_html_evaluator.py` | Playwright-based page navigation, locator extraction, and required-content checks. |
| `url_match_evaluator.py` | URL normalization and reference URL matching, with expected-answer details. |
| `run_url_match_eval.py` | Scores generated-task answers from existing logs first; may execute a script when its log is absent. Writes URL-only and optional combined reports under `generated/<site>/`. |
| `run_webarena_verified.py` | ReAct or browser-only agent runs with response artifacts and Playwright network traces for external WebArena-Verified grading. |
| `compare_wa_results.py` | Runs or reads external WebArena-Verified evaluation and combines it with local results using OR semantics. Requires external evaluator configuration when running that evaluator. |
| `gitlab_state_reset.py` | Task-specific GitLab mutation cleanup through its API. |
| `reddit_state_reset.py` | Postmill checkpoint/image restore helpers using remote container operations. Requires separately prepared infrastructure. |
| `REDDIT_RESET.md` | Existing operational notes for preparing and restoring Postmill snapshots; includes local infrastructure assumptions. |

## Task datasets

Files are under `eval/tests/test_files/`. Counts below are the number of entries currently present, not the number that every suite will select; site/evaluation filters can reduce them.

| File | Entries | Purpose |
|---|---:|---|
| `gitlab_verified_program_html.json` | 106 | GitLab page/state checks |
| `gitlab_verified_string_match.json` | 69 | GitLab answer checks |
| `raw_webarena_tasks_no_map.json` | 684 | Raw multi-site task set |
| `raw_webarena_tasks_url_match_updated.json` | 119 | Updated URL-match task set |
| `reddit_verified_program_html.json` | 95 | Reddit page/state checks |
| `reddit_verified_string_match.json` | 11 | Reddit answer checks |
| `shopping_admin_verified_program_html.json` | 66 | Shopping Admin page/state checks |
| `shopping_admin_verified_string_match.json` | 89 | Shopping Admin answer checks |
| `shopping_verified_program_html.json` | 41 | Shopping page/state checks |
| `shopping_verified_string_match.json` | 130 | Shopping answer checks |
| `webarena-verified.json` | 812 | WebArena-Verified task set |

## `tests/` files

| File | Purpose |
|---|---|
| `__init__.py` | Package marker. |
| `conftest.py` | Shared pytest options, fixtures, runner loading, and result/resume helpers. |
| `agent_test_utils.py` | Task-file mappings, task-ID loading, result status, and agent usage/detail helpers. |
| `test_agent_verified_all_sites.py` | Codegen benchmark across one selected site, combining string-match and page/state tasks; supports generation retries. |
| `test_agent_verified_all_gitlab.py` | GitLab page/state benchmark. |
| `test_agent_verified_all_shopping_admin.py` | Shopping Admin benchmark. |
| `test_agent_verified_reddit_program_html.py` | Reddit page/state benchmark. |
| `test_agent_verified_reddit_string_match.py` | Reddit answer benchmark. |
| `test_agent_verified_shopping_program_html.py` | Shopping page/state benchmark. |
| `test_agent_verified_shopping_string_match.py` | Shopping answer benchmark. |
| `test_react_agent_gitlab.py` | ReAct GitLab benchmark integration. |
| `test_ucm_agent_gitlab.py` | Trusted UCM GitLab benchmark integration. |
| `test_codeact_step.py` | Focused tests of CodeAct step/error behavior. |
| `test_openai_responses_agent.py` | Focused tests of the provider-isolated OpenAI Responses agent path. |
| `test_prompt_cache.py` | Prompt caching invariants for the ReAct agent. |
| `test_reddit_program_html_routing.py` | Static tests that Reddit page/state grading targets the assigned instance. |

Focused checks can be selected without launching a live benchmark:

```bash
python -m pytest eval/tests/test_codeact_step.py \
  eval/tests/test_openai_responses_agent.py \
  eval/tests/test_prompt_cache.py \
  eval/tests/test_reddit_program_html_routing.py -v
```

Dependencies required by imported agent modules must still be installed. Avoid an unfiltered `pytest eval/tests/` when intending to run only unit checks.

## Pytest options

These options are registered in `tests/conftest.py`. Individual suites consume different subsets; they do not all implement identical filtering or output behavior.

| Option | Purpose |
|---|---|
| `--task-limit N` | Limit selected tasks. |
| `--task-id ID[,ID,...]` | Select task IDs. |
| `--site SITE` | Site selection: `gitlab`, `reddit`, `shopping`, or `shopping_admin`; required by the all-site codegen suite. |
| `--server SERVER` | Authentication/target server for suites that use the shared runner fixture; defaults to GitLab. |
| `--base-url URL` | Override the target server URL. |
| `--agent-runner MODULE.CLASS` | Load a custom `BaseAgentRunner` subclass. Defaults depend on the suite. |
| `--output FILENAME` | Save results; many suites use `eval/tests/logs/`, while the all-site codegen suite uses `generated/<site>/`. |
| `--resume` | Skip task IDs already in the selected output file where supported. |
| `--start TASK_ID` | Start at a task ID in loaded order; ignored when explicit task IDs are supplied. |
| `--force-reset` | Force pre-task reset behavior where supported. |
| `--agent-trace` | Print HTTP execution commands and responses. |
| `--regenerate-code` | Request fresh code generation rather than cached-script reuse. |
| `--max-attempts N` | All-site codegen execution/generation attempt budget; defaults to 3. An incorrect graded answer is not by itself an execution failure requiring retry. |
| `--multi-docker` | Use the local remote-worker orchestration integration where supported. |
| `--restart-on-release` | Request worker restart after a task in the all-site suite. |
| `--no-reddit-restore` | Skip the all-site suite's post-task single-instance Reddit restore. |

`--no-reset` belongs to the standalone `run_pte_benchmark.py` CLI; it is not registered as a shared pytest option. Use a specific script's `--help` for its standalone CLI options.

## Custom runners

Subclass `BaseAgentRunner` from `eval.run_pte_benchmark` and implement:

```python
from eval.run_pte_benchmark import BaseAgentRunner

class MyAgentRunner(BaseAgentRunner):
    async def _init_agent(self):
        self.client = ...  # Initialize your agent/client.

    async def _run_task(self, task):
        result = await self.client.run(task["intent"])
        return {"final_url": result.url, "answer": result.answer}
```

Return `{"success": False, "error": "description"}` for a hard failure. Text grading needs `answer`; URL grading needs `final_url`. Page/state checks can use an explicit configured URL or the final URL with the evaluator's fallback behavior. The shared harness supplies grading and relevant login/reset behavior.

## Grading and results

- `string_match` checks reference answers using required inclusions/exclusions, alternatives, normalized matching, and fuzzy matching. The current `exact_match` implementation uses normalized containment rather than strict whole-answer equality. `fuzzy_match` set to `N/A` (or `["N/A"]`) is an auto-pass sentinel for unscoreable tasks.
- `program_html` navigates to configured pages, extracts content with locators, and checks required contents. All configured checks must pass.
- `url_match` compares the returned URL with resolved reference alternatives according to the configured matching mode.

Generated code, script output, and codegen results live under `generated/<site>/`. Other suites commonly use `eval/tests/logs/`. External WebArena-Verified runs use their selected output directory for per-task responses and traces. OR-combined reports from the comparison tools are separate scoring views; they do not replace the underlying evaluator results.
