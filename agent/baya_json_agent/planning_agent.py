"""
Planning agent: selects the appropriate swagger file(s), parses them with prance,
identifies the best API endpoints for a given task, and returns a full execution
plan in the planner.py ToolBasedResponse format.
"""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, List, Optional, Union

import prance
from pydantic import BaseModel
from pydantic_ai import Agent

from agent.common.configurator import Configurator
from agent.baya_json_agent.plan_checker import SwaggerPlanChecker
from agent.baya_json_agent.planner import build_agent_models, validate_plan
from agent.providers.provider import ModelProvider
from api.schema_index import resolve_schema_files

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


# ── Structured output models for each LLM sub-call ──────────────────────────

# _ApiSelection was the structured-output model for the LLM-based swagger
# file selector (_select_api_files, below). Commented out along with it —
# file selection is now driven by the task's `sites` list via
# api.schema_index.resolve_schema_files() instead of an LLM guess.
# class _ApiSelection(BaseModel):
#     files: List[str] = []

class _ExclusionResult(BaseModel):
    excluded_indices: List[int] = []

class _Arg(BaseModel):
    key: str
    value: Union[str, int, float, bool, None]

def _args_to_dict(args: List[_Arg]) -> dict:
    return {a.key: a.value for a in args}

class _ResolverSpec(BaseModel):
    endpoint_index: int
    capability: str = ""
    satisfies_param: str = ""
    literal_args: List[_Arg] = []
    foreach: Optional[Union[str, List[str]]] = None

class _GoalResult(BaseModel):
    goal_index: int
    literal_args: List[_Arg] = []
    foreach: Optional[Union[str, List[str]]] = None
    required_resolvers: List[_ResolverSpec] = []

class _ResolverResult(BaseModel):
    endpoint_index: Optional[int] = None
    satisfies_param: str = ""
    literal_args: List[_Arg] = []
    foreach: Optional[Union[str, List[str]]] = None
    capability: str = ""

class _CheckResult(BaseModel):
    issues: List[str] = []
    ok: bool = True


class EndpointInfo(BaseModel):
    api: str            # source swagger filename
    method: str         # GET, POST, etc.
    path: str           # /V1/carts/mine
    summary: str
    description: str
    parameters: list    # raw swagger parameter dicts
    response_schema: str = ""  # human-readable description of the 200 response shape
    base_path: str = ""  # swagger basePath (e.g. /api/v4) — path prefix, NOT host


class ChainStep(BaseModel):
    endpoint: EndpointInfo
    capability: str
    satisfies_param: str = ""        # which param in the next chain step this step's output satisfies
    literal_args: dict = {}          # {param_name: value} directly from the task
    foreach: Union[str, List[str], None] = None  # literal list or "step_N.result[*].field" reference


class PlanningAgent:
    """
    Given a natural language task, selects the right swagger file(s) from
    api/index.json, parses them with prance (resolving all $refs),
    chooses the best endpoints via LLM, and builds a full execution plan.

    The LLM backend is resolved from config.yaml (agent_llm_provider /
    agent_llm_model) via ModelProvider, matching the pattern in agent_replan.py.

    Debug flags are auto off by default but can be enabled when initializing the agent to print
    the full LLM prompts and responses for each step.
    """

    def __init__(self, api_dir: str = "api", debug_prompts: bool = False, debug_responses: bool = False) -> None:
        self.api_dir = Path(api_dir)
        self.debug_prompts = debug_prompts
        self.debug_responses = debug_responses
        self._run_log: list = []  # structured log for the current plan() call — always populated
        self._run_costs: list = []  # per-LLM-call costs for the last plan() run
        # cumulative token usage for the last plan() run
        self._run_tokens: dict = {"input": 0, "output": 0, "total": 0, "llm_calls": 0}

        config = Configurator()
        config.load_all_env()
        provider = ModelProvider(config)
        self._model_name: str = provider.model_name  # from config.yaml agent_llm_model
        self.llm = provider.get_llm_model_provider()
        self._agent_kwargs = provider.get_agent_kwargs()

    def _debug_print(self, label: str, prompt: str = None, response: str = None) -> None:
        if prompt is not None and self.debug_prompts:
            print("\n" + "=" * 60)
            print(f"[PlanningAgent] {label} PROMPT:")
            print(prompt)
            print("=" * 60)
        if response is not None and self.debug_responses:
            print(f"\n[PlanningAgent] {label} RESPONSE:")
            print(response)
            print("=" * 60 + "\n")

    # Labels whose response data is too verbose to be worth storing in the run log.
    _SKIP_LOG_LABELS: frozenset = frozenset({
        "_exclude_unrelated_endpoints.response",
    })

    def _record(self, label: str, data) -> None:
        """Always append a structured entry to _run_log, regardless of debug flags."""
        if label not in self._SKIP_LOG_LABELS:
            self._run_log.append({"label": label, "data": data})

    @property
    def last_run_log(self) -> list:
        """Return the accumulated log from the most recent plan() call."""
        return list(self._run_log)

    @property
    def last_run_costs(self) -> list:
        """Return per-LLM-call costs (floats) from the most recent plan() call."""
        return list(self._run_costs)

    @property
    def last_run_tokens(self) -> dict:
        """Return cumulative token usage from the most recent plan() call."""
        return dict(self._run_tokens)

    async def _run_agent(self, label: str, prompt: str, output_type: type) -> Any:
        self._debug_print(label, prompt=prompt)
        if self.debug_prompts or self.debug_responses:
            print(f"[PlanningAgent] {label} calling LLM (prompt length: {len(prompt)} chars)...")
        agent = Agent(self.llm, output_type=output_type, **self._agent_kwargs)
        try:
            result = await asyncio.wait_for(agent.run(prompt), timeout=300) #increased from 120s for gemini
        except asyncio.TimeoutError as exc:
            msg = f"[PlanningAgent] {label} LLM call timed out after 300s"
            print(f"\n{msg}")
            self._record(f"{label}.llm_timeout", msg)
            raise TimeoutError(msg) from exc
        except BaseException as exc:
            if isinstance(exc, Exception):
                cause = getattr(exc, "__cause__", None)
                err_msg = (f"[PlanningAgent] {label} LLM ERROR: {type(exc).__name__}: {exc}"
                           + (f"\n  caused by: {type(cause).__name__}: {cause}" if cause else ""))
                print(f"\n{err_msg}")
                self._record(f"{label}.llm_error", err_msg)
            else:
                msg = f"[PlanningAgent] {label} cancelled (outer timeout or shutdown)"
                print(f"\n{msg}")
                self._record(f"{label}.llm_cancelled", msg)
            raise
        try:
            import litellm
            usage = result.usage()
            in_tok = usage.input_tokens or 0
            out_tok = usage.output_tokens or 0
            self._run_tokens["input"] += in_tok
            self._run_tokens["output"] += out_tok
            self._run_tokens["total"] += in_tok + out_tok
            self._run_tokens["llm_calls"] += 1
            cost_in, cost_out = litellm.cost_per_token(
                model=self._model_name,
                prompt_tokens=in_tok,
                completion_tokens=out_tok,
            )
            self._run_costs.append(round(cost_in + cost_out, 8))
        except Exception:
            self._run_costs.append(None)
        output = result.output
        self._record(f"{label}.response", output.model_dump(mode="json") if hasattr(output, "model_dump") else output)
        self._debug_print(label, response=str(output))
        return output

    # ------------------------------------------------------------------
    # Step 1: load swagger index and api hints
    # ------------------------------------------------------------------
    def _load_index(self) -> dict:
        index_path = self.api_dir / "schemas" / "index.json"
        with open(index_path) as f:
            return json.load(f)

    def _load_hints(self) -> dict:
        hints_path = self.api_dir / "api_guidance.json"
        if not hints_path.exists():
            return {}
        with open(hints_path) as f:
            raw = json.load(f)
        import importlib.util
        prompts_path = self.api_dir / "api_server_prompts.py"
        spec = importlib.util.spec_from_file_location("api_server_prompts", prompts_path)
        prompts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prompts)
        return {
            k: getattr(prompts, v, v)
            for k, v in raw.items()
        }

    def _build_hints_section(self, endpoints: List[EndpointInfo], header: str = "API context") -> str:
        hints = self._load_hints()
        api_files_used = {ep.api for ep in endpoints}
        relevant_hints = [
            f"[ {fname} ]\n{hints[fname]}"
            for fname in api_files_used
            if fname in hints
        ]
        if not relevant_hints:
            return ""
        return f"\n{header}:\n" + "\n\n".join(relevant_hints) + "\n"

    # ------------------------------------------------------------------
    # Step 2: LLM picks which swagger file(s) to use
    #
    # Commented out: swagger-file selection is now driven directly by the
    # task's `sites` list (see plan() below), via
    # api.schema_index.resolve_schema_files(). Every task already carries a
    # `sites` field naming the site(s) it targets, so guessing the platform
    # from task text with an LLM call is both unnecessary and a source of
    # misselection. Left here (commented, not deleted) in case the LLM-based
    # approach needs to be revisited.
    # ------------------------------------------------------------------
    # async def _select_api_files(self, task: str, index: dict) -> List[str]:
    #     api_list = "\n".join(f"- {fname}: {desc}" for fname, desc in index.items())
    #     prompt = (
    #         f'''Task: {task}
    #
    #         Available API schema files:
    #         {api_list}
    #
    #         Your job is to select the API schema file(s) that are most likely needed to complete the task.
    #
    #         Before choosing files, internally think about:
    #         - what system the task refers to
    #         - which APIs would normally support that action
    #         - which filenames are most likely to contain those APIs
    #
    #         Important rules:
    #
    #         1. Do NOT assume the platform or service from the task wording alone.
    #         Example: "repository" does NOT automatically mean GitHub. It could refer to GitLab, Gitea, Bitbucket, or another system.
    #
    #         2. Match the task to APIs by meaning, not by exact words.
    #         Consider synonyms and related concepts. For example:
    #         - repository ≈ project
    #         - pull request ≈ merge request
    #         - user account ≈ profile
    #         - cart ≈ basket
    #         - post ≈ submission
    #         - comment ≈ reply
    #
    #         3. If the task appears unrelated to the API filenames, still check for conceptual matches.
    #         Many APIs expose similar functionality under different names.
    #
    #         4. The concept lists below are high-level summaries and are NOT comprehensive.
    #         A task may still be supported by a schema even if the exact keywords do not appear.
    #
    #         Do not reject a file simply because the task wording does not match the listed concepts.
    #         Match by meaning and related functionality.
    #
    #         When uncertain, prefer selecting a plausibly relevant file rather than excluding it.
    #         Only return [] if you are confident that none of the schemas could possibly support the task.
    #
    #         5. Multiple files may be required to complete the task. Select all that might contain relevant endpoints.
    #
    #         Output format: return a JSON object with a "files" key containing the list of selected filenames.
    #         Examples:
    #         {{"files": ["shopping_api_schema.json"]}}
    #         {{"files": ["gitlab_api_schema.json", "user_api_schema.json"]}}
    #         If no file is relevant: {{"files": []}}
    #         '''
    #     )
    #     result: _ApiSelection = await self._run_agent("_select_api_files", prompt, _ApiSelection)
    #     return [f for f in result.files if f in index]

    # ------------------------------------------------------------------
    # Step 3: parse swagger file with prance
    # ------------------------------------------------------------------
    def _parse_swagger(self, filename: str) -> dict:
        filepath = (self.api_dir / "schemas" / filename).absolute()
        parser = prance.BaseParser(str(filepath), lazy=False)
        return parser.specification

    # ------------------------------------------------------------------
    # Step 4: extract endpoints from resolved spec
    # ------------------------------------------------------------------
    def _extract_response_schema(self, spec: dict, operation: dict) -> str:
        responses = operation.get("responses", {})
        ok = responses.get("200") or responses.get("201")
        if not isinstance(ok, dict):
            return ""

        description = ok.get("description", "")
        schema = ok.get("schema")

        _GENERIC = {"successful", "success", "ok", "200", "201", "no content", "accepted"}

        if not isinstance(schema, dict):
            return description if description.lower().rstrip(".") not in _GENERIC else ""

        def _resolve_ref(ref: str) -> dict:
            if ref.startswith("#/definitions/"):
                def_name = ref.split("/definitions/", 1)[-1]
                return spec.get("definitions", {}).get(def_name, {})
            return {}

        def _collect_props(definition: dict, max_props: int = 12) -> list:
            props = list(definition.get("properties", {}).keys())
            for sub in definition.get("allOf", []):
                if isinstance(sub, dict) and "$ref" not in sub:
                    props.extend(sub.get("properties", {}).keys())
            return props[:max_props]

        def _props_str(schema_obj: dict, depth: int = 0) -> str:
            ref = schema_obj.get("$ref", "")
            if ref:
                def_name = ref.split("/definitions/", 1)[-1]
                definition = _resolve_ref(ref)
                props = _collect_props(definition)
                return f"{def_name}{{{', '.join(props)}}}" if props else def_name
            if schema_obj.get("type") == "array":
                inner = _props_str(schema_obj.get("items", {}), depth + 1)
                return f"[{inner}]" if inner else "[]"
            props_dict = schema_obj.get("properties", {})
            if not props_dict:
                return ""
            if depth >= 1:
                # At depth 1+, just list property names (avoid infinite nesting)
                return f"{{{', '.join(list(props_dict.keys())[:12])}}}"
            # At depth 0: expand array-valued properties one level to show item shape
            parts = []
            for k, v in list(props_dict.items())[:12]:
                if isinstance(v, dict) and v.get("type") == "array":
                    inner = _props_str(v.get("items", {}), depth + 1)
                    parts.append(f"{k}: [{inner}]" if inner else k)
                else:
                    parts.append(k)
            return f"{{{', '.join(parts)}}}" if parts else ""

        shape = _props_str(schema)
        if shape:
            return shape
        return description if description.lower().rstrip(".") not in _GENERIC else ""

    def _extract_endpoints(self, spec: dict, api_name: str) -> List[EndpointInfo]:
        base_path = spec.get("basePath", "").rstrip("/")
        endpoints: List[EndpointInfo] = []
        for path, path_item in spec.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method.lower() not in _HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue
                raw_params = operation.get("parameters", [])
                params = [p for p in raw_params if isinstance(p, dict) and "$ref" not in p]
                endpoints.append(EndpointInfo(
                    api=api_name,
                    method=method.upper(),
                    path=path,
                    summary=operation.get("summary", ""),
                    description=operation.get("description", ""),
                    parameters=params,
                    response_schema=self._extract_response_schema(spec, operation),
                    base_path=base_path,
                ))
        return endpoints

    # ------------------------------------------------------------------
    # Step 5b: LLM identifies endpoints that are DEFINITELY unrelated
    # ------------------------------------------------------------------
    async def _exclude_unrelated_endpoints(
        self, task: str, endpoints: List[EndpointInfo]
    ) -> List[int]:
        # `summary` is empty across the shopping schema; fall back to a truncated
        # `description` so the selector sees what each endpoint does without
        # bloating the ~379-row list with full docs.
        def _blurb(ep: EndpointInfo) -> str:
            text = ep.summary or ep.description or ""
            text = text.split("\n", 1)[0].strip()
            return text if len(text) <= 120 else text[:117] + "..."
        endpoint_list = "\n".join(
            f"{i}. {ep.method} {ep.path} — {_blurb(ep)}"
            for i, ep in enumerate(endpoints)
        )
        api_guidance_section = self._build_hints_section(endpoints, header="API context")

        prompt = (
            f"Task: {task}\n\n"
            f"Available endpoints:\n{endpoint_list}\n\n"
            + api_guidance_section
            + "Identify endpoints that are DEFINITELY UNRELATED to this task.\n\n"
            "Rules:\n"
            "- Only exclude an endpoint if you are confident it has zero possible connection to the task.\n"
            "- When in doubt, do NOT exclude — keeping a marginally relevant endpoint is safer than missing a needed one.\n"
            "- Exclude endpoints that operate on completely different resources or systems.\n"
            "- Do NOT try to pick the best endpoints here — only filter out the obviously irrelevant ones.\n"
            "- Never exclude a GET endpoint with no path parameters when the API also has deeper endpoints "
            "under the same path prefix. These root collection endpoints are commonly needed as name-to-ID "
            "resolvers even when the task does not mention them explicitly.\n"
        )
        result: _ExclusionResult = await self._run_agent(
            "_exclude_unrelated_endpoints", prompt, _ExclusionResult
        )
        excluded = [i for i in result.excluded_indices if 0 <= i < len(endpoints)]

        # Deterministic safety net: never exclude a parameterless GET endpoint that
        # has children in this API (i.e. its path is a prefix of another endpoint's
        # path). Such endpoints are root collection/search endpoints that are
        # commonly needed as name-to-ID resolvers regardless of task wording.
        all_paths = {ep.path for ep in endpoints}
        resolver_protected = {
            i for i, ep in enumerate(endpoints)
            if ep.method == "GET"
            and "{" not in ep.path
            and any(
                p != ep.path and p.startswith(ep.path.rstrip("/") + "/")
                for p in all_paths
            )
        }
        excluded = [i for i in excluded if i not in resolver_protected]

        if self.debug_responses:
            kept_count = len(endpoints) - len(excluded)
            print("\n[PlanningAgent] _exclude_unrelated_endpoints RESULT:")
            print(f"  Excluded {len(excluded)}, keeping {kept_count} of {len(endpoints)} total")
            print("=" * 60 + "\n")

        return excluded

    # ------------------------------------------------------------------
    # Step 5c: Expand full details for all non-excluded endpoints
    # ------------------------------------------------------------------
    def _expand_endpoint_details(
        self, all_endpoints: List[EndpointInfo], excluded_indices: List[int]
    ) -> tuple:
        excluded_set = set(excluded_indices)
        kept = [ep for i, ep in enumerate(all_endpoints) if i not in excluded_set]
        detailed_list = "\n\n".join(
            f"{i}. {self._format_endpoint_detail(ep)}"
            for i, ep in enumerate(kept)
        )
        return kept, detailed_list

    def _format_endpoint_detail(self, ep: EndpointInfo) -> str:
        params = []
        for p in ep.parameters:
            if isinstance(p, dict):
                pname = p.get("name", "")
                pin = p.get("in", "")
                required = p.get("required", False)
                ptype = p.get("type", "")
                pdesc = p.get("description", "")
                enums = p.get("enum", [])

                # Body parameter with nested schema: expand properties so the planner
                # knows the full body object shape and creates one body argument with
                # a dict value rather than separate per-field arguments.
                if pin == "body" and "schema" in p:
                    schema = p["schema"]
                    schema_required = set(schema.get("required", []))
                    schema_props = schema.get("properties", {})
                    if schema_props:
                        line = f"  - {pname} (in={pin}, required={required}, type=object)"
                        if pdesc:
                            line += f"\n    description: {pdesc}"
                        line += "\n    properties:"
                        for prop_name, prop_info in schema_props.items():
                            prop_req = prop_name in schema_required
                            prop_type = prop_info.get("type", "")
                            prop_desc = prop_info.get("description", "")
                            prop_enums = prop_info.get("enum", [])
                            prop_line = f"\n      - {prop_name} (required={prop_req}, type={prop_type})"
                            if prop_enums:
                                prop_line += f", allowed values: {prop_enums}"
                            if prop_desc:
                                prop_line += f": {prop_desc}"
                            line += prop_line
                        params.append(line)
                        continue

                line = f"  - {pname} (in={pin}, required={required}, type={ptype})"
                if enums:
                    line += f", allowed values: {enums}"
                if pdesc:
                    line += f"\n    description: {pdesc}"
                params.append(line)
        param_str = "\n".join(params) if params else "  (none)"
        # Render summary and description, each only when present. This
        # Magento-derived schema carries its human docs in `description`
        # (`summary` is empty everywhere), while standard OpenAPI schemas use a
        # short `summary` plus a longer `description` — surfacing both lets the
        # planner see per-endpoint guidance (body shape, gotchas) in all cases.
        lines = [f"{ep.method} {ep.path}"]
        if ep.summary:
            lines.append(f"Summary: {ep.summary}")
        if ep.description:
            lines.append(f"Description: {ep.description}")
        lines.append(f"Parameters:\n{param_str}")
        entry = "\n".join(lines)
        if ep.response_schema:
            entry += f"\nReturns: {ep.response_schema}"
        return entry

    # ------------------------------------------------------------------
    # Step 5d helpers
    # ------------------------------------------------------------------
    def _get_missing_params(self, step: ChainStep, already_satisfied: set) -> List[dict]:
        """Return all required params not covered by literal_args or a prior chain step."""
        missing = []
        seen_names: set = set()

        for p in step.endpoint.parameters:
            if not isinstance(p, dict):
                continue
            name = p.get("name", "")
            if not name or name in seen_names:
                continue
            if not p.get("required", False):
                continue
            if name in step.literal_args or name in already_satisfied:
                continue
            seen_names.add(name)
            missing.append({
                "name": name,
                "in": p.get("in", ""),
                "description": (p.get("description") or "")[:120],
            })

        # Also catch path params via regex — swagger sometimes marks them required=False
        for name in re.findall(r'\{(\w+)\}', step.endpoint.path):
            if name in seen_names:
                continue
            if name in step.literal_args or name in already_satisfied:
                continue
            seen_names.add(name)
            missing.append({"name": name, "in": "path", "description": ""})

        return missing

    async def _pick_goal(
        self,
        task: str,
        kept_endpoints: List[EndpointInfo],
        detailed_list: str,
        api_guidance_section: str,
        prior_issues: Optional[list] = None,
    ) -> tuple:
        """Returns (goal_step, required_resolvers).

        When prior_issues is provided the LLM also identifies which resolver
        steps are needed to address the issues — steps the swagger-param-based
        backward chainer cannot detect because they are required by API usage
        rules rather than declared swagger parameters.
        """
        prior_issues_section = (
            "⚠️ REQUIRED CORRECTIONS — a prior attempt failed. Apply these constraints before choosing:\n"
            + "\n".join(f"  - {iss}" for iss in prior_issues) + "\n\n"
            if prior_issues else ""
        )

        prompt = (
            prior_issues_section
            + f"Task: {task}\n\n"
            + f"Available endpoints:\n{detailed_list}\n\n"
            + api_guidance_section
            + "Identify the single goal endpoint — the one whose response directly contains the final answer the task is asking for.\n\n"
            "Rules for choosing the goal:\n"
            "- The goal endpoint is the LAST step to execute. Its output must directly satisfy the task.\n"
            "- If the task asks for specific data about a resource, the goal is the endpoint that fetches "
            "that specific resource, NOT a search or list endpoint.\n"
            "- Search and list endpoints are almost always resolvers (prerequisite steps), not goals. "
            "Only pick a list endpoint as the goal if the task explicitly asks for a list.\n"
            "- Write/mutate endpoints (POST, PUT, DELETE, PATCH) are the goal when the task asks to "
            "create, update, or delete something.\n\n"
            "Also list any literal arguments for the goal that can be filled directly from the task description.\n\n"
            "Foreach (bulk operations only):\n"
            "- If the task requires the SAME operation on MULTIPLE entities resolved from a prior step, "
            "set foreach to the string 'LOOP_OVER_PRIOR' — the exact step reference will be resolved when the full plan is built.\n"
            "- Otherwise set foreach to null.\n\n"
            "Required resolvers:\n"
            "List any prerequisite steps whose need is semantic rather than structural. Common cases:\n"
            "- The task refers to 'me' or 'my' → include a current-user lookup endpoint so the plan "
            "knows who the authenticated user is.\n"
            "- The task names a resource by a plain display name but the goal endpoint requires a numeric ID → "
            "include a search/lookup endpoint to resolve the name to an ID.\n"
            "If no semantic prerequisite is needed, emit an empty list: [].\n"
            "For each resolver: endpoint_index is its index in the Available endpoints list above; "
            "satisfies_param is the parameter name in the goal (or a later step) that this resolver's output feeds.\n\n"
            "CRITICAL — multiple entities of the same type:\n"
            "- If the task names N entities that all need the same lookup (e.g. two users, three items), "
            "include that resolver endpoint EXACTLY ONCE with foreach set to all their names: "
            'foreach: ["Name1", "Name2", ...]. Do NOT list the same resolver endpoint multiple times.\n'
            "- The foreach step runs once per element and collects all results as a list for the goal step to iterate over.\n\n"
            f"IMPORTANT: goal_index must be an integer from 0 to {len(kept_endpoints) - 1} inclusive.\n\n"
            "Return a JSON object with fields: goal_index, literal_args, foreach, required_resolvers.\n"
            "literal_args is a list of {key, value} pairs for parameters known from the task, e.g. [{\"key\": \"limit\", \"value\": 25}] or [] if none.\n"
            "Examples (goal_index=2, no resolvers): goal_index=2, literal_args=[], foreach=null, required_resolvers=[]\n"
            "Example with resolver: required_resolvers=[{endpoint_index: 1, capability: \"...\", satisfies_param: \"author_id\", literal_args: [], foreach: null}]\n"
            "Example multi-entity: foreach=\"LOOP_OVER_PRIOR\", required_resolvers=[{..., foreach: [\"Alice\", \"Bob\"]}]"
        )

        MAX_GOAL_RETRIES = 2
        data: _GoalResult = await self._run_agent("_pick_goal", prompt, _GoalResult)
        for _retry in range(MAX_GOAL_RETRIES):
            if 0 <= data.goal_index < len(kept_endpoints):
                break
            retry_prompt = (
                f"⚠️ CORRECTION REQUIRED: you returned goal_index={data.goal_index} but valid indices are "
                f"0 to {len(kept_endpoints) - 1}. Re-read the endpoint list and return a corrected response.\n\n"
                + prompt
            )
            data = await self._run_agent("_pick_goal", retry_prompt, _GoalResult)

        if not (0 <= data.goal_index < len(kept_endpoints)):
            raise ValueError(
                f"LLM returned goal_index {data.goal_index} but only {len(kept_endpoints)} endpoints available."
            )
        goal_step = ChainStep(
            endpoint=kept_endpoints[data.goal_index],
            capability="performs the main action of the task",
            literal_args=_args_to_dict(data.literal_args),
            foreach=data.foreach,
        )

        required_resolvers: List[ChainStep] = []
        seen_keys = {f"{goal_step.endpoint.method} {goal_step.endpoint.path}"}
        for r in data.required_resolvers:
            if not (0 <= r.endpoint_index < len(kept_endpoints)):
                continue
            ep = kept_endpoints[r.endpoint_index]
            key = f"{ep.method} {ep.path}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            required_resolvers.append(ChainStep(
                endpoint=ep,
                capability=r.capability or "provides prerequisite data",
                satisfies_param=r.satisfies_param,
                literal_args=_args_to_dict(r.literal_args),
                foreach=r.foreach,
            ))

        return goal_step, required_resolvers

    async def _find_resolver(
        self,
        task: str,
        missing_params: List[dict],
        kept: List[EndpointInfo],
        detailed_list: str,
        api_guidance_section: str,
        chain_so_far: List[ChainStep],
        prior_issues: Optional[list] = None,
    ) -> Optional[ChainStep]:
        """Pick one resolver from the already-filtered endpoint subset."""
        if not kept:
            return None

        param_descriptions = ", ".join(
            f"'{p['name']}' ({p['description']})" if p.get("description") else f"'{p['name']}'"
            for p in missing_params
        )
        chain_keys = {f"{cs.endpoint.method} {cs.endpoint.path}" for cs in chain_so_far}

        prior_issues_section = (
            "⚠️ REQUIRED CORRECTIONS — a prior attempt failed. Apply these constraints before choosing:\n"
            + "\n".join(f"  - {iss}" for iss in prior_issues) + "\n\n"
            if prior_issues else ""
        )

        prompt = (
            prior_issues_section
            + f"Task context: {task}\n\n"
            + f"You need to find ONE endpoint that provides value(s) for: {param_descriptions}\n\n"
            + f"Available endpoints:\n{detailed_list}\n\n"
            + api_guidance_section
            + "Rules:\n"
            "- Pick the single best endpoint whose response output can supply the needed parameter value(s).\n"
            "- Prefer endpoints that return the specific resource containing the needed identifier.\n"
            "- Also extract any literal argument values directly from the task description for this resolver.\n"
            "- Specify which parameter name (from the missing list above) this resolver's output will satisfy.\n"
            "- If no endpoint can provide the needed value, return null for endpoint_index.\n\n"
            "Foreach (bulk operations only):\n"
            "- If the task names multiple specific entities that all need this resolver (e.g. two users, three items), "
            "set foreach to a list of ALL their names: foreach: [\"Name1\", \"Name2\", ...]. "
            "Do NOT return this resolver with foreach=null and expect a second call — each resolver endpoint is used at most once.\n"
            "- Otherwise set foreach to null.\n\n"
            "Return a JSON object with fields: endpoint_index (int or null), satisfies_param, literal_args, foreach, capability.\n"
            "literal_args is a list of {key, value} pairs for parameters known from the task, e.g. [{\"key\": \"search\", \"value\": \"Alice\"}] or [].\n"
            "If nothing fits, set endpoint_index to null."
        )
        data: _ResolverResult = await self._run_agent("_find_resolver", prompt, _ResolverResult)

        if data.endpoint_index is None or not (0 <= data.endpoint_index < len(kept)):
            return None

        chosen_ep = kept[data.endpoint_index]
        key = f"{chosen_ep.method} {chosen_ep.path}"
        if key in chain_keys:
            self._debug_print("_find_resolver", response=f"Cycle detected: {key} already in chain, stopping.")
            return None

        return ChainStep(
            endpoint=chosen_ep,
            capability=data.capability or "provides prerequisite data",
            satisfies_param=data.satisfies_param,
            literal_args=_args_to_dict(data.literal_args),
            foreach=data.foreach,
        )

    # ------------------------------------------------------------------
    # Step 5d: Build backward chain — goal first, then prepend resolvers
    # Exclusion (5b) and expansion (5c) happen once in plan() and are passed in.
    # ------------------------------------------------------------------
    async def _build_chain_backward(
        self,
        task: str,
        kept: List[EndpointInfo],
        detailed_list: str,
        api_guidance_section: str,
        prior_issues: Optional[list] = None,
    ) -> List[ChainStep]:
        MAX_RESOLVER_ITERATIONS = 5

        if not kept:
            raise ValueError("No endpoints available — cannot build a plan.")

        goal_step, required_resolvers = await self._pick_goal(
            task, kept, detailed_list, api_guidance_section, prior_issues
        )
        chain: List[ChainStep] = required_resolvers + [goal_step]

        # Backward chaining: iteratively prepend resolvers for missing params
        for iteration in range(MAX_RESOLVER_ITERATIONS):
            already_satisfied = {cs.satisfies_param for cs in chain if cs.satisfies_param}

            # Find the first chain step (from front) that still has unsatisfied required params
            target_step = None
            missing_params: List[dict] = []
            for cs in chain:
                missing = self._get_missing_params(cs, already_satisfied)
                if missing:
                    target_step = cs
                    missing_params = missing
                    break

            if not target_step:
                break  # all params satisfied

            self._debug_print(
                "_build_chain_backward",
                response=(
                    f"Iteration {iteration + 1}: finding resolver for "
                    f"{[p['name'] for p in missing_params]} "
                    f"needed by {target_step.endpoint.method} {target_step.endpoint.path}"
                ),
            )

            resolver = await self._find_resolver(
                task, missing_params, kept, detailed_list, api_guidance_section, chain, prior_issues
            )

            if resolver is None:
                self._debug_print(
                    "_build_chain_backward",
                    response=f"No resolver found for {[p['name'] for p in missing_params]}, proceeding with current chain.",
                )
                break

            chain.insert(0, resolver)

        if self.debug_responses:
            print("\n[PlanningAgent] _build_chain_backward FINAL CHAIN:")
            for i, cs in enumerate(chain):
                print(f"  {i + 1}. {cs.endpoint.method} {cs.endpoint.path} — {cs.capability}")
                if cs.satisfies_param:
                    print(f"     → satisfies_param: {cs.satisfies_param}")
                if cs.literal_args:
                    print(f"     literal_args: {cs.literal_args}")
            print("=" * 60 + "\n")

        return chain

    # ------------------------------------------------------------------
    # Step 5e: Read current-user context from env vars for the APIs in use
    # ------------------------------------------------------------------
    def _get_user_context(self, api_files: set) -> str:
        from config.servers import SERVERS
        lines = []
        for fragment, entry in SERVERS.items():
            if not any(fragment in f.lower() for f in api_files):
                continue
            username_env = entry.get("username_env")
            if not username_env:
                continue
            val = os.environ.get(username_env)
            if val:
                label = entry.get("label", fragment)
                lines.append(f"{label}:\n  - username: {val}")

        if not lines:
            return ""
        return (
            "\nCurrent user context (use these values directly (if needed) for tasks that contain me/mine/my etc.):\n"
            + "\n".join(lines) + "\n"
        )

    # ------------------------------------------------------------------
    # Step 6: LLM builds a full execution plan
    # ------------------------------------------------------------------
    async def _build_plan(
        self,
        task: str,
        chain_steps: List[ChainStep],
        kept: List[EndpointInfo],
        detailed_list: str,
    ):
        # ToolEnum and endpoint details use ALL kept endpoints so the LLM can
        # pick the best set and the fix step is never blocked by a missing tool.
        tool_names = list(dict.fromkeys(f"{ep.method} {ep.path}" for ep in kept))
        bundle = build_agent_models(tool_names)

        endpoint_details = detailed_list  # pre-formatted full list of kept endpoints
        schema = json.dumps(bundle.ToolBasedResponse.model_json_schema(), indent=2)

        hints = self._load_hints()
        api_files_used = {ep.api for ep in kept}
        relevant_hints = [
            f"[ {fname} ]\n{hints[fname]}"
            for fname in api_files_used
            if fname in hints
        ]
        api_guidance_section = (
            "\nAPI-specific parameter rules (apply these before building the plan):\n"
            + "\n\n".join(relevant_hints) + "\n"
            if relevant_hints else ""
        )

        step_ids = [f"step_{i+1}" for i in range(len(chain_steps))]

        # Build a lookup so each step can find which earlier step provides each param
        param_to_provider: dict = {}  # param_name -> (provider_idx, provider_step_id)
        for i, cs in enumerate(chain_steps):
            if cs.satisfies_param:
                param_to_provider[cs.satisfies_param] = (i, step_ids[i])

        wiring_lines = []
        for i, cs in enumerate(chain_steps):
            sid = step_ids[i]
            lines = [f"  {sid}: {cs.endpoint.method} {cs.endpoint.path} [{cs.capability}]"]
            if cs.foreach is not None:
                lines.append(f"    foreach: {json.dumps(cs.foreach)}")
                lines.append(f"    note: use {{loop_item}} as the argument value for the iterated parameter")
            if cs.literal_args:
                for k, v in cs.literal_args.items():
                    lines.append(f"    literal arg: {k}={v!r}")

            # Wire every parameter this step needs that any earlier step provides.
            # This handles non-adjacent dependencies (e.g. author from step_1 → step_3).
            step_param_names: set = set()
            for p in cs.endpoint.parameters:
                if isinstance(p, dict):
                    pname = p.get("name", "")
                    if pname:
                        step_param_names.add(pname)
            for pname in re.findall(r'\{(\w+)\}', cs.endpoint.path):
                step_param_names.add(pname)

            for pname in sorted(step_param_names):
                if pname in param_to_provider and pname not in cs.literal_args:
                    provider_idx, provider_sid = param_to_provider[pname]
                    if provider_idx < i:
                        lines.append(f"    {pname}: reference {{{provider_sid}.result}}")

            if cs.satisfies_param:
                lines.append(f"    output provides: {cs.satisfies_param}")
            wiring_lines.append("\n".join(lines))
        wiring_section = (
            "\nValidated execution chain (follow this endpoint sequence exactly as the backbone of your plan):\n"
            + "\n\n".join(wiring_lines) + "\n"
        )

        user_context_section = self._get_user_context(api_files_used)

        prompt = (
            f"Task: {task}\n\n"
            f"Available API endpoints:\n{endpoint_details}\n\n"
            + wiring_section
            + user_context_section
            + api_guidance_section + "\n"
            + "Build a complete step-by-step execution plan for the task above.\n"
            "The validated chain above is the binding backbone of the plan — implement each chain endpoint "
            "in order. Do NOT substitute a different endpoint for any chain step, and do NOT skip chain steps. "
            "You may insert a small bridge step between two chain steps only when a specific wiring gap "
            "(e.g. a missing ID lookup) cannot be resolved with a reference from an earlier step. "
            "Bridge steps must come from the available endpoint list above.\n"
            "Steps can depend on each other using depends_on and reference prior outputs with '{step_id.result}'.\n\n"
            f"Respond with ONLY valid JSON matching this schema:\n{schema}\n\n"
            "Rules:\n"
            "- tool_call_required must be true\n"
            '- response must be empty string ""\n'
            '- Each step needs a unique step_id (e.g. "step_1", "step_2")\n'
            f"- tool_name must be EXACTLY one of: {json.dumps(tool_names)}\n"
            "  Do NOT substitute actual IDs or path values into tool_name — keep template placeholders like {{id}} as-is.\n"
            "  Path parameters (e.g. id='a11yproject/myrepo') go in the arguments list, NOT in tool_name.\n"
            "- arguments is a list of {name, value, value_type, param_in} objects\n"
            '- value_type is "literal" for known values, "reference" for {step_id.result} placeholders\n'
            '- param_in must be set from the swagger "in" field for that parameter: "path", "query", "body", "formData", or "header"\n'
            "- CRITICAL: argument names must ONLY be parameter names explicitly listed in the endpoint's schema above. "
            "Never invent or guess parameter names. If a parameter name is not in the schema, the task cannot be done with that endpoint.\n\n"
            "Argument sourcing — every argument value must come from exactly one of:\n"
            "  - 'literal': the value is a documented constant for that parameter\n"
            "  - 'reference': the value comes from a prior step's output via {step_id.result} or a field accessor like {step_id.result.field_name}\n\n"
            "Reference field accessor rule (CRITICAL):\n"
            "- When a prior step returns a JSON object and you need only one field from it, use dot-notation: {step_id.result.field_name}\n"
            "  Example: if step_1 returns an object and you need its name field, write {step_1.result.name}\n"
            "- Use {step_id.result} (no field) ONLY when the prior step returns a plain scalar (string, number) or you genuinely need the entire object.\n"
            "- Never use {step_id.result} when you need a specific named field — always drill down with .field_name.\n"
            "- For JSON body arguments that embed multiple references (e.g. a body string), use the most specific accessor for each embedded reference.\n\n"
            "Accessor chain tokens (usable in both foreach references and {step_id.result...} argument values):\n"
            "  [*]                  — wildcard: apply the rest of the chain to every element of a list\n"
            "  [?(@.field==value)]  — filter: keep only list elements where field EXACTLY equals value (use for IDs, slugs, or values you know precisely)\n"
            "  [?(@.field*=value)]  — contains filter: keep list elements where field contains value as a substring (use for titles, names, or user-provided search terms that may appear inside a longer string)\n"
            "  [n]                  — numeric index: take element n (0-based)\n"
            "  [sort_desc:f]        — sort the list descending by field f (each element must be an object with that field)\n"
            "  [:N]                 — slice: keep only the first N elements\n"
            "  .key                 — field access on a dict\n"
            "These tokens chain left-to-right:\n"
            "  step_1.result[*].id                                        → id from every element\n"
            "  step_1.result[sort_desc:count][:5][*].id                  → top-5 by 'count' field, then their ids\n"
            "  step_1.result[sort_desc:count][0].id                       → id of the single element with the highest 'count'\n"
            "  step_1.result[?(@.category.id=={step_2.result.id})][0].slug → first entry where category.id matches a prior step's value\n"
            "  step_1.result[?(@.title*=keyword)][0].url                 → first item whose title contains 'keyword'\n"
            "NEVER use invented syntax like max_by(field) — use [sort_desc:f][0] instead.\n"
            "If the API does not support sorting by a required field as a query param, fetch with valid params "
            "(using the endpoint's documented pagination parameter, e.g. limit or page_size) and sort/slice client-side via [sort_desc:field] and [:N] in the accessor chain.\n\n"
            "Closed-enum rule:\n"
            "- If a parameter lists allowed values (in its description), treat that list as CLOSED.\n"
            "- ONLY use exact listed values — do not invent, paraphrase, or substitute.\n"
            "- If the task requires sorting or filtering by a value not supported as a query param, "
            "fetch with the closest valid param and apply accessor-chain sorting/slicing in the next step.\n\n"
            "Free-string parameter rule:\n"
            "- If a parameter is a free string (no enum, no documented example values), do NOT invent a value for it. "
            "Only include it if: (a) the task explicitly states the exact value, or (b) a prior step's output supplies it, or (c) you can look it up via another API endpoint first. \n\n"
            "If none of these apply and the parameter is optional and not necessary for the task, omit it entirely.\n\n"
            "Task-content literal rule (CRITICAL):\n"
            "- If the task explicitly provides the content to post or submit (a title, body text, message, review text, etc.), "
            "always use value_type='literal' with that exact text. NEVER reference an API result to supply content the task already gives you.\n"
            "- A prior step's output should ONLY supply values the task does NOT already state: a canonical forum name from /find_forum_by_name, "
            "a post ID from a search, a URL to embed, etc.\n"
            "- In particular: do NOT run a search step and extract a found post's body or title to use as the new post's body or title. "
            "If the task says what to post, post exactly that as a literal.\n\n"
            "Foreach rule:\n"
            "- If a step needs to process each item from a prior step's list output individually "
            "(e.g., fetch details for each ID returned by a search or events step), set foreach to "
            "'step_N.result[*].field_name' where N is the prior step and field_name is the relevant field.\n"
            "- To loop over a sorted or sliced subset, use accessor-chain tokens in the foreach reference. "
            "Example: foreach: \"step_1.result[sort_desc:count][:5][*].id\" iterates only the top-5 items by 'count'.\n"
            "- If the wiring shows 'foreach: LOOP_OVER_PRIOR' or 'foreach: [...]' or 'foreach: \"...\"', "
            "resolve or copy that value to the correct step reference.\n"
            "- CRITICAL consistency rule: if any argument value in a step contains '{loop_item}', "
            "that step's foreach field MUST be set. A step with {loop_item} arguments but no foreach is always wrong.\n"
            "- foreach accepts a literal list ([\"Alice\", \"Bob\"]) or a reference string (\"step_1.result[*].id\").\n"
            "- In argument values, use {loop_item} as the placeholder for the current iterated element.\n"
            "- A foreach step runs N times and its output is automatically collected as a list.\n\n"
            "Conditional step rule:\n"
            "- When a task requires branching (e.g. 'if condition A is true do X, otherwise do Y'), "
            "add a step with step_type='conditional' BEFORE the action step.\n"
            "- Set condition to an equality expression using {step_id.result} references: "
            "'{step_3.result.status} == {step_2.result.status}'\n"
            "- Set if_true and if_false to the string values to produce for each branch. "
            "Both may contain {step_id.result} references.\n"
            "- The conditional step stores the chosen string as its output. "
            "Reference it in the next step's arguments as '{step_N.result}'.\n"
            "- NEVER embed conditional logic (if/else/then) as a literal string inside an argument value. "
            "Always use a conditional step instead.\n\n"
        )
        # pydantic_ai validates against bundle.ToolBasedResponse (including our model_validators)
        # and auto-retries on schema violations — no manual JSON parsing needed.
        plan_result = await self._run_agent("_build_plan", prompt, bundle.ToolBasedResponse)
        if not validate_plan(plan_result.plan):
            raise ValueError("LLM produced an invalid plan.")

        # Annotate each tool_call step with returns/base_url; skip conditional steps.
        endpoint_map = {f"{ep.method} {ep.path}": ep for ep in kept}
        _fallback = EndpointInfo(api="", method="", path="", summary="", description="", parameters=[])
        annotated_steps = []
        for step in plan_result.plan:
            if getattr(step, "step_type", "tool_call") == "conditional":
                annotated_steps.append(step)
                continue
            tn = step.tool_name.value if hasattr(step.tool_name, "value") else str(step.tool_name)
            ep = endpoint_map.get(tn, _fallback)
            annotated_steps.append(step.model_copy(update={
                "returns": ep.response_schema,
                "base_url": "|".join([ep.api, ep.base_path]),
            }))
        return plan_result.model_copy(update={"plan": annotated_steps})

    # ------------------------------------------------------------------
    # Step 7: LLM semantic verification + auto-fix loop
    # ------------------------------------------------------------------
    def _plan_context(self, kept: List[EndpointInfo]) -> tuple:
        endpoint_details = "\n\n".join(self._format_endpoint_detail(ep) for ep in kept)
        hints = self._load_hints()
        api_files_used = {ep.api for ep in kept}
        relevant_hints = [
            f"[ {fname} ]\n{hints[fname]}"
            for fname in api_files_used
            if fname in hints
        ]
        api_guidance_section = (
            "\nAPI-specific rules:\n"
            + "\n\n".join(relevant_hints) + "\n"
            if relevant_hints else ""
        )
        tool_names = list(dict.fromkeys(f"{ep.method} {ep.path}" for ep in kept))
        bundle = build_agent_models(tool_names)
        user_context_section = self._get_user_context(api_files_used)
        return endpoint_details, api_guidance_section, tool_names, bundle, user_context_section

    @staticmethod
    def _format_plan_text(plan_result) -> str:
        parts = []
        for step in plan_result.plan:
            if getattr(step, "step_type", "tool_call") == "conditional":
                parts.append(
                    f"  step_id: {step.step_id}\n"
                    f"  step_type: conditional\n"
                    f"  condition: {step.condition}\n"
                    f"  if_true: {step.if_true}\n"
                    f"  if_false: {step.if_false}\n"
                    f"  depends_on: {step.depends_on or []}"
                )
            else:
                parts.append(
                    f"  step_id: {step.step_id}\n"
                    f"  tool: {step.tool_name.value if hasattr(step.tool_name, 'value') else str(step.tool_name)}\n"
                    f"  arguments: {[{'name': a.name, 'value': a.value, 'value_type': a.value_type} for a in (step.arguments or [])]}\n"
                    f"  depends_on: {step.depends_on or []}\n"
                    f"  hints: {step.hints or ''}"
                )
        return "\n\n".join(parts)

    async def _check_plan(
        self, task: str, plan_result, kept: List[EndpointInfo]
    ) -> tuple:
        endpoint_details, api_guidance_section, _, _, user_context_section = self._plan_context(kept)
        plan_text = self._format_plan_text(plan_result)

        prompt = (
            f"Task: {task}\n\n"
            f"Endpoint definitions:\n{endpoint_details}\n\n"
            + api_guidance_section
            + user_context_section
            + f"\nGenerated plan:\n{plan_text}\n\n"
            "Review this plan for argument-level correctness. Do NOT flag endpoint selection "
            "or high-level sequencing — focus only on the wiring within the plan as written.\n\n"
            "Check ALL of the following:\n\n"
            "1. Missing required parameters: are any required parameters absent from a step "
            "that cannot be inferred from a prior step or the task description?\n\n"
            "2. Type mismatch in reference wiring: does each {step_N.result} reference supply "
            "the correct resource type for the parameter it feeds? "
            "(e.g., a step returning user IDs must NOT feed into a parameter that expects a different resource's ID — "
            "only a step returning that resource type can do that)\n\n"
            "3. Parameter value correctness: are literal argument values valid for their parameter "
            "according to the endpoint description? (wrong enum, wrong format, user display name "
            "used where a machine identifier is needed, etc.) "
            "4. Task accomplishment: does the plan as a whole accomplish the stated task?\n\n"
            "Return a JSON object with fields: issues (list of strings) and ok (bool).\n"
            "Example with issues: {\"issues\": [\"step_2 arg 'id' wires step_1.result (user_id) into an item {id} — type mismatch\"], \"ok\": false}\n"
            "If no issues: {\"issues\": [], \"ok\": true}"
        )
        result: _CheckResult = await self._run_agent("_check_plan", prompt, _CheckResult)
        return result.issues, result.ok

    async def _fix_plan(
        self, task: str, plan_result, kept: List[EndpointInfo], issues: list
    ):
        endpoint_details, api_guidance_section, tool_names, bundle, user_context_section = self._plan_context(kept)
        plan_text = self._format_plan_text(plan_result)
        schema = json.dumps(bundle.ToolBasedResponse.model_json_schema(), indent=2)
        issues_text = "\n".join(f"  - {iss}" for iss in issues)

        prompt = (
            f"Task: {task}\n\n"
            f"Endpoint definitions:\n{endpoint_details}\n\n"
            + api_guidance_section
            + user_context_section
            + f"\nCurrent plan (has issues):\n{plan_text}\n\n"
            f"Issues to fix:\n{issues_text}\n\n"
            "Produce a corrected plan that resolves all issues above. "
            "Keep steps that are already correct unchanged.\n\n"
            f"tool_name must be EXACTLY one of: {json.dumps(tool_names)}\n"
            "- value_type is 'literal' for known values, 'reference' for {step_id.result} placeholders\n"
            "- param_in must be set from the swagger 'in' field: 'path', 'query', 'body', 'formData', or 'header'\n"
            "- argument names must ONLY be parameter names explicitly listed in the endpoint schema\n"
            "- If a step has foreach set, preserve it. Use {loop_item} as the argument value for the iterated parameter. "
            "The foreach field MUST be set on any step whose argument values contain {loop_item} — a step with {loop_item} but no foreach is always wrong.\n"
            "- Accessor chain tokens available in foreach references and {step_id.result...} values: "
            "[*] (wildcard over list), [n] (index), [sort_desc:f] (sort list desc by field f), [:N] (keep first N), .key (field). "
            "Chain them: e.g. step_1.result[sort_desc:count][:5][*].id for top-5 by count. "
            "Never use invented syntax like max_by(field) — use [sort_desc:f][0] instead.\n"
            "- To implement branching logic, add a step with step_type='conditional' before the action step. "
            "Set condition='{left} == {right}', if_true=<value>, if_false=<value>. "
            "Never write if/else/then as a literal string inside an argument value.\n"
            "- If an issue requires adding a chain endpoint back, include it exactly as specified. "
            "Do NOT substitute a different endpoint. If the API does not support sorting by a required field, "
            "fetch with valid params and apply [sort_desc:field] / [:N] in the accessor chain.\n"
            "Task-content literal rule (CRITICAL):\n"
            "- If the task explicitly provides the content to post or submit (a title, body text, message, review text, etc.), "
            "always use value_type='literal' with that exact text. NEVER reference an API result to supply content the task already gives you.\n"
            "- A prior step's output should ONLY supply values the task does NOT already state: a canonical forum name from /find_forum_by_name, "
            "a post ID from a search, a URL to embed, etc.\n"
            "- In particular: do NOT run a search step and extract a found post's body or title to use as the new post's body or title. "
            "If the task says what to post, post exactly that as a literal.\n\n"
            f"Respond with ONLY valid JSON matching this schema:\n{schema}"
        )
        fixed = await self._run_agent("_fix_plan", prompt, bundle.ToolBasedResponse)
        if not validate_plan(fixed.plan):
            raise ValueError("_fix_plan produced an invalid plan structure.")

        endpoint_map = {f"{ep.method} {ep.path}": ep for ep in kept}
        _fallback = EndpointInfo(api="", method="", path="", summary="", description="", parameters=[])
        annotated = []
        for step in fixed.plan:
            if getattr(step, "step_type", "tool_call") == "conditional":
                annotated.append(step)
                continue
            tn = step.tool_name.value if hasattr(step.tool_name, "value") else str(step.tool_name)
            ep = endpoint_map.get(tn, _fallback)
            annotated.append(step.model_copy(update={
                "returns": ep.response_schema,
                "base_url": "|".join([ep.api, ep.base_path]),
            }))
        return fixed.model_copy(update={"plan": annotated})

    async def _check_chain(
        self,
        task: str,
        chain_steps: List[ChainStep],
        api_guidance_section: str,
    ) -> tuple:
        """Verify the chain of steps before building the plan.

        Checks logical correctness (goal, ordering, missing prerequisites, data-flow
        mismatches) without needing a fully-built plan.  Returns (issues, ok).
        """
        # Chain text: one line per endpoint showing its role and what data it contributes.
        # No wiring detail — wiring is verified separately after the plan is built.
        chain_text = "\n".join(
            f"  {i + 1}. {cs.endpoint.method} {cs.endpoint.path} — {cs.capability}"
            + (f" | provides: {cs.satisfies_param}" if cs.satisfies_param else "")
            + (f" | foreach: {json.dumps(cs.foreach)}" if cs.foreach is not None else "")
            for i, cs in enumerate(chain_steps)
        )

        prompt = (
            f"Task: {task}\n\n"
            f"Selected endpoints:\n{chain_text}\n\n"
            + api_guidance_section
            + "Check that this set of endpoints is COMPLETE and SUFFICIENT to accomplish the task.\n\n"
            "Ask yourself:\n"
            "1. Does the last endpoint produce the final result the task asks for?\n"
            "2. Is there any data or identifier the task needs that NO endpoint in this set can provide? "
            "(e.g. the task requires a resource identifier but no endpoint in this set can provide it)\n"
            "3. Is any endpoint clearly wrong or irrelevant for this task?\n"
            "4. Is the execution order conceptually correct?\n\n"
            "IMPORTANT: Do NOT check how arguments are wired between steps, what parameter names are used, "
            "or whether reference values are correct — argument-level wiring is verified in a separate pass "
            "after the plan is built.\n\n"
            "foreach rule (CRITICAL — read before evaluating):\n"
            "- A step with 'foreach: [\"Name1\", \"Name2\", ...]' runs ONCE PER ELEMENT and collects all results.\n"
            "- This means a SINGLE lookup step with foreach set to a list of N names fully satisfies the "
            "identifier-resolution requirement for ALL N entities. It is NOT missing N-1 steps.\n"
            "- Only flag a missing lookup if there is NO step (with or without foreach) that can resolve "
            "the needed identifier for one or more entities in the task.\n\n"
            "Return a JSON object with fields: issues (list of strings) and ok (bool).\n"
            "If the endpoint set is complete and correct: {\"issues\": [], \"ok\": true}"
        )
        result: _CheckResult = await self._run_agent("_check_chain", prompt, _CheckResult)
        return result.issues, result.ok

    # ------------------------------------------------------------------
    # Step 8: Validate the plan for semantic correctness
    # ------------------------------------------------------------------
    async def _validate_plan(self, plan_result, kept: List[EndpointInfo]) -> None:
        plan = plan_result.plan
        endpoint_map = {(ep.method + " " + ep.path): ep for ep in kept}

        errors = []
        step_ids_seen = []

        for step in plan:
            if getattr(step, "step_type", "tool_call") == "conditional":
                step_ids_seen.append(step.step_id)
                continue
            tool_name = step.tool_name.value if hasattr(step.tool_name, "value") else str(step.tool_name)
            ep = endpoint_map.get(tool_name)
            param_map = {}
            if ep:
                for p in ep.parameters:
                    if isinstance(p, dict) and p.get("name"):
                        param_map[p["name"]] = p

            uses_loop_item = any(
                "{loop_item" in str(arg.value)
                for arg in (step.arguments or [])
            )
            if uses_loop_item and getattr(step, "foreach", None) is None:
                errors.append(
                    f"Step '{step.step_id}' uses {{loop_item}} in arguments but 'foreach' is not set. "
                    "Set foreach to the source of iteration (e.g. 'step_1.result[*].id')."
                )

            for arg in (step.arguments or []):
                aname = arg.name
                avalue = arg.value
                atype = arg.value_type

                if atype == "reference":
                    refs = re.findall(r'\{(\w+)\.result(?:\.\w+)*\}', str(avalue))
                    for ref_id in refs:
                        if ref_id not in step_ids_seen:
                            errors.append(
                                f"Step '{step.step_id}', arg '{aname}': references '{ref_id}.result' "
                                f"but '{ref_id}' has not appeared yet in the plan."
                            )

                if atype == "literal" and aname in param_map:
                    param_def = param_map[aname]
                    allowed = param_def.get("enum")
                    if allowed and avalue not in allowed:
                        errors.append(
                            f"Step '{step.step_id}', arg '{aname}': value {avalue!r} is not in "
                            f"the documented allowed values {allowed}."
                        )

            step_ids_seen.append(step.step_id)

        if errors:
            raise ValueError("Plan validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def _debug_context(
        self,
        selected_files: list,
        kept: "List[EndpointInfo]",
        chain_steps: "List[ChainStep]",
        plan_result=None,
    ) -> str:
        """Format a concise debug summary to embed in failure exception messages."""
        lines = [
            f"\n\n[debug] selected_api_files: {selected_files}",
            "[debug] kept_endpoints:\n"
            + "\n".join(f"  {ep.method} {ep.path}" for ep in kept),
            "[debug] chain_steps:\n"
            + "\n".join(
                f"  {i+1}. {cs.endpoint.method} {cs.endpoint.path} — {cs.capability}"
                + (f" (foreach={cs.foreach})" if cs.foreach is not None else "")
                for i, cs in enumerate(chain_steps)
            ),
        ]
        if plan_result is not None:
            plan_steps = [
                f"[conditional:{step.step_id}]" if getattr(step, "step_type", "tool_call") == "conditional"
                else (step.tool_name.value if hasattr(step.tool_name, "value") else str(step.tool_name))
                for step in plan_result.plan
            ]
            lines.append(f"[debug] plan_steps: {plan_steps}")
        return "\n".join(lines)

    async def plan(self, task: str, sites: List[str]):
        """
        Given a natural language task and the list of site(s) it targets,
        select the appropriate swagger file(s), parse them with prance,
        select the best API endpoints, and return a ToolBasedResponse
        containing a validated execution plan.

        `sites` (e.g. ["gitlab"]) drives swagger file selection directly via
        api.schema_index.resolve_schema_files() — no LLM guess involved.

        The entire planning pipeline is retried up to MAX_PLANNING_ATTEMPTS times
        from scratch (fresh LLM calls for endpoint exclusion, chain building,
        and plan building) before giving up.
        """
        MAX_PLANNING_ATTEMPTS = 3
        last_error: Optional[Exception] = None

        index = self._load_index()
        selected_files = resolve_schema_files(sites, index)
        if not selected_files:
            raise ValueError(f"No suitable API file found for sites: {sites!r}")

        for planning_attempt in range(MAX_PLANNING_ATTEMPTS):
            self._run_log = []
            self._run_costs = []
            self._run_tokens = {"input": 0, "output": 0, "total": 0, "llm_calls": 0}
            self._record("task", task)
            self._record("selected_api_files", selected_files)
            if planning_attempt > 0:
                self._record("planning_attempt", planning_attempt + 1)

            try:
                all_endpoints: List[EndpointInfo] = []
                for fname in selected_files:
                    spec = self._parse_swagger(fname)
                    all_endpoints.extend(self._extract_endpoints(spec, fname))

                if not all_endpoints:
                    raise ValueError(
                        f"No endpoints extracted from selected swagger files."
                        f"\n\n[debug] selected_api_files: {selected_files}"
                    )

                # 5b+5c: exclusion filter and detail expansion happen once per attempt.
                # The resulting kept/detailed_list/api_guidance_section are threaded through
                # to _build_chain_backward and _check_chain so rebuilds within this
                # attempt never re-scan.
                api_guidance_section = self._build_hints_section(
                    all_endpoints, header="API context (use to understand data models and identifier types)"
                )
                excluded = await self._exclude_unrelated_endpoints(task, all_endpoints)
                kept, detailed_list = self._expand_endpoint_details(all_endpoints, excluded)
                self._record("kept_endpoints", [f"{ep.method} {ep.path}" for ep in kept])
                if not kept:
                    raise ValueError(
                        f"All endpoints were excluded — cannot build a plan."
                        f"\n\n[debug] selected_api_files: {selected_files}"
                        f"\n[debug] total_endpoints_before_exclusion: {len(all_endpoints)}"
                    )

                chain_steps = await self._build_chain_backward(task, kept, detailed_list, api_guidance_section)
                self._record("initial_chain", [
                    f"{cs.endpoint.method} {cs.endpoint.path} — {cs.capability}" for cs in chain_steps
                ])
                if not chain_steps:
                    raise ValueError(
                        f"No endpoints selected for this task."
                        + self._debug_context(selected_files, kept, [])
                    )

                # Verify chain logic before building the plan so a bad chain never
                # wastes an expensive _build_plan call.
                MAX_CHAIN_ATTEMPTS = 2
                accumulated_issues: list = []
                for attempt in range(MAX_CHAIN_ATTEMPTS):
                    issues, ok = await self._check_chain(task, chain_steps, api_guidance_section)
                    self._record(f"check_chain.attempt_{attempt + 1}", {"issues": issues, "ok": ok})
                    if ok or not issues:
                        break
                    for iss in issues:
                        if iss not in accumulated_issues:
                            accumulated_issues.append(iss)
                    self._debug_print(
                        "plan",
                        response=f"chain attempt {attempt + 1}: rebuilding due to {len(accumulated_issues)} accumulated issue(s): {accumulated_issues}",
                    )
                    chain_steps = await self._build_chain_backward(
                        task, kept, detailed_list, api_guidance_section, prior_issues=accumulated_issues
                    )
                    if not chain_steps:
                        raise ValueError(
                            f"Rebuild produced no chain steps."
                            + self._debug_context(selected_files, kept, [])
                        )
                else:
                    issues, ok = await self._check_chain(task, chain_steps, api_guidance_section)
                    self._record("check_chain.final", {"issues": issues, "ok": ok})
                    if not ok and issues:
                        raise ValueError(
                            f"Chain verification failed after {MAX_CHAIN_ATTEMPTS} rebuild(s):\n"
                            + "\n".join(f"  - {iss}" for iss in issues)
                            + self._debug_context(selected_files, kept, chain_steps)
                        )

                # Step 1 check passed — build the plan using all kept endpoints so the
                # LLM has full visibility and _fix_plan is never blocked by a missing tool.
                plan_result = await self._build_plan(task, chain_steps, kept, detailed_list)

                # Catch validation errors from the initial build so they feed into the fix loop
                # rather than immediately aborting planning.
                pending_validation_error: Optional[str] = None
                try:
                    await self._validate_plan(plan_result, kept)
                except ValueError as exc:
                    pending_validation_error = str(exc)
                    self._record("validate_plan.initial_error", pending_validation_error)

                # Code-level chain compliance check: every chain endpoint must appear in the
                # plan. The LLM sometimes substitutes a "better" endpoint despite the prompt
                # instruction; injecting a concrete error here forces _fix_plan to restore it.
                plan_tool_names = {
                    step.tool_name.value if hasattr(step.tool_name, "value") else str(step.tool_name)
                    for step in plan_result.plan
                    if getattr(step, "step_type", "tool_call") != "conditional"
                }
                chain_violations = [
                    f"Chain endpoint '{cs.endpoint.method} {cs.endpoint.path}' was omitted from the plan "
                    f"and must be included. If this endpoint has API limitations (e.g. no branch filter), "
                    f"add a post_processing note on that step — do not substitute a different endpoint."
                    for cs in chain_steps
                    if f"{cs.endpoint.method} {cs.endpoint.path}" not in plan_tool_names
                ]
                if chain_violations:
                    violation_msg = "Chain compliance failure:\n" + "\n".join(f"  - {v}" for v in chain_violations)
                    self._record("chain_compliance_violations", chain_violations)
                    pending_validation_error = (
                        (pending_validation_error + "\n" if pending_validation_error else "") + violation_msg
                    )

                # Static swagger required-parameter check (deterministic, no LLM).
                # Catches missing required body fields / path params before execution.
                _swagger_checker = SwaggerPlanChecker()
                _swagger_issues = _swagger_checker.check(plan_result.plan, kept)
                if _swagger_issues:
                    self._record("swagger_required_param_violations", _swagger_issues)
                    _swagger_msg = "Swagger required-parameter violations:\n" + "\n".join(
                        f"  - {i}" for i in _swagger_issues
                    )
                    pending_validation_error = (
                        (pending_validation_error + "\n" if pending_validation_error else "") + _swagger_msg
                    )

                # Step 2: verify argument-level wiring in the built plan, fix if needed.
                # Validation errors from _validate_plan are merged with _check_plan issues
                # so _fix_plan gets full context in one pass.
                MAX_PLAN_FIX_ATTEMPTS = 2
                for attempt in range(MAX_PLAN_FIX_ATTEMPTS):
                    issues, ok = await self._check_plan(task, plan_result, kept)
                    self._record(f"check_plan.attempt_{attempt + 1}", {"issues": issues, "ok": ok})
                    all_issues = list(issues)
                    if pending_validation_error:
                        all_issues.append(pending_validation_error)
                    if not all_issues or (ok and not pending_validation_error):
                        break
                    self._debug_print(
                        "plan",
                        response=f"plan fix attempt {attempt + 1}: {len(all_issues)} issue(s): {all_issues}",
                    )
                    plan_result = await self._fix_plan(task, plan_result, kept, all_issues)
                    pending_validation_error = None
                    try:
                        await self._validate_plan(plan_result, kept)
                    except ValueError as exc:
                        pending_validation_error = str(exc)
                        self._record(f"validate_plan.attempt_{attempt + 1}_error", pending_validation_error)
                    # Re-run swagger check after fix to catch newly introduced violations
                    _swagger_issues = _swagger_checker.check(plan_result.plan, kept)
                    if _swagger_issues:
                        self._record(f"swagger_required_param_violations.attempt_{attempt + 1}", _swagger_issues)
                        _swagger_msg = "Swagger required-parameter violations:\n" + "\n".join(
                            f"  - {i}" for i in _swagger_issues
                        )
                        pending_validation_error = (
                            (pending_validation_error + "\n" if pending_validation_error else "") + _swagger_msg
                        )

                if pending_validation_error:
                    raise ValueError(
                        pending_validation_error
                        + self._debug_context(selected_files, kept, chain_steps, plan_result)
                    )

                return plan_result

            except ValueError as exc:
                last_error = exc
                if planning_attempt < MAX_PLANNING_ATTEMPTS - 1:
                    print(
                        f"  Planning attempt {planning_attempt + 1}/{MAX_PLANNING_ATTEMPTS} failed, "
                        f"retrying from scratch: {exc}"
                    )

        raise last_error
