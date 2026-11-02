#!/usr/bin/env python3
"""Assert agent/baya_code_agent/codegen/schema_lib.py still renders identically to agent/baya_json_agent/planning_agent.py.

schema_lib.py is a deliberate copy of the planner's swagger rendering (see its
module docstring). Copies rot silently, so this check renders EVERY endpoint in
EVERY registered schema through both implementations and requires the strings to
match exactly — response_schema and the full endpoint detail block.

Run it after touching either side:

    ./venv/bin/python agent/baya_code_agent/tests/schema_lib_check.py

Exit 0 = identical. Exit 1 = drift, with the first few mismatches printed.
This does not touch the network and does not need the sites to be running.
"""
import sys
from pathlib import Path

MAX_SHOWN = 3


def main() -> None:
    here = Path(__file__).resolve().parent
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(here.parent / "codegen"))
    sys.path.insert(0, str(root))

    import schema_lib
    from agent.baya_json_agent.planning_agent import PlanningAgent

    api_dir = root / "api"

    # PlanningAgent.__init__ builds a Configurator and a ModelProvider, which
    # need config/env present and are irrelevant here: none of the four methods
    # under test touch anything but self.api_dir.
    planner = PlanningAgent.__new__(PlanningAgent)
    planner.api_dir = api_dir

    index = schema_lib.load_index(api_dir)
    total = 0
    mismatches = []

    for filename in sorted(index):
        if not (api_dir / "schemas" / filename).exists():
            print(f"  {filename:36} SKIPPED (file absent)")
            continue

        spec = planner._parse_swagger(filename)
        old_eps = planner._extract_endpoints(spec, filename)
        new_eps = schema_lib.extract_endpoints(spec, filename)

        if len(old_eps) != len(new_eps):
            mismatches.append(
                f"{filename}: endpoint COUNT differs "
                f"(planner {len(old_eps)}, schema_lib {len(new_eps)})"
            )
            print(f"  {filename:36} COUNT MISMATCH")
            continue

        file_bad = 0
        for a, b in zip(old_eps, new_eps):
            total += 1
            if a.response_schema != b.response_schema:
                file_bad += 1
                mismatches.append(
                    f"{filename} {a.method} {a.path}: response_schema differs\n"
                    f"    planner   : {a.response_schema!r}\n"
                    f"    schema_lib: {b.response_schema!r}"
                )
            old_detail = planner._format_endpoint_detail(a)
            new_detail = schema_lib.format_endpoint_detail(b)
            if old_detail != new_detail:
                file_bad += 1
                mismatches.append(
                    f"{filename} {a.method} {a.path}: endpoint detail differs\n"
                    f"    planner   : {old_detail[:300]!r}\n"
                    f"    schema_lib: {new_detail[:300]!r}"
                )
        print(f"  {filename:36} {len(old_eps):>5} endpoints  "
              f"{'OK' if file_bad == 0 else f'{file_bad} MISMATCHES'}")

    print(f"\n{total} endpoints compared across {len(index)} registered schemas")

    if mismatches:
        print(f"\nFAIL: {len(mismatches)} mismatch(es). First {MAX_SHOWN}:\n")
        for m in mismatches[:MAX_SHOWN]:
            print(f"  - {m}")
        print("\nagent/baya_code_agent/codegen/schema_lib.py has drifted from agent/baya_json_agent/planning_agent.py.")
        sys.exit(1)

    print("PASS: agent/baya_code_agent/codegen/schema_lib.py renders identically to agent/baya_json_agent/planning_agent.py")


if __name__ == "__main__":
    main()
