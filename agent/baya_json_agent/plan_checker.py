"""
Static plan checker: validates that a generated plan satisfies all required
parameters declared in the Swagger 2.0 specification.

Runs deterministically (no LLM) after _build_plan and feeds issues into the
existing _fix_plan loop in planning_agent.py.
"""

import json
import re
from typing import Any, Dict, List, Optional

_WRAPPER_RE = re.compile(r"^(Get|Post|Put|Patch|Delete)[A-Z].*Body$")
_PURE_REF_RE = re.compile(r"^\{(?:step_\w+\.result|loop_item)")
_ANY_REF_RE = re.compile(r"\{(?:step_\w+\.result|loop_item)")


def _is_pure_reference(value: Any) -> bool:
    """True if the entire value is a single step/loop reference."""
    return isinstance(value, str) and bool(_PURE_REF_RE.match(value.strip()))


def _contains_reference(value: Any) -> bool:
    """True if value (str/dict/list) contains any step or loop_item placeholder."""
    if isinstance(value, str):
        return bool(_ANY_REF_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_reference(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_reference(v) for v in value)
    return False


class SwaggerPlanChecker:
    """
    Validates a plan against EndpointInfo objects extracted from Swagger 2.0 specs.

    Usage:
        checker = SwaggerPlanChecker()
        issues = checker.check(plan_result.plan, kept_endpoints)
        # issues is a list of strings; empty means no problems found
    """

    def check(self, plan: list, kept_endpoints: list) -> List[str]:
        """
        Check all ExecutionSteps in `plan` for missing required parameters.
        ConditionalSteps are skipped.

        Returns a list of human-readable issue strings.
        """
        endpoint_map: Dict[str, Any] = {
            f"{ep.method} {ep.path}": ep for ep in kept_endpoints
        }
        issues: List[str] = []
        for step in plan:
            if getattr(step, "step_type", "tool_call") == "conditional":
                continue
            tool_name = (
                step.tool_name.value
                if hasattr(step.tool_name, "value")
                else str(step.tool_name)
            )
            ep = endpoint_map.get(tool_name)
            if ep is None:
                continue
            issues.extend(self._check_step(step, ep))
        return issues

    # ── Per-step logic ───────────────────────────────────────────────────────

    def _check_step(self, step, ep) -> List[str]:
        method = ep.method.upper()
        args = step.arguments or []

        # Path params extracted from URL template
        path_param_names = set(re.findall(r"\{(\w+)\}", ep.path))

        # Classify args by mirroring _build_cmd routing logic exactly
        provided_path: set = set()
        provided_query: set = set()
        body_args: list = []

        for arg in args:
            name = arg.name
            pin = getattr(arg, "param_in", None)

            if name in path_param_names:
                provided_path.add(name)
            elif pin == "body":
                body_args.append(arg)
            elif pin in ("query", "formData", "header"):
                provided_query.add(name)
            elif method in ("POST", "PUT", "PATCH"):
                body_args.append(arg)
            else:
                provided_query.add(name)

        issues: List[str] = []

        # 1. Path parameters
        for pname in path_param_names:
            if pname not in provided_path:
                issues.append(
                    f"Step '{step.step_id}' ({ep.method} {ep.path}): "
                    f"required path parameter '{pname}' is missing"
                )

        # 2. Required query / formData / header params, and body schema required fields
        for p in ep.parameters:
            if not isinstance(p, dict):
                continue
            pname = p.get("name", "")
            pin = p.get("in", "")
            required = p.get("required", False)

            if pin in ("query", "formData", "header") and required:
                if pname not in provided_query:
                    issues.append(
                        f"Step '{step.step_id}' ({ep.method} {ep.path}): "
                        f"required {pin} parameter '{pname}' is missing"
                    )

            elif pin == "body":
                schema = p.get("schema", {})
                # Strip Rails [] suffix (e.g. "actions[]" → "actions") — swagger specs
                # generated from Rails docs use this notation, but JSON bodies use plain keys.
                required_fields = {
                    (f[:-2] if f.endswith("[]") else f) for f in schema.get("required", [])
                }
                if required_fields:
                    issues.extend(
                        self._check_body_fields(step, ep, required_fields, body_args)
                    )

        return issues

    # ── Body required-field check ────────────────────────────────────────────

    def _check_body_fields(
        self, step, ep, required_fields: set, body_args: list
    ) -> List[str]:
        label = f"Step '{step.step_id}' ({ep.method} {ep.path})"

        if not body_args:
            return [
                f"{label}: required body field '{f}' is missing (no body argument supplied)"
                for f in sorted(required_fields)
            ]

        # If any body arg is a pure top-level reference, we cannot inspect content
        if any(_is_pure_reference(arg.value) for arg in body_args):
            return []

        if len(body_args) == 1:
            arg = body_args[0]
            is_wrapper = arg.name == "body" or bool(_WRAPPER_RE.match(arg.name))

            if is_wrapper:
                val = arg.value
                # Try to JSON-parse if value is a string representation of a dict
                if isinstance(val, str):
                    stripped = val.strip()
                    if stripped.startswith("{") and not _ANY_REF_RE.match(stripped):
                        try:
                            parsed = json.loads(stripped)
                            if isinstance(parsed, dict):
                                val = parsed
                        except json.JSONDecodeError:
                            pass

                if isinstance(val, dict):
                    # Keys present = provided fields; values may themselves be references.
                    # Normalize [] suffix so "actions[]" and "actions" both match "actions".
                    provided = {(k[:-2] if k.endswith("[]") else k) for k in val.keys()}
                    missing = required_fields - provided
                    return [
                        f"{label}: required body field '{f}' is missing from body object"
                        for f in sorted(missing)
                    ]

                # value is a reference or unparseable string — can't check
                return []

            # Single non-wrapper body arg: arg.name IS the body field name
            provided_name = arg.name[:-2] if arg.name.endswith("[]") else arg.name
            missing = required_fields - {provided_name}
            return [
                f"{label}: required body field '{f}' is missing"
                for f in sorted(missing)
            ]

        # Multiple body args: each arg.name is a body field
        # (Pure-reference args were already handled above — none exist here)
        provided = {(a.name[:-2] if a.name.endswith("[]") else a.name) for a in body_args}
        missing = required_fields - provided
        return [
            f"{label}: required body field '{f}' is missing"
            for f in sorted(missing)
        ]
