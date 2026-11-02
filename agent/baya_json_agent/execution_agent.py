"""
Execution agent: takes a ToolBasedResponse plan from planning_agent.py and
executes each step as a real HTTP call via curl (subprocess).

No swagger parsing is needed for routing — that comes from the plan itself.
The schemas are consulted only for declared body-field types (see
_primitive_body_fields). Base URL and authentication are injected at
construction time.

After all steps complete, an LLM call synthesizes the raw API outputs into
a natural language answer to the user's original request.
"""

import ast
import asyncio
import json
import re
import subprocess
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote, quote_plus

from pydantic import BaseModel
from pydantic_ai import Agent

from agent.baya_json_agent.auth import AuthProvider
from agent.baya_json_agent.planner import _AnyValue

class _ParsedValue(BaseModel):
    found: bool
    value: _AnyValue = None
    reason: Optional[str] = None


class _MissingDependency(Exception):
    """Raised when a step cannot run because a dependency produced no extractable value."""

class _HttpError(Exception):
    """Raised when a curl call returns a non-2xx HTTP status."""

class _AlreadyExistsBody(dict):
    """Dict subclass returned by _run_curl for HTTP 409 already-exists responses.
    Stored directly without LLM parsing so downstream steps are never blocked
    by a None output, even when they declare a depends_on on this step."""
from agent.common.configurator import Configurator
from agent.baya_json_agent.planner import ExecutionContext
from agent.providers.provider import ModelProvider


@dataclass
class ExecutionResult:
    """Returned by ExecutionAgent.execute()."""
    outputs: Dict[str, Any]  # raw curl responses — use for JSON saving / {step_id.result} refs
    answer: str              # LLM-generated natural language answer to the original task


@lru_cache(maxsize=None)
def _primitive_body_fields(schemas_dir: str) -> frozenset:
    """(METHOD, path_template, field_name) triples whose swagger-declared type
    is a primitive. Their values may legitimately look like JSON (wiki content,
    note bodies, ...) and must never be un-stringified by _build_cmd; body
    fields NOT in this set are arrays/objects (or off-schema) and get parsed.
    Missing/unreadable schemas yield an empty set → parse everything leniently.
    """
    primitive = {"string", "integer", "number", "boolean"}
    fields = set()
    try:
        filenames = list(json.loads((Path(schemas_dir) / "index.json").read_text()))
    except (OSError, json.JSONDecodeError):
        return frozenset()
    for fname in filenames:
        try:
            spec = json.loads((Path(schemas_dir) / fname).read_text())
        except (OSError, json.JSONDecodeError):
            continue

        def _deref(node: Any, depth: int = 0) -> dict:
            ref = node.get("$ref") if isinstance(node, dict) else None
            if not (isinstance(ref, str) and ref.startswith("#/")) or depth > 10:
                return node if isinstance(node, dict) else {}
            target: Any = spec
            for part in ref[2:].split("/"):
                target = target.get(part, {}) if isinstance(target, dict) else {}
            return _deref(target, depth + 1)

        for path, methods in (spec.get("paths") or {}).items():
            for method, op in (methods if isinstance(methods, dict) else {}).items():
                for param in (op.get("parameters") or []) if isinstance(op, dict) else []:
                    if not isinstance(param, dict):
                        continue
                    if param.get("in") == "body":  # real fields are the schema's properties
                        for pname, pschema in (_deref(param.get("schema")).get("properties") or {}).items():
                            if _deref(pschema).get("type") in primitive:
                                fields.add((method.upper(), path, pname))
                    # formData params too: the planner sometimes mislabels them as body args
                    elif param.get("in") == "formData" and param.get("type") in primitive:
                        fields.add((method.upper(), path, param["name"]))
    return frozenset(fields)


# ──────────────────────────────────────────────────────────────────────────────
# Execution agent
# ──────────────────────────────────────────────────────────────────────────────

class ExecutionAgent:
    """
    Executes a ToolBasedResponse plan step by step using curl.

    Steps that share no dependencies are run concurrently.
    Outputs from prior steps are substituted into later step arguments via
    {step_id.result} placeholders.

    After execution, an LLM synthesizes all API outputs into a final answer
    to the user's original task.

    Usage:
        registry = AuthRegistry.build_default("config/.server_env")
        agent = ExecutionAgent(auth=registry.get("gitlab"))
        result = await agent.execute(plan_response, task="Open an issue about dark mode")
        print(result.answer)
    """

    def __init__(self, auth: AuthProvider, base_url: str = "", debug: bool = False, api_dir: str = "api") -> None:
        self.base_url = base_url.rstrip("/")  # fallback if step has no base_url
        self.auth = auth
        self.debug = debug
        self.task_id: str = ""
        self.schemas_dir = str(Path(api_dir) / "schemas")

        config = Configurator()
        config.load_all_env()
        provider = ModelProvider(config)
        self.llm = provider.get_llm_model_provider()
        # cumulative token usage for the last execute() run
        self._run_tokens: dict = {"input": 0, "output": 0, "total": 0, "llm_calls": 0}

    @property
    def last_run_tokens(self) -> dict:
        """Return cumulative token usage from the most recent execute() call."""
        return dict(self._run_tokens)

    def _record_usage(self, result) -> None:
        """Accumulate token usage from a pydantic-ai run result; never raises."""
        try:
            usage = result.usage()
            in_tok = usage.input_tokens or 0
            out_tok = usage.output_tokens or 0
            self._run_tokens["input"] += in_tok
            self._run_tokens["output"] += out_tok
            self._run_tokens["total"] += in_tok + out_tok
            self._run_tokens["llm_calls"] += 1
        except Exception:
            pass

    def _debug(self, msg: str) -> None:
        if self.debug:
            print(f"[ExecutionAgent][DEBUG] {msg}")

    # ── Reference resolution ────────────────────────────────────────────────

    @staticmethod
    def _follow_accessor(obj: Any, accessor: str) -> Any:
        """Navigate a dot/bracket accessor chain into obj, e.g. '.default_branch', '[0].id', '[*].id'.

        Supported tokens:
          [*]                   wildcard — map remaining chain over each list element
          [?(@.field==value)]   filter — keep only list elements where dict[field] == value
          [n] / [-n]            numeric index (negatives count from the end, like Python)
          [sort_asc:f]          sort list ascending by field f (dicts) or value (scalars)
          [sort_desc:f]         sort list descending by field f (dicts) or value (scalars)
          [:N]                  take first N elements (slice)
          .key                  dict field access

        Unrecognised tokens return None (fail-loud). Callers rely on this to
        detect accessor failures and abort the dependent step rather than
        silently embedding the upstream object as a string.
        """
        pos = 0
        length = len(accessor)
        while pos < length:
            # [*] wildcard — apply the remaining accessor to every element of the list
            if accessor[pos:pos + 3] == "[*]":
                pos += 3
                if not isinstance(obj, list):
                    return None
                remaining = accessor[pos:]
                if remaining:
                    return [ExecutionAgent._follow_accessor(item, remaining) for item in obj]
                return list(obj)
            # [?(@.field==value)]  exact match filter
            # [?(@.field*=value)]  fuzzy contains filter — tries exact substring, then
            #                      normalized (no-space), then word-token overlap, then
            #                      difflib similarity. Exact matches always pass first.
            m = re.match(r'\[\?\(@\.(\w+(?:\.\w+)*)\s*(==|\*=)\s*([^\]]+)\)\]', accessor[pos:])
            if m:
                field_path = m.group(1)
                operator   = m.group(2)
                raw_val    = m.group(3).strip()
                # Strip surrounding quotes: planner emits "value" or 'value' in JSONPath syntax
                if (raw_val.startswith('"') and raw_val.endswith('"')) or \
                   (raw_val.startswith("'") and raw_val.endswith("'")):
                    raw_val = raw_val[1:-1]
                pos += len(m.group(0))
                def _get_nested(d: Any, path: str) -> Any:
                    for part in path.split("."):
                        if not isinstance(d, dict):
                            return None
                        d = d.get(part)
                    return d
                def _matches(item: Any, fp: str = field_path, rv: str = raw_val, op: str = operator) -> bool:
                    actual = _get_nested(item, fp)
                    if actual is None:
                        return False
                    if op == "*=":
                        rv_lower = rv.lower()
                        actual_lower = str(actual).lower()
                        # 1. Exact case-insensitive substring
                        if rv_lower in actual_lower:
                            return True
                        # 2. Normalized (spaces stripped) substring — "deep learning" → "deeplearning"
                        rv_nospace = re.sub(r'\s+', '', rv_lower)
                        actual_nospace = re.sub(r'\s+', '', actual_lower)
                        if rv_nospace and rv_nospace in actual_nospace:
                            return True
                        # 3. All query words appear in normalized field
                        words = rv_lower.split()
                        if words and all(w in actual_nospace for w in words):
                            return True
                        # 4. Difflib similarity — "future technology" → "futurology"
                        score = SequenceMatcher(None, rv_nospace or rv_lower, actual_nospace or actual_lower).ratio()
                        return score >= 0.6
                    try:
                        return actual == type(actual)(rv)
                    except (ValueError, TypeError):
                        return str(actual) == rv
                # Auto-unwrap wrapper dicts like {"posts": [...]} before filtering
                if isinstance(obj, dict):
                    list_vals = [v for v in obj.values() if isinstance(v, list)]
                    if list_vals:
                        obj = list_vals[0]
                if isinstance(obj, list):
                    obj = [item for item in obj if _matches(item)]
                continue
            # [field=value] shorthand equality filter (planner-generated alternative to [?(@.field==value)])
            m = re.match(r'\[(\w+)=([^\]]+)\]', accessor[pos:])
            if m:
                field_name = m.group(1)
                field_val  = m.group(2).strip("'\"")
                pos += len(m.group(0))
                if isinstance(obj, list):
                    obj = [
                        item for item in obj
                        if isinstance(item, dict) and str(item.get(field_name, "")) == field_val
                    ]
                continue
            # [sort_asc:field] / [sort_desc:field] — sort list by a named field (or value for scalars)
            m = re.match(r'\[sort_(asc|desc):(\w+)\]', accessor[pos:])
            if m:
                direction = m.group(1)
                field = m.group(2)
                pos += len(m.group(0))
                if isinstance(obj, list):
                    try:
                        obj = sorted(
                            obj,
                            key=lambda x: (x.get(field, 0) if isinstance(x, dict) else x),
                            reverse=(direction == "desc"),
                        )
                    except (TypeError, AttributeError):
                        pass
                continue
            # [:N] slice — take first N elements
            m = re.match(r'\[:(\d+)\]', accessor[pos:])
            if m:
                n = int(m.group(1))
                pos += len(m.group(0))
                if isinstance(obj, list):
                    obj = obj[:n]
                continue
            # [n] / [-n] numeric index (negative indices count from the end, like Python)
            m = re.match(r'\[(-?\d+)\]', accessor[pos:])
            if m:
                pos += len(m.group(0))
                if isinstance(obj, list):
                    idx = int(m.group(1))
                    if idx >= len(obj) or idx < -len(obj):
                        return None
                    obj = obj[idx]
                # If obj is a dict (fan-out already distributed this item), skip the
                # bracket index — the dict IS the item, so just continue down the chain.
                elif not isinstance(obj, dict):
                    return None
                if obj is None:
                    return None
                continue
            # .key dot accessor
            m = re.match(r'\.(\w+)', accessor[pos:])
            if m:
                pos += len(m.group(0))
                if not isinstance(obj, dict):
                    return None
                obj = obj.get(m.group(1))
                if obj is None:
                    return None
                continue
            # Arithmetic on a numeric leaf: ` - 5`, `+ 5`, `* 0.9`, `/ 2`.
            # Handles planner expressions like `.price - 5` (e.g. "reduce the
            # price by $5"): the navigation above yields the scalar, then this
            # applies the operation against a literal. Only fires on int/float
            # so it never shadows a navigation token; chains left-to-right.
            m = re.match(r'\s*([-+*/])\s*(\d+(?:\.\d+)?)', accessor[pos:])
            if m and isinstance(obj, (int, float)) and not isinstance(obj, bool):
                op, num = m.group(1), float(m.group(2))
                if op == "/" and num == 0:
                    return None
                obj = {"+": obj + num, "-": obj - num,
                       "*": obj * num, "/": (obj / num if num else obj)}[op]
                # Collapse whole-number results to int (e.g. 32 - 5 → 27, not
                # 27.0) so downstream string args read cleanly and integer-only
                # fields like quantity aren't sent a trailing ".0".
                if isinstance(obj, float) and obj.is_integer():
                    obj = int(obj)
                pos += len(m.group(0))
                continue
            # Unrecognised token — fail loud rather than silently returning
            # the current obj. _resolve treats None as accessor failure and
            # _execute_step's guard raises _MissingDependency, which is the
            # correct outcome: the planner used an unsupported accessor
            # pattern, so the dependent step cannot run with valid inputs.
            return None
        return obj

    def _resolve_foreach(
        self,
        foreach_value: Any,
        outputs: Dict[str, Any],
        depends_on: Optional[List[str]] = None,
    ) -> List[Any]:
        """Resolve a foreach field to a concrete list of items to iterate over."""
        if isinstance(foreach_value, list):
            return foreach_value
        if isinstance(foreach_value, str):
            # concat(step_X.result, step_Y.result, ...)<accessor>
            # Concatenates list outputs of multiple steps into a single list, then
            # applies the outer accessor (e.g. [sort_desc:field][:N]) to the combined
            # result. Useful for ranking/top-N tasks that span multiple paginated
            # GET calls.
            m_concat = re.match(r'^concat\(([^)]+)\)(.*)$', foreach_value)
            if m_concat:
                refs_str, outer_accessor = m_concat.group(1), m_concat.group(2)
                combined: List[Any] = []
                for ref in [r.strip() for r in refs_str.split(',')]:
                    rm = re.match(r'^(\w+)\.result(.*)$', ref)
                    if not rm:
                        continue
                    sid, sub_acc = rm.group(1), rm.group(2)
                    out = outputs.get(sid)
                    if out is None:
                        continue
                    if sub_acc:
                        out = self._follow_accessor(out, sub_acc)
                    if isinstance(out, list):
                        combined.extend(out)
                    elif out is not None:
                        combined.append(out)
                if outer_accessor:
                    resolved = self._follow_accessor(combined, outer_accessor)
                    if isinstance(resolved, list):
                        return resolved
                    return [resolved] if resolved is not None else []
                return combined
            m = re.match(r'^(\w+)\.result(.*)$', foreach_value)
            if m:
                step_id, accessor = m.group(1), m.group(2)
                out = outputs.get(step_id)
                if out is not None:
                    if not accessor:
                        return out if isinstance(out, list) else [out]
                    resolved = self._follow_accessor(out, accessor)
                    if isinstance(resolved, list) and resolved:
                        return resolved
                    if resolved is not None and not isinstance(resolved, list):
                        return [resolved]
                    # Accessor resolved to None/[] — common when the planner emits a
                    # bad accessor (e.g. .field on a list-of-lists like [[u1],[u2]]
                    # produced by an upstream foreach). Fall through to LOOP_OVER_PRIOR
                    # fallback so the step still iterates the dependency list.
            # "LOOP_OVER_PRIOR" sentinel OR a failed accessor on a real reference:
            # fall back to the most recent dependency that produced a non-empty list
            # so execution continues correctly.
            if depends_on:
                for dep_id in reversed(depends_on):
                    out = outputs.get(dep_id)
                    if isinstance(out, list) and out:
                        return out
        return []

    def _resolve(self, value: Any, outputs: Dict[str, Any]) -> Any:
        """
        Recursively replace {step_id.result} placeholders with prior outputs.

        Follows any accessor chain after .result (e.g. {step_1.result.default_branch})
        by navigating into the stored value. Falls back to the stored value as-is if
        navigation fails or no accessor is present.

        Nested references inside accessor chains (e.g. {step_2.result.entries[id=={step_1.result.id}]})
        are resolved by first substituting all inner {step.result} references so that
        _follow_accessor receives a fully concrete accessor string.
        """
        if isinstance(value, str):
            # Substitute {loop_item}, {loop_item.field}, {loop_item[n].field}, etc.
            if "__loop_item__" in outputs:
                def _sub_loop(m: re.Match) -> str:
                    item = outputs["__loop_item__"]
                    accessor = m.group(1)  # everything after "loop_item": "", ".field", "[0].id", etc.
                    if accessor:
                        result = ExecutionAgent._follow_accessor(item, accessor)
                        if result is None and not isinstance(item, (dict, list)):
                            # loop_item is already a scalar — the accessor was redundant
                            # (foreach extracted the leaf value, e.g. [*].id, but the
                            # argument still references {loop_item.id}). Use item directly.
                            pass
                        elif result is None:
                            return "null"
                        else:
                            item = result
                    return json.dumps(item) if isinstance(item, (dict, list)) else str(item)
                value = re.sub(r"\{loop_item([^}]*)\}", _sub_loop, value)

            _null_hits: list = []  # tracks accessor failures for null-resolution detection

            def _sub(m: re.Match) -> str:
                step_id, accessor = m.group(1), m.group(2)
                out = outputs.get(step_id)
                if out is None:
                    return m.group(0)  # leave unchanged if step not found
                # Follow accessor chain if present and the stored value is navigable
                if accessor and isinstance(out, (dict, list)):
                    navigated = self._follow_accessor(out, accessor)
                    if navigated is not None:
                        out = navigated
                    else:
                        _null_hits.append(m.group(0))
                        return "null"  # accessor failed — don't embed full object in URL
                return json.dumps(out) if isinstance(out, (dict, list)) else str(out)

            # Use [^{}]* (exclude both brace types) so only "leaf" references — those
            # whose accessor contains no nested {…} — are substituted per pass.
            # Iterate until stable so inner refs resolve first, then outer ones can use
            # the concrete values (e.g. [forum.id=={step_1.result.id}] → [forum.id==10079]).
            _LEAF_REF = re.compile(r"\{(\w+)\.result([^{}]*)\}")
            for _ in range(10):
                prev = value
                value = _LEAF_REF.sub(_sub, value)
                if value == prev:
                    break

            if _null_hits:
                return None  # signal failed accessor resolution to callers
        if isinstance(value, dict):
            return {k: self._resolve(v, outputs) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v, outputs) for v in value]
        return value

    # ── Curl command construction ────────────────────────────────────────────

    def _build_cmd(self, step, outputs: Dict[str, Any]) -> List[str]:
        tool_name = (
            step.tool_name.value
            if hasattr(step.tool_name, "value")
            else str(step.tool_name)
        )
        method, path_template = tool_name.split(" ", 1)
        method = method.upper()

        # Resolve all argument values, substituting {step_id.result} and {loop_item} references.
        # Bare-reference normalization (missing {}) is enforced at the Argument model level.
        _JSON_TEMPLATE_RE = re.compile(r"\{(?:step_\w+\.result|loop_item)")

        def _normalize(arg) -> Any:
            raw = arg.value
            # If the raw value is a JSON-object template string with embedded references,
            # parse the template BEFORE substituting references. This avoids escaping
            # failures when substituted values (e.g. license/file content) contain
            # newlines or quotes that would make the post-substitution string invalid JSON.
            if isinstance(raw, str):
                stripped = raw.strip()
                if (
                    stripped.startswith("{")
                    and stripped.endswith("}")
                    and _JSON_TEMPLATE_RE.search(stripped)
                ):
                    try:
                        template = json.loads(stripped)
                        if isinstance(template, dict):
                            return {k: self._resolve(v, outputs) for k, v in template.items()}
                    except json.JSONDecodeError:
                        pass
            return self._resolve(raw, outputs)

        # Detect path parameters from {name} tokens in the URL template
        path_param_names = set(re.findall(r"\{(\w+)\}", path_template))

        path_params: Dict[str, str] = {}
        query_params: Dict[str, Any] = {}
        body: Optional[Any] = None
        body_args_pending: List[tuple] = []  # (name, value) for body args, decided below

        for arg in step.arguments:
            name = arg.name
            value = _normalize(arg)
            pin = getattr(arg, "param_in", None)

            # URL template always wins for path params
            if name in path_param_names:
                path_params[name] = str(value)
            elif pin == "body":
                body_args_pending.append((name, value))
            elif pin in ("query", "formData", "header"):
                query_params[name] = value
            elif method in ("POST", "PUT", "PATCH"):
                body_args_pending.append((name, value))
            else:
                query_params[name] = value

        # The Argument value schema only advertises primitive types to the
        # planner LLM, so array/object body values arrive serialized as JSON
        # strings (e.g. tracks='[{"track_number": "239..."}]'). Parse them back
        # — unless the swagger declares the field as a primitive (wiki content,
        # note body, ...), whose value may legitimately look like JSON.
        def _parse_stringified_json(name: str, v: Any) -> Any:
            if not isinstance(v, str) or (method, path_template, name) in _primitive_body_fields(self.schemas_dir):
                return v
            stripped = v.strip()
            if (stripped.startswith("{") and stripped.endswith("}")) or (
                stripped.startswith("[") and stripped.endswith("]")
            ):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, (dict, list)):
                        return parsed
                except json.JSONDecodeError:
                    pass
                # Fallback: the Argument.value schema advertises only primitive
                # types to the planner LLM, so some models serialize an object
                # body as a Python repr (single quotes, True/False/None) rather
                # than JSON — e.g. "{'product_url': 'x.html', 'quantity': 2}".
                # json.loads rejects that, leaving the value a bare string that
                # then gets mis-wrapped as {"body": "<string>"} → HTTP 422.
                # ast.literal_eval safely parses Python literals (no code
                # execution) and recovers the intended dict/list.
                try:
                    parsed = ast.literal_eval(stripped)
                    if isinstance(parsed, (dict, list)):
                        return parsed
                except (ValueError, SyntaxError):
                    pass
            return v

        # Decide body shape from the collected body args.
        # Swagger generates body-parameter names like "<OperationId>Body"
        # (e.g. PostV1CartsQuoteIdItemsBody, PutV1OrdersParent_idBody) for the
        # Webarena shopping website. These are NOT the real JSON keys.
        # When the planner emits a single body arg
        # under such a name, the real top-level keys are inside `value` and we
        # must use `value` directly as the body. Multiple body args → merge by
        # name (each arg.name is then a real body field like "cartItem").
        if len(body_args_pending) == 1:
            name, value = body_args_pending[0]
            value = _parse_stringified_json(name, value)
            is_auto_wrapper = (
                name == "body"
                or bool(re.match(r"^(Get|Post|Put|Patch|Delete)[A-Z].*Body$", name))
            )
            if is_auto_wrapper and isinstance(value, (dict, list)):
                body = value
            else:
                body = {name: value}
        elif len(body_args_pending) > 1:
            body = {}
            for name, value in body_args_pending:
                body[name] = _parse_stringified_json(name, value)

        # Strip Rails-style [] suffix from JSON body keys.
        # Swagger specs generated from Rails docs use "field[]" for array params
        # (e.g. "actions[]"), but GitLab's JSON API expects plain "field" ("actions").
        if isinstance(body, dict):
            body = {(k[:-2] if k.endswith("[]") else k): v for k, v in body.items()}

        # Build final URL — step.base_url wins, fall back to init-time base_url
        step_base_url = getattr(step, "base_url", "").rstrip("/") or self.base_url
        path = path_template
        for pname, pval in path_params.items():
            path = path.replace(f"{{{pname}}}", quote(pval, safe=""))
        url = step_base_url + path
        if query_params:
            qs = "&".join(f"{k}={quote_plus(str(v))}" for k, v in query_params.items())
            url = f"{url}?{qs}"

        # Assemble curl command
        cmd = ["curl", "-g", "-s", "-X", method, "-H", "Content-Type: application/json"]
        for hname, hval in self.auth.get_headers(url=url).items():
            cmd += ["-H", f"{hname}: {hval}"]
        if body is not None:
            cmd += ["-d", json.dumps(body)]
        cmd.append(url)
        return cmd

    # ── Curl execution (blocking, runs in executor) ──────────────────────────

    def _run_curl(self, cmd: List[str]) -> Any:
        # Append -w "\n%{http_code}" so the status code appears on the last line
        cmd = cmd[:-1] + ["-w", "\n%{http_code}", cmd[-1]]
        for attempt in range(3):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                break
            except subprocess.TimeoutExpired:
                if attempt < 2:
                    continue
                raise _HttpError("Request timed out after 3 attempts")

        output = result.stdout
        # Split off the status code line appended by -w
        *body_lines, status_line = output.rsplit("\n", 1)
        raw = "\n".join(body_lines).strip()
        status_code = int(status_line.strip()) if status_line.strip().isdigit() else 0

        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = raw

        if 200 <= status_code < 300 or status_code == 304:
            # Some MCP servers (notably the reddit FastAPI wrapper) return HTTP 200
            # with {"success": false, "error_message": "..."} for business-logic
            # failures (e.g. the playwright vote click didn't persist). The HTTP
            # success doesn't reflect the actual operation outcome — propagate the
            # business-logic failure as an _HttpError so the executor treats it the
            # same as any other tool failure (and cascade/alternative paths fire).
            if (
                isinstance(body, dict)
                and body.get("success") is False
                and "error_message" in body
            ):
                raise _HttpError(
                    f"HTTP {status_code} (success=false): {body.get('error_message')}"
                )
            return body
        # 409 Conflict: resource already exists — idempotent success.
        # Return a marked dict so _execute_step can skip LLM parsing and store
        # a non-None value, keeping dependent steps unblocked.
        if status_code == 409:
            return _AlreadyExistsBody(body if isinstance(body, dict) else {})
        raise _HttpError(
            f"HTTP {status_code}: {json.dumps(body) if isinstance(body, dict) else body}"
        )

    # ── Inter-step output parsing ────────────────────────────────────────────

    async def _parse_step_output(self, step, raw_output: Any, dependent_steps: list) -> Any:
        """
        After a step completes, ask the LLM to extract the specific value(s)
        that downstream steps actually need from the raw curl response.

        The parsed value is stored in ctx.step_outputs and is what gets
        substituted when downstream steps reference {step_id.result}.
        The full raw response is preserved separately for final answer generation.
        """
        tool_name = step.tool_name.value if hasattr(step.tool_name, "value") else str(step.tool_name)

        # Collect all {step_id.result...} accessor patterns used across dependent steps
        all_accessors: list[str] = []
        for ds in dependent_steps:
            for a in (ds.arguments or []):
                serialized = json.dumps(a.value) if isinstance(a.value, (dict, list)) else (a.value if isinstance(a.value, str) else "")
                all_accessors += re.findall(r"\{" + step.step_id + r"\.result[^}]*\}", serialized)

        # Describe what each dependent step needs from this result
        needs = []
        for ds in dependent_steps:
            ds_tool = ds.tool_name.value if hasattr(ds.tool_name, "value") else str(ds.tool_name)
            ref_args = [
                f"  - argument '{a.name}' (value: {json.dumps(a.value) if isinstance(a.value, (dict, list)) else a.value!r})"
                for a in (ds.arguments or [])
                if step.step_id in (json.dumps(a.value) if isinstance(a.value, (dict, list)) else (a.value if isinstance(a.value, str) else ""))
            ]
            hint = getattr(ds, "hints", "") or ""
            dep_desc = f"Step {ds.step_id} ({ds_tool}):"
            if ref_args:
                dep_desc += "\n" + "\n".join(ref_args)
            if hint:
                dep_desc += f"\n  Hint: {hint}"
            needs.append(dep_desc)

        # Build accessor guidance: tell the LLM exactly which field(s) to extract
        accessor_guidance = ""
        unique_accessors = list(dict.fromkeys(all_accessors))
        if unique_accessors:
            # If every accessor has a structural path after .result (e.g. [0].field,
            # [sort_desc:f][0].id, .key), _follow_accessor handles all the navigation
            # correctly on the raw output — skip the LLM entirely. The LLM is only
            # needed for bare {step.result} references that require semantic extraction,
            # OR when a downstream hint describes a content transformation to apply.
            structural_path_re = re.compile(
                r"\{" + re.escape(step.step_id) + r"\.result[.\[]"
            )
            _transform_keywords = {"decode", "encode", "base64", "replace", "transform", "modify"}
            _has_transform_hint = any(
                any(kw in (getattr(ds, "hints", "") or "").lower() for kw in _transform_keywords)
                for ds in dependent_steps
            )
            # [*] wildcard returns a list — can't be used as-is for a single-value
            # argument. Let the LLM parse step run so hints can guide selection.
            has_wildcard = any(r"\[\*\]" in acc or "[*]" in acc for acc in unique_accessors)
            if not has_wildcard and all(structural_path_re.match(acc) for acc in unique_accessors) and not _has_transform_hint:
                return raw_output

            # Map each accessor to the hint of the step that uses it (first match wins)
            _acc_to_hint: dict[str, str] = {}
            for ds in dependent_steps:
                ds_hint = getattr(ds, "hints", "") or ""
                for a in (ds.arguments or []):
                    serialized = json.dumps(a.value) if isinstance(a.value, (dict, list)) else (a.value if isinstance(a.value, str) else "")
                    for acc in re.findall(r"\{" + step.step_id + r"\.result[^}]*\}", serialized):
                        if acc not in _acc_to_hint:
                            _acc_to_hint[acc] = ds_hint

            lines = []
            for acc in unique_accessors:
                # Extract the field path after .result (e.g. ".default_branch" → "default_branch")
                m = re.match(r"\{" + step.step_id + r"\.result\.?(.*)\}", acc)
                field_path = m.group(1) if m and m.group(1) else None
                acc_hint = _acc_to_hint.get(acc, "")
                if acc_hint and any(kw in acc_hint.lower() for kw in _transform_keywords):
                    if field_path:
                        lines.append(
                            f"  '{acc}' → read the value at field '{field_path}', apply the transformation described in the downstream hint, and return the transformed result"
                        )
                    else:
                        lines.append(
                            f"  '{acc}' → apply the transformation described in the downstream hint to the full value and return the transformed result"
                        )
                elif field_path:
                    lines.append(f"  '{acc}' → extract the value at field '{field_path}' (must be a scalar string/number, not an object)")
                else:
                    lines.append(f"  '{acc}' → extract the full value")
            accessor_guidance = "Accessor patterns used by dependent steps:\n" + "\n".join(lines) + "\n\n"

        prompt = (
            f"An API call just completed:\n"
            f"  {tool_name}\n\n"
            f"Raw response:\n{json.dumps(raw_output, indent=2) if isinstance(raw_output, (dict, list)) else raw_output}\n\n"
            f"The following steps depend on this result:\n" + "\n\n".join(needs) + "\n\n"
            + accessor_guidance
            + f"Extract the value(s) from the response that should be substituted for "
            f"{{{step.step_id}.result}} in the dependent steps above.\n\n"
            f"Rules:\n"
            f"- Always extract the most specific, deeply-nested scalar (string, number) when that is what the dependent step needs. Never return a wrapper object when a plain value suffices.\n"
            f"- If the dependent step needs a structured sub-object, return that object.\n"
            f"- If the dependent step uses the value as a path parameter and multiple values are needed, return a JSON array. The engine will call the step once per item.\n"
            f"- If the response is a list and only one specific item is needed, extract only that item or field.\n"
            f"- If a downstream hint describes a transformation (e.g. decode base64, replace text, re-encode), apply that transformation to the extracted field value and return the transformed result — not the original raw value.\n"
            f"- Set found=false only if the required value is genuinely absent from the response."
        )
        self._debug(f"Parsing output of {step.step_id}:\n{prompt}")
        agent = Agent(self.llm, output_type=_ParsedValue)
        try:
            result = await agent.run(prompt)
        except Exception as e:
            print(f"[ExecutionAgent] ✗ _parse_step_output LLM call failed for {step.step_id}: {type(e).__name__}: {e}")
            raise
        self._record_usage(result)
        envelope = result.output
        self._debug(f"Parsed value for {step.step_id}: found={envelope.found} value={envelope.value!r}")

        if not envelope.found:
            self._debug(f"No value found for {step.step_id}: {envelope.reason} — storing None")
            return None

        return envelope.value

    async def _execute_step(self, step, ctx: ExecutionContext, plan: list, raw_outputs: Dict[str, Any]) -> None:
        tool_name = (
            step.tool_name.value
            if hasattr(step.tool_name, "value")
            else str(step.tool_name)
        )
        prefix = f"[task {self.task_id}]" if self.task_id else "[ExecutionAgent]"
        print(f"{prefix} → {step.step_id}: {tool_name}")
        try:
            loop = asyncio.get_event_loop()

            # Conditional step: evaluate an equality condition and store the chosen branch.
            if getattr(step, "step_type", "tool_call") == "conditional":
                # Resolve against raw_outputs (full API responses) so deep accessor
                # chains like [0].author.username work — ctx.step_outputs holds the
                # LLM-extracted summary which often drops nested fields.
                resolved_condition = self._resolve(step.condition, raw_outputs)
                if not isinstance(resolved_condition, str) or " == " not in resolved_condition:
                    raise _MissingDependency(
                        f"conditional step '{step.step_id}': condition "
                        f"{step.condition!r} could not be resolved to a comparable "
                        f"string (got {resolved_condition!r}). Likely a referenced "
                        f"step output is missing a field used in the condition."
                    )
                lhs, _, rhs = resolved_condition.partition(" == ")
                result = lhs.strip() == rhs.strip()
                value = self._resolve(step.if_true if result else step.if_false, raw_outputs)
                raw_outputs[step.step_id] = value
                ctx.mark_completed(step.step_id, value)
                branch = "true" if result else "false"
                print(f"{prefix} ✓ {step.step_id} done (conditional → {branch}: {value!r})")
                return

            # Guard: if any argument references a prior step's output that could
            # not be extracted (stored as None), abort before making any curl call.
            for arg in (step.arguments or []):
                arg_str = json.dumps(arg.value) if isinstance(arg.value, (dict, list)) else str(arg.value)
                m = re.search(r"\{(\w+)\.result", arg_str)
                if m:
                    ref_id = m.group(1)
                    if ref_id not in ctx.step_outputs:
                        continue
                    val = ctx.step_outputs[ref_id]
                    if val is None:
                        raise _MissingDependency(
                            f"step '{ref_id}' produced no extractable value for argument '{arg.name}'"
                        )
                    # Also guard against indexing into an empty list, e.g. {step_2.result[0].id}
                    # when step_2 returned [].
                    if isinstance(val, list) and len(val) == 0:
                        idx_m = re.search(r"\{" + ref_id + r"\.result\[(\d+)\]", arg_str)
                        if idx_m:
                            raise _MissingDependency(
                                f"step '{ref_id}' returned an empty list; "
                                f"argument '{arg.name}' cannot index into it"
                            )
                    # Guard against deep accessor failures, e.g. [?(@.title==X)][0].field
                    # where the filter matches nothing — _resolve returns None in this case.
                    if re.search(r'\{' + re.escape(ref_id) + r'\.result[^}]*\[', arg_str):
                        resolved = self._resolve(arg.value, ctx.step_outputs)
                        if resolved is None:
                            raise _MissingDependency(
                                f"argument '{arg.name}': accessor on step '{ref_id}' output "
                                f"resolved to null — no matching value found"
                            )

            # Foreach: step declares an explicit iteration list — run once per element
            # and collect all results as a list stored in ctx.step_outputs.
            foreach_val = getattr(step, "foreach", None)
            if foreach_val is not None:

                # If the foreach expression embeds inner {step_X.result.field}
                # references (e.g. JSONPath filter values like
                # [?(@.subreddit=={step_1.result.name})]), substitute them now
                # so _follow_accessor sees a concrete filter. Without this the
                # filter literal "{step_1.result.name}" is compared as a string
                # and matches nothing — every foreach silently iterates 0 times.
                if isinstance(foreach_val, str) and "{" in foreach_val:
                    resolved = self._resolve(foreach_val, ctx.step_outputs)
                    if isinstance(resolved, str):
                        foreach_val = resolved
                # Use raw_outputs (full curl responses) so accessor chains like
                # [sort_desc:field][:N][*].id operate on the complete API data,
                # not on the LLM-extracted subset stored in ctx.step_outputs.
                items = self._resolve_foreach(
                    foreach_val, raw_outputs,
                    depends_on=getattr(step, "depends_on", None),
                )
                if not items:
                    raw_outputs[step.step_id] = []
                    ctx.mark_completed(step.step_id, [])
                    print(f"{prefix} ✓ {step.step_id} done (foreach: 0 items)")
                    return
                print(f"{prefix} → foreach: {step.step_id} × {len(items)} item(s)")
                all_results = []
                for item in items:
                    loop_outputs = dict(ctx.step_outputs)
                    loop_outputs["__loop_item__"] = item

                    cmd = self._build_cmd(step, loop_outputs)
                    self._debug(f"{step.step_id} foreach curl (item={item!r}): " + " ".join(
                        f"'{p}'" if " " in p else p for p in cmd
                    ))
                    item_result = await loop.run_in_executor(None, self._run_curl, cmd)
                    all_results.append(item_result)
                raw = all_results
                raw_outputs[step.step_id] = raw
                # Store raw list directly — downstream foreach refs resolve via _resolve_foreach
                ctx.mark_completed(step.step_id, raw)
                print(f"{prefix} ✓ {step.step_id} done (foreach: {len(items)} iterations)")
                return

            # Detect fan-out: if a path parameter resolves to a list, call the
            # step once per item and collect all results into a list.
            _, path_template = tool_name.split(" ", 1)
            path_param_names = set(re.findall(r"\{(\w+)\}", path_template))

            fan_out_ref_step: Optional[str] = None
            fan_out_values: Optional[List[Any]] = None
            for arg in (step.arguments or []):
                if arg.name in path_param_names:
                    # Check the raw stored output (not _resolve, which stringifies lists).
                    # Only fan-out for bare {step.result} or [*] wildcard patterns —
                    # [N] index and [sort_desc:...] patterns resolve to a single item.
                    m = re.search(r"\{(\w+)\.result([^}]*)\}", str(arg.value))
                    ref_step_id = m.group(1) if m else None
                    accessor_path = m.group(2) if m else ""
                    raw_val = ctx.step_outputs.get(ref_step_id) if ref_step_id else None
                    is_multi = not accessor_path or "[*]" in accessor_path
                    if isinstance(raw_val, list) and is_multi:
                        fan_out_ref_step = ref_step_id
                        fan_out_values = raw_val
                        break

            if fan_out_values is not None:
                # Detect if multiple steps share the same fan-out source.
                # If so, distribute one item per step rather than each step
                # iterating all items independently.
                def _references_same_source(s) -> bool:
                    _, pt = (s.tool_name.value if hasattr(s.tool_name, "value") else str(s.tool_name)).split(" ", 1)
                    pp = set(re.findall(r"\{(\w+)\}", pt))
                    return any(
                        arg.name in pp and
                        re.search(r"\{(\w+)\.result", str(arg.value)) and
                        re.search(r"\{(\w+)\.result", str(arg.value)).group(1) == fan_out_ref_step
                        for arg in (s.arguments or [])
                    )

                fan_out_group = [s for s in plan if _references_same_source(s)]

                if len(fan_out_group) > 1:
                    # Distributed mode: this step handles exactly one item.
                    step_index = next(
                        (i for i, s in enumerate(fan_out_group) if s.step_id == step.step_id), 0
                    )
                    item = fan_out_values[step_index] if step_index < len(fan_out_values) else fan_out_values[-1]
                    print(f"[ExecutionAgent] Fan-out distributed for {step.step_id}: item {step_index + 1}/{len(fan_out_values)} (value={item})")
                    modified_outputs = dict(ctx.step_outputs)
                    modified_outputs[fan_out_ref_step] = item
                    cmd = self._build_cmd(step, modified_outputs)
                    self._debug(f"{step.step_id} curl command (item={item}):\n  " + " ".join(
                        f"'{p}'" if " " in p else p for p in cmd
                    ))
                    raw = await loop.run_in_executor(None, self._run_curl, cmd)
                else:
                    # Single fan-out step: iterate all items and collect results.
                    print(f"[ExecutionAgent] Fan-out detected for {step.step_id}: {len(fan_out_values)} item(s)")
                    all_results = []
                    for item in fan_out_values:
                        modified_outputs = dict(ctx.step_outputs)
                        modified_outputs[fan_out_ref_step] = item
                        cmd = self._build_cmd(step, modified_outputs)
                        self._debug(f"{step.step_id} curl command (item={item}):\n  " + " ".join(
                            f"'{p}'" if " " in p else p for p in cmd
                        ))
                        result = await loop.run_in_executor(None, self._run_curl, cmd)
                        all_results.append(result)
                    raw = all_results
            else:
                cmd = self._build_cmd(step, ctx.step_outputs)
                self._debug(f"{step.step_id} curl command:\n  " + " ".join(
                    f"'{p}'" if " " in p else p for p in cmd
                ))
                raw = await loop.run_in_executor(None, self._run_curl, cmd)

            self._debug(f"{step.step_id} response (2xx OK): {json.dumps(raw, indent=2) if isinstance(raw, (dict, list)) else raw}")

            # Always preserve the full raw response for final answer generation
            raw_outputs[step.step_id] = raw

            if isinstance(raw, _AlreadyExistsBody):
                # 409 already-exists: store the body directly (non-None) so that
                # any step with depends_on this one is never blocked by the guard.
                ctx.mark_completed(step.step_id, dict(raw))
                print(f"{prefix} ✓ {step.step_id} done (already exists, skipping parse)")
            else:
                # If downstream steps reference this result, parse it down to what they need
                dependent_steps = [s for s in plan if step.step_id in (getattr(s, "depends_on", []) or [])]
                if dependent_steps:
                    print(f"{prefix} Parsing {step.step_id} output for {len(dependent_steps)} dependent step(s)...")
                    parsed = await self._parse_step_output(step, raw, dependent_steps)
                    # Explicitly store None so the downstream guard can detect parse failure.
                    ctx.step_outputs[step.step_id] = parsed
                    ctx.mark_completed(step.step_id, parsed)
                else:
                    ctx.mark_completed(step.step_id, raw)
                print(f"{prefix} ✓ {step.step_id} done")
        except _MissingDependency as e:
            ctx.mark_failed(step.step_id, str(e))
            print(f"{prefix} ✗ {step.step_id} skipped: {e}")
            raise
        except _HttpError as e:
            ctx.mark_failed(step.step_id, str(e))
            raw_outputs[step.step_id] = str(e)
            print(f"{prefix} ✗ {step.step_id} failed: {e}")
            raise
        except Exception as e:
            ctx.mark_failed(step.step_id, str(e))
            print(f"{prefix} ✗ {step.step_id} failed: {e}")
            raise

    # ── LLM answer generation ────────────────────────────────────────────────

    async def _generate_answer(self, task: str, plan, outputs: Dict[str, Any]) -> str:
        """
        Ask the LLM to answer the original task based on what was executed and returned.
        """
        steps_summary = []
        skipped_steps = []
        for step in plan:
            sid = step.step_id
            tool_name = step.tool_name.value if hasattr(step.tool_name, "value") else str(step.tool_name)
            if sid not in outputs:
                skipped_steps.append(f"Step {sid} ({tool_name}): skipped — earlier step failed")
            else:
                output = outputs[sid]
                steps_summary.append(
                    f"Step {sid} ({tool_name}):\n{json.dumps(output, indent=2) if isinstance(output, (dict, list)) else output}"
                )

        skipped_note = ""
        if skipped_steps:
            skipped_note = (
                "\n\nThe following steps did not complete (API error, missing resource, or unextractable upstream value):\n"
                + "\n".join(skipped_steps)
                + "\n"
            )

        prompt = (
            f"The user asked: {task}\n\n"
            f"The following API calls were made to fulfill this request:\n\n"
            + "\n\n".join(steps_summary or ["(no steps completed)"])
            + skipped_note
            + "\n\nBased on the above, provide a concise answer to the user's original request. "
            "If some steps were skipped, explain what could not be completed and why. "
            "Do not repeat raw JSON — describe the result in plain language."
        )
        self._debug(f"Answer generation prompt:\n{prompt}")
        agent = Agent(self.llm, output_type=str)
        result = await agent.run(prompt)
        self._record_usage(result)
        answer = result.output
        self._debug(f"Answer: {answer}")
        return answer

    # ── Public entry point ───────────────────────────────────────────────────

    async def execute(self, plan_response, task: str = "") -> ExecutionResult:
        """
        Execute a ToolBasedResponse plan and generate a natural language answer.

        Args:
            plan_response: ToolBasedResponse returned by PlanningAgent.plan()
            task:          The original user request, used by the LLM to frame its answer.

        Returns:
            ExecutionResult with raw outputs (for saving/reference resolution)
            and a natural language answer to the original task.
        """
        plan = plan_response.plan
        ctx = ExecutionContext(plan)
        self._run_tokens = {"input": 0, "output": 0, "total": 0, "llm_calls": 0}
        self.last_ctx = ctx
        self.last_raw_outputs: Dict[str, Any] = {}  # unmodified curl responses, used for final answer
        self.last_answer: str = ""
        raw_outputs = self.last_raw_outputs

        while not ctx.is_complete():
            ready = ctx.get_ready_steps()
            if not ready:
                remaining = [
                    s.step_id for s in plan if s.step_id not in ctx.completed_steps
                ]
                raise RuntimeError(
                    f"Plan stuck — no ready steps. Remaining: {remaining}"
                )

            for step_id in ready:
                ctx.mark_executing(step_id)

            steps_to_run = [s for s in plan if s.step_id in ready]
            # return_exceptions=True so a single failure in the batch doesn't
            # cancel sibling steps that are parallel alternatives (e.g. a /groups
            # path and a /users path both running against an ambiguous namespace).
            # Each step's own except clauses in _execute_step already record the
            # failure via ctx.mark_failed; transitive dependents are skipped via
            # the _MissingDependency guard at the top of _execute_step.
            await asyncio.gather(*[
                self._execute_step(s, ctx, plan, raw_outputs) for s in steps_to_run
            ], return_exceptions=True)

            # Alternative-aware early termination:
            # Steps with the same numeric prefix and different letter suffix
            # (e.g. step_3a, step_3b) are alternative approaches to the same
            # sub-goal. After each batch, decide whether any remaining unstarted
            # step can still potentially succeed:
            #   - For each "family" (numeric prefix), track if any member succeeded.
            #   - A remaining step is "viable" if every dependency family either
            #     has a successful member or is still pending (has unstarted members).
            #   - If NO remaining step is viable, every path to the goal is blocked
            #     and we should stop wasting calls — fail the task now.
            def _family(sid: str) -> str:
                m = re.match(r"^(step_\d+)[a-z]?$", sid)
                return m.group(1) if m else sid

            family_succeeded: set = set()
            family_has_pending: set = set()
            family_has_failed: set = set()
            for s in plan:
                fam = _family(s.step_id)
                if s.step_id in ctx.completed_steps and s.step_id not in ctx.failed_steps:
                    family_succeeded.add(fam)
                elif s.step_id in ctx.failed_steps:
                    family_has_failed.add(fam)
                else:
                    family_has_pending.add(fam)

            remaining_unstarted = [
                s for s in plan if s.step_id not in ctx.completed_steps
            ]
            any_viable = False
            for s in remaining_unstarted:
                deps = getattr(s, "depends_on", []) or []
                blocked = False
                for d in deps:
                    dfam = _family(d)
                    if dfam in family_succeeded or dfam in family_has_pending:
                        continue
                    if dfam in family_has_failed:
                        blocked = True
                        break
                if not blocked:
                    any_viable = True
                    break

            if remaining_unstarted and not any_viable:
                tag = f"[task {self.task_id}]" if self.task_id else "[ExecutionAgent]"
                for s in remaining_unstarted:
                    ctx.mark_failed(s.step_id, "skipped — all alternative paths failed")
                    print(f"{tag} ✗ {s.step_id} skipped (all alternative paths failed)")
                break

        tag = f"[task {self.task_id}]" if self.task_id else "[ExecutionAgent]"
        print(f"{tag} Generating answer...")
        answer = await self._generate_answer(task, plan, raw_outputs)
        self.last_answer = answer
        return ExecutionResult(outputs=raw_outputs, answer=answer)
