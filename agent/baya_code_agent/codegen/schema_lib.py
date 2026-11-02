"""
Swagger parsing and endpoint rendering for the codegen-facing schema tools.

DELIBERATE COPY. The rendering logic here is duplicated from
agent/baya_json_agent/planning_agent.py (_parse_swagger, _extract_response_schema,
_extract_endpoints, _format_endpoint_detail, EndpointInfo) rather than shared
with it, so that the planner keeps working exactly as it does today and nothing
in this file can perturb it. The JSON planner does not import this module.

Fidelity to the original is checked, not assumed: see the copy-fidelity check in
agent/baya_code_agent/tests/schema_lib_check.py, which renders every endpoint in every schema
through both this module and the live PlanningAgent methods and requires the
strings to match. Re-run it if either side changes.

Imported by agent/baya_code_agent/codegen/build_endpoint_catalog.py (prompt build time, outside the
agent sandbox) and agent/baya_code_agent/codegen/query_schema.py (run by the codegen agent inside the
sandbox). Kept dependency-free of api/ and agent/ so the sandbox only has to
grant reads to the codegen schema tools.

DETERMINISM CONTRACT
--------------------
agent/baya_code_agent/codegen/build_site_prompt.sh must emit byte-identical output for a given
(site, slot) on every run, or Anthropic prompt caching cold-starts the prefix
(measured: ~26k tokens re-created instead of read). Everything reachable from
catalog_lines() and resolve_site_schema_files() therefore honours:

  - no time, datetime, mtime/os.stat, or any other clock or filesystem metadata
  - no glob, os.listdir, or os.walk
  - no set iteration and no dict iteration used to determine ORDER

Ordering is fully specified: paths are emitted in sorted() codepoint order
(locale-independent, so LC_ALL cannot perturb it) and methods follow the fixed
_METHOD_ORDER tuple below. Schema files are ordered main-then-extra by an
explicit sort key, never by index.json insertion order.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Canonical emission order for the catalog. Deliberately a fixed tuple rather
# than sorted(_HTTP_METHODS) or schema order: it is what makes a path's method
# list independent of both dict iteration order and set iteration order.
_METHOD_ORDER = ("get", "post", "put", "patch", "delete", "head", "options")

_GENERIC_RESPONSE_DESCRIPTIONS = {
    "successful", "success", "ok", "200", "201", "no content", "accepted",
}


class EndpointInfo:
    """Plain-object copy of agent.baya_json_agent.planning_agent.EndpointInfo.

    Deliberately not a pydantic BaseModel: these scripts have no other reason to
    import pydantic, and the fields are never validated or serialised here.
    """

    __slots__ = ("api", "method", "path", "summary", "description",
                 "parameters", "response_schema", "base_path")

    def __init__(self, api, method, path, summary, description, parameters,
                 response_schema="", base_path=""):
        self.api = api                      # source swagger filename
        self.method = method                # GET, POST, etc.
        self.path = path                    # /V1/carts/mine
        self.summary = summary
        self.description = description
        self.parameters = parameters        # raw swagger parameter dicts
        self.response_schema = response_schema
        self.base_path = base_path          # swagger basePath — prefix, NOT host


# ── Loading ─────────────────────────────────────────────────────────────────

def load_index(api_dir: Path) -> dict:
    with open(Path(api_dir) / "schemas" / "index.json") as f:
        return json.load(f)


def parse_swagger(filename: str, api_dir: Path) -> dict:
    """Fully-resolved spec via prance. Slow (~1.9s shopping, ~2.7s gitlab).

    prance is imported lazily so importing this module costs nothing for the
    catalog generator, which runs on every task and must not pay that cost.

    Note: prance does NOT resolve internal #/definitions/ refs, which is why the
    body renderer below takes an explicit `definitions` dict.
    """
    import prance

    filepath = (Path(api_dir) / "schemas" / filename).absolute()
    return prance.BaseParser(str(filepath), lazy=False).specification


def load_raw_spec(filename: str, api_dir: Path) -> dict:
    """Unresolved spec via plain json (~0.02s). Sufficient for the catalog,
    which only needs paths and method keys and never dereferences anything."""
    with open(Path(api_dir) / "schemas" / filename) as f:
        return json.load(f)


def _resolve_schema_files(sites: List[str], index: Dict[str, str]) -> List[str]:
    """Copy of api.schema_index.resolve_schema_files, inlined to keep this
    module free of api/ imports. Keep the matching rule identical to that file:
    a schema belongs to a site if it starts with "{site}_api" or
    "{site}_extra_api"."""
    selected: List[str] = []
    for site in sites:
        site = (site or "").strip().lower()
        if not site:
            continue
        for fname in index:
            if fname in selected:
                continue
            if fname.startswith(f"{site}_api") or fname.startswith(f"{site}_extra_api"):
                selected.append(fname)
    return selected


def resolve_site_schema_files(site: str, api_dir: Path) -> List[str]:
    """Schema filenames for a site, main API first, extra API second.

    Imposes an explicit total order so the result cannot inherit index.json's
    key order — see the determinism contract above.
    """
    files = _resolve_schema_files([site], load_index(api_dir))
    if not files:
        raise ValueError(f"no schema files resolve for site {site!r}")

    main = f"{site}_api_schema.json"
    if main not in files:
        # Guards a divergence that would otherwise be silent: scripts/
        # build_site_prompt.sh decides a site's schemas with a [[ -s ... ]] file
        # test, while everything else goes through index.json. A schema added to
        # api/schemas/ but not registered in index.json would vanish from the
        # catalog while still being named in the prompt's AVAILABLE API SCHEMAS.
        raise ValueError(
            f"{main} did not resolve for site {site!r} (got {files}). "
            f"Is it registered in api/schemas/index.json?"
        )

    return sorted(files, key=lambda f: (0 if f.startswith(f"{site}_api") else 1, f))


# ── Catalog ─────────────────────────────────────────────────────────────────

def catalog_lines(filename: str, api_dir: Path) -> List[str]:
    """One 'GET,POST /path' line per path, in sorted path order.

    Paths carrying no HTTP method are skipped. This output feeds a cached prompt
    prefix — see the determinism contract above.
    """
    spec = load_raw_spec(filename, api_dir)
    lines: List[str] = []
    for path in sorted(spec.get("paths", {})):
        path_item = spec["paths"][path]
        if not isinstance(path_item, dict):
            continue
        methods = [m.upper() for m in _METHOD_ORDER if isinstance(path_item.get(m), dict)]
        if methods:
            lines.append(f"{','.join(methods)} {path}")
    return lines


# ── Response shape rendering ────────────────────────────────────────────────

def extract_response_schema(spec: dict, operation: dict, max_props: int = 12) -> str:
    """Human-readable 200/201 response shape.

    max_props defaults to 12, reproducing agent/baya_json_agent/planning_agent.py byte-for-byte.
    Callers needing deeply-nested definitions raise it — at 12, `addresses` is
    truncated out of customer-data-customer-interface, where it sits at index 19.
    """
    responses = operation.get("responses", {})
    ok = responses.get("200") or responses.get("201")
    if not isinstance(ok, dict):
        return ""

    description = ok.get("description", "")
    schema = ok.get("schema")

    if not isinstance(schema, dict):
        return description if description.lower().rstrip(".") not in _GENERIC_RESPONSE_DESCRIPTIONS else ""

    def _resolve_ref(ref: str) -> dict:
        if ref.startswith("#/definitions/"):
            def_name = ref.split("/definitions/", 1)[-1]
            return spec.get("definitions", {}).get(def_name, {})
        return {}

    def _collect_props(definition: dict) -> list:
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
            return f"{{{', '.join(list(props_dict.keys())[:max_props])}}}"
        # At depth 0: expand array-valued properties one level to show item shape
        parts = []
        for k, v in list(props_dict.items())[:max_props]:
            if isinstance(v, dict) and v.get("type") == "array":
                inner = _props_str(v.get("items", {}), depth + 1)
                parts.append(f"{k}: [{inner}]" if inner else k)
            else:
                parts.append(k)
        return f"{{{', '.join(parts)}}}" if parts else ""

    shape = _props_str(schema)
    if shape:
        return shape
    return description if description.lower().rstrip(".") not in _GENERIC_RESPONSE_DESCRIPTIONS else ""


def extract_endpoints(spec: dict, api_name: str) -> List[EndpointInfo]:
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
                response_schema=extract_response_schema(spec, operation),
                base_path=base_path,
            ))
    return endpoints


# ── Request body rendering ──────────────────────────────────────────────────
#
# prance.BaseParser(lazy=False) does NOT resolve internal #/definitions/ refs,
# so a body parameter's schema still reads {"customer": {"$ref": "..."}}. The
# planner's rendering stops there — one level, refs unfollowed — which is why
# region_id (at customer.addresses[].region.region_id) was invisible to codegen
# and got guessed instead. Passing `definitions` turns the expansion on; the
# planner never does, so its output is unchanged.

def _render_prop_list(props: dict, definitions: dict, hops_left: int,
                      seen: frozenset, max_props: int) -> str:
    parts = []
    for name, sub in list(props.items())[:max_props]:
        shape = _render_shape(sub, definitions, hops_left, seen, max_props)
        parts.append(f"{name}: {shape}" if shape else name)
    return ", ".join(parts)


def _render_shape(obj, definitions: dict, hops_left: int,
                  seen: frozenset, max_props: int) -> str:
    """Render a schema object, following $refs up to `hops_left` more times.

    `seen` carries the definition names already expanded on this branch. It is
    what makes the render terminate: extension_attributes chains would otherwise
    recurse indefinitely. Once a name repeats, emit the bare name.
    """
    if not isinstance(obj, dict):
        return ""

    ref = obj.get("$ref", "")
    if ref:
        name = ref.split("/definitions/", 1)[-1]
        if hops_left <= 0 or name in seen:
            return name
        props = definitions.get(name, {}).get("properties", {})
        if not props:
            return name
        inner = _render_prop_list(props, definitions, hops_left - 1, seen | {name}, max_props)
        return f"{name}{{{inner}}}" if inner else name

    if obj.get("type") == "array":
        inner = _render_shape(obj.get("items", {}), definitions, hops_left, seen, max_props)
        return f"[{inner}]" if inner else "[]"

    props = obj.get("properties", {})
    if props:
        inner = _render_prop_list(props, definitions, hops_left, seen, max_props)
        return f"{{{inner}}}" if inner else ""

    return ""


def deep_response_shape(spec: dict, path: str, method: str,
                        depth: int, max_props: int) -> str:
    """Response shape with $refs followed `depth` times.

    extract_response_schema() renders one level and stops, so a collection like
    directory-data-country-information-interface.available_regions shows up as a
    bare name — hiding the {id, code, name} rows that answer "which id is
    Georgia". This follows the refs instead. Returns "" when there is no 200/201
    schema to render.
    """
    operation = spec.get("paths", {}).get(path, {}).get(method.lower(), {})
    responses = operation.get("responses", {}) if isinstance(operation, dict) else {}
    ok = responses.get("200") or responses.get("201")
    if not isinstance(ok, dict):
        return ""
    schema = ok.get("schema")
    if not isinstance(schema, dict):
        return ""
    return _render_shape(schema, spec.get("definitions", {}), depth,
                         frozenset(), max_props)


def format_endpoint_detail(ep: EndpointInfo, definitions: Optional[dict] = None,
                           body_depth: int = 1, max_props: int = 12) -> str:
    """Render one endpoint's parameters and response shape.

    definitions=None with body_depth=1 reproduces agent/baya_json_agent/planning_agent.py
    byte-for-byte: body properties are listed one level deep and $refs are not
    followed. That default is what the copy-fidelity check asserts.

    Pass `definitions` (a spec's "definitions" dict) to follow $refs. body_depth
    counts ref hops: 1 reaches customer-data-customer-interface, 2 reaches
    customer-data-address-interface and therefore surfaces region_id. max_props
    must exceed 19 for `addresses` to survive truncation.
    """
    params = []
    for p in ep.parameters:
        if isinstance(p, dict):
            pname = p.get("name", "")
            pin = p.get("in", "")
            required = p.get("required", False)
            ptype = p.get("type", "")
            pdesc = p.get("description", "")
            enums = p.get("enum", [])

            # Body parameter with nested schema: expand properties so the caller
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
                        if definitions is not None:
                            shape = _render_shape(prop_info, definitions, body_depth,
                                                  frozenset(), max_props)
                            if shape:
                                prop_line += f"\n        shape: {shape}"
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
    # Render summary and description, each only when present. The
    # Magento-derived schemas carry their human docs in `description`
    # (`summary` is empty everywhere), while standard OpenAPI schemas use a
    # short `summary` plus a longer `description` — surfacing both covers each.
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
