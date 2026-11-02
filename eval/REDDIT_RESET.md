# Reddit Environment Reset

How we return the Reddit (Postmill) benchmark environment to a clean, fully-populated
baseline between tasks, why the obvious approaches don't work, and how to run/port it.

---

## 1. Why a reset is needed

Reddit `program_html` tasks are **write** tasks — the agent creates posts, casts
votes, edits post bodies, changes bios, subscribes to forums, etc. If a task
leaves its changes in the database, the *next* task's evaluation reads a
contaminated site:

- Feed-position-sensitive tasks break. Many reddit tasks target "the newest post
  in forum X" or "the top post ever in forum Y". A post created by an earlier
  task shifts those feeds, so the agent acts on the wrong post.
- Idempotency/dedup checks trip. Post-creation verifies by title; a title left
  behind by a previous run causes false "already exists" matches.
- Stray votes/edits linger and are read as the current state.

So we must restore Postmill to a **known-clean, fully-populated baseline** around
every write task. Read-only tasks (`string_match`) mutate nothing and run with
the reset disabled.

---

## 2. Approaches that do **not** work (and why)

### 2a. In-place / surgical undo
The natural idea is to reverse each task's specific effect via an API (delete the
post it created, retract the vote, restore the bio). This fails for Reddit
because:
- The Postmill image ships **no "reset to pristine" endpoint** — there's nothing
  to call.
- Reversing effects per-task requires a hand-written undo recipe for every one of
  the ~95 write tasks, and agent runs leave **partial, unpredictable** state (a
  half-created post, a bio changed twice, a vote toggled). Targeted undo kept
  missing edge cases, so it's not reliable.

### 2b. `docker checkpoint` (CRIU snapshot/restore) — the tempting "fast" reset
The intuitive approach is a container-level snapshot: freeze a clean state once,
thaw it in ~2 seconds between tasks. **This is fundamentally incompatible with a
database-backed site**, which we confirmed four ways:

1. **CRIU cannot dump a running Postgres.** Postgres relies on shared-memory
   segments and other kernel resources CRIU refuses to serialize;
   `docker checkpoint create` fails with `criu failed: type DUMP`.
2. **Docker hides that failure** — it returns exit code 0 *even when the CRIU
   dump failed*, so it looks like a checkpoint was written when nothing valid was.
   (This is a trap: the reset silently does nothing.)
3. **Restore is separately broken** on Docker 28 hosts:
   `bind-mount /proc/0/ns/net … no such file` (a network-namespace restore bug).
4. **The dealbreaker even if 1–3 were solved:** checkpoint/restore snapshots
   **process memory, not the filesystem**. The database lives on disk, so a
   restore brings back stale in-memory state while leaving the on-disk database
   exactly as the previous task left it — the thing we need reset isn't reset.
   (Verified directly: write CLEAN → checkpoint → write DIRTY → restore →
   still reads DIRTY.)

Because a stateful DB can be neither CRIU-frozen nor rolled back by a memory
snapshot, the fast-checkpoint path is off the table — it's an incompatibility,
not a tuning problem.

---

## 3. The approach that works: recreate the container from a committed clean image

Since we can't snapshot or surgically undo, we do the blunt-but-correct thing:
**throw the container away and recreate it from a pre-baked clean image.** A fresh
container gets a fresh copy of the clean on-disk database, guaranteeing a real
clean state every time.

There are two phases: a one-time **capture** of the golden image, and a per-task
**restore** from it.

### Phase 1 — Capture the golden image (one-time)
Script: `eval/scripts/take_reddit_checkpoint.sh`

1. `docker stop` + `docker rm` the reddit container, then `docker run` a fresh one
   from the base image (`am1n3e/webarena-verified-reddit`), publishing
   `127.0.0.1:9999→80` and `9998→8877`.
2. **Wait 20 minutes.** This step is critical and non-obvious: Postmill seeds and
   populates its database **asynchronously after boot**. Snapshotting too early
   captures a half-populated site (missing forums/posts), and every task then
   evaluates against incomplete data. Twenty minutes is what full population takes.
3. `docker commit` the running container to the tag **`webarena-reddit-clean:latest`**.

The result is a frozen image of a fully-populated, clean Postmill. (The script
also runs `docker checkpoint create`, but per §2b that artifact is never usable —
it's vestigial; ignore it.)

### Phase 2 — Restore between tasks
Function: `restore_checkpoint()` in **`eval/reddit_state_reset.py`** (shared by the
pytest suite and the ReactAgent runner). Each reset:

1. **Tries CRIU first** (`docker stop` + `docker start --checkpoint clean_state`).
   This always fails on our host and prints "CRIU restore failed" — that message
   is **expected/normal**, not an error. It falls through.
2. **Commit-image restore (the real path):** `docker stop` + `docker rm` the
   container, then `docker run` a brand-new one from `webarena-reddit-clean:latest`.
   Fresh container = fresh clean database. ~**58 seconds**.
3. **Health-poll:** repeatedly `GET http://127.0.0.1:9999/f/books` until it returns
   HTTP 200 with real content (`"article"` in the body), up to a **300-second**
   timeout.
4. **Refresh the session** *(MCP-tool suite only):* if given an `mcp_url`, hit
   `{mcp_url}/session` so the MCP server re-authenticates — the restored DB has no
   record of the old `PHPSESSID`. (The browser-based runner logs in fresh each
   task, so it passes no `mcp_url` and skips this.)

Everything is driven over **SSH** to the host where the container runs; port
`9999` is an SSH tunnel from the laptop to the container.

### Where it's wired in
- **pytest suite** (`test_agent_verified_reddit_program_html.py`): resets **after**
  each write task's evaluation completes — so task N's eval sees task N's changes,
  and we clean up before N+1. Read-only tasks skip it.
- **ReactAgent runner** (`run_webarena_verified.py`): resets after each task where
  the site is reddit and it's a write (`program_html`) task; read-only reddit
  tasks skip it.

---

## 4. Gotchas / lessons learned

1. **Reset time is dominated by Postgres crash-recovery.** `docker rm` kills
   Postgres uncleanly, so every `docker run` boots via WAL crash-recovery of a
   multi-GB database — inherently tens of seconds. You cannot get this to ~2s;
   budget ~1 min per reset (≈1.5 hrs of pure reset overhead across a 95-task run).

2. **Keep the golden image to a single clean database.** We briefly baked a second
   redundant copy of the database into `webarena-reddit-clean:latest`. That
   doubled Postgres recovery to ~98s, which — under host load and a too-tight
   120s health timeout — left Postmill half-started and cascaded HTTP 500s through
   the rest of the run. Fix: keep one clean DB (back to ~58s) and give the health
   check generous headroom (**300s**), since the host is shared and other
   processes steal CPU/IO.

3. **Wait for the site to be *fully* serviceable, not just reachable.** The health
   check must confirm the site renders a real page (200 + content), because a
   still-starting Postmill can accept connections while the login/session path is
   still returning 500s.

---

## 5. Porting checklist
To run or adapt this elsewhere, set these in `eval/reddit_state_reset.py` /
`eval/scripts/take_reddit_checkpoint.sh`:
- SSH target (host where the container runs)
- Container name (`webarena-verified-reddit`)
- Golden image tag (`webarena-reddit-clean:latest`)
- Port maps (`9999→80`, `9998→8877`)

And **run the capture script once** so `webarena-reddit-clean:latest` exists —
no reset will work until the golden image is present.

---

### One-line summary
We don't reset in place. Between write tasks we `docker rm` the reddit container
and `docker run` a fresh one from a pre-baked clean image, then wait for Postmill
to come back up. Fast container snapshots (`docker checkpoint`/CRIU) can't be used
because they can't dump a live Postgres or roll back its on-disk data — so the
full container recreate is the only correct reset.
