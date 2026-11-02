# Baya Agent

The `agent/baya_json_agent/` package implements Baya's two-phase **plan → execute** pipeline for completing natural language tasks against web server APIs. It uses direct HTTP requests through `curl`; the MCP utilities in `agent/common/` are separate from this pipeline.

## How it works

1. `Agent.initialize(servers)` creates the planner and, unless execution is disabled, an executor with authentication for the supplied server names.
2. `Agent.run_task(prompt, servers=None, sites=None)` asks `PlanningAgent.plan(task, sites)` to build a plan. Sites default to the active server names. Schema files are selected deterministically through `api.schema_index.resolve_schema_files`, using `api/schemas/index.json`.
3. The planner parses schemas with `prance`, uses an LLM to filter endpoints and build a backward-chained plan, then checks and repairs the plan using LLM feedback and deterministic validation.
4. The wrapper resolves schema routing tags to runtime server URLs. `ExecutionAgent` executes HTTP calls, resolves dependencies and loops, and produces an `ExecutionResult` containing raw outputs and a natural language answer.

Call `initialize()` before `run_task()`. Per-task `servers` can override the initialized URLs, and `sites` can explicitly select the target sites. `skip_execution=True` builds a plan without making the execution requests; the plan is available as `last_plan_response`. Planning still requires an LLM.

Run callers from the repository root when using the default relative paths. See [configuration](../../config/README.md) for credentials and model settings, and [evaluation](../../eval/README.md) for benchmark runners.

## Top-level files

| File | What it does |
|---|---|
| `__init__.py` | Package marker. |
| `agent.py` | High-level `Agent` wrapper: initializes planner/executor, supports per-task server and site overrides, injects runtime base URLs, and applies planning and execution timeouts. |
| `planning_agent.py` | Loads schemas and API guidance, selects relevant endpoints, builds backward chains and typed plans, checks and repairs plans, and records planning usage and debug information. |
| `execution_agent.py` | Executes plan steps via `curl`, routes path/query/body arguments, resolves references to prior outputs, handles conditional steps and iteration, and uses an LLM for output parsing and final answers. Defines `ExecutionResult` and tracks execution token usage. |
| `planner.py` | Defines `ConditionalStep`, the `build_agent_models` factory for runtime-typed plan models, dependency validation, ready-step selection, plan/execution formatting, and `ExecutionContext` for step state. |
| `plan_checker.py` | `SwaggerPlanChecker`: deterministic checks for missing required parameters and body fields against Swagger endpoint definitions. Issues feed into the planner's repair loop. |
| `auth.py` | Defines `AuthProvider`, `HeaderAuth`, `CookieAuth`, `MultiAuth`, `StaticAuth`, `RoutingAuth`, `RefreshableAuth`, and `AuthRegistry`. Supports fixed headers/cookies, combined providers, URL-based routing, and token refresh. |
| `README.md` | Package overview and file reference. |

## Shared modules

Configuration, shared types, logging, and legacy MCP utilities live in `agent/common/`. LLM adapters live in `agent/providers/`, supporting OpenAI, Anthropic, Google/Gemini, DeepSeek, and OpenRouter. See the [agent overview](../README.md) for the shared file inventory.

## Schemas, guidance, and authentication

| File | Purpose |
|---|---|
| `api/schemas/index.json` | Schema filenames and descriptions. The site resolver matches main and companion schemas to requested sites. |
| `api/schemas/*.json` | Swagger/OpenAPI definitions parsed by the planner and used for endpoint details and validation. |
| `api/schema_index.py` | Shared deterministic site-to-schema resolver. |
| `api/api_guidance.json` | Per-schema guidance loaded by the planner for API conventions and planning context. |
| `config/config.yaml` | Provider/model selection, model definitions, and environment-file paths. |
| `config/servers.py` | Canonical server URLs, labels, and username environment-variable names used to build planning context. |
| `config/.env` | Local provider keys and site credentials; excluded from version control. |
| `config/.server_env` | Token file read by `AuthRegistry.build_default()`; excluded from version control. GitLab is registered when `GITLAB_TOKEN` is present. |

The default auth registry registers empty `StaticAuth` placeholders for Shopping, Shopping Admin, and Reddit; callers must supply their runtime authentication. Companion API services are registered without outbound authentication headers because they handle site login internally. `RoutingAuth` can choose credentials by request URL, and `RefreshableAuth` can refresh expiring tokens. The `Agent` wrapper does not automatically configure either of these providers.

Adding a site can require changes to schema definitions/index entries, guidance, server context, and authentication registration. URL and site overrides let callers reuse the same agent against different benchmark instances.
