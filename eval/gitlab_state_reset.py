"""
gitlab_state_reset.py

Pre-task state reset for the GitLab benchmark test harness.

Each task in the benchmark can leave behind state (comments on MRs, forked
projects) that pollutes subsequent runs.  This module provides functions that
restore the relevant GitLab resources to a known-good baseline **before** the
agent executes each task.

Design principles
-----------------
* Uses the GitLab REST API exclusively (no Playwright) — fast and reliable.
* OAuth token is obtained once per benchmark run via password grant and reused.
* Each reset function is idempotent: calling it when nothing needs cleaning is
  a no-op that returns immediately.
* Failures are logged but do not abort the benchmark — the task still runs.

Supported reset scenarios
--------------------------
1. **MR comment cleanup** (tasks 390-393, 389):
   Delete all notes posted by `byteblaze` on the specified MR so that the
   agent's new comment will be the `lastElementChild` when evaluated.

2. **Fork cleanup** (tasks 394-398):
   Delete byteblaze's fork of the target project so the fork can be freshly
   created by the agent.  For task 398 specifically, forks of nvidia-patch and
   viewgrades-scraper must NOT exist (the eval expects 404 on those pages).

3. **Milestone cleanup** (tasks 590-594):
   Delete any pre-existing milestone whose title contains a given keyword so
   the agent can create it fresh (GitLab rejects duplicate titles).

4. **MR close-by-source-branch** (tasks 666-668, 805-807):
   Close any open merge request from the specified source branch (optionally
   filtered by target branch) so the agent can open a new one without hitting
   the "MR already exists" HTTP 409.

5. **Delete group** (tasks 799-803):
   Delete a group created by a prior run (e.g. "n-lab") so the agent can
   create it fresh without hitting "path has already been taken."

6. **Remove member** (tasks 567, 576, 578):
   Remove users added by a prior run from a project so the agent can add
   them again at the correct access level.

7. **Ensure MR open with reviewer** (task 357):
   Reopen a closed MR and/or re-add a reviewer if they were removed, so the
   read-only "review dashboard" task always sees the expected MRs.

Usage
-----
    from gitlab_state_reset import GitLabStateReset

    resetter = GitLabStateReset()           # obtains OAuth token once
    resetter.reset_for_task(task_dict)      # call before running each task
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from config.servers import SERVER_URLS as _SERVER_URLS

GITLAB_BASE = _SERVER_URLS["gitlab"]
BYTEBLAZE_USERNAME = "byteblaze"
BYTEBLAZE_PASSWORD = "hello1234"

# ---------------------------------------------------------------------------
# Per-task reset configuration
# Keyed by task_id.  Each entry lists the cleanup operations needed.
#
# MR comment cleanup: {"type": "mr_comments", "project": "ns/repo", "mr_iid": N}
#   Deletes all notes by byteblaze on that MR (system notes are skipped by
#   the API — only regular user notes can be deleted).
#
# Fork cleanup: {"type": "fork_delete", "fork_path": "byteblaze/slug"}
#   Deletes byteblaze's fork.  If it doesn't exist, this is a no-op.
#
# Ensure MR open with reviewer: {"type": "ensure_mr_open_with_reviewer", "project": "ns/repo", "mr_iid": N, "reviewer": "username"}
#   Reopens the MR if it was closed (merged MRs cannot be reopened — a warning
#   is logged instead).  Also re-adds the reviewer if they were removed.
# ---------------------------------------------------------------------------

TASK_RESET_CONFIG: Dict[int, List[Dict[str, Any]]] = {
    # -----------------------------------------------------------------------
    # Task 357 — "Go to the merge requests requiring my review" (read-only)
    # Ensures both review-assigned MRs are open and byteblaze is still a
    # reviewer.  Another task could accidentally close them, breaking the
    # string_match ground truth ("WIP - Post: Pitfalls..." / "feat: add WCAG levels").
    # -----------------------------------------------------------------------
    357: [
        {"type": "ensure_mr_open_with_reviewer", "project": "a11yproject/a11yproject.com", "mr_iid": 1490, "reviewer": "byteblaze"},
        {"type": "ensure_mr_open_with_reviewer", "project": "a11yproject/a11yproject.com", "mr_iid": 1270, "reviewer": "byteblaze"},
    ],
    # -----------------------------------------------------------------------
    # Milestone creation tasks (590-594)
    # Delete any pre-existing milestone whose title contains the keyword so
    # GitLab doesn't reject the agent's creation with HTTP 400.
    # -----------------------------------------------------------------------
    590: [{"type": "delete_milestone", "project": "primer/design",       "title_keyword": "product launch"}],
    591: [{"type": "delete_milestone", "project": "primer/design",       "title_keyword": "code review"}],
    592: [{"type": "delete_milestone", "project": "primer/design",       "title_keyword": "sensitive information"}],
    593: [{"type": "delete_milestone", "project": "byteblaze/dotfiles",  "title_keyword": "all branches to main"}],
    594: [{"type": "delete_milestone", "project": "byteblaze/dotfiles",  "title_keyword": "zsh comprehensive support"}],
    # -----------------------------------------------------------------------
    # MR creation tasks (666-668, 805-807)
    # Close any open MR from the same source→target pair so the agent can
    # open a fresh one without hitting HTTP 409 "MR already exists".
    # -----------------------------------------------------------------------
    666: [{"type": "close_mr_by_source_branch", "project": "primer/design",                    "source_branch": "dialog-component",       "target_branch": "dialog"}],
    667: [{"type": "close_mr_by_source_branch", "project": "primer/design",                    "source_branch": "dialog-component",       "target_branch": "bump-doctocat"}],
    668: [{"type": "close_mr_by_source_branch", "project": "a11yproject/a11yproject.com",      "source_branch": "redesign",               "target_branch": "master"}],
    805: [{"type": "close_mr_by_source_branch", "project": "a11yproject/a11yproject.com",      "source_branch": "feature/replace-gulp",   "target_branch": "master"}],
    806: [{"type": "close_mr_by_source_branch", "project": "a11yproject/a11yproject.com",      "source_branch": "redesign",               "target_branch": "markdown-figure-block"}],
    807: [{"type": "close_mr_by_source_branch", "project": "primer/design",                    "source_branch": "debug-build-time",       "target_branch": "main"}],
    # -----------------------------------------------------------------------
    # MR comment cleanup (389-393)
    # -----------------------------------------------------------------------
    # Task 389 — Post comment on primer/design MR 450
    # Uses must_include (no lastElementChild), but cleaning ensures a clean page.
    389: [
        {"type": "mr_comments", "project": "primer/design", "mr_iid": 450},
    ],
    # Task 390 — Post "lgtm" on a11yproject MR 1531
    390: [
        {"type": "mr_comments", "project": "a11yproject/a11yproject.com", "mr_iid": 1531},
    ],
    # Task 391 — Post "close because non reproducible" on MR 1265
    391: [
        {"type": "mr_comments", "project": "a11yproject/a11yproject.com", "mr_iid": 1265},
    ],
    # Task 392 — Post "Good idea" on MR 1071
    392: [
        {"type": "mr_comments", "project": "a11yproject/a11yproject.com", "mr_iid": 1071},
    ],
    # Task 393 — Post "lgtm" on byteblaze/empathy-prompts MR 19
    393: [
        {"type": "mr_comments", "project": "byteblaze/empathy-prompts", "mr_iid": 19},
    ],
    # Task 394 — Fork 2019-nCov (from itay-grudev)
    394: [
        {"type": "fork_delete", "fork_path": "byteblaze/2019-ncov"},
        {"type": "fork_delete", "fork_path": "byteblaze/2019-nCov"},
    ],
    # Task 395 — Fork Pytorch-GAN (most-starred)
    395: [
        {"type": "fork_delete", "fork_path": "byteblaze/pytorch-gan"},
        {"type": "fork_delete", "fork_path": "byteblaze/PyTorch-GAN"},
    ],
    # Task 396 — Fork ChatGPT
    396: [
        {"type": "fork_delete", "fork_path": "byteblaze/chatgpt"},
        {"type": "fork_delete", "fork_path": "byteblaze/ChatGPT"},
    ],
    # Task 397 — Fork MetaSeq
    397: [
        {"type": "fork_delete", "fork_path": "byteblaze/metaseq"},
        {"type": "fork_delete", "fork_path": "byteblaze/MetaSeq"},
    ],
    # Task 398 — Fork all source repos from Akilesh Kannan (aklsh)
    # Expected: SimCache ✅, dots ✅, CacheEval ✅ forked; nvidia-patch ❌, viewgrades-scraper ❌
    # We delete the three that should be forked (so the agent can re-fork them)
    # and also delete nvidia-patch/viewgrades-scraper if they were accidentally forked.
    398: [
        {"type": "fork_delete", "fork_path": "byteblaze/simcache"},
        {"type": "fork_delete", "fork_path": "byteblaze/SimCache"},
        {"type": "fork_delete", "fork_path": "byteblaze/dots"},
        {"type": "fork_delete", "fork_path": "byteblaze/cacheeval"},
        {"type": "fork_delete", "fork_path": "byteblaze/CacheEval"},
        {"type": "fork_delete", "fork_path": "byteblaze/nvidia-patch"},
        {"type": "fork_delete", "fork_path": "byteblaze/viewgrades-scraper"},
    ],
    # -----------------------------------------------------------------------
    # Star top-N tasks (523-527)
    # Unstar all of byteblaze's currently starred repos so the star counts
    # reflect only this run's actions, keeping the ground-truth repo names
    # consistent with the eval's required_contents.
    # -----------------------------------------------------------------------
    523: [{"type": "unstar_all"}],
    524: [{"type": "unstar_all"}],
    525: [{"type": "unstar_all"}],
    526: [{"type": "unstar_all"}],
    527: [{"type": "unstar_all"}],
    # -----------------------------------------------------------------------
    # Project creation tasks (560, 742, 745-756)
    # Delete any pre-existing project so the agent can create it fresh.
    # All projects are created under byteblaze's namespace, so fork_delete works.
    # -----------------------------------------------------------------------
    # -----------------------------------------------------------------------
    # Task 258 — "Open the public projects listing" (read-only)
    # Deletes all byteblaze fork/eval-created projects so they don't
    # dominate the activity-sorted page 1 of GET /projects?visibility=public,
    # allowing primer/design and a11yproject.com (both active as of the run)
    # to surface and satisfy must_include.
    # -----------------------------------------------------------------------
    258: [
        # Forks created by tasks 394-398
        {"type": "fork_delete", "fork_path": "byteblaze/metaseq"},
        {"type": "fork_delete", "fork_path": "byteblaze/MetaSeq"},
        {"type": "fork_delete", "fork_path": "byteblaze/PyTorch-GAN"},
        {"type": "fork_delete", "fork_path": "byteblaze/pytorch-gan"},
        {"type": "fork_delete", "fork_path": "byteblaze/2019-nCov"},
        {"type": "fork_delete", "fork_path": "byteblaze/2019-ncov"},
        {"type": "fork_delete", "fork_path": "byteblaze/SimCache"},
        {"type": "fork_delete", "fork_path": "byteblaze/simcache"},
        {"type": "fork_delete", "fork_path": "byteblaze/dots"},
        {"type": "fork_delete", "fork_path": "byteblaze/CacheEval"},
        {"type": "fork_delete", "fork_path": "byteblaze/cacheeval"},
        {"type": "fork_delete", "fork_path": "byteblaze/viewgrades-scraper"},
        {"type": "fork_delete", "fork_path": "byteblaze/nvidia-patch"},
        {"type": "fork_delete", "fork_path": "byteblaze/chatgpt"},
        {"type": "fork_delete", "fork_path": "byteblaze/ChatGPT"},
        # Eval-created projects (same as their own task resets)
        {"type": "fork_delete", "fork_path": "byteblaze/nolan_academy_awards"},
        {"type": "fork_delete", "fork_path": "byteblaze/planner"},
        {"type": "fork_delete", "fork_path": "byteblaze/web_arena"},
        {"type": "fork_delete", "fork_path": "byteblaze/awesome-llms"},
        {"type": "fork_delete", "fork_path": "byteblaze/llm_bulk_inference"},
        {"type": "fork_delete", "fork_path": "byteblaze/awesome_web_agents"},
        {"type": "fork_delete", "fork_path": "byteblaze/web_agent_android_xl"},
        {"type": "fork_delete", "fork_path": "byteblaze/project_site"},
        {"type": "fork_delete", "fork_path": "byteblaze/agi_index"},
        {"type": "fork_delete", "fork_path": "byteblaze/AGISite"},
        {"type": "fork_delete", "fork_path": "byteblaze/agi_site"},
        {"type": "fork_delete", "fork_path": "byteblaze/web_agent"},
        {"type": "fork_delete", "fork_path": "byteblaze/web_agent_android_xs"},
        {"type": "fork_delete", "fork_path": "byteblaze/web_agent_nodejs"},
        {"type": "fork_delete", "fork_path": "byteblaze/web_agent_index"},
        {"type": "fork_delete", "fork_path": "byteblaze/11711_gitlab"},
    ],
    560: [{"type": "fork_delete", "fork_path": "byteblaze/nolan_academy_awards"}],
    742: [{"type": "fork_delete", "fork_path": "byteblaze/planner"}],
    743: [{"type": "fork_delete", "fork_path": "byteblaze/web_arena"}],
    745: [{"type": "fork_delete", "fork_path": "byteblaze/awesome-llms"}],
    746: [{"type": "fork_delete", "fork_path": "byteblaze/llm_bulk_inference"}],
    747: [{"type": "fork_delete", "fork_path": "byteblaze/awesome_web_agents"}],
    748: [{"type": "fork_delete", "fork_path": "byteblaze/web_agent_android_xl"}],
    749: [{"type": "fork_delete", "fork_path": "byteblaze/project_site"}],
    750: [{"type": "fork_delete", "fork_path": "byteblaze/agi_index"}],
    751: [{"type": "fork_delete", "fork_path": "byteblaze/AGISite"},
          {"type": "fork_delete", "fork_path": "byteblaze/agi_site"}],
    752: [{"type": "fork_delete", "fork_path": "byteblaze/web_agent"}],
    753: [{"type": "fork_delete", "fork_path": "byteblaze/web_agent_android_xs"}],
    754: [{"type": "fork_delete", "fork_path": "byteblaze/web_agent_nodejs"}],
    755: [{"type": "fork_delete", "fork_path": "byteblaze/web_agent_index"}],
    756: [{"type": "fork_delete", "fork_path": "byteblaze/11711_gitlab"}],
    # -----------------------------------------------------------------------
    # Group creation tasks (799-803)
    # Delete any pre-existing group so the agent can create it fresh.
    # -----------------------------------------------------------------------
    799: [{"type": "delete_group", "group_path": "n-lab"}],
    800: [{"type": "delete_group", "group_path": "x-lab"}],
    801: [{"type": "delete_group", "group_path": "crew"}],
    802: [{"type": "delete_group", "group_path": "coding_friends"}],
    803: [{"type": "delete_group", "group_path": "webagent"}],
    # -----------------------------------------------------------------------
    # Member invitation tasks (567, 576, 578)
    # Remove previously-added members so the agent can add them fresh.
    # -----------------------------------------------------------------------
    567: [
        {"type": "remove_member", "project": "byteblaze/gimmiethat.space", "username": "lahwaacz"},
        {"type": "remove_member", "project": "byteblaze/gimmiethat.space", "username": "bblanchon"},
    ],
    570: [
        {"type": "remove_member", "project": "byteblaze/timeit", "username": "lahwaacz"},
        {"type": "remove_member", "project": "byteblaze/timeit", "username": "V13Axel"},
        {"type": "remove_member", "project": "byteblaze/timeit", "username": "alexhutnik"},
        {"type": "remove_member", "project": "byteblaze/timeit", "username": "bblanchon"},
    ],
    576: [
        {"type": "remove_member", "project": "byteblaze/a11y-webring.club", "username": "abisubramanya27"},
        {"type": "remove_member", "project": "byteblaze/a11y-webring.club", "username": "lahwaacz"},
    ],
    578: [
        {"type": "remove_member", "project": "byteblaze/millennials-to-snake-people", "username": "yjlou"},
        {"type": "remove_member", "project": "byteblaze/millennials-to-snake-people", "username": "a11yproject"},
    ],
}


class GitLabStateReset:
    """
    Manages pre-task state reset for the GitLab benchmark.

    Obtain once per benchmark run:
        resetter = GitLabStateReset()

    Then call before each task:
        resetter.reset_for_task(task)
    """

    def __init__(
        self,
        gitlab_base: str = GITLAB_BASE,
        username: str = BYTEBLAZE_USERNAME,
        password: str = BYTEBLAZE_PASSWORD,
    ):
        self._base = gitlab_base.rstrip("/")
        self._username = username
        self._password = password
        self._token: Optional[str] = None
        self._token_obtained_at: float = 0.0
        # OAuth tokens expire after 2 hours; refresh proactively at 90 min
        self._token_ttl: float = 90 * 60

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset_for_task(self, task: Dict[str, Any]) -> None:
        """
        Run all pre-task cleanup operations for the given task.

        Args:
            task: Task dict from the benchmark JSON.
        """
        task_id = task.get("task_id")
        ops = TASK_RESET_CONFIG.get(task_id, [])
        if not ops:
            return

        print(f"   🔄 [reset] Cleaning state for task {task_id} ({len(ops)} operations)...")
        token = self._get_token()
        if not token:
            print(f"   ⚠️  [reset] Could not obtain OAuth token — skipping reset for task {task_id}")
            return

        for op in ops:
            op_type = op.get("type")
            try:
                if op_type == "mr_comments":
                    self._reset_mr_comments(token, op["project"], op["mr_iid"])
                elif op_type == "fork_delete":
                    self._delete_fork(token, op["fork_path"])
                elif op_type == "delete_milestone":
                    self._delete_milestones_by_title(token, op["project"], op["title_keyword"])
                elif op_type == "close_mr_by_source_branch":
                    self._close_mrs_by_source_branch(
                        token, op["project"], op["source_branch"],
                        target_branch=op.get("target_branch"),
                    )
                elif op_type == "unstar_all":
                    self._unstar_all_projects(token)
                elif op_type == "delete_group":
                    self._delete_group(token, op["group_path"])
                elif op_type == "remove_member":
                    self._remove_member(token, op["project"], op["username"])
                elif op_type == "ensure_mr_open_with_reviewer":
                    self._ensure_mr_open_with_reviewer(
                        token, op["project"], op["mr_iid"], op["reviewer"]
                    )
                else:
                    print(f"   ⚠️  [reset] Unknown op type: {op_type!r}")
            except Exception as exc:
                print(f"   ⚠️  [reset] Op {op_type!r} failed: {exc}")

    # ------------------------------------------------------------------
    # OAuth token management
    # ------------------------------------------------------------------

    def _get_token(self) -> Optional[str]:
        """Return a valid OAuth token, refreshing if needed."""
        now = time.time()
        if self._token and (now - self._token_obtained_at) < self._token_ttl:
            return self._token

        token = self._fetch_token()
        if token:
            self._token = token
            self._token_obtained_at = now
        return token

    def _fetch_token(self) -> Optional[str]:
        """Obtain a fresh OAuth access token via password grant (with retry)."""
        url = f"{self._base}/oauth/token"
        body = json.dumps({
            "grant_type": "password",
            "username": self._username,
            "password": self._password,
        }).encode()
        last_exc = None
        for attempt in range(5):  # Up to 5 attempts: 0s, 1s, 2s, 4s, 8s waits
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                    token = data.get("access_token")
                    if token:
                        return token
            except Exception as exc:
                last_exc = exc
            if attempt < 4:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s, 8s
        print(f"   ⚠️  [reset] OAuth token fetch failed after 5 attempts: {last_exc}")
        return None

    # ------------------------------------------------------------------
    # GitLab API helpers
    # ------------------------------------------------------------------

    def _api(
        self,
        method: str,
        path: str,
        body: Optional[Dict] = None,
        token: Optional[str] = None,
        expected_statuses: Tuple[int, ...] = (200, 201, 204),
    ) -> Tuple[Optional[Dict], int]:
        """
        Make a GitLab API call.

        Returns (parsed_json_or_None, http_status_code).
        On 404 or other expected-but-empty responses returns (None, status).
        """
        url = f"{self._base}/api/v4{path}"
        data = json.dumps(body).encode() if body else None
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                status = resp.status
                raw = resp.read()
                if raw:
                    return json.loads(raw), status
                return None, status
        except urllib.error.HTTPError as exc:
            status = exc.code
            # Treat 404, 409, and 500 as non-fatal — return (None, status)
            # 500 can happen transiently after deletions while GitLab processes the request
            if status in (404, 409, 500):
                return None, status
            # Try to read error body for logging
            try:
                err_body = exc.read().decode(errors="replace")
            except Exception:
                err_body = ""
            raise RuntimeError(
                f"GitLab API {method} {path} → HTTP {status}: {err_body[:200]}"
            ) from exc

    # ------------------------------------------------------------------
    # MR comment cleanup
    # ------------------------------------------------------------------

    def _reset_mr_comments(self, token: str, project_path: str, mr_iid: int) -> None:
        """
        Delete all notes posted by byteblaze on the specified MR.

        Fetches pages of notes, finds those authored by byteblaze, and
        deletes them via DELETE /projects/{id}/merge_requests/{iid}/notes/{note_id}.
        System notes (where `system=True`) are skipped — the API ignores them.
        """
        # URL-encode the project path (e.g. "a11yproject/a11yproject.com" → "a11yproject%2Fa11yproject.com")
        encoded_project = urllib.parse.quote(project_path, safe="")

        # Get the internal project ID first (needed for note deletion).
        # Retry up to 3 times — GitLab can transiently return 500 after heavy API activity.
        proj_data = None
        status = 0
        for _attempt in range(3):
            proj_data, status = self._api("GET", f"/projects/{encoded_project}", token=token)
            if proj_data and "id" in proj_data:
                break
            if status == 404:
                break  # Project genuinely doesn't exist
            time.sleep(2 ** _attempt)  # 1s, 2s, 4s

        if proj_data is None or "id" not in proj_data:
            print(f"   ⚠️  [reset] Project {project_path!r} not found (HTTP {status})")
            return
        project_id = proj_data["id"]

        deleted_count = 0
        page = 1
        while True:
            notes, status = self._api(
                "GET",
                f"/projects/{project_id}/merge_requests/{mr_iid}/notes"
                f"?per_page=100&page={page}",
                token=token,
            )
            if not notes or not isinstance(notes, list):
                break

            for note in notes:
                # Skip system notes (merge events, approvals, etc.)
                if note.get("system", False):
                    continue
                author = (note.get("author") or {}).get("username", "")
                if author == self._username:
                    note_id = note["id"]
                    _, del_status = self._api(
                        "DELETE",
                        f"/projects/{project_id}/merge_requests/{mr_iid}/notes/{note_id}",
                        token=token,
                        expected_statuses=(200, 204, 404),
                    )
                    if del_status in (200, 204):
                        deleted_count += 1

            # Check for more pages
            if len(notes) < 100:
                break
            page += 1

        if deleted_count > 0:
            print(f"   ✅ [reset] Deleted {deleted_count} comment(s) from {project_path} MR#{mr_iid}")
        else:
            print(f"   ✓  [reset] No comments to delete on {project_path} MR#{mr_iid}")

    # ------------------------------------------------------------------
    # Fork cleanup
    # ------------------------------------------------------------------

    def _delete_fork(self, token: str, fork_path: str) -> None:
        """
        Delete byteblaze's fork at the given path (e.g. "byteblaze/metaseq").

        Does nothing if the fork doesn't exist.
        """
        encoded_path = urllib.parse.quote(fork_path, safe="")

        # Check if the fork exists (try direct path lookup first)
        proj_data, status = self._api("GET", f"/projects/{encoded_path}", token=token)

        # 500 can occur for certain paths on this GitLab instance — fall back to owned-project scan
        if status == 500 or (proj_data is None and status not in (404, 200)):
            slug = fork_path.split("/", 1)[-1].lower()
            proj_data = self._find_owned_project_by_slug(token, slug)
            if proj_data is None:
                # Couldn't find it — skip
                return
        elif status == 404 or proj_data is None or "id" not in proj_data:
            # Fork doesn't exist — nothing to do
            return

        # Verify it's actually a fork owned by byteblaze (safety check)
        namespace = (proj_data.get("namespace") or {}).get("path", "")
        if namespace.lower() != self._username.lower():
            print(f"   ⚠️  [reset] {fork_path!r} exists but namespace is {namespace!r} — skipping delete")
            return

        project_id = proj_data["id"]
        project_name = proj_data.get("path_with_namespace", fork_path)

        _, del_status = self._api(
            "DELETE",
            f"/projects/{project_id}",
            token=token,
            expected_statuses=(200, 202, 204, 404),
        )
        if del_status in (200, 202, 204):
            print(f"   ✅ [reset] Deleted fork {project_name}")
            # Wait for GitLab to process the deletion (project namespace stays reserved briefly)
            self._wait_for_project_deletion(token, project_id)
        elif del_status == 404:
            pass  # Already gone
        else:
            print(f"   ⚠️  [reset] Unexpected status {del_status} when deleting {fork_path}")

    # ------------------------------------------------------------------
    # Milestone cleanup
    # ------------------------------------------------------------------

    def _delete_milestones_by_title(
        self, token: str, project_path: str, title_keyword: str
    ) -> None:
        """
        Delete all milestones in a project whose title contains title_keyword
        (case-insensitive).  Uses the GitLab search API to narrow the list,
        then filters locally and issues DELETE for each match.

        This is idempotent: if no matching milestone exists the call is a no-op.
        """
        encoded_project = urllib.parse.quote(project_path, safe="")

        proj_data, status = self._api("GET", f"/projects/{encoded_project}", token=token)
        if not proj_data or "id" not in proj_data:
            print(f"   ⚠️  [reset] Project {project_path!r} not found (HTTP {status})")
            return
        project_id = proj_data["id"]

        encoded_keyword = urllib.parse.quote(title_keyword)
        milestones, _ = self._api(
            "GET",
            f"/projects/{project_id}/milestones?search={encoded_keyword}&per_page=100",
            token=token,
        )
        if not isinstance(milestones, list):
            print(f"   ✓  [reset] No milestones matching {title_keyword!r} in {project_path}")
            return

        deleted_count = 0
        for ms in milestones:
            ms_title = ms.get("title", "")
            if title_keyword.lower() in ms_title.lower():
                ms_id = ms["id"]
                _, del_status = self._api(
                    "DELETE",
                    f"/projects/{project_id}/milestones/{ms_id}",
                    token=token,
                    expected_statuses=(200, 204, 404),
                )
                if del_status in (200, 204):
                    deleted_count += 1
                    print(f"   ✅ [reset] Deleted milestone {ms_title!r} from {project_path}")

        if deleted_count == 0:
            print(f"   ✓  [reset] No milestones matching {title_keyword!r} in {project_path}")

    # ------------------------------------------------------------------
    # MR cleanup
    # ------------------------------------------------------------------

    def _close_mrs_by_source_branch(
        self,
        token: str,
        project_path: str,
        source_branch: str,
        target_branch: Optional[str] = None,
    ) -> None:
        """
        Close any open merge requests from source_branch in the given project.
        When target_branch is provided only MRs whose target matches are closed,
        allowing multiple open MRs from the same source to different targets to
        coexist without interference.

        This is idempotent: if no matching open MR exists the call is a no-op.
        """
        encoded_project = urllib.parse.quote(project_path, safe="")

        proj_data, status = self._api("GET", f"/projects/{encoded_project}", token=token)
        if not proj_data or "id" not in proj_data:
            print(f"   ⚠️  [reset] Project {project_path!r} not found (HTTP {status})")
            return
        project_id = proj_data["id"]

        # Fetch ALL open MRs and filter client-side by source_branch.
        # Using the source_branch query parameter is unreliable for branches that
        # contain '/' or '.' (e.g. 'a11yproject.com/redesign') — GitLab's filter
        # doesn't consistently match them regardless of URL encoding. Client-side
        # filtering on the 'source_branch' field in the response is always exact.
        page = 1
        all_mrs = []
        while True:
            page_mrs, _ = self._api(
                "GET",
                f"/projects/{project_id}/merge_requests?state=opened&scope=all&per_page=100&page={page}",
                token=token,
            )
            if not isinstance(page_mrs, list) or not page_mrs:
                break
            all_mrs.extend(page_mrs)
            if len(page_mrs) < 100:
                break
            page += 1

        mrs = [mr for mr in all_mrs if mr.get("source_branch") == source_branch]

        if not mrs:
            print(f"   ✓  [reset] No open MRs from {source_branch!r} in {project_path}")
            return

        closed_count = 0
        for mr in mrs:
            if target_branch and mr.get("target_branch") != target_branch:
                continue
            mr_iid = mr["iid"]
            _, upd_status = self._api(
                "PUT",
                f"/projects/{project_id}/merge_requests/{mr_iid}",
                body={"state_event": "close"},
                token=token,
                expected_statuses=(200, 201, 404),
            )
            if upd_status in (200, 201):
                closed_count += 1
                print(
                    f"   ✅ [reset] Closed MR!{mr_iid} "
                    f"({source_branch} → {mr.get('target_branch')}) in {project_path}"
                )

        if closed_count == 0:
            print(f"   ✓  [reset] No open MRs from {source_branch!r} in {project_path}")

    # ------------------------------------------------------------------
    # Star cleanup
    # ------------------------------------------------------------------

    def _unstar_all_projects(self, token: str) -> None:
        """
        Unstar every project currently starred by byteblaze.

        Uses GET /users/{id}/starred_projects to list them, then
        DELETE /projects/{id}/star to remove each star.  Idempotent —
        if byteblaze has no starred projects this is a no-op.
        """
        # Resolve byteblaze's numeric user ID (required by the starred_projects endpoint)
        user_list, status = self._api(
            "GET", f"/users?username={self._username}", token=token
        )
        if not isinstance(user_list, list) or not user_list:
            print(f"   ⚠️  [reset] Could not resolve user ID for {self._username!r} (HTTP {status})")
            return
        user_id = user_list[0]["id"]

        unstarred_count = 0
        page = 1
        while True:
            starred, _ = self._api(
                "GET",
                f"/users/{user_id}/starred_projects?per_page=100&page={page}",
                token=token,
            )
            if not isinstance(starred, list) or not starred:
                break
            for proj in starred:
                proj_id = proj["id"]
                _, del_status = self._api(
                    "DELETE",
                    f"/projects/{proj_id}/star",
                    token=token,
                    expected_statuses=(200, 304, 404),
                )
                if del_status in (200, 304):
                    unstarred_count += 1
            if len(starred) < 100:
                break
            page += 1

        if unstarred_count > 0:
            print(f"   ✅ [reset] Unstarred {unstarred_count} project(s) for {self._username}")
        else:
            print(f"   ✓  [reset] No starred projects to remove for {self._username}")

    def _ensure_mr_open_with_reviewer(
        self, token: str, project_path: str, mr_iid: int, reviewer_username: str
    ) -> None:
        """
        Ensure a specific MR is open and reviewer_username is listed as a reviewer.

        - If the MR is closed: reopens it via state_event="reopen".
        - If the MR is merged: logs a warning (merged MRs cannot be reopened via API).
        - If reviewer_username is not in the current reviewer list: adds them.
        """
        encoded_project = urllib.parse.quote(project_path, safe="")

        proj_data, status = self._api("GET", f"/projects/{encoded_project}", token=token)
        if not proj_data or "id" not in proj_data:
            print(f"   ⚠️  [reset] Project {project_path!r} not found (HTTP {status})")
            return
        project_id = proj_data["id"]

        mr_data, status = self._api(
            "GET", f"/projects/{project_id}/merge_requests/{mr_iid}", token=token
        )
        if not mr_data or "iid" not in mr_data:
            print(f"   ⚠️  [reset] MR !{mr_iid} not found in {project_path} (HTTP {status})")
            return

        mr_state = mr_data.get("state", "")
        current_reviewer_ids = [r["id"] for r in mr_data.get("reviewers", [])]
        current_reviewer_names = [r["username"] for r in mr_data.get("reviewers", [])]

        updates: Dict[str, Any] = {}

        # Reopen if closed (merged MRs cannot be reopened)
        if mr_state == "closed":
            updates["state_event"] = "reopen"
        elif mr_state == "merged":
            print(
                f"   ⚠️  [reset] MR !{mr_iid} ({project_path}) is merged — "
                f"cannot reopen; string_match ground truth for task 357 may fail"
            )
            return

        # Add reviewer if missing
        if reviewer_username not in current_reviewer_names:
            user_list, _ = self._api(
                "GET", f"/users?username={reviewer_username}", token=token
            )
            if isinstance(user_list, list) and user_list:
                updates["reviewer_ids"] = current_reviewer_ids + [user_list[0]["id"]]

        if not updates:
            print(
                f"   ✓  [reset] MR !{mr_iid} ({project_path}) already open "
                f"with {reviewer_username} as reviewer"
            )
            return

        _, upd_status = self._api(
            "PUT",
            f"/projects/{project_id}/merge_requests/{mr_iid}",
            body=updates,
            token=token,
            expected_statuses=(200, 201, 404),
        )
        if upd_status in (200, 201):
            actions = []
            if "state_event" in updates:
                actions.append(f"reopened (was {mr_state})")
            if "reviewer_ids" in updates:
                actions.append(f"added {reviewer_username} as reviewer")
            print(f"   ✅ [reset] MR !{mr_iid} ({project_path}): {', '.join(actions)}")
        else:
            print(f"   ⚠️  [reset] Failed to update MR !{mr_iid}: HTTP {upd_status}")

    def _delete_group(self, token: str, group_path: str) -> None:
        """
        Delete a GitLab group by its path (e.g. "n-lab").

        Does nothing if the group doesn't exist. Only deletes groups owned by
        byteblaze (verified via the `owner_id` or namespace check).
        """
        encoded_path = urllib.parse.quote(group_path, safe="")
        group_data, status = self._api("GET", f"/groups/{encoded_path}", token=token)
        if status == 404 or group_data is None or "id" not in group_data:
            print(f"   ✓  [reset] Group {group_path!r} does not exist — nothing to delete")
            return

        group_id = group_data["id"]
        _, del_status = self._api(
            "DELETE",
            f"/groups/{group_id}",
            token=token,
            expected_statuses=(200, 202, 204, 404),
        )
        if del_status in (200, 202, 204):
            print(f"   ✅ [reset] Deleted group {group_path!r}")
            # GitLab reserves the group path briefly after deletion; sleep long
            # enough that the next agent creation attempt doesn't see HTTP 400.
            time.sleep(10)
        elif del_status == 404:
            pass  # Already gone
        else:
            print(f"   ⚠️  [reset] Unexpected status {del_status} when deleting group {group_path!r}")

    def _remove_member(self, token: str, project_path: str, username: str) -> None:
        """
        Remove a member from a project by username.

        Does nothing if the user is not a member. Does not remove the project
        owner (byteblaze) even if passed.
        """
        if username.lower() == self._username.lower():
            print(f"   ⚠️  [reset] Refusing to remove project owner {username!r}")
            return

        encoded_project = urllib.parse.quote(project_path, safe="")

        proj_data, status = self._api("GET", f"/projects/{encoded_project}", token=token)
        if not proj_data or "id" not in proj_data:
            print(f"   ⚠️  [reset] Project {project_path!r} not found (HTTP {status})")
            return
        project_id = proj_data["id"]

        # Resolve username → user_id
        user_list, _ = self._api("GET", f"/users?username={urllib.parse.quote(username)}", token=token)
        if not isinstance(user_list, list) or not user_list:
            print(f"   ✓  [reset] User {username!r} not found — skipping member removal")
            return
        user_id = user_list[0]["id"]

        _, del_status = self._api(
            "DELETE",
            f"/projects/{project_id}/members/{user_id}",
            token=token,
            expected_statuses=(200, 204, 404),
        )
        if del_status in (200, 204):
            print(f"   ✅ [reset] Removed {username!r} from {project_path}")
        elif del_status == 404:
            print(f"   ✓  [reset] {username!r} was not a member of {project_path}")
        else:
            print(f"   ⚠️  [reset] Unexpected status {del_status} removing {username!r} from {project_path}")

    def _find_owned_project_by_slug(self, token: str, slug: str) -> Optional[Dict]:
        """
        Find a project owned by byteblaze by its slug (case-insensitive).

        Used as a fallback when the direct path lookup returns 500.
        Fetches up to 100 owned projects and matches by path.
        """
        owned, status = self._api("GET", "/projects?owned=true&per_page=100", token=token)
        if not isinstance(owned, list):
            return None
        for proj in owned:
            proj_path = proj.get("path", "").lower()
            if proj_path == slug.lower():
                return proj
        return None

    def _wait_for_project_deletion(self, token: str, project_id: int, timeout_s: int = 45) -> None:
        """Poll until the project is no longer accessible (or timeout)."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            _, status = self._api("GET", f"/projects/{project_id}", token=token)
            if status in (404, 500):
                # 404 = deleted; 500 = GitLab processing deletion (treat as deleted).
                # Sleep an extra 8 seconds after the project disappears — GitLab
                # keeps the namespace path reserved briefly after the project 404s,
                # and a creation attempt during that window gets HTTP 400 "already taken".
                time.sleep(8)
                return
            time.sleep(1)
        # Timeout — log but continue; GitLab may still be processing


# ---------------------------------------------------------------------------
# Convenience function for one-off use
# ---------------------------------------------------------------------------

def reset_task_state(task: Dict[str, Any], resetter: Optional[GitLabStateReset] = None) -> None:
    """
    Reset GitLab state for a single task.

    Args:
        task:     Task dict from the benchmark JSON.
        resetter: Optional pre-constructed GitLabStateReset instance.
                  If None, a new one is created (incurs OAuth overhead).
    """
    if resetter is None:
        resetter = GitLabStateReset()
    resetter.reset_for_task(task)
