#!/usr/bin/env python3
"""Look up API endpoints for a site without reading the raw schema file.

The schema files are 1.3MB+; this prints just what you asked about — an
endpoint's description, parameters, request body shape (nested) and response
shape.

    ./venv/bin/python agent/baya_code_agent/codegen/query_schema.py shopping --search region
    ./venv/bin/python agent/baya_code_agent/codegen/query_schema.py shopping --endpoint "GET /V1/directory/countries/{countryId}"
    ./venv/bin/python agent/baya_code_agent/codegen/query_schema.py gitlab --list

Exit codes: 0 results printed, 1 no match, 2 usage error.
"""
import argparse
import sys
from pathlib import Path

# Deep enough to reach nested identifier fields. One ref hop lands on the
# top-level entity (e.g. customer-data-customer-interface); two reach the
# collections hanging off it (customer.addresses[] -> region_id), which is the
# level real request bodies are actually built at.
DEFAULT_DEPTH = 2
MAX_DEPTH = 4

# The planner renders at 12, which truncates `addresses` out of
# customer-data-customer-interface (it sits at index 19 of 23). Anything that
# hides a field is worse than useless here, since a hidden field is exactly what
# gets guessed instead of looked up.
MAX_PROPS = 60

DEFAULT_LIMIT = 20

_METHOD_RANK = {m: i for i, m in enumerate(
    ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
)}


def _load(site, api_dir, schema_lib):
    """Return [(filename, spec, [EndpointInfo, ...]), ...] in main-then-extra order."""
    loaded = []
    for filename in schema_lib.resolve_site_schema_files(site, api_dir):
        spec = schema_lib.parse_swagger(filename, api_dir)
        loaded.append((filename, spec, schema_lib.extract_endpoints(spec, filename)))
    return loaded


def _emit(filename, ep, spec, depth, schema_lib):
    print(f"[{filename}]")
    print(schema_lib.format_endpoint_detail(
        ep,
        definitions=spec.get("definitions", {}),
        body_depth=depth,
        max_props=MAX_PROPS,
    ))
    # The one-level "Returns:" line above renders nested collections as bare
    # definition names. Print the followed-through shape when it adds anything.
    deep = schema_lib.deep_response_shape(spec, ep.path, ep.method, depth, MAX_PROPS)
    if deep and deep != ep.response_schema:
        print(f"Returns (expanded): {deep}")
    print()


def _rank(term, ep):
    """Lower is better; None means no match. Ordering is fully deterministic:
    where the term matched first, then method order, then path."""
    where = None
    if term in ep.path.lower():
        where = 0
    elif term in (ep.summary or "").lower():
        where = 1
    elif term in (ep.description or "").lower():
        where = 2
    elif any(term in str(p.get("name", "")).lower()
             for p in ep.parameters if isinstance(p, dict)):
        where = 3
    if where is None:
        return None
    return (where, _METHOD_RANK.get(ep.method, 99), ep.path)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="query_schema.py",
        description="Look up API endpoints for a site from its swagger schema.",
    )
    parser.add_argument("site", help="site name, e.g. shopping or gitlab")
    # Long flags only: the codegen sandbox denies Bash(* -c *), and short flags
    # invite collisions with that pattern.
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--search", metavar="TERM",
                      help="show every endpoint matching TERM")
    mode.add_argument("--endpoint", metavar='"METHOD /path"',
                      help="show one endpoint in full")
    mode.add_argument("--list", action="store_true",
                      help="list every path with its methods")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"max --search results to print (default {DEFAULT_LIMIT})")
    parser.add_argument("--depth", type=int, default=DEFAULT_DEPTH,
                        help=f"request-body nesting depth, 1-{MAX_DEPTH} "
                             f"(default {DEFAULT_DEPTH})")
    args = parser.parse_args()

    depth = max(1, min(MAX_DEPTH, args.depth))

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    import schema_lib

    api_dir = Path(__file__).resolve().parents[3] / "api"

    try:
        files = schema_lib.resolve_site_schema_files(args.site, api_dir)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    # --list needs no $ref resolution, so skip the slow prance parse entirely.
    if args.list:
        for filename in files:
            print(f"[{filename}]")
            for line in schema_lib.catalog_lines(filename, api_dir):
                print(line)
            print()
        sys.exit(0)

    loaded = _load(args.site, api_dir, schema_lib)

    if args.endpoint:
        raw = args.endpoint.strip()
        parts = raw.split(None, 1)
        if len(parts) != 2:
            print('Error: --endpoint takes "METHOD /path", '
                  'e.g. "GET /V1/directory/countries/{countryId}"', file=sys.stderr)
            sys.exit(2)
        want_method, want_path = parts[0].upper(), parts[1].strip()

        for filename, spec, eps in loaded:
            for ep in eps:
                if ep.method == want_method and ep.path == want_path:
                    _emit(filename, ep, spec, depth, schema_lib)
                    sys.exit(0)

        # Path exists but not with that method — say which methods it does take,
        # rather than implying the path is absent.
        available = [
            (filename, ep.method)
            for filename, _spec, eps in loaded
            for ep in eps
            if ep.path == want_path
        ]
        if available:
            methods = ", ".join(sorted({m for _f, m in available},
                                       key=lambda m: _METHOD_RANK.get(m, 99)))
            print(f"Error: {want_path} exists but not for {want_method}. "
                  f"Available: {methods}", file=sys.stderr)
        else:
            print(f"Error: no endpoint {want_method} {want_path} in "
                  f"{args.site} schemas. Try --search, or --list to see "
                  f"every path.", file=sys.stderr)
        sys.exit(1)

    term = args.search.lower()
    hits = []
    for filename, spec, eps in loaded:
        for ep in eps:
            rank = _rank(term, ep)
            if rank is not None:
                hits.append((rank, filename, ep, spec))
    if not hits:
        print(f"Error: nothing matching {args.search!r} in {args.site} schemas.",
              file=sys.stderr)
        sys.exit(1)

    hits.sort(key=lambda h: h[0])
    for _rank_key, filename, ep, spec in hits[:args.limit]:
        _emit(filename, ep, spec, depth, schema_lib)

    if len(hits) > args.limit:
        print(f"... {len(hits) - args.limit} more match(es); narrow the term "
              f"or use --endpoint.", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
