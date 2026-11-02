#!/usr/bin/env python3
"""
Compare PTE and WebArena-Verified evaluation results, with an OR merge.

Usage:
    # Run WA eval on all tasks then combine:
    python eval/compare_wa_results.py \\
        --output-dir wa_react_web_api_output \\
        --config /path/to/config.example.json

    python eval/compare_wa_results.py --output-dir wa_gitlab_claude_output --config ../webarena-verified/examples/configs/config.example.json
    
    # Run WA eval on just 2 re-run tasks, then combine with ALL existing results:
    python eval/compare_wa_results.py \\
        --output-dir wa_react_web_api_output \\
        --config /path/to/config.example.json \\
        --task-ids 105,445

    # Skip running the WA evaluator (compare existing results only):
    python eval/compare_wa_results.py --output-dir wa_react_web_api_output --skip-eval

    # Score a finished shopping_admin run (site is auto-detected from run_summary_*.json):
    python eval/compare_wa_results.py --output-dir wa_output_shopping_admin_claude \\
        --config ../webarena-verified/examples/configs/config.example.json

A task is considered PASS if EITHER:
  - PTE eval says correct=true  (string_match / program_html, from *_detailed.jsonl)
  - WA eval  says score=1.0     (NetworkEventEvaluator, from {task_id}/eval_result.json)

Multiple *_detailed.jsonl files are merged: later files (by filename sort) override earlier
ones for the same task_id, so re-running a subset picks up the fresh PTE results for those
tasks while keeping old results for everything else.

Besides combined_results.json (every task in the output dir), one {site}_only_results.json
is written per site the run covers, filtered to the task_ids that have a PTE counterpart for
that site. The site set comes from --site, or is auto-detected from the newest
run_summary_*.json (falling back to the per-task eval_result.json "sites" field).
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eval.tests.agent_test_utils import PTE_TEST_FILES, load_pte_task_ids

_SITES = tuple(PTE_TEST_FILES)


def resolve_sites(site: str | None) -> list[str] | None:
    """Turn a --site value into the site list compare_and_report() wants.

    "auto" (or None) → None, meaning "detect from the output dir".
    """
    if site is None or site == "auto":
        return None
    if site == "all":
        return list(_SITES)
    return [site]


def _detect_sites(output_dir: Path) -> list[str]:
    """Best-effort guess at which site(s) the output dir holds results for.

    1. The newest run_summary_*.json's "site" field (written by run_webarena_verified.py).
    2. The primary site of each {task_id}/eval_result.json (only present after a WA eval run).
    3. Everything, so nothing is silently skipped.
    """
    summaries = sorted(output_dir.glob("run_summary_*.json"))
    if summaries:
        try:
            site = json.loads(summaries[-1].read_text()).get("site")
            if site in PTE_TEST_FILES:
                print(f"  Site: {site} (from {summaries[-1].name})")
                return [site]
        except (json.JSONDecodeError, ValueError):
            pass

    found: set[str] = set()
    for task_dir in output_dir.iterdir():
        if not task_dir.is_dir() or not task_dir.name.isdigit():
            continue
        result_file = task_dir / "eval_result.json"
        if not result_file.exists():
            continue
        try:
            sites = json.loads(result_file.read_text()).get("sites") or []
        except (json.JSONDecodeError, ValueError):
            continue
        # A task may list a secondary site (e.g. ["gitlab", "wikipedia"]); the first is the one
        # the PTE task files are keyed by.
        if sites and sites[0] in PTE_TEST_FILES:
            found.add(sites[0])
    if found:
        sites_sorted = sorted(found)
        print(f"  Site: {', '.join(sites_sorted)} (from eval_result.json)")
        return sites_sorted

    print(f"  Site: could not detect — reporting all of {', '.join(_SITES)}")
    return list(_SITES)


def run_wa_eval(
    output_dir: Path,
    config_path: Path,
    wa_dir: Path,
    task_ids: list[int] | None,
) -> None:
    """Run the webarena-verified eval-tasks subprocess."""
    cmd = [
        "uvx", "--from", ".", "webarena-verified", "eval-tasks",
        "--output-dir", str(output_dir.resolve()),
        "--config", str(config_path),
    ]
    if task_ids:
        cmd += ["--task-ids", ",".join(str(t) for t in task_ids)]
    print(f"\nRunning WA evaluator from {wa_dir}")
    print(f"  Command: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=str(wa_dir), check=False)
    if result.returncode != 0:
        print(f"\n⚠️  WA evaluator exited with code {result.returncode} — continuing with comparison")


def _read_pte_results(output_dir: Path) -> dict[int, bool]:
    """Merge all *_detailed.jsonl files in output_dir into task_id → correct.

    Files are processed in filename order (ascending), so later filenames
    (higher timestamps) override earlier ones for the same task_id.
    """
    jsonl_files = sorted(output_dir.glob("*_detailed.jsonl"))
    if not jsonl_files:
        print(f"⚠️  No *_detailed.jsonl found in {output_dir} — PTE results will all be False")
        return {}

    pte: dict[int, bool] = {}
    for jsonl_path in jsonl_files:
        count_before = len(pte)
        for line in jsonl_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                pte[int(row["task_id"])] = bool(row.get("correct", False))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        added = len(pte) - count_before
        print(f"  PTE: {jsonl_path.name}  (+{added} tasks, {len(pte)} total)")

    return pte


def _read_wa_results(output_dir: Path) -> dict[int, bool]:
    """Read task_id → passed from per-task eval_result.json files."""
    wa: dict[int, bool] = {}
    for task_dir in sorted(output_dir.iterdir()):
        if not task_dir.is_dir() or not task_dir.name.isdigit():
            continue
        result_file = task_dir / "eval_result.json"
        if not result_file.exists():
            continue
        try:
            data = json.loads(result_file.read_text())
            wa[int(task_dir.name)] = (data.get("score", 0.0) == 1.0)
        except (json.JSONDecodeError, ValueError):
            continue
    return wa


def _write_subset_results(
    output_dir: Path,
    all_rows: list[dict],
    run_ids: set[int],
    pte_ids: set[int],
    label: str,
    out_filename: str,
    description: str,
) -> None:
    """Filter all_rows to pte_ids, write a JSON results file, and print a summary."""
    subset_rows = [r for r in all_rows if r["task_id"] in pte_ids]
    for missing_id in sorted(pte_ids - run_ids):
        subset_rows.append({"task_id": missing_id, "pte": False, "wa": False, "combined": False})
    subset_rows.sort(key=lambda r: r["task_id"])

    total = len(subset_rows)
    pte_n  = sum(1 for r in subset_rows if r["pte"])
    wa_n   = sum(1 for r in subset_rows if r["wa"])
    comb_n = sum(1 for r in subset_rows if r["combined"])

    def _pct(n: int) -> str:
        return f"{100 * n / total:.0f}%" if total else "n/a"

    out_file = output_dir / out_filename
    out_file.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": description,
        "summary": {
            "total": total,
            "run": len(run_ids & pte_ids),
            "not_run": len(pte_ids - run_ids),
            "pte_passed": pte_n,
            "wa_passed": wa_n,
            "combined_passed": comb_n,
        },
        "results": subset_rows,
    }, indent=2))
    print(f"  Written: {out_file}")

    print(f"\n{'='*52}")
    print(f"{label}  ({total} PTE tasks)")
    print(f"{'='*52}")
    print(f"  Total PTE tasks : {total}")
    print(f"  Run             : {len(run_ids & pte_ids)}")
    print(f"  Not run yet     : {len(pte_ids - run_ids)}")
    print(f"  PTE passed      : {pte_n} / {total}  ({_pct(pte_n)})")
    print(f"  WA  passed      : {wa_n} / {total}  ({_pct(wa_n)})")
    print(f"  Combined PASS   : {comb_n} / {total}  ({_pct(comb_n)})  ← OR of both evals")
    print()


def _print_table(rows: list[dict]) -> None:
    """Print a formatted table of per-task results."""
    col_w = {"task_id": 8, "pte": 6, "wa": 6, "combined": 8}
    header = (
        f"{'task_id':<{col_w['task_id']}}  {'pte':<{col_w['pte']}}  "
        f"{'wa':<{col_w['wa']}}  {'combined':<{col_w['combined']}}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        pte_s = "PASS" if row["pte"] else "FAIL"
        wa_s = "PASS" if row["wa"] else "FAIL"
        combined_s = "PASS" if row["combined"] else "FAIL"
        print(
            f"{row['task_id']:<{col_w['task_id']}}  {pte_s:<{col_w['pte']}}  "
            f"{wa_s:<{col_w['wa']}}  {combined_s:<{col_w['combined']}}"
        )


def compare_and_report(output_dir: Path, sites: list[str] | None = None) -> dict:
    """Merge PTE + WA results already on disk, print them, and write the report files.

    Writes combined_results.json (all tasks in the dir) plus one {site}_only_results.json
    per site in `sites` (auto-detected from the output dir when None). Returns the
    combined_results.json payload.
    """
    # --- Load results ---
    print()
    pte = _read_pte_results(output_dir)
    wa  = _read_wa_results(output_dir)
    print(f"  WA:  {len(wa)} tasks have eval_result.json")
    if sites is None:
        sites = _detect_sites(output_dir)
    print()

    if not pte and not wa:
        print("ERROR: No results found in output directory")
        sys.exit(1)

    # --- Merge ---
    all_ids = sorted(set(pte) | set(wa))
    rows = []
    for task_id in all_ids:
        pte_pass = pte.get(task_id, False)
        wa_pass  = wa.get(task_id, False)
        rows.append({
            "task_id":  task_id,
            "pte":      pte_pass,
            "wa":       wa_pass,
            "combined": pte_pass or wa_pass,
        })

    # --- Print table ---
    print(f"{'='*52}")
    print("Combined Evaluation Results  (PASS = either eval passes)")
    print(f"{'='*52}")
    _print_table(rows)

    # --- Summary ---
    total      = len(rows)
    pte_n      = sum(1 for r in rows if r["pte"])
    wa_n       = sum(1 for r in rows if r["wa"])
    combined_n = sum(1 for r in rows if r["combined"])

    def _pct(n: int) -> str:
        return f"{100 * n / total:.0f}%" if total else "n/a"

    print(f"\n{'='*52}")
    print("Summary")
    print(f"{'='*52}")
    print(f"  Total tasks    : {total}")
    print(f"  PTE passed     : {pte_n} / {total}  ({_pct(pte_n)})")
    print(f"  WA  passed     : {wa_n} / {total}  ({_pct(wa_n)})")
    print(f"  Combined PASS  : {combined_n} / {total}  ({_pct(combined_n)})  ← OR of both evals")
    print()

    # --- Write combined_results.json ---
    out_file = output_dir / "combined_results.json"
    combined_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": total,
            "pte_passed": pte_n,
            "wa_passed": wa_n,
            "combined_passed": combined_n,
        },
        "results": rows,
    }
    out_file.write_text(json.dumps(combined_data, indent=2))
    print(f"  Written: {out_file}")

    run_ids = {r["task_id"] for r in rows}

    for site in sites:
        pte_ids = load_pte_task_ids(site)
        _write_subset_results(
            output_dir, rows, run_ids,
            pte_ids=pte_ids,
            label=f"{site}-only Summary",
            out_filename=f"{site}_only_results.json",
            description=f"Results filtered to the {len(pte_ids)} {site} tasks with PTE counterparts",
        )

    return combined_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Run WA eval and merge with PTE results")
    parser.add_argument(
        "--output-dir", default="wa_react_web_api_output",
        help="Directory with agent run outputs (default: wa_react_web_api_output)",
    )
    parser.add_argument(
        "--site", default="auto", choices=(*_SITES, "all", "auto"),
        help="Which site's {site}_only_results.json to write. 'auto' (default) detects it from "
             "the newest run_summary_*.json; 'all' writes one report per known site.",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to webarena-verified config JSON (required unless --skip-eval)",
    )
    parser.add_argument(
        "--wa-dir", default=None,
        help="Path to webarena-verified source dir (default: <project-root>/../webarena-verified)",
    )
    parser.add_argument(
        "--task-ids", default=None,
        help="Comma-separated task IDs to (re-)evaluate with WA evaluator. "
             "PTE results are still merged from ALL jsonl files regardless.",
    )
    parser.add_argument(
        "--skip-eval", action="store_true",
        help="Skip running the WA evaluator; just compare existing results",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = _PROJECT_ROOT / output_dir
    if not output_dir.exists():
        print(f"ERROR: output directory not found: {output_dir}")
        sys.exit(1)

    task_ids: list[int] | None = None
    if args.task_ids:
        try:
            task_ids = [int(t.strip()) for t in args.task_ids.split(",") if t.strip()]
        except ValueError:
            print(f"ERROR: invalid --task-ids value: {args.task_ids}")
            sys.exit(1)

    wa_dir = Path(args.wa_dir) if args.wa_dir else (_PROJECT_ROOT.parent / "webarena-verified")

    if not args.skip_eval:
        if not args.config:
            parser.error("--config is required unless --skip-eval is set")
        config_path = Path(args.config)
        if not config_path.is_absolute():
            config_path = wa_dir / config_path
        if not config_path.exists():
            print(f"ERROR: config file not found: {config_path}")
            sys.exit(1)
        if not wa_dir.exists():
            print(f"ERROR: webarena-verified directory not found: {wa_dir}")
            sys.exit(1)
        run_wa_eval(output_dir, config_path, wa_dir, task_ids)

    compare_and_report(output_dir, resolve_sites(args.site))


if __name__ == "__main__":
    main()
