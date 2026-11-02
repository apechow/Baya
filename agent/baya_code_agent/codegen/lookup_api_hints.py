#!/usr/bin/env python3
"""Resolve the site-specific API hints string for a given site.

Looks up api/api_guidance.json to map the site's schema filename to a hints
variable name, then pulls that variable's value out of api/api_server_prompts.py.
"""
import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <site>", file=sys.stderr)
        sys.exit(2)

    site = sys.argv[1]

    root = Path(__file__).resolve().parents[3]
    hints_map_path = root / "api" / "api_guidance.json"

    with open(hints_map_path) as f:
        hints_map = json.load(f)

    schema_name = f"{site}_api_schema.json"
    hint_var = hints_map.get(schema_name)
    if hint_var is None:
        print(f"Error: no API hints mapping for schema '{schema_name}' in {hints_map_path}", file=sys.stderr)
        sys.exit(1)

    sys.path.insert(0, str(root))
    from api import api_server_prompts

    hint_text = getattr(api_server_prompts, hint_var, None)
    if hint_text is None:
        print(f"Error: {hint_var} not found in api/api_server_prompts.py", file=sys.stderr)
        sys.exit(1)

    print(hint_text)


if __name__ == "__main__":
    main()
