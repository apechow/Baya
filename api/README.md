# Baya API Package

The `api/` directory contains the API schemas Baya plans against, HTTP helper services, reusable Playwright browser helpers, and API tests. The agent's main pipeline calls HTTP endpoints directly; browser helpers support operations that require website interaction.

## Top-level files

| File | Purpose |
|---|---|
| `__init__.py` | Package marker. |
| `README.md` | Package overview and file reference. |
| `schema_index.py` | `resolve_schema_files(sites, index)` deterministically selects main and companion schemas by site-name prefixes, preserving index order and removing duplicates. Shared by the planning and CodeAct agents. |
| `api_guidance.json` | Per-schema guidance loaded by the planner for API conventions, argument formats, and site-specific context. |
| `api_server_prompts.py` | Python prompt constants (`GITLAB_HINTS`, `REDDIT_HINTS`, `SHOPPING_HINTS`, `SHOPPING_ADMIN_HINTS`) and GitLab project-template metadata. Separate from the JSON guidance loaded by the planner. |
| `template.py` | Legacy FastMCP example with typed models and tool decorators. It references the obsolete `api/index.json` path and currently contains a syntax error; it is not the template for the current HTTP helper services. |

## `schemas/` — API definitions

The planner reads `schemas/index.json` and resolves schemas from the requested sites rather than asking an LLM to choose files. The index currently lists seven main and companion schemas. 

| File | Purpose |
|---|---|
| `index.json` | Maps active schema filenames to API descriptions. |
| `gitlab_api_schema.json` | GitLab REST API schema. |
| `gitlab_extra_api_schema.json` | GitLab helper endpoints for reading and regenerating the current user's RSS/feed token. |
| `reddit_api_schema.json` | HTTP schema for the Reddit/Postmill helper service. |
| `shopping_api_schema.json` | Magento storefront REST API schema. |
| `shopping_extra_api_schema.json` | Storefront helper endpoints for fuzzy product search and adding to a wishlist. |
| `shopping_admin_api_schema.json` | Magento admin REST API schema for the separate Luma Admin instance. |
| `shopping_admin_extra_api_schema.json` | Admin helper endpoints for reports, search terms, and fuzzy product search. |
| `openapi_swagger_doc_ce.json` | GitLab CE reference specification; not listed in the active schema index. |


## `servers/` — HTTP helper services

These FastAPI services combine browser automation and HTTP requests to expose operations needed by the benchmark. `initialize.py` at the repository root starts all four by default and provides individual skip flags. They can also be started individually from the repository root:

```bash
python -m api.servers.shopping_extra
python -m api.servers.reddit
python -m api.servers.gitlab_extra
python -m api.servers.shopping_admin_extra
```

Each command runs a long-lived service; use separate terminals when starting several manually. The FastAPI documentation is available at `/docs` on each service's port.

| File | Default port | Operations |
|---|---|---|
| `shopping_extra.py` | 7790 | Fuzzy product search and wishlist additions, including required product options. |
| `reddit.py` | 7791 | Session retrieval; forum listing and lookup; post listing, reading, search, creation, editing, deletion, and voting; user posts; comments and replies; forum creation and subscription; messages; biography/email updates; block-list retrieval. |
| `gitlab_extra.py` | 7792 | Read or reset the current user's RSS/feed token through the GitLab UI. |
| `shopping_admin_extra.py` | 7793 | Bestsellers, orders, and customer totals reports; search terms; fuzzy product catalog search. |

Services accept target `base_url` overrides for benchmark workers. Site credentials come from configuration/environment helpers. Shopping storefront and Shopping Admin are separate instances and require the corresponding credentials and tokens. The services currently bind to `0.0.0.0` when started as modules and perform operations using configured benchmark accounts.

## Playwright helper libraries

The playwright folders implement browser actions, URL resolution, credential configuration, and typed results. Many action functions accept a Playwright `Page`; configuration, URL, and token helpers have their own interfaces. `__init__.py` in each library exposes its public helpers.


## Adding or updating an API

1. Add or update the schema in `schemas/` and its description in `schemas/index.json`. Follow the existing site filename conventions so `schema_index.py` can resolve it.
2. Add relevant planning guidance to `api_guidance.json`.
3. For browser-backed HTTP operations, extend a matching service in `servers/` and reusable helpers in the corresponding `*_pw/` library. Use an existing FastAPI service as the pattern.
4. Keep schema paths, methods, parameters, and response models consistent with the implementation. Add appropriate helper or integration coverage.
5. For a new site or service, update `config/servers.py`, authentication registration/caller setup, and `initialize.py` as needed.

See [agent documentation](../agent/README.md) for schema selection and authentication, and [configuration](../config/README.md) for environment setup.
