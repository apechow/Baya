# tracks which steps are ready, completed, failed, and manages dependencies between steps
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Sequence, Set, Tuple, Union

from pydantic import BaseModel, Field, GetJsonSchemaHandler, field_validator, model_validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema as _core_schema


class _AnyValue:
    """Runtime-accepts any JSON value (str/int/float/bool/list/dict/None).
    Presents a primitive-only schema so Gemini doesn't see additionalProperties."""

    @classmethod
    def __get_pydantic_core_schema__(cls, _source, _handler):
        return _core_schema.no_info_plain_validator_function(lambda v: v)

    @classmethod
    def __get_pydantic_json_schema__(cls, _cs, _handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        return {"anyOf": [{"type": "string"}, {"type": "integer"},
                          {"type": "number"}, {"type": "boolean"}]}


# =======================
# Small internal utilities
# =======================
def _tool_name_str(tn: Any) -> str:
    """Return a printable tool name from Enum or str."""
    return tn.value if isinstance(tn, Enum) else str(tn)

def _abbr(v: Any, max_len: int = 50) -> str:
    s = str(v)
    return s if len(s) <= max_len else s[:max_len] + "…"


# =======================
# Conditional step (no HTTP call — evaluates a condition and stores a string result)
# =======================
class ConditionalStep(BaseModel):
    step_type: Literal["conditional"] = "conditional"
    step_id: str = Field(description="Unique identifier for this step")
    condition: str = Field(
        description=(
            "Equality expression to evaluate, using {step_id.result} references. "
            "Format: '{left} == {right}'. References are resolved before comparison. "
            "Example: '{step_3.result[0].author.username} == {step_2.result[0].author.username}'"
        )
    )
    if_true: str = Field(
        description="String value to store as this step's result when condition is true. May contain {step_id.result} references."
    )
    if_false: str = Field(
        description="String value to store as this step's result when condition is false. May contain {step_id.result} references."
    )
    depends_on: List[str] = Field(
        default_factory=list,
        description="List of step_ids that must complete before this step.",
    )
    hints: str = Field(default="", description="Optional human-readable notes.")
    # Stubs so generic plan-walking code (pretty_print, etc.) doesn't need special-casing
    tool_name: None = None
    arguments: List[str] = Field(default_factory=list)
    foreach: None = None
    base_url: str = ""
    returns: str = ""


# =======================
# Public bundle container
# =======================
@dataclass(frozen=True)
class AgentModelBundle:
    """A convenience container for runtime-generated types."""
    ToolBasedResponse: type[BaseModel]


# =======================
# Model factory (Option 1)
# =======================
def build_agent_models(allowed_tools: Sequence[str]) -> AgentModelBundle:
    """
    Build a runtime-constrained ToolEnum and the associated Pydantic models.

    Parameters
    ----------
    allowed_tools : Sequence[str]
        The exact set of tool names that are valid at runtime.

    Returns
    -------
    AgentModelBundle
        A bundle containing the ToolBasedResponse model for the given tool set.
    """
    if not allowed_tools:
        raise ValueError("allowed_tools must be a non-empty sequence")

    # Preserve order and uniqueness
    uniq = list(dict.fromkeys(allowed_tools))
    ToolEnum = Enum("ToolEnum", {name: name for name in uniq})

    class Argument(BaseModel):
        name: str = Field(description="Name of the argument parameter")
        value: _AnyValue = Field(
            description=(
                "Value of the argument. Literals can be any JSON type. "
                "References must be a string placeholder like '{step_1.result}' or '{loop_item}'."
            )
        )
        value_type: Literal["literal", "reference"] = Field(
            default="literal",
            description="Type of value: 'literal' for direct values, 'reference' for dependency references",
        )
        param_in: Optional[Literal["path", "query", "body", "formData", "header"]] = Field(
            default=None,
            description=(
                "Where this argument is sent in the HTTP request, taken from the swagger 'in' field. "
                "'body' means the value is the entire request body. "
                "'path' means substituted into the URL. "
                "'query' means appended as a query parameter. "
                "Always set this from the endpoint's parameter definition."
            ),
        )

        @model_validator(mode="before")
        @classmethod
        def normalize_reference(cls, data: Any) -> Any:
            if not isinstance(data, dict):
                return data
            if data.get("value_type") != "reference":
                return data
            v = data.get("value", "")
            if not isinstance(v, str):
                # LLM sometimes emits an int step index instead of a reference
                # string (e.g. 2 instead of "{step_2.result}"). Coerce it rather
                # than hard-failing so the model gets a retry with corrected data.
                if isinstance(v, int):
                    data["value"] = "{step_" + str(v) + ".result}"
                    v = data["value"]
                else:
                    data["value"] = str(v)
                    v = data["value"]
            # Auto-wrap bare references that are missing braces:
            # "step_1.result[0].id" → "{step_1.result[0].id}"
            if not v.startswith("{") and re.match(r"\w+\.result", v):
                data["value"] = "{" + v + "}"
            return data

        @model_validator(mode="after")
        def no_embedded_conditionals(self) -> "Argument":
            if not isinstance(self.value, str):
                return self
            v = self.value
            # Bare step_N.result references without braces indicate the LLM forgot
            # to wrap them — or worse, embedded a conditional expression as a string.
            if re.search(r'\bstep_\w+\.result\b', v) and '{' not in v:
                raise ValueError(
                    f"Argument '{self.name}': value contains bare step references without "
                    f"braces (e.g. 'step_3.result.field' must be '{{step_3.result.field}}'). "
                    f"If branching logic is needed, use a step with step_type='conditional' "
                    f"and reference its output with '{{step_N.result}}'."
                )
            # Detect if/then/else conditional logic embedded as a literal string.
            if re.search(r'\bthen\b.+\belse\b', v, re.IGNORECASE) or \
               re.search(r'\bif\b.+\bthen\b', v, re.IGNORECASE):
                raise ValueError(
                    f"Argument '{self.name}': value contains conditional logic as a literal "
                    f"string ('{v[:80]}...'). Add a step with step_type='conditional' before "
                    f"this step to evaluate the condition, then reference its output here."
                )
            return self

        @model_validator(mode="after")
        def validate_accessor_syntax(self) -> "Argument":
            if not isinstance(self.value, str):
                return self
            # Mask inner {step.result} refs so filter values like =={step_1.result.id}
            # don't interfere with token parsing of the outer accessor chain.
            masked = re.sub(r'\{[^{}]*\}', '__REF__', self.value)
            for m in re.finditer(r'\{(\w+)\.result([^{}]*)\}', masked):
                accessor = m.group(2)
                if not accessor:
                    continue
                pos = 0
                while pos < len(accessor):
                    if re.match(r'\.\w+', accessor[pos:]):
                        pos += re.match(r'\.\w+', accessor[pos:]).end()
                    elif accessor[pos:pos + 3] == '[*]':
                        pos += 3
                    elif re.match(r'\[\?\(@\.\w+(?:\.\w+)*\s*(?:==|\*=)\s*[^\]]*\)\]', accessor[pos:]):
                        pos += re.match(r'\[\?\(@\.\w+(?:\.\w+)*\s*(?:==|\*=)\s*[^\]]*\)\]', accessor[pos:]).end()
                    elif re.match(r'\[sort_desc:\w+\]', accessor[pos:]):
                        pos += re.match(r'\[sort_desc:\w+\]', accessor[pos:]).end()
                    elif re.match(r'\[:\d+\]', accessor[pos:]):
                        pos += re.match(r'\[:\d+\]', accessor[pos:]).end()
                    elif re.match(r'\[\d+\]', accessor[pos:]):
                        pos += re.match(r'\[\d+\]', accessor[pos:]).end()
                    else:
                        bad = accessor[pos:pos + 40]
                        raise ValueError(
                            f"Argument '{self.name}': unrecognized accessor token '{bad}' in "
                            f"'{accessor}'. Supported tokens: .key  [*]  [?(@.field==value)]  [?(@.field*=value)]  "
                            f"[n]  [sort_desc:f]  [:N]. "
                            f"For filtering use [?(@.field==value)] or [?(@.field*=value)], not [field==value] or JSONPath variants."
                        )
            return self

    class ExecutionStep(BaseModel):
        step_type: Literal["tool_call"] = Field(default="tool_call", description="Step type discriminator")
        step_id: str = Field(description="Unique identifier for this step")
        tool_name: ToolEnum = Field(description="Name of the tool to execute (one of the allowed tools)")
        arguments: List[Argument] = Field(
            default_factory=list,
            description="List of arguments with name-value mappings",
        )
        depends_on: List[str] = Field(
            default_factory=list,
            description="List of step_ids that must complete before this step. Empty if no dependencies.",
        )
        hints: str = Field(
            default="",
            description="Instructions for how to use outputs from dependent steps to fill arguments during replanning",
        )
        returns: str = Field(
            default="",
            description="Expected output/return schema of this step (e.g. field names available in {step_id.result})",
        )
        base_url: str = Field(
            default="",
            description="Base URL of the API server for this step (e.g. http://127.0.0.1:8023/api/v4)",
        )
        foreach: Optional[Union[str, List[Union[str, int, float, bool]]]] = Field(
            default=None,
            description=(
                "If set, run this step once per element and collect all results as a list. "
                "Value is a literal list (['Alice', 'Bob']) or a reference like 'step_1.result[*].id'. "
                "Use {loop_item} in argument values as a placeholder for the current element."
            ),
        )

        @model_validator(mode="after")
        def foreach_required_when_loop_item_used(self) -> "ExecutionStep":
            uses_loop_item = any(
                "{loop_item" in str(arg.value)
                for arg in (self.arguments or [])
            )
            if uses_loop_item and self.foreach is None:
                raise ValueError(
                    f"Step '{self.step_id}' uses {{loop_item}} in arguments but 'foreach' is not set. "
                    "Set foreach to the source of iteration "
                    "(e.g. 'step_1.result[*].id' or a literal list)."
                )
            return self

    class ToolBasedResponse(BaseModel):
        plan: List[Annotated[Union[ConditionalStep, ExecutionStep], Field(discriminator="step_type")]] = Field(
            min_length=1,
            description="Execution plan with at least one step",
        )

        @field_validator("plan", mode="before")
        @classmethod
        def _default_step_type(cls, v):
            # A discriminated union requires 'step_type' to be present in the input;
            # Pydantic does not consult the field default. Weaker models (e.g. DeepSeek)
            # omit the defaulted field, so inject it here to match the "tool_call unless
            # marked conditional" contract used everywhere via getattr(step, "step_type", ...).
            if isinstance(v, list):
                for s in v:
                    if isinstance(s, dict) and "step_type" not in s:
                        s["step_type"] = (
                            "conditional"
                            if ("condition" in s or "if_true" in s or "if_false" in s)
                            else "tool_call"
                        )
            return v

    return AgentModelBundle(
        ToolBasedResponse=ToolBasedResponse,
    )


# ==========================================
# Plan validation, readiness, and pretty-prints
# ==========================================
def explain_plan_errors(plan: List["BaseModel"]) -> Tuple[bool, Optional[str]]:
    """
    Validate the plan and return (ok, error_message).
    error_message is None if ok is True.
    """
    if plan is None:
        return False, "Plan is None"
    if not isinstance(plan, list):
        return False, "Plan must be a list"
    if len(plan) == 0:
        return True, None  # empty plan is allowed

    # Collect IDs and basic checks
    step_ids: List[str] = []
    for idx, step in enumerate(plan):
        sid = getattr(step, "step_id", None)
        if not sid:
            return False, f"Step at index {idx} is missing step_id"
        if getattr(step, "step_type", "tool_call") != "conditional" and not getattr(step, "tool_name", None):
            return False, f"Step '{sid}' missing tool_name"
        step_ids.append(sid)

    # Duplicate step IDs
    seen: Set[str] = set()
    for sid in step_ids:
        if sid in seen:
            return False, f"Duplicate step_id detected: '{sid}'"
        seen.add(sid)

    # Dependency references
    id_set = set(step_ids)
    for step in plan:
        sid = step.step_id
        for dep in getattr(step, "depends_on", []) or []:
            if dep not in id_set:
                return False, f"Step '{sid}' depends on unknown step_id '{dep}'"
            if dep == sid:
                return False, f"Step '{sid}' cannot depend on itself"

    # Cycle detection via DFS
    from collections import defaultdict
    graph = defaultdict(list)
    for step in plan:
        for dep in getattr(step, "depends_on", []) or []:
            graph[dep].append(step.step_id)

    visited: Set[str] = set()
    rec_stack: Set[str] = set()

    def has_cycle(node: str) -> bool:
        if node in rec_stack:
            return True
        if node in visited:
            return False
        visited.add(node)
        rec_stack.add(node)
        for nei in graph.get(node, []):
            if has_cycle(nei):
                return True
        rec_stack.remove(node)
        return False

    for sid in step_ids:
        if sid not in visited and has_cycle(sid):
            return False, "Cycle detected in plan dependencies"

    return True, None


def validate_plan(plan: List["BaseModel"]) -> bool:
    """Boolean wrapper around explain_plan_errors."""
    ok, _ = explain_plan_errors(plan)
    return ok


def get_ready_steps(
    plan: List["BaseModel"], 
    completed_steps: Set[str], 
    executing_steps: Set[str]
) -> List[str]:
    """
    Return step_ids that are ready to execute (all deps completed, not done/executing).
    """
    ready = []
    for step in plan:
        sid = step.step_id
        if sid in completed_steps or sid in executing_steps:
            continue
        deps = getattr(step, "depends_on", []) or []
        if all(dep in completed_steps for dep in deps):
            ready.append(sid)
    return ready


def pretty_print_plan(
    plan: List["BaseModel"],
    *,
    show_hints: bool = True,
    max_value_len: int = 50,
    header: bool = True
) -> str:
    """Human-friendly rendering of a plan; supports Enum tool_name."""
    if not plan:
        return "No execution steps in plan"

    lines: List[str] = []
    if header:
        lines.append("\n" + "=" * 60)
        lines.append("EXECUTION PLAN")
        lines.append("=" * 60)

    for i, step in enumerate(plan, 1):
        lines.append(f"\nStep {i}: {step.step_id}")
        if getattr(step, "step_type", "tool_call") == "conditional":
            lines.append(f"  Type: conditional")
            lines.append(f"  Condition : {step.condition}")
            lines.append(f"  If true   : {step.if_true}")
            lines.append(f"  If false  : {step.if_false}")
        else:
            tn = _tool_name_str(getattr(step, "tool_name", ""))
            lines.append(f"  Tool: {tn}")

        # Dependencies (all step types)
        deps = getattr(step, "depends_on", []) or []
        if deps:
            lines.append("  Depends on: " + ", ".join(deps))
        else:
            lines.append("  Depends on: None (can execute immediately)")

        if getattr(step, "step_type", "tool_call") != "conditional":
            # Arguments
            args = getattr(step, "arguments", []) or []
            if args:
                lines.append("  Arguments:")
                for arg in args:
                    name = getattr(arg, "name", "<unnamed>")
                    v = getattr(arg, "value", None)
                    vt = getattr(arg, "value_type", "literal")
                    lines.append(f"    - {name}: {_abbr(v, max_value_len)} ({vt})")
            else:
                lines.append("  Arguments: None")

            # Foreach
            foreach_val = getattr(step, "foreach", None)
            if foreach_val is not None:
                lines.append(f"  Foreach: {foreach_val}")

            # Returns
            returns = getattr(step, "returns", "")
            if returns:
                lines.append(f"  Returns: {returns}")

        # Hints
        hints = getattr(step, "hints", "")
        if show_hints and hints:
            lines.append(f"  Hints: {hints}")

    if header:
        lines.append("=" * 60 + "\n")

    return "\n".join(lines)


def pretty_print_layers(plan: List["BaseModel"]) -> str:
    """
    Show parallelizable 'layers' using Kahn's algorithm (topological levels).
    """
    if not plan:
        return "No execution steps in plan"

    from collections import defaultdict, deque

    indeg = {s.step_id: 0 for s in plan}
    adj = defaultdict(list)
    by_id = {s.step_id: s for s in plan}

    for s in plan:
        for d in getattr(s, "depends_on", []) or []:
            adj[d].append(s.step_id)
            indeg[s.step_id] += 1

    q = deque([sid for sid, d in indeg.items() if d == 0])
    layers: List[List[str]] = []
    while q:
        layer = list(q)
        layers.append(layer)
        for _ in range(len(layer)):
            u = q.popleft()
            for v in adj[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    q.append(v)

    out = ["\nExecution Layers (parallelizable groups):"]
    for i, layer in enumerate(layers, 1):
        out.append(f"\nLayer {i}:")
        for sid in layer:
            tn = _tool_name_str(getattr(by_id[sid], "tool_name", ""))
            out.append(f"  - {sid}  [{tn}]")
    return "\n".join(out)


def pretty_print_execution(
    plan: List["BaseModel"],
    answer: str,
    *,
    header: bool = True,
) -> str:
    """
    Human-friendly rendering of execution results.

    Args:
        plan:   List of ExecutionStep objects (used to show which steps ran).
        answer: LLM-generated answer from ExecutionResult.answer.
    """
    if not plan:
        return "No execution steps"

    lines: List[str] = []
    if header:
        lines.append("\n" + "=" * 60)
        lines.append("EXECUTION RESULTS")
        lines.append("=" * 60)

    lines.append(f"\nSteps executed: {', '.join(s.step_id for s in plan)}")
    lines.append(f"\nAnswer:\n  {answer}")

    if header:
        lines.append("=" * 60 + "\n")

    return "\n".join(lines)


# =======================
# ExecutionContext (Enum-safe)
# =======================
class ExecutionContext:
    """
    Encapsulates execution state tracking for a plan.
    Manages completed steps, executing steps, outputs, and summaries.
    """

    def __init__(self, plan: List["BaseModel"]):
        self.plan = plan
        self.completed_steps: Set[str] = set()
        self.executing_steps: Set[str] = set()
        self.failed_steps: Set[str] = set()
        self.step_outputs: Dict[str, Any] = {}
        self.tool_summaries: List[str] = []

    def mark_executing(self, step_id: str) -> None:
        self.executing_steps.add(step_id)

    def mark_completed(self, step_id: str, output: Any = None) -> None:
        self.executing_steps.discard(step_id)
        self.completed_steps.add(step_id)
        if output is not None:
            self.step_outputs[step_id] = output

    def mark_failed(self, step_id: str, error: str) -> None:
        self.executing_steps.discard(step_id)
        self.completed_steps.add(step_id)   # treat as done so dependents can unblock
        self.failed_steps.add(step_id)
        self.step_outputs[step_id] = f"Error: {error}"

    def add_summary(self, summary: str) -> None:
        self.tool_summaries.append(summary)

    def get_ready_steps(self) -> List[str]:
        ready = []
        for step in self.plan:
            sid = step.step_id
            if sid in self.completed_steps or sid in self.executing_steps:
                continue
            deps = getattr(step, "depends_on", []) or []
            if all(dep in self.completed_steps for dep in deps):
                ready.append(sid)
        return ready

    def get_step_output(self, step_id: str) -> Any:
        return self.step_outputs.get(step_id)

    def is_complete(self) -> bool:
        return len(self.completed_steps) == len(self.plan)

    def get_progress(self) -> str:
        total = len(self.plan)
        completed = len(self.completed_steps)
        failed = len(self.failed_steps)
        executing = len(self.executing_steps)
        return f"{completed}/{total} completed ({failed} failed), {executing} executing"