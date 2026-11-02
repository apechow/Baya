#!/usr/bin/env python3
"""
run_url_match_eval.py

Score the `url_match` tasks whose generated implementation scripts live under
generated/<site>/ (default site: gitlab; pass --site shopping or
--site shopping_admin for the other supported sites).

Each generated task (generated/<site>/task_<id>/task_<id>.py) prints its result
between the markers:

    ===== FINAL ANSWER =====
    <answer>
    ===== END FINAL ANSWER =====

and that stdout is captured in the sibling python_output.log.

Answer source (logs-first):
  - If python_output.log exists, parse the answer from it. Do NOT run the
    script live.
  - Only run task_<id>.py live when no log exists (a fallback). A log that
    exists but has no FINAL ANSWER block counts as "no answer" -> fail; it is
    not re-run.

The extracted answer is normalized (its scheme+host is rewritten to the site
base the evaluator resolves __<SITE>__ to, since generated logs used mixed
docker ports) and then scored with the existing UrlMatchEvaluator.

Each task is reported with the answer it produced and what it was expected to
be, on every axis the task is graded on: the URL comparison, plus the
string_match reference answers / program_html required_contents that the same
task carries (UrlMatchEvaluator surfaces those in expected_answers). Pass
--brief for the old one-line-per-task table.

Two reports are written to generated/<site>/:
  - url_match_eval_results.json     : pure url_match grading (url itself only).
  - url_match_combined_results.json : url_match OR'd with a second grading
                                      auto-detected from generated/<site>/
                                      (see discover_compare_file). A task passes
                                      here if either grading passed it.

Usage:
    ./venv/bin/python eval/run_url_match_eval.py
    ./venv/bin/python eval/run_url_match_eval.py --site shopping
    ./venv/bin/python eval/run_url_match_eval.py --site shopping_admin
    ./venv/bin/python eval/run_url_match_eval.py --task-ids 44,45
    ./venv/bin/python eval/run_url_match_eval.py --print-only
    ./venv/bin/python eval/run_url_match_eval.py --brief
    ./venv/bin/python eval/run_url_match_eval.py --compare-json path/to/run.json
    ./venv/bin/python eval/run_url_match_eval.py --no-compare
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.servers import SERVER_URLS as _SERVER_URLS
from eval.program_html_evaluator import DEFAULT_BASE_URLS
from eval.url_match_evaluator import UrlMatchEvaluator

# The curated url_match set, not the raw 684-task dump. Entries here carry
# hand-checked reference_urls (and a "fixed_notes" field recording why each was
# changed) — e.g. |OR| alternatives for golds the raw file states too narrowly.
# Grading against the raw file would score those tasks against the unfixed URL.
# Covers gitlab/shopping/shopping_admin plus the reddit url_match set (task_ids
# 595-652, verified program_html + url_match golds).
RAW_TASKS_PATH = (
    PROJECT_ROOT
    / "eval"
    / "tests"
    / "test_files"
    / "raw_webarena_tasks_url_match_updated.json"
)
VENV_PYTHON = PROJECT_ROOT / "venv" / "bin" / "python"

SUPPORTED_SITES = ("gitlab", "shopping", "shopping_admin", "reddit")

# The following are per-site and set by configure_site(); they default to gitlab
# so importing this module (or older callers) keeps working unchanged.
SITE = "gitlab"
GENERATED_DIR = PROJECT_ROOT / "generated" / SITE
# Pure url_match grading (url itself only) — never OR'd with anything.
RESULTS_PATH = GENERATED_DIR / "url_match_eval_results.json"
# Combined grading: url_match OR the comparison file (written to a new file so
# the pure url_match report above is left untouched).
COMBINED_RESULTS_PATH = GENERATED_DIR / "url_match_combined_results.json"
SITE_BASE = DEFAULT_BASE_URLS["__GITLAB__"]  # e.g. http://127.0.0.1:8023


def configure_site(site: str) -> None:
    """Point the module's per-site paths and base URL at `site`.

    Drives GENERATED_DIR (generated/<site>), the two output report paths, and
    SITE_BASE (the scheme+host the evaluator resolves __<SITE>__ to). Call once
    before evaluating.
    """
    global SITE, GENERATED_DIR, RESULTS_PATH, COMBINED_RESULTS_PATH, SITE_BASE
    if site not in SUPPORTED_SITES:
        raise ValueError(f"unsupported site {site!r}; choose from {SUPPORTED_SITES}")
    SITE = site
    GENERATED_DIR = PROJECT_ROOT / "generated" / site
    RESULTS_PATH = GENERATED_DIR / "url_match_eval_results.json"
    COMBINED_RESULTS_PATH = GENERATED_DIR / "url_match_combined_results.json"
    SITE_BASE = DEFAULT_BASE_URLS[f"__{site.upper()}__"]

_FINAL_ANSWER_RE = re.compile(
    r"===== FINAL ANSWER =====\s*(.*?)\s*===== END FINAL ANSWER =====",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Task selection
# ---------------------------------------------------------------------------


def load_tasks() -> List[Dict[str, Any]]:
    with open(RAW_TASKS_PATH) as fh:
        return json.load(fh)


def select_url_match_tasks(
    tasks: List[Dict[str, Any]],
    only_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """SITE url_match tasks that have a generated implementation directory."""
    selected = []
    for task in tasks:
        sites = task.get("sites", [])
        eval_types = task.get("eval", {}).get("eval_types", [])
        if SITE not in sites or "url_match" not in eval_types:
            continue
        task_id = task["task_id"]
        if only_ids is not None and task_id not in only_ids:
            continue
        if not (GENERATED_DIR / f"task_{task_id}").is_dir():
            continue
        selected.append(task)
    selected.sort(key=lambda t: t["task_id"])
    return selected


# ---------------------------------------------------------------------------
# Answer extraction
# ---------------------------------------------------------------------------


def extract_final_answer(text: str) -> Optional[str]:
    """Return the last FINAL ANSWER block's content, or None if absent."""
    matches = _FINAL_ANSWER_RE.findall(text)
    if not matches:
        return None
    answer = matches[-1].strip()
    return answer or None


def run_task_live(task_id: int) -> Tuple[Optional[str], str]:
    """Run task_<id>.py against the SITE base, append stdout to the log.

    Returns (raw_stdout, note).
    """
    task_dir = GENERATED_DIR / f"task_{task_id}"
    script = task_dir / f"task_{task_id}.py"
    if not script.exists():
        return None, f"script missing: {script}"

    env = dict(os.environ)
    env[f"{SITE.upper()}_DOMAIN"] = SITE_BASE
    # Match ClaudeCodeAgentRunner: also export the companion extra server's
    # host, so scripts do not have to guess where it lives.
    _extra_site = f"{SITE}_extra"
    if _extra_site in _SERVER_URLS:
        env[f"{_extra_site.upper()}_DOMAIN"] = _SERVER_URLS[_extra_site]
    try:
        proc = subprocess.run(
            [str(VENV_PYTHON), str(script)],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return None, "live run timed out"

    output = (proc.stdout or "") + (proc.stderr or "")
    header = (
        f"\n===== run_url_match_eval live run: task_id={task_id} "
        f"exit={proc.returncode} =====\n"
    )
    with open(task_dir / "python_output.log", "a") as fh:
        fh.write(header + output)
    note = f"live run exit={proc.returncode}"
    return output, note


def get_answer(task_id: int) -> Tuple[Optional[str], str, str]:
    """Resolve the agent's answer for a task.

    Returns (answer, source, note) where source is "log" or "live".
    """
    log_path = GENERATED_DIR / f"task_{task_id}" / "python_output.log"

    if log_path.exists():
        answer = extract_final_answer(log_path.read_text())
        note = "" if answer is not None else "log present but no FINAL ANSWER block"
        return answer, "log", note

    # Fallback: no log at all -> run the script live.
    output, note = run_task_live(task_id)
    answer = extract_final_answer(output) if output is not None else None
    return answer, "live", note


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


# An http(s) URL wherever it appears in an answer — bare, or embedded in a
# free-form sentence. Stops at whitespace or a closing quote/paren/bracket.
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'()<>\]]+")


def _extract_url(answer: str) -> Optional[str]:
    """Return the first http(s) URL mentioned in `answer`, or None.

    Shopping scripts often print the answer as prose that mentions the URL
    inline ("Opened order details page ... http://.../order_id/122/"), and some
    concatenate two URLs with no separator ("...a.htmlhttp://.../b.html"). Grab
    the first URL and, if a second scheme is glued onto its tail, cut there.
    """
    m = _URL_IN_TEXT_RE.search(answer)
    if not m:
        return None
    url = m.group(0)
    nxt = url.find("http", 1)
    if nxt != -1:
        url = url[:nxt]
    return url


def _looks_like_path(answer: str) -> bool:
    """True if `answer` is a bare relative path / product slug (no scheme, no
    whitespace) — e.g. "professional-medi-spa-scar-...html?price=0-30".

    Shopping answers are frequently just the product slug with no host, which
    would otherwise reach the evaluator without a leading '/' and fail the
    gold_in_pred substring check even when the product is correct. Prose (which
    contains whitespace) is excluded so it is left untouched.
    """
    return bool(answer) and not any(c.isspace() for c in answer) and "://" not in answer


def normalize_answer_url(answer: Optional[str]) -> Optional[str]:
    """Rewrite an answer's URL to the SITE base so path/query comparison works.

    Handles the shapes generated scripts actually emit:
      - a clean absolute URL (gitlab and many shopping tasks);
      - a URL embedded in a prose sentence, or two URLs concatenated
        (shopping) — the first URL is extracted;
      - a bare relative path / product slug with no host (shopping) — it is
        resolved against SITE_BASE so it gains a leading-slash path.

    In every case the scheme+host is rewritten to SITE_BASE (generated logs used
    mixed docker ports like :20016/:20046, while reference_url resolves
    __<SITE>__ to SITE_BASE). Answers with no URL and no slug pass through
    unchanged.
    """
    if not answer:
        return answer
    answer = answer.strip()
    base = urlparse(SITE_BASE)

    url = _extract_url(answer)
    if url is None and _looks_like_path(answer):
        # Treat the bare slug as a path relative to SITE_BASE.
        url = SITE_BASE.rstrip("/") + "/" + answer.lstrip("/")

    if url is None:
        return answer

    parsed = urlparse(url)
    return urlunparse(
        (base.scheme, base.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


def is_url(answer: Optional[str]) -> bool:
    if not answer:
        return False
    parsed = urlparse(answer)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


# ---------------------------------------------------------------------------
# Comparison source (OR'd against the url_match evaluator)
# ---------------------------------------------------------------------------
#
# Besides scoring each answer with UrlMatchEvaluator, we also load a second,
# independent grading produced by a full test run (test_agent_verified_all_
# sites.py) that lives in generated/<site>/. A url_match task is then counted as
# passed if EITHER grading passed it (see evaluate_all). Two on-disk shapes are
# supported:
#   - summary JSON  : {"results": [{"task_id": N, "passed": bool}, ...]}
#                     e.g. generated/gitlab/claude_code_gitlab_try2
#   - detailed JSONL: one obj per line, {"task_id": N, "correct": bool, ...}
#                     e.g. generated/gitlab/claude_code_gitlab_try2_detailed.jsonl


def _looks_like_summary(path: Path) -> bool:
    """True if `path` parses as a run-summary dict with a per-task results list."""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    results = data.get("results")
    return (
        isinstance(results, list)
        and bool(results)
        and isinstance(results[0], dict)
        and "task_id" in results[0]
        and "passed" in results[0]
    )


def discover_compare_file() -> Optional[Path]:
    """Auto-pick a comparison file from generated/<site>/.

    Prefer a run-summary JSON (newest), else fall back to a detailed .jsonl
    (newest). The script's own url_match_eval_results.json is never chosen.
    """
    if not GENERATED_DIR.is_dir():
        return None
    # Never auto-pick either of this script's own output files.
    own = {RESULTS_PATH.name, COMBINED_RESULTS_PATH.name}
    summaries: List[Path] = []
    detailed: List[Path] = []
    for entry in GENERATED_DIR.iterdir():
        if not entry.is_file() or entry.name in own:
            continue
        if entry.name.endswith(".jsonl"):
            detailed.append(entry)
        elif _looks_like_summary(entry):
            summaries.append(entry)
    pool = summaries or detailed
    if not pool:
        return None
    return max(pool, key=lambda p: p.stat().st_mtime)


def load_compare_records(path: Path) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, Any]]:
    """Load per-task records from a summary JSON or a detailed JSONL file.

    Returns (records, meta) where records is {task_id: {"passed", "intent",
    "eval_types"}} and meta describes the source: total rows, unique task_ids,
    and any duplicated task_ids. Duplicate task_ids (e.g. retried tasks) are OR'd
    together: a task counts as passed if any of its rows passed.
    """
    records: Dict[int, Dict[str, Any]] = {}
    rows = 0
    seen_counts: Dict[int, int] = {}

    def _record(task_id: Any, ok: Any, intent: str, eval_types: Any) -> None:
        nonlocal rows
        if task_id is None:
            return
        rows += 1
        tid = int(task_id)
        seen_counts[tid] = seen_counts.get(tid, 0) + 1
        rec = records.setdefault(
            tid, {"passed": False, "intent": intent or "", "eval_types": eval_types or []}
        )
        rec["passed"] = bool(ok) or rec["passed"]
        # Backfill intent/eval_types from whichever row first has them.
        if not rec["intent"] and intent:
            rec["intent"] = intent
        if not rec["eval_types"] and eval_types:
            rec["eval_types"] = eval_types

    if path.name.endswith(".jsonl"):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            # detailed entries use "correct"; tolerate "passed" too.
            ok = obj.get("correct", obj.get("passed"))
            _record(obj.get("task_id"), ok, obj.get("intent", ""), obj.get("eval_types"))
    else:
        data = json.loads(path.read_text())
        for obj in data.get("results", []):
            _record(
                obj.get("task_id"), obj.get("passed"),
                obj.get("intent", ""), obj.get("eval_types"),
            )

    meta = {
        "rows": rows,
        "unique": len(records),
        "duplicate_task_ids": sorted(t for t, c in seen_counts.items() if c > 1),
    }
    return records, meta


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def fail_reason(raw_answer: Optional[str], normalized: Optional[str], detail: Dict[str, Any]) -> str:
    if raw_answer is None:
        return "no-answer"
    # Label off the normalized value: a bare product slug is resolved to a URL,
    # so "non-url" now means the answer had no URL and no slug (i.e. free-form
    # prose with no link), which really can't be url-matched.
    if not is_url(normalized):
        return "non-url"
    if detail.get("error"):
        return detail["error"]
    return "mismatch"


def evaluate_url_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Score each SITE url_match task with UrlMatchEvaluator (url itself only).

    Returns one entry per task with `url_match_passed` and the url-comparison
    detail. No comparison-file OR happens here — that is layered on in
    build_combined_report.
    """
    evaluator = UrlMatchEvaluator()
    entries = []
    for task in tasks:
        task_id = task["task_id"]
        answer, source, note = get_answer(task_id)
        normalized = normalize_answer_url(answer)
        detail = evaluator.evaluate(task, normalized, actual_answer=answer)
        url_passed = bool(detail["passed"])
        entries.append(
            {
                "task_id": task_id,
                "intent": task.get("intent", ""),
                "eval_types": detail["eval_types"],
                "url_match_passed": url_passed,
                "raw_answer": answer,
                "actual_url": normalized,
                "expected_urls": detail["expected_urls"],
                # Non-url expectations (program_html / string_match) on the same
                # task — reported, not graded, by the url evaluator.
                "expected_answers": detail["expected_answers"],
                "match_mode": detail["match_mode"],
                "matched_url": detail["matched_url"],
                "answer_source": source,
                "reason": None if url_passed else fail_reason(answer, normalized, detail),
                "note": note or None,
            }
        )
    return entries


def build_combined_report(
    url_entries: List[Dict[str, Any]],
    compare_records: Dict[int, Dict[str, Any]],
    compare_meta: Dict[str, Any],
    compare_label: Optional[str],
) -> Dict[str, Any]:
    """Combine url_match with the comparison grading across ALL tasks.

    The combined universe is every task_id in the comparison file, unioned with
    the url_match tasks (so a url_match task absent from the comparison file is
    still included). A task passes if the url_match evaluator passed it OR the
    comparison grading did. Non-url_match tasks are carried straight from the
    comparison grading (url_match_passed = None for them).
    """
    url_by_id = {e["task_id"]: e for e in url_entries}
    all_ids = sorted(set(compare_records) | set(url_by_id))

    results = []
    passed_count = 0
    counts = {"both": 0, "url_only": 0, "compare_only": 0, "neither": 0}

    for tid in all_ids:
        ue = url_by_id.get(tid)
        crec = compare_records.get(tid)
        # None => not a url_match task we evaluated / not in the comparison file.
        url_passed = ue["url_match_passed"] if ue else None
        compare_passed = crec["passed"] if crec else None

        passed = (url_passed is True) or (compare_passed is True)
        if passed:
            passed_count += 1

        if url_passed and compare_passed:
            counts["both"] += 1
        elif url_passed:
            counts["url_only"] += 1
        elif compare_passed:
            counts["compare_only"] += 1
        else:
            counts["neither"] += 1

        intent = (ue or {}).get("intent") or (crec or {}).get("intent", "")
        eval_types = (crec or {}).get("eval_types") or (["url_match"] if ue else [])

        entry = {
            "task_id": tid,
            "intent": intent,
            "eval_types": eval_types,
            "is_url_match": ue is not None,
            "passed": passed,
            "url_match_passed": url_passed,
            "compare_passed": compare_passed,
        }
        # Carry the url-comparison detail only for tasks we actually url-scored.
        if ue:
            entry.update(
                {
                    "raw_answer": ue["raw_answer"],
                    "actual_url": ue["actual_url"],
                    "expected_urls": ue["expected_urls"],
                    "expected_answers": ue["expected_answers"],
                    "match_mode": ue["match_mode"],
                    "matched_url": ue["matched_url"],
                    "answer_source": ue["answer_source"],
                    "reason": None if url_passed else ue["reason"],
                    "note": ue["note"],
                }
            )
        results.append(entry)

    return {
        "total": len(all_ids),
        "passed": passed_count,
        "failed": len(all_ids) - passed_count,
        "compare_source": compare_label,
        "compare_rows": compare_meta.get("rows"),
        "compare_unique": compare_meta.get("unique"),
        "duplicate_task_ids": compare_meta.get("duplicate_task_ids"),
        "url_match_evaluated": len(url_entries),
        "breakdown": counts,
        "results": results,
    }


def build_pure_report(url_entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure url_match report (url itself only) from the url_match entries.

    No comparison OR — `passed` reflects the url_match evaluator alone. This is
    what goes to RESULTS_PATH.
    """
    results = []
    passed_count = 0
    for e in url_entries:
        up = bool(e["url_match_passed"])
        if up:
            passed_count += 1
        results.append(
            {
                "task_id": e["task_id"],
                "intent": e["intent"],
                "eval_types": e["eval_types"],
                "passed": up,
                "raw_answer": e["raw_answer"],
                "actual_url": e["actual_url"],
                "expected_urls": e["expected_urls"],
                "expected_answers": e["expected_answers"],
                "match_mode": e["match_mode"],
                "matched_url": e["matched_url"],
                "answer_source": e["answer_source"],
                "reason": None if up else e["reason"],
                "note": e["note"],
            }
        )
    return {
        "total": len(results),
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _pass_flag(val: Optional[bool]) -> str:
    """Render a per-source pass flag: Y / N / - (not present)."""
    if val is None:
        return "-"
    return "Y" if val else "N"


def _fmt_value(value: Any, limit: int = 220) -> str:
    """One-line, length-capped rendering of an answer or an expectation."""
    if value is None:
        return "(none)"
    if isinstance(value, str):
        text = " ".join(value.split())
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    if not text:
        return "(empty)"
    return text if len(text) <= limit else text[:limit] + f"... (+{len(text) - limit} chars)"


def _print_answer_detail(r: Dict[str, Any], indent: str = " " * 8) -> None:
    """Print what the agent answered and what every eval type expected.

    A url_match task is almost always graded on program_html too (and sometimes
    string_match), so the URL comparison alone doesn't explain a row. This shows
    the answer once, then the expectation per axis: url first, then whatever
    non-url expectations the task carries.
    """
    print(f"{indent}answer          : {_fmt_value(r.get('raw_answer'))}")
    print(f"{indent}actual url      : {_fmt_value(r.get('actual_url'))}")
    print(f"{indent}expected url    : {_fmt_value(r.get('expected_urls'))}")
    for eval_type, expected in (r.get("expected_answers") or {}).items():
        label = f"expected {eval_type}"
        print(f"{indent}{label:<16}: {_fmt_value(expected)}")


def print_report(report: Dict[str, Any], brief: bool = False) -> None:
    results = report["results"]
    url_rows = [r for r in results if r.get("is_url_match")]

    print("=" * 96)
    print(f"Combined evaluation (url_match OR comparison file) — generated/{SITE}")
    print(f"{SITE} base : {SITE_BASE}")
    print(f"compare src : {report.get('compare_source') or '(none found)'}"
          f"  (rows={report.get('compare_rows')}, unique={report.get('compare_unique')})")
    if report.get("duplicate_task_ids"):
        print(f"duplicates  : task_ids appearing >1x in compare file: "
              f"{report['duplicate_task_ids']} (OR'd together)")
    print("=" * 96)

    # Detailed table for the url_match tasks this script actually re-scored.
    print(f"url_match tasks ({len(url_rows)}) — url = url_match evaluator, cmp = comparison file")
    print(f"{'task':>6}  {'result':<6}  {'url':<3}  {'cmp':<3}  {'src':<4}  {'mode':<16}  detail")
    print("-" * 96)
    for r in url_rows:
        result = "PASS" if r["passed"] else "FAIL"
        if r["passed"]:
            detail = (
                f"matched {r['matched_url']}" if r["url_match_passed"]
                else "passed via comparison file only"
            )
        else:
            exp = r["expected_urls"][0] if r["expected_urls"] else "(none)"
            detail = f"[{r['reason']}] expected {exp}  <-  actual {r['actual_url']!r}"
        print(
            f"{r['task_id']:>6}  {result:<6}  "
            f"{_pass_flag(r['url_match_passed']):<3}  {_pass_flag(r['compare_passed']):<3}  "
            f"{r['answer_source']:<4}  {r['match_mode']:<16}  {detail}"
        )
        if not brief:
            _print_answer_detail(r)

    # Non-url_match tasks are carried from the comparison grading as-is.
    other_rows = [r for r in results if not r.get("is_url_match")]
    if other_rows:
        other_pass = sum(1 for r in other_rows if r["passed"])
        other_fail = [r["task_id"] for r in other_rows if not r["passed"]]
        print("-" * 96)
        print(f"other tasks ({len(other_rows)}) — carried from comparison file: "
              f"{other_pass} passed, {len(other_fail)} failed")
        if other_fail:
            print(f"  failed task_ids: {other_fail}")

    print("-" * 96)
    b = report.get("breakdown", {})
    print(f"SUMMARY (all tasks): {report['passed']}/{report['total']} passed "
          f"({report['failed']} failed)  [url_match OR comparison file]")
    if b:
        print(
            f"  breakdown: both={b.get('both', 0)}  "
            f"url_only={b.get('url_only', 0)}  "
            f"compare_only={b.get('compare_only', 0)}  "
            f"neither={b.get('neither', 0)}"
        )
    print("=" * 96)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--site",
        type=str,
        default="gitlab",
        choices=SUPPORTED_SITES,
        help="Which site's generated/<site>/ tasks to score (default: gitlab).",
    )
    parser.add_argument(
        "--task-ids",
        type=str,
        default=None,
        help="Comma-separated task ids to restrict the run (e.g. 44,45).",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print to console only; do not write the JSON report.",
    )
    parser.add_argument(
        "--compare-json",
        type=str,
        default=None,
        help=(
            "Path to a second grading to OR against the url_match evaluator. "
            "Accepts a run-summary JSON ({'results': [{'task_id','passed'}]}) or "
            "a detailed .jsonl ({'task_id','correct'} per line). "
            "If omitted, auto-detected from generated/<site>/ (newest summary, "
            "else newest .jsonl)."
        ),
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="Disable the OR comparison entirely; score with url_match only.",
    )
    parser.add_argument(
        "--brief",
        action="store_true",
        help=(
            "One line per task: drop the per-task answer / expected-url / "
            "expected string_match+program_html detail lines."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_site(args.site)
    only_ids = None
    if args.task_ids:
        only_ids = [int(x) for x in args.task_ids.split(",") if x.strip()]

    tasks = select_url_match_tasks(load_tasks(), only_ids)
    if not tasks:
        print(f"No matching {SITE} url_match tasks found in generated/{SITE}.")
        return 1

    # Score the url_match tasks (url itself only).
    url_entries = evaluate_url_tasks(tasks)

    # Resolve the comparison grading to OR against (unless disabled).
    compare_records: Dict[int, Dict[str, Any]] = {}
    compare_meta: Dict[str, Any] = {"rows": 0, "unique": 0, "duplicate_task_ids": []}
    compare_label: Optional[str] = None
    if not args.no_compare:
        compare_path: Optional[Path] = None
        if args.compare_json:
            compare_path = Path(args.compare_json)
            if not compare_path.exists():
                print(f"--compare-json not found: {compare_path}")
                return 1
        else:
            compare_path = discover_compare_file()
        if compare_path is not None:
            compare_records, compare_meta = load_compare_records(compare_path)
            try:
                compare_label = str(compare_path.relative_to(PROJECT_ROOT))
            except ValueError:
                compare_label = str(compare_path)
            print(f"Comparison grading: {compare_label} "
                  f"({compare_meta['rows']} rows, {compare_meta['unique']} unique tasks)")
        else:
            print(f"No comparison file found in generated/{SITE} — url_match only.")

    # Combined report spans every task in the comparison file (unioned with the
    # url_match tasks), OR-improved on the url_match subset.
    report = build_combined_report(url_entries, compare_records, compare_meta, compare_label)
    print_report(report, brief=args.brief)

    if not args.print_only:
        # Pure url_match report (url itself only) — the 39 url tasks.
        pure_report = build_pure_report(url_entries)
        with open(RESULTS_PATH, "w") as fh:
            json.dump(pure_report, fh, indent=2)
        print(f"\nurl_match report (url only, {pure_report['total']} tasks) "
              f"written to: {RESULTS_PATH}")

        # Combined report (all tasks, url_match OR comparison file) -> new file.
        with open(COMBINED_RESULTS_PATH, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"combined report (all {report['total']} tasks) "
              f"written to: {COMBINED_RESULTS_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
