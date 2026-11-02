#!/usr/bin/env bash
# Runs COMMAND in an OS-level sandbox for a Codex CLI codegen session.
#
#   codex_sandbox.sh <site> <out_dir> <codex_dir> -- COMMAND [ARG...]
#
# WHY THIS EXISTS
#
# run_claude_task.sh hands the Claude CLI a --settings file that denies network
# access, hides the repo's secrets, and confines writes to the slot directory.
# The Codex CLI has its own --sandbox, but the model can request a bypass per
# command -- and under `codex exec --dangerously-bypass-approvals-and-sandbox`
# (which the headless session needs, so tool calls run without an approval
# prompt) that request is honoured. So the same policy has to be enforced from
# OUTSIDE the process. That is this script, and a bypass inside Codex changes
# nothing because the boundary is out here.
#
# This is a direct copy of agent/baya_code_agent/sandbox/antigravity_sandbox.sh -- the security
# mechanism is CLI-agnostic. The ONLY functional difference is the per-slot
# state dir bind: Codex keeps its state under ~/.codex (its CODEX_HOME), where
# agy uses ~/.gemini. Everything else -- the network isolation, the filesystem
# allow-list, the secret hiding, the environment emptying -- is identical.
#
# The policy it enforces is deliberately close to the one
# agent/baya_code_agent/sandbox/claude_settings/<site>_settings.sh expresses for the Claude path:
# readable = a short allow-list of repo files; writable = the task's output dir
# passed as OUT_DIR (and the session's own state dir); network = the model API and
# nothing else. Like the antigravity path, Codex writes into that output dir
# DIRECTLY -- there is no staging slot and nothing is copied out afterwards.
#
# HOW THE NETWORK HALF WORKS
#
# Every WebArena site is on host loopback -- config/servers.py has
# gitlab 127.0.0.1:8023, shopping localhost:7770, shopping_admin localhost:7780,
# *_extra 7790-7793, and multi-docker rewrites those to localhost:200xx. A
# private network namespace has its OWN loopback, so entering one blocks every
# site for free, without needing to enumerate them. slirp4netns then puts public
# internet back (Codex cannot reach the model API otherwise) -- and
# --disable-host-loopback is what stops it also putting the host's loopback back.
#
# An /etc/hosts override would not work here: seven of the nine URLs are raw
# 127.0.0.1, so there is no hostname to poison.
#
# LAYER ORDER IS LOAD-BEARING
#
#   1. unshare --user --map-root-user --net --mount
#   2. slirp4netns (from the parent, targeting the unshared child)
#   3. tmpfs on /run          -- iptables needs a writable /run/xtables.lock
#   4. iptables               -- must be HERE, not under bwrap: bwrap 0.4.0 drops
#                                the caps iptables needs and its --cap-add only
#                                works when already privileged. Rules set here
#                                persist for the life of the namespace.
#   5. bwrap                  -- the filesystem allow-list
#
# Steps 3-5 run in stage 2 (this script re-executing itself inside the
# namespaces). nsenter cannot be used to bolt them on from outside: it fails
# with "setgroups failed" in a --map-root-user userns because this host has no
# newuidmap/newgidmap.
set -euo pipefail

log() {
  echo "[codex_sandbox] $*" >&2
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"

# slirp4netns's default CIDR. The gateway doubles as an alias for the host, and
# .3 is its DNS forwarder -- the host's own resolver is systemd-resolved on
# 127.0.0.53, which is unreachable from inside a private netns.
SLIRP_CIDR="10.0.2.0/24"
SLIRP_GATEWAY="10.0.2.2"
SLIRP_DNS="10.0.2.3"

# This host publishes the site containers on 0.0.0.0, so its own public address
# reaches them from inside the netns even with host loopback disabled. That is
# the one real hole in the design and rule 4 below is what closes it.
HOST_LAN_SUBNET="128.32.112.0/24"

usage() {
  echo "Usage: $0 <site> <out_dir> <codex_dir> -- COMMAND [ARG...]" >&2
  echo "Example: $0 shopping /path/generated/shopping/task_281 /path/slot0/.codex -- codex --version" >&2
  echo "  out_dir is the single writable path; Codex writes its output there directly." >&2
  echo "  codex_dir is the per-slot CODEX_HOME, bound at ~/.codex inside the sandbox." >&2
  exit 2
}

# --- Argument parsing (both stages see the same argv) -----------------------
[[ $# -ge 5 ]] || usage

SITE="$1"
OUT_DIR="$2"
CODEX_DIR="$3"
shift 3
[[ "$1" == "--" ]] || usage
shift
[[ $# -ge 1 ]] || usage

if [[ ! "$SITE" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "Error: invalid site name: $SITE" >&2
  exit 2
fi
if [[ "$OUT_DIR" != /* || "$CODEX_DIR" != /* ]]; then
  echo "Error: out_dir and codex_dir must be absolute paths" >&2
  exit 2
fi

# HOME inside the sandbox. Made a tmpfs, with only the per-slot state dir bound
# into it, so ~/.ssh and the canonical ~/.codex simply are not there. The repo
# lives under this path too, hence the binds below have to come after the tmpfs;
# bwrap applies operations in order and creates missing parent directories.
SANDBOX_HOME="/home/annabella"

##############################################################################
# STAGE 2 -- inside the user, network and mount namespaces
##############################################################################
if [[ "${_PTE_SANDBOX_STAGE:-1}" == "2" ]]; then
  # Tell the parent we are in the namespaces, then wait for it to finish
  # attaching slirp4netns. Without this handshake the parent can race ahead and
  # configure a namespace this process has not entered yet.
  echo "in-ns" > "$_PTE_SANDBOX_NS_FIFO"
  read -r _ < "$_PTE_SANDBOX_GO_FIFO"

  # iptables wants a lock at /run/xtables.lock; the host's /run is not writable
  # by this (namespace-only) root. Private mount propagation is unshare's
  # default, so this is invisible outside.
  mount -t tmpfs tmpfs /run

  # REJECT is preferred over DROP so a violation fails fast and loudly rather
  # than hanging until a timeout, but it needs the xt_REJECT module.
  REJECT_TARGET="REJECT"
  if ! iptables -A OUTPUT -d 127.0.0.1 -j REJECT 2>/dev/null; then
    REJECT_TARGET="DROP"
    log "WARNING: REJECT unavailable, falling back to DROP"
  else
    iptables -D OUTPUT -d 127.0.0.1 -j REJECT
  fi

  # Order matters: slirp's own subnet is accepted before the RFC1918 rejects,
  # or the DNS forwarder and gateway would be caught by 10.0.0.0/8.
  iptables -A OUTPUT -d "$SLIRP_CIDR" -j ACCEPT
  iptables -A OUTPUT -o lo -j "$REJECT_TARGET"
  iptables -A OUTPUT -d "$SLIRP_GATEWAY" -j "$REJECT_TARGET"
  iptables -A OUTPUT -d "$HOST_LAN_SUBNET" -j "$REJECT_TARGET"
  iptables -A OUTPUT -d 10.0.0.0/8 -j "$REJECT_TARGET"
  iptables -A OUTPUT -d 172.16.0.0/12 -j "$REJECT_TARGET"
  iptables -A OUTPUT -d 192.168.0.0/16 -j "$REJECT_TARGET"

  # A sandbox that silently failed to install its firewall is worse than no
  # sandbox, because the run looks clean. Refuse to continue.
  for check in \
    "-d $HOST_LAN_SUBNET -j $REJECT_TARGET" \
    "-o lo -j $REJECT_TARGET" \
    "-d 10.0.0.0/8 -j $REJECT_TARGET"
  do
    # shellcheck disable=SC2086
    if ! iptables -C OUTPUT $check 2>/dev/null; then
      echo "Error: firewall rule failed to apply: OUTPUT $check" >&2
      exit 3
    fi
  done

  # --- Filesystem allow-list ------------------------------------------------
  # /usr read-only covers /lib, /lib64, /bin and /sbin, which are usrmerge
  # symlinks into it and are recreated as such below.
  BWRAP_ARGS=(
    --ro-bind /usr /usr
    --symlink usr/lib /lib
    --symlink usr/lib64 /lib64
    --symlink usr/bin /bin
    --symlink usr/sbin /sbin
    --proc /proc
    --dev /dev
    --tmpfs /dev/shm
    --tmpfs /tmp
    --tmpfs /run
    # /etc is an allow-list rather than a wholesale bind, for two reasons: it
    # keeps the policy explicit, and the host's /etc/resolv.conf is a symlink
    # into /run/systemd/resolve/, which is a tmpfs in here -- binding /etc
    # whole leaves that symlink dangling and bwrap cannot then write over it.
    --tmpfs /etc
  )

  # --ro-bind-try tolerates absence, so this list stays valid across hosts.
  for f in \
    /etc/ssl /etc/ca-certificates /etc/ca-certificates.conf \
    /etc/nsswitch.conf /etc/gai.conf /etc/host.conf \
    /etc/passwd /etc/group \
    /etc/hosts /etc/hostname \
    /etc/protocols /etc/services \
    /etc/localtime
  do
    BWRAP_ARGS+=( --ro-bind-try "$f" "$f" )
  done

  # The host resolver is systemd-resolved on 127.0.0.53, which does not exist in
  # a private netns. Point at slirp's forwarder instead.
  exec {RESOLV_FD}< <(printf 'nameserver %s\n' "$SLIRP_DNS")
  BWRAP_ARGS+=( --ro-bind-data "$RESOLV_FD" /etc/resolv.conf )

  # HOME as a tmpfs is what hides ~/.ssh and the canonical ~/.codex. The
  # per-slot state dir is bound at ~/.codex, which is Codex's default CODEX_HOME,
  # so the session reads the per-slot config/auth rather than the real one -- and
  # CODEX_HOME is also set explicitly below so a future default change cannot
  # divert it back to the hidden real home.
  BWRAP_ARGS+=(
    --tmpfs "$SANDBOX_HOME"
    --bind "$CODEX_DIR" "$SANDBOX_HOME/.codex"
  )

  # The command's own binary, bound at its real path. It usually lives under
  # ~/.local/bin, which the tmpfs above has just hidden -- and since the
  # environment is emptied below, PATH cannot be relied on to find it either, so
  # callers pass an absolute path and it is bound here.
  #
  # Codex ships as a symlink (~/.local/bin/codex -> ~/.codex/packages/.../bin/codex)
  # alongside sibling helper binaries in that same real bin dir -- notably
  # codex-code-mode-host, which the "code-mode" tool loop spawns by looking NEXT TO
  # its own argv[0] (~/.local/bin/codex-code-mode-host). That path is hidden by the
  # HOME tmpfs unless bound.
  #
  # Binding it is a DOUBLE-EDGED move: gpt-5.x models run in code-mode and fail with
  # "workspace execution host is unavailable" if the host is absent -- but binding it
  # also makes Codex OFFER the interactive exec_command path to plain-shell models
  # (e.g. deepseek), which then try to write to a closed stdin and hang until the
  # wall-clock timeout. So the helpers are bound ONLY when the caller opts in via
  # PTE_CODEX_BIND_HELPERS=1 (the runner sets it for code-mode models only). They are
  # statically linked, so the binary alone is enough; --ro-bind resolves the symlink.
  if [[ -f "$1" ]]; then
    BWRAP_ARGS+=( --ro-bind "$1" "$1" )
    if [[ "${PTE_CODEX_BIND_HELPERS:-0}" == "1" ]]; then
      _real_bindir="$(dirname "$(readlink -f "$1")")"
      _invoked_dir="$(dirname "$1")"
      for _helper in "$_real_bindir"/*; do
        [[ -f "$_helper" ]] || continue
        _hpath="$_invoked_dir/$(basename "$_helper")"
        [[ "$_hpath" == "$1" ]] && continue   # codex itself, already bound above
        BWRAP_ARGS+=( --ro-bind "$_helper" "$_hpath" )
      done
    fi
  fi

  # The one writable place in the repo: the task's own output dir, bound at its
  # real path. bwrap creates the read-only tmpfs parents (generated/<site>/) under
  # it and --remount-ro / below leaves this leaf bind read-write -- so Codex writes
  # task_<id>.py straight here, and the engine's own mkdir on this already-existing
  # dir is a no-op instead of the EROFS that killed staged runs.
  BWRAP_ARGS+=( --bind "$OUT_DIR" "$OUT_DIR" )

  # Read-only repo allow-list, mirroring permissions.allow in the Claude
  # settings generators. The venv is built with
  # include-system-site-packages = false over the system 3.12, so the system
  # stdlib has to come along or nothing imports.
  RO_REPO_PATHS=(
    "$ROOT/venv"
    "/usr/lib/python3.12"
    "$ROOT/tools"
    "$ROOT/api/schemas"
    "$ROOT/config/config.yaml"
    # config/servers.py defines SERVER_URLS and config/__init__.py makes `config`
    # an importable package. refresh_<site>_tokens.py runs `from config.servers
    # import SERVER_URLS` at IMPORT time to resolve the site base URL, so without
    # both files bound the import raises ModuleNotFoundError and the session
    # dead-ends hunting the filesystem for a servers.py the allow-list had hidden.
    # The Claude path exposes both (agent/baya_code_agent/sandbox/claude_settings/<site>_settings.sh
    # allowRead); mirror that here.
    "$ROOT/config/servers.py"
    "$ROOT/config/__init__.py"
    "$ROOT/agent/common/configurator.py"
    "$ROOT/agent/providers"
    "$ROOT/agent/baya_code_agent/codegen/query_schema.py"
    "$ROOT/agent/baya_code_agent/codegen/schema_lib.py"
  )
  # Only this site's token module, matching the per-site allow-list. reddit is
  # deliberately excluded: its refresh module is refresh_reddit_session.py, which
  # this glob does not match, so nothing is bound -- and that is correct. The
  # reddit API server logs itself in (api/servers/reddit.py), so a generated
  # reddit script needs no credentials, and leaving refresh_reddit_session.py
  # unreachable makes a session that reaches for it fail loudly (mirroring the
  # note in agent/baya_code_agent/sandbox/claude_settings/reddit_settings.sh).
  for tok in "$ROOT"/config/init_tokens/refresh_"${SITE}"_token*.py; do
    [[ -e "$tok" ]] && RO_REPO_PATHS+=( "$tok" )
  done

  for p in "${RO_REPO_PATHS[@]}"; do
    if [[ -e "$p" ]]; then
      BWRAP_ARGS+=( --ro-bind "$p" "$p" )
    else
      log "WARNING: allow-listed path missing, not bound: $p"
    fi
  done

  # The workspace rules file (the invariant site prompt), bound at the repo root
  # because that is the working directory and Codex discovers AGENTS.md by walking
  # up from there. Its source is passed in PTE_AGENTS_MD and lives OUTSIDE OUT_DIR
  # -- deliberately, because OUT_DIR is now the task dir the session writes into
  # and is not copied anywhere afterwards, so an AGENTS.md staged inside it would
  # linger in the results. Read-only here so the session cannot rewrite its own
  # instructions; env -i on the exec below drops PTE_AGENTS_MD so the child never
  # sees it.
  if [[ -n "${PTE_AGENTS_MD:-}" && -f "$PTE_AGENTS_MD" ]]; then
    BWRAP_ARGS+=( --ro-bind "$PTE_AGENTS_MD" "$ROOT/AGENTS.md" )
  fi

  # Deliberately absent, and therefore unreachable: $ROOT/config/.env,
  # $ROOT/config/.server_env, $ROOT/.git, $ROOT/tests, every other slot under
  # $ROOT/generated, ~/.ssh, ~/.aws, ~/.netrc, and the canonical ~/.codex.

  # Lock the scaffolding. bwrap creates the intermediate directories for every
  # bind on the tmpfs it owns -- so without this, $ROOT itself is a writable
  # tmpfs directory. Writes there would vanish with the sandbox, which is worse
  # than failing: a session could "write" task_<id>.py to the wrong place, see
  # it succeed, and leave copy_out with nothing to copy. Not recursive, so the
  # bind mounts underneath keep their own read-write flags.
  BWRAP_ARGS+=(
    --remount-ro "$SANDBOX_HOME"
    --remount-ro /etc
    --remount-ro /
  )

  # --clearenv, then name everything back. The parent environment is not safe to
  # forward: whatever launched this may hold site tokens, API keys or harness
  # variables, and all of it would otherwise be visible to the codegen session.
  # Observed on the antigravity path: a PTE_* variable set for a test run showed
  # up in the session, which then spent steps trying to read the path it named.
  #
  # The PYTHON* and PYTEST_ADDOPTS values below lived in the settings file's
  # "env" block on the Claude path and were lost with it. Without
  # PYTEST_ADDOPTS, pytest writes $ROOT/.pytest_cache and breaches the write
  # boundary the whole design rests on.
  # (bwrap 0.4.0 has no --clearenv, so the emptying is done with `env -i` on the
  # exec below and everything needed is named back here.)
  BWRAP_ARGS+=(
    --setenv PATH "/usr/local/bin:/usr/bin:/bin"
    --setenv LANG "${LANG:-C.UTF-8}"
    --setenv TERM "dumb"
    --setenv USER "annabella"
    --setenv HOME "$SANDBOX_HOME"
    # Point Codex at the per-slot state dir explicitly. It equals the default
    # (~/.codex) that the --bind above lands on, but naming it means a future
    # change to Codex's default cannot silently divert it to the hidden real home.
    --setenv CODEX_HOME "$SANDBOX_HOME/.codex"
    --setenv PYTEST_ADDOPTS "-p no:cacheprovider"
    --setenv PYTHONDONTWRITEBYTECODE "1"
    # Kept OUT of OUT_DIR (the task dir): with no copy-out to strip it, a
    # __pycache__ written here would be left behind in the results. /tmp is a
    # tmpfs inside the sandbox, so any bytecode is discarded with the session.
    --setenv PYTHONPYCACHEPREFIX "/tmp/__pycache__"
    # The repo root, matching the Claude path: every relative path in the prompt
    # corpus ("./venv/bin/python", "agent/baya_code_agent/codegen/query_schema.py") is written for it.
    --chdir "$ROOT"
    --unshare-pid
    --unshare-ipc
    --unshare-uts
    --die-with-parent
    --new-session
  )
  # Note: NOT --unshare-net. The netns from stage 1 is the one we want; a fresh
  # one here would have no connectivity at all.

  # env -i is what actually empties the environment; the --setenv args above then
  # put back the only variables the session is allowed to see.
  exec env -i bwrap "${BWRAP_ARGS[@]}" -- "$@"
fi

##############################################################################
# STAGE 1 -- the outer process
##############################################################################
for tool in unshare slirp4netns bwrap; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Error: required sandbox tool not found: $tool" >&2
    exit 2
  fi
done

if [[ ! -d "$OUT_DIR" ]]; then
  echo "Error: out dir does not exist: $OUT_DIR" >&2
  exit 2
fi
if [[ ! -d "$CODEX_DIR" ]]; then
  echo "Error: codex state dir does not exist: $CODEX_DIR" >&2
  exit 2
fi

if [[ "$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || echo 0)" != "1" ]]; then
  echo "Error: unprivileged user namespaces are disabled on this host." >&2
  echo "  /proc/sys/kernel/unprivileged_userns_clone must be 1" >&2
  exit 2
fi

HANDSHAKE_DIR="$(mktemp -d -t pte-sandbox.XXXXXXXX)"
NS_FIFO="$HANDSHAKE_DIR/ns"
GO_FIFO="$HANDSHAKE_DIR/go"
READY_FIFO="$HANDSHAKE_DIR/ready"
mkfifo "$NS_FIFO" "$GO_FIFO" "$READY_FIFO"

SLIRP_PID=""
CHILD_PID=""
cleanup() {
  if [[ -n "$SLIRP_PID" ]] && kill -0 "$SLIRP_PID" 2>/dev/null; then
    kill "$SLIRP_PID" 2>/dev/null || true
  fi
  rm -rf "$HANDSHAKE_DIR"
}
trap cleanup EXIT

# unshare execs the target in place rather than forking, so $! really is the
# process sitting in the new namespaces -- which is the pid slirp4netns needs.
_PTE_SANDBOX_STAGE=2 \
_PTE_SANDBOX_NS_FIFO="$NS_FIFO" \
_PTE_SANDBOX_GO_FIFO="$GO_FIFO" \
  unshare --user --map-root-user --net --mount "$0" "$SITE" "$OUT_DIR" "$CODEX_DIR" -- "$@" &
CHILD_PID=$!

# Blocks until stage 2 confirms it is inside the namespaces.
if ! read -r _ < "$NS_FIFO"; then
  echo "Error: sandbox child never entered its namespaces" >&2
  exit 3
fi

SLIRP_LOG="$HANDSHAKE_DIR/slirp.log"
slirp4netns \
  --configure \
  --disable-host-loopback \
  --netns-type=pid \
  --userns-path="/proc/$CHILD_PID/ns/user" \
  --ready-fd=3 \
  "$CHILD_PID" tap0 3>"$READY_FIFO" >"$SLIRP_LOG" 2>&1 &
SLIRP_PID=$!

# slirp4netns signals readiness by writing a single byte "1" and closing the fd.
# `read` would report that as failure -- it returns non-zero on EOF without a
# newline, having read the byte anyway -- so take the byte directly.
READY_BYTE="$(timeout 30 head -c 1 < "$READY_FIFO" || true)"
if [[ "$READY_BYTE" != "1" ]]; then
  echo "Error: slirp4netns did not become ready within 30s" >&2
  if [[ -s "$SLIRP_LOG" ]]; then
    echo "  slirp4netns said:" >&2
    sed 's/^/    /' "$SLIRP_LOG" >&2
  fi
  exit 3
fi

# Releases stage 2 to install the firewall and enter bwrap.
echo "go" > "$GO_FIFO"

set +e
wait "$CHILD_PID"
CHILD_RC=$?
set -e

exit "$CHILD_RC"
