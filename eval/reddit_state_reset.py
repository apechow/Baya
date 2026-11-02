"""
reddit_state_reset.py

Post-task state reset for the Reddit (Postmill) benchmark.

Unlike GitLab — whose reset (eval/gitlab_state_reset.py) undoes specific
mutations per task via the REST API — Reddit tasks have no fine-grained undo
config, so we restore the whole container to a pristine, fully-populated
snapshot between write tasks. This is the proven, contamination-free reset used
by the reddit program_html suite.

Strategy (tried in order by restore_checkpoint):
  1. CRIU checkpoint restore (fast, ~2s) — usually unavailable: CRIU cannot dump
     a running Postgres, so the checkpoint is typically invalid on this host.
  2. Docker commit-image restore (reliable, ~15-20s): docker rm + docker run from
     the clean image captured by eval/scripts/take_reddit_checkpoint.sh.

Both the pytest suite (test_agent_verified_reddit_program_html.py) and the
ReactAgent runner (run_webarena_verified.py) import restore_checkpoint from here.

Usage:
    from eval.reddit_state_reset import restore_checkpoint
    restore_checkpoint(mcp_url)   # call after a write task's eval completes
"""

from __future__ import annotations

import subprocess
import time
from typing import Optional

import requests as _requests

_SSH_TARGET = "sylvie@red5k.cs.berkeley.edu"
_CONTAINER = "webarena-verified-reddit"
_CHECKPOINT = "clean_state"
# Commit image created by eval/scripts/take_reddit_checkpoint.sh — used when
# CRIU restore fails (e.g. Docker network-namespace bug on Docker 28+, or CRIU
# being unable to dump Postgres).
_COMMIT_IMAGE = "webarena-reddit-clean:latest"


def restore_checkpoint(mcp_url: Optional[str] = None) -> None:
    """Restore Postmill to the clean-state snapshot and refresh the MCP session.

    Strategy (tried in order):
      1. CRIU checkpoint restore (fast, ~2s): docker stop + docker start --checkpoint
      2. Docker commit image fallback (reliable, ~15s): docker rm + docker run
         from the commit image captured by eval/scripts/take_reddit_checkpoint.sh

    After either path, waits until Postmill is serving. If ``mcp_url`` is given
    (the MCP-tool suite), also triggers the MCP server to re-authenticate (the
    restored DB has no record of the old session). The ReactAgent runner passes
    no ``mcp_url`` — it logs in via the browser fresh on every task, so there is
    no MCP session to refresh.
    """
    # --- Attempt 1: CRIU checkpoint restore ---
    criu_cmd = (
        f"docker stop {_CONTAINER} 2>/dev/null || true"
        f" && docker start --checkpoint {_CHECKPOINT} {_CONTAINER} 2>&1"
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", _SSH_TARGET, criu_cmd],
        capture_output=True, text=True, timeout=60,
    )
    criu_ok = result.returncode == 0 and "Error" not in result.stdout

    if not criu_ok:
        print(
            f"  [reset] CRIU restore failed ({result.stdout.strip()!r}); "
            "falling back to docker commit image",
            flush=True,
        )
        # --- Attempt 2: docker commit image fallback ---
        # docker rm removes the stopped/failed container; docker run starts
        # a fresh one from the clean-state image captured at snapshot time.
        commit_cmd = (
            f"docker stop {_CONTAINER} 2>/dev/null || true"
            f" && docker rm {_CONTAINER} 2>/dev/null || true"
            f" && docker run -d --name {_CONTAINER}"
            f" -p 127.0.0.1:9999:80 -p 127.0.0.1:9998:8877"
            f" {_COMMIT_IMAGE}"
        )
        subprocess.run(
            ["ssh", "-o", "BatchMode=yes", _SSH_TARGET, commit_cmd],
            check=True, timeout=60,
        )

    # Generous window: a restore boots Postgres via crash-recovery of a multi-GB
    # DB, which can take ~1-2 min and slows further when red5k is under load from
    # other containers. Too tight a timeout declares a still-starting Postmill
    # "failed" and the run proceeds against a half-up instance (HTTP 500s that
    # cascade to every later task).
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            r = _requests.get("http://127.0.0.1:9999/f/books", timeout=3)
            if r.status_code == 200 and "article" in r.text:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        raise TimeoutError("Postmill did not come up within 300s after checkpoint restore")

    # Refresh MCP session (restored DB has no record of the old PHPSESSID).
    # Only relevant for the MCP-tool suite; the ReactAgent runner passes no url.
    if mcp_url:
        _requests.get(f"{mcp_url}/session", timeout=10).raise_for_status()
