# `run_claude_task.sh` — design overview

This is the **codegen driver** for the Claude Code agent back-end. Its whole job
is to turn one `(site, task_id)` pair into a generated, locally-validated Python
implementation script sitting at `generated/<site>/task_<id>/task_<id>.py`.

It deliberately does **not** solve the task. It orchestrates a single `claude`
CLI session that *writes code*, and hands the result off. The live, state-changing
run happens later, elsewhere (`agent/baya_code_agent/execution_agent.py`). Holding
that line — "generate and mock-validate here, execute there" — is the design
decision everything else serves.

Read this for the *why*. The script itself is heavily commented for the *how*.

---

## The core idea: reasoning offline, then running once

A conventional web agent interleaves thinking and acting: it reads a page, clicks,
reads the result, clicks again. That couples every decision to a live, mutable
site. This driver breaks the coupling. The session it launches is **sandboxed and
forbidden from touching the site at all** — not even a read-only probe to confirm
an endpoint exists. It has only the API schema, the site-specific hints, and its
own reasoning to work from.

That constraint is the point:

- The graded artifact becomes a plain, inspectable `.py` file — diffable,
  re-runnable, and debuggable from a stack trace rather than a lost trajectory.
- The model can't mutate state while "checking its work," because it has no way
  to reach the site. The one real run is deliberate and happens under the eval
  harness's control.
- The session is free to be wrong on paper; a wrong script fails loudly at
  execution time and can be *repaired* (see **Replan**), rather than silently
  corrupting a live database mid-reasoning.

---

## The prompt is split in two, for caching

The instructions handed to the session come in two halves, and the split is an
economic decision, not an organizational one.

- **The invariant half** — API hints, the authenticated identity, the full
  endpoint catalog, auth and base-URL contracts, the write boundary. This is
  built by `build_site_prompt.sh` and delivered as the CLI's *system prompt*. It
  is **byte-identical for every task on a given site**, by construction.
- **The per-task half** — the task text, the exact filenames, the FINAL ANSWER
  contract, the logging rules, and (on a retry) the replan section. This is the
  `-p` argument and is the only thing that changes per task.

Anthropic prompt caching keys on an exact prefix. Keeping the large, invariant
half stable means every run after the first *reads back* that prefix instead of
re-paying for it (measured: ~26k tokens re-created vs. read on a cold prefix).
This is why the script goes to real lengths — stable file names, excluding
volatile `cwd`/`env`/git-status from the system prompt — to avoid perturbing that
prefix. Anything task-specific that leaked into it would cold-start the cache
every run.

---

## Slots: how many tasks share one workspace safely

The session needs a working directory to write into, and two forces pull against
each other:

- The cache wants a **stable** directory name (a task-named settings file would
  cold-start the prefix every run — see above).
- Many tasks for the same site run **concurrently**, so a single shared directory
  would have one session overwriting another's files.

The resolution is a small **pool of stable-named slots**. A run claims slot *N*
for its duration using a kernel file lock (so the slot frees automatically if the
process dies, with nothing to clean up), works entirely inside that slot, and each
slot carries its own settings file and thus its own cache line. Sessions in
different slots can't read each other's directories.

When the session finishes, its output is **copied out** of the slot into the
permanent task directory, and the slot is emptied for the next claimant. The task
directory is the only place any later stage looks. A leftover, non-empty slot
means a previous run was killed before copy-out (usually a wall-clock timeout);
those files are *archived, not deleted*, so a timeout stays inspectable.

---

## Replan: repair, don't restart

The first time a task runs, the session writes a script from scratch. But scripts
fail against the live site — a wrong filter, a stale endpoint, a missing field.
When that happens, the next attempt is **not** a fresh start. The driver stages
the prior attempt's script, its tests, and its two logs back into the slot and
reframes the whole session: *this already exists and it failed; read the evidence
and repair it.*

The design commitments here:

- **Work from evidence, not guesses.** The failing run's own log is the primary
  input. The session is told to find the specific line that failed and explain the
  root cause before editing — not to paper over it with a broad `try/except` or a
  blind retry.
- **Fix the tests too.** If the tests passed while the real run failed, their
  mocks encode the same wrong assumption; leaving them would keep certifying the
  bug.
- **A timeout is diagnosed differently from an error.** A timeout almost always
  means the script scoped too broadly (listing a whole collection, unbounded
  paging). The fix is to *narrow the work* — push filters server-side, use
  id-specific endpoints — never to raise the timeout or add retries.

### The final-attempt reversal

One rule flips on the last attempt, and it's the subtlest decision in the script.

While retries remain, an empty result is treated as *probably a bug*. Giving up
early is expensive: printing the FINAL ANSWER markers exits zero, which the harness
reads as "answered" and stops retrying. So the guidance stays hedged — keep
diagnosing.

On the final attempt that trade reverses. Nothing runs afterward, so exiting
non-zero submits *nothing* and scores zero regardless. So a genuine absence — a
search returning no items, a 404 on a specific id, an "none of these"
classification — becomes a legitimate **answer**: report the natural negative
("no", "0", "none") and exit zero. It's still required to *log* the diagnostic
first, so a real negative stays distinguishable from a broken run when reviewed
later. Broken-run signals (401/403, 429, 5xx, timeouts, unparseable bodies) are
never absence, and still fail hard even here.

---

## What actually counts as the answer

The session writes a lot — code, tests, logs, prose. Almost none of it is graded.
The contract the driver imposes is narrow and strict:

- Running the script with **no arguments** must carry out the whole task. No
  `--execute`/`--dry-run` flags that let the default run skip the real work.
- The script's last output on success is a `FINAL ANSWER` marker block, and only
  the text inside it is submitted verbatim.
- The block is printed **only** on genuine success (or, per the final-attempt
  rule, a genuine absence). Any caught error exits non-zero *without* the block.

This is what lets the executor cleanly separate a real answer from a broken run,
and it's mirrored in the answer-content rules the prompt spells out (report
values not descriptions, quote verbatim, bound the output, a "view X" task
answers with the page URL, only a state change gets a confirmation).

---

## Logging is written for a machine to re-read

The script's run log (`python_output.log`) isn't just for humans — it's the
primary evidence a *future replan session* reads. That reframes what may go into
it. Because the log is fed straight back into a model, it must contain **no
untrusted data**: nothing fetched from a successful site response — no field
values, names, review text, usernames — ever gets logged. Only operational
feedback does: progress, request method and path, status codes, response *shape*
(counts and key names, never values), and full error text.

The one exception is the body of an *error* (non-2xx) response — that's the API's
own message about why the call failed, which is trusted operational feedback and
must be surfaced so the next attempt can act on it. Site content is confined to
exactly one place: the FINAL ANSWER block.

---

## Everything the session did is captured, then cleaned up

Two more design intentions run through the script:

- **Full accountability.** Each run appends to append-only records — the session's
  own closing report (`claude_output.log`), and a per-run JSON line of token
  usage, cost, turn count, and error signals (`claude_result.jsonl`). The full
  session transcript (every turn, tool call, and result) is archived into the task
  directory too. The session is given a *known id up front*, so its transcript can
  always be located — even if the run crashed before emitting result JSON, and
  even with many concurrent slots sharing one transcript directory.
- **Net-zero footprint.** Enabling transcript persistence writes into the user's
  real `~/.claude`; the driver copies the transcript into the task dir and then
  deletes the source, scoped to exactly this run's id so it can never touch an
  interactive session or another slot. Eval runs leave nothing behind outside
  `generated/`.

A subtler correctness signal lives here too: a **zero exit code is not trusted on
its own**. An interrupted stream can leave the driver exiting 0 while the session
wrote nothing. So the real success signal is `is_error` in the result JSON, not
the exit code or the (misleadingly cheerful) `subtype` field — which is what stops
a replan from burning an attempt on an unchanged script.

Finally, auth is pinned: any ambient API keys are unset before launch so the run
always bills the logged-in Claude subscription rather than silently charging the
API.

---

## In one paragraph

`run_claude_task.sh` exists to make code generation **cacheable, concurrent,
repairable, and accountable** while keeping the model strictly away from the live
site. The invariant/per-task prompt split makes it cheap; the slot pool makes it
parallel; the replan flow (with its final-attempt reversal) makes it resilient;
the FINAL ANSWER and logging contracts make its output gradeable and its failures
diagnosable; and the transcript archival makes every run reconstructable after the
fact. The script is where all of those policies are enforced — the generated
`task_<id>.py` is just their output.
