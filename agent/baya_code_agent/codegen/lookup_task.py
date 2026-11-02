#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <site> <task_id>", file=sys.stderr)
        sys.exit(2)

    site, task_id_arg = sys.argv[1], sys.argv[2]

    # Intent override (used by the WASP integration, see
    # wasp/webarena_prompt_injections/run_claude_code_agent.py). WASP task ids
    # (1000+) and their injected/benign intents are not in PTE's verified-task
    # dataset, so the caller passes the intent through the environment instead.
    # When PTE_TASK_INTENT is set, emit the prompt block from it and skip the
    # dataset search entirely. Absent the env var, behaviour is unchanged.
    override_intent = os.environ.get("PTE_TASK_INTENT", "").strip()
    if override_intent:
        project_path = os.environ.get("PTE_TASK_PROJECT_PATH", "").strip()
        print(f"Task: {override_intent}")
        print(f"Site: {site}")
        print(f"Project path: {project_path}")
        return

    try:
        task_id = int(task_id_arg)
    except ValueError:
        print(f"Error: task_id must be an integer, got: {task_id_arg}", file=sys.stderr)
        sys.exit(2)

    root = Path(__file__).resolve().parents[3]
    test_files_dir = root / "eval" / "tests" / "test_files"

    candidates = [
        test_files_dir / f"{site}_verified_program_html.json",
        test_files_dir / f"{site}_verified_string_match.json",
    ]

    placeholder = f"__{site.upper()}__"

    for path in candidates:
        if not path.is_file():
            continue
        with open(path) as f:
            tasks = json.load(f)
        for task in tasks:
            if task.get("task_id") == task_id:
                intent = task["intent"]
                project_path = (task.get("start_url") or "").replace(placeholder, "")
                print(f"Task: {intent}")
                print(f"Site: {site}")
                print(f"Project path: {project_path}")
                return

    print(f"Error: task_id {task_id} not found for site '{site}' in:", file=sys.stderr)
    for path in candidates:
        print(f"  {path}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
