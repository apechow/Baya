# Baya — Compile-Then-Execute Agents

Baya separates task compilation from execution against WebArena benchmark websites: GitLab, Reddit (Postmill), Shopping, and Shopping Admin. It includes two agent implementations, API schemas and browser helpers, evaluation code, and deterministic matching tools.

## Agents

- **Baya Code Agent** executes generated Python scripts and extracts their final answers. Its codegen drivers support Claude Code, Codex, and Antigravity; all three evaluation runners use `BayaCodeExecutionAgent`. Generation and live script execution are separate stages.

- **Baya JSON Agent** compiles a natural language task into a validated JSON execution plan, then executes its steps as HTTP requests through `curl`. The planner selects schemas from the target sites, resolves dependencies, and checks and repairs the plan before execution.

Shared configuration utilities live in `agent/common/`, and LLM adapters live in `agent/providers/`. Supported providers include OpenAI, Anthropic, Google/Gemini, DeepSeek, and OpenRouter.

## Configuration

Install dependencies from `config/pip_requirements.txt` in your Python environment. Copy the credential template before supplying your provider keys and benchmark account credentials:

```bash
python -m pip install -r config/pip_requirements.txt
cp config/.env.example config/.env
```

Select a provider and a matching model from the `llm_providers` entries in `config/config.yaml`. Server URLs are defined in `config/servers.py`; token-refresh helpers live in `config/init_tokens/`. See [configuration](config/README.md) for details.

The agents require reachable benchmark websites and the relevant credentials. Browser-backed operations also require Playwright and its browser installation. Code generation requires the corresponding authenticated CLI; its shell drivers expect Bash 4 or newer, the configured Python environment, and the applicable sandbox tooling.

## API services

`api/schemas/` contains the main and companion API definitions. `api/schema_index.py` resolves schemas by site, and `api/api_guidance.json` supplies planning guidance.

The included FastAPI helper services expose operations that need browser interaction or additional site-specific handling. Run each needed service in its own terminal from the repository root:

| Command | Port | Purpose |
|---|---:|---|
| `python -m api.servers.shopping_extra` | 7790 | Product search and wishlist additions. |
| `python -m api.servers.reddit` | 7791 | Postmill posts, comments, forums, messages, and account operations. |
| `python -m api.servers.gitlab_extra` | 7792 | GitLab RSS/feed token operations. |
| `python -m api.servers.shopping_admin_extra` | 7793 | Admin reports, search terms, and product search. |

See [API documentation](api/README.md) for schemas, helper libraries, integration tests, and service behavior.

## Evaluation and tests

`eval/` includes the shared benchmark harness, agent runners, text/URL/page-state evaluators, result comparison tools, state-reset helpers, and task datasets. The codegen runners call drivers included under `agent/baya_code_agent/`.

Some live evaluation entry points depend on local orchestration components that are not included here. They need that infrastructure before they can run, including some single-instance paths. The included tests also cover other agent integrations whose implementations are not part of this snapshot. See [evaluation documentation](eval/README.md) for per-runner requirements and options.


## Repository layout

| Path | Contents |
|---|---|
| `agent/baya_json_agent/` | JSON planning, validation, HTTP execution, and authentication. |
| `agent/baya_code_agent/` | Script execution, codegen drivers and invariant instructions, sandbox settings, and schema consistency checks. |
| `agent/common/` | Configuration, shared types, logging, and MCP utilities. |
| `agent/providers/` | LLM provider adapters. |
| `api/` | Schemas, API guidance, HTTP services, Playwright helpers, and API tests. |
| `backend/` | Standalone Anthropic, Gemini, OpenAI, and DeepSeek backend wrappers. |
| `config/` | Model/server settings, environment template, dependency files, and token-refresh helpers. |
| `eval/` | Benchmark runners, evaluators, reset helpers, tests, and task datasets. |
| `tools/` | Deterministic fuzzy matching helpers and their tests. |
| `baya_code_agent/`, `baya_json_agent/` | Earlier top-level agent copies also present in this snapshot; the integrated runners use the implementations under `agent/`. |

## Documentation

- [Agent overview](agent/README.md)
- [Baya JSON Agent](agent/baya_json_agent/README.md)
- [Baya Code Agent](agent/baya_code_agent/README.md)
- [API package](api/README.md)
- [Configuration](config/README.md)
- [Evaluation](eval/README.md)
- [Tools](tools/README.md)
