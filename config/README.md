# Baya Configuration

`config/` contains model selection, benchmark server definitions, dependency files, a credential template, and authentication refresh helpers for Baya's compile-then-execute agents. Run the commands below from the repository root.

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Package marker. |
| `config.yaml` | Active provider/model, provider model lists and temperatures, environment-file paths, and the `llm_access_retry` setting. |
| `servers.py` | `SERVERS` definitions with URLs, labels, and username environment-variable names; `SERVER_URLS` exposes the URL mapping. |
| `.env.example` | Credential template with blank provider keys and benchmark account defaults. |
| `pip_requirements.txt` | Broad dependency list for the agent and evaluation code, including pinned and unpinned entries. |
| `conda_env.yaml` | Older Conda environment named `miniscope`, specifying Python 3.10 and a separate dependency set. It is not equivalent to `pip_requirements.txt`. |
| `init_tokens/index.py` | Standalone dictionary expression mapping site names to refresh filenames. It is not an executable dispatcher; its Shopping Admin entry is empty despite the dedicated refresh module below. |
| `init_tokens/refresh_gitlab_token.py` | Browser-based GitLab personal access token creation. |
| `init_tokens/refresh_reddit_session.py` | Postmill session refresh and optional persistence. |
| `init_tokens/refresh_shopping_tokens.py` | Magento admin token for the Shopping storefront instance. |
| `init_tokens/refresh_shopping_admin_tokens.py` | Magento admin token for the separate Shopping Admin instance. |
| `init_tokens/refresh_shopping_customer_token.py` | Magento customer token for the Shopping storefront instance. |
| `README.md` | Configuration reference and setup notes. |

## Model selection

Set `agent_llm_provider` and `agent_llm_model` in `config.yaml`. The currently selected pair is:

```yaml
agent_llm_provider: anthropic
agent_llm_model: claude-sonnet-4-6
```

The model must match an entry under the corresponding `llm_providers` section. Model entries contain `model` and `temp`; individual adapters control how those settings are applied. The configured model list is a local lookup table, not a live provider availability check.

| Provider selector | Credential variable | Model-list section |
|---|---|---|
| `openai` | `OPENAI_API_KEY` | `llm_providers.openai` |
| `anthropic` | `ANTHROPIC_API_KEY` | `llm_providers.anthropic` |
| `google` or `google-gla` | `GOOGLE_API_KEY` or `GEMINI_API_KEY` | `llm_providers.google` |
| `deepseek` | `DEEPSEEK_API_KEY` | `llm_providers.deepseek` |
| `openrouter` | `OPENROUTER_API_KEY` | `llm_providers.openrouter` |

Adapters live in `agent/providers/`. CLI codegen drivers have their own provider restrictions and authentication requirements; selecting a provider here does not authenticate an external CLI.

## Local environment files

Create the client credential file from the template:

```bash
cp config/.env.example config/.env
```

Fill in the key for your active provider and the credentials for your benchmark instances. `.env` and `.server_env` are ignored by Git.

The shared `Configurator` in `agent/common/configurator.py` reads `config.yaml` relative to the repository root. Its environment loaders use these configured paths:

| Setting | Default path | Loading behavior |
|---|---|---|
| `client_env_path` | `config/.env` | Loads provider keys and site credentials with `override=True`, replacing existing shell values. |
| `shared_env_path` | `config/.sharedenv` | Optional shared environment loaded without overriding existing values. |
| `server_env_path` | `config/.server_env` | Server token environment loaded without overriding existing values. |
| `mcp_env_path` | `config/.mcpenv` | Separate loader for MCP configuration; not included in `load_all_env()`. |

`load_all_env()` loads client, shared, then server files. The JSON planner and executor call it during initialization. Merely constructing `Configurator` reads YAML; it does not load every environment file automatically. Token refresh functions may read credential files directly rather than use exported shell values.

### Site credentials

The template defines these names:

| Site | Variables |
|---|---|
| GitLab | `GITLAB_USERNAME`, `GITLAB_PASSWORD` |
| Reddit/Postmill | `REDDIT_USERNAME`, `REDDIT_PASSWORD` |
| Shopping customer | `SHOPPING_USERNAME`, `SHOPPING_PASSWORD` |
| Magento admin accounts | `SHOPPING_ADMIN_USER`, `SHOPPING_ADMIN_PASS` |

The supplied values are benchmark defaults. Adjust them for your deployment. Two refresh helpers differ from this convention:

- `refresh_gitlab_token.py` uses its module-level `USERNAME` and `PASSWORD` constants rather than reading the GitLab variables from `.env`.
- `refresh_shopping_customer_token.py` reads `SHOPPING_USER` and `SHOPPING_PASS` from the credential file, falling back to its built-in benchmark account. Set those names explicitly when using that helper with another customer.

`REMOTE_HOST` in the template is an SSH target for separately maintained remote orchestration. It is not needed for the model adapters or token helpers themselves.

## Server URLs

`servers.py` defines the following single-instance defaults. Callers can pass runtime URLs for other instances; editing this mapping does not provision services.

| Server | Default URL |
|---|---|
| `gitlab` | `http://127.0.0.1:8023` |
| `reddit` | `http://127.0.0.1:9999` |
| `shopping` | `http://localhost:7770` |
| `shopping_admin` | `http://localhost:7780` |
| `wikipedia` | `http://127.0.0.1:8889` |
| `shopping_extra` | `http://127.0.0.1:7790` |
| `reddit_extra` | `http://127.0.0.1:7791` |
| `gitlab_extra` | `http://127.0.0.1:7792` |
| `shopping_admin_extra` | `http://127.0.0.1:7793` |

The Magento instances use `localhost` because redirects to their configured canonical host can discard query parameters when addressed through `127.0.0.1`. Shopping and Shopping Admin have separate databases and tokens; keep their URLs distinct. A server definition alone does not imply schema or authentication support for that site.

## Tokens and sessions

`agent/baya_json_agent/auth.py` reads `config/.server_env` through `AuthRegistry.build_default()`. A GitLab entry can be supplied as:

```dotenv
GITLAB_TOKEN=<personal-access-token>
```

The registry sends it as a `PRIVATE-TOKEN` header. It registers empty authentication placeholders for Shopping, Shopping Admin, and Reddit; callers must inject the appropriate runtime credentials. Companion services handle site authentication internally.

Some helpers and legacy MCP utilities also recognize `ADMIN_AUTH_TOKEN`, `CUSTOMER_AUTH_TOKEN`, or `REDDIT_PHPSESSID`. Their presence in the token file does not automatically authenticate the default JSON registry's placeholder entries.

### Refresh helpers

| Module | Callable and behavior |
|---|---|
| `refresh_gitlab_token.py` | `refresh_token(base_url=...)` creates and returns a GitLab PAT through Playwright. Disk-writing code is commented out; it does not update `.server_env`. Its CLI prints the complete token. |
| `refresh_reddit_session.py` | `refresh_session(base_url=..., credentials_file=..., server_env_file=...)` logs in using Reddit credentials. On refresh failure it can fall back to `REDDIT_PHPSESSID` in the token file. `update_server_env(...)`, or CLI `--write`, persists the session. Its CLI prints the complete session ID. |
| `refresh_shopping_tokens.py` | `refresh_tokens(base_url=..., credentials_file=..., server_env_file=...)` obtains an admin token from the Shopping instance. On refresh failure it can reuse `ADMIN_AUTH_TOKEN`. Refresh itself does not write to disk; `write_admin_token_to_env(...)` is a separate persistence helper. |
| `refresh_shopping_admin_tokens.py` | `refresh_tokens(base_url=..., credentials_file=...)` obtains an admin token from Shopping Admin. It does not reuse the storefront token or write to disk. Use the bare instance root, without `/admin`. |
| `refresh_shopping_customer_token.py` | `refresh_customer_token(base_url=..., credentials_file=...)` obtains a customer token from Shopping using the helper-specific credential names described above. Returns the token without persisting it. |

For example, these commands refresh tokens against the corresponding configured defaults:

```bash
python -m config.init_tokens.refresh_shopping_tokens
python -m config.init_tokens.refresh_shopping_admin_tokens
python -m config.init_tokens.refresh_shopping_customer_token
```

Inspect `--help` for supported URL/credential overrides. For GitLab, use `python -m config.init_tokens.refresh_gitlab_token`; that CLI has no argument parser, so a different target URL must be passed to `refresh_token()` in Python. Reddit supports:

```bash
python -m config.init_tokens.refresh_reddit_session --write
```

These commands require live websites and valid credentials. GitLab token creation additionally requires Playwright with Chromium installed. CLI token/session output should be kept out of shared logs.

See [agent documentation](../agent/README.md), [API documentation](../api/README.md), and [evaluation documentation](../eval/README.md) for integration details.
