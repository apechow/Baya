"""
Resolve which swagger schema files (from api/schemas/index.json) apply to a
given list of site names.

A schema filename belongs to a site if it starts with "{site}_api" (the
site's main schema) or "{site}_extra_api" (that site's extra/companion
schema). This keeps agent/baya_json_agent/planning_agent.py and
react_agent/codeact_agent/codeact_agent.py resolving schemas the same way.
"""

from typing import Dict, List


def resolve_schema_files(sites: List[str], index: Dict[str, str]) -> List[str]:
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
