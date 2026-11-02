#!/usr/bin/env python3
"""Print the complete endpoint catalog for one site.

The output is injected into the cached codegen system prompt by
agent/baya_code_agent/codegen/build_site_prompt.sh, so it MUST be byte-identical for a given site on
every invocation — see that script's header comment on prompt-cache prefixes.
Ordering is fully specified in api/schema_catalog.py; nothing here consults a
clock, a file mtime, or the iteration order of a dict or set.

Uses plain json rather than prance (0.02s vs 2.7s on the gitlab schema): the
catalog only needs path and method keys and never dereferences a $ref.
"""
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <site>", file=sys.stderr)
        sys.exit(2)

    site = sys.argv[1]

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    from schema_lib import catalog_lines, resolve_site_schema_files

    api_dir = Path(__file__).resolve().parents[3] / "api"

    try:
        files = resolve_site_schema_files(site, api_dir)
    except (ValueError, OSError) as exc:
        print(f"Error: cannot resolve schemas for site '{site}': {exc}", file=sys.stderr)
        sys.exit(1)

    blocks = []
    for filename in files:
        try:
            lines = catalog_lines(filename, api_dir)
        except (OSError, ValueError) as exc:
            print(f"Error: cannot read {filename}: {exc}", file=sys.stderr)
            sys.exit(1)
        if not lines:
            print(f"Error: {filename} defines no endpoints", file=sys.stderr)
            sys.exit(1)
        blocks.append(f"[{filename}]\n" + "\n".join(lines))

    print("\n\n".join(blocks))


if __name__ == "__main__":
    main()
