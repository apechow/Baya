# Baya Agents
`baya_code_agent/` generates Python scripts through CLI backends and executes those scripts.

`baya_json_agent/` plans typed JSON steps and executes HTTP calls (earlier design). 

| Directory | Purpose |
|---|---|
| [baya_json_agent](baya_json_agent/README.md) | Planner, plan models/checker, HTTP execution, and HTTP authentication. |
| [baya_code_agent](baya_code_agent/README.md) | Code generation drivers, sandbox configuration, script executor, and schema rendering check. |
| `common/` | Shared `configurator.py`, `types.py`, and `utils.py`; legacy MCP utilities `mcp_client.py`, `tool_manager.py`, and `token_manager.py`; package marker `__init__.py`. |
| `providers/` | `provider.py` dispatches to `openai.py`, `anthropic.py`, `google.py`, `deepseek.py`, or `openrouter.py`; `__init__.py` marks the package. |
| `__init__.py` | Agent package marker. |

Import shared utilities through `agent.common` and adapters through `agent.providers`. Runtime codegen dependencies are included under `baya_code_agent/`; the ignored root `scripts/` directory is for local utilities. Run commands from the repository root and configure the environment through [config](../config/README.md).
