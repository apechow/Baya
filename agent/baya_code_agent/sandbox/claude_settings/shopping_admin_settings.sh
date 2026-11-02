#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 3 ]] || {
  echo "Usage: $0 <root> <slot_relative_dir> <settings_file>" >&2
  exit 2
}

ROOT="$1"
# The stable per-slot staging directory the session works in, NOT the task
# directory. Every rule below is keyed to it, which is what lets this file stay
# byte-identical across tasks -- and a settings file that changes per task is a
# cold prompt-cache prefix on every run. run_claude_task.sh copies the results
# out to generated/<site>/task_<id>/ afterwards.
SLOT_REL_DIR="$2"
SETTINGS_FILE="$3"

# --- Resolve ROOT to an absolute, symlink-free path -------------------------
# Every rule below is absolutely anchored, so the settings file behaves the
# same no matter which directory claude is launched from.
ROOT="$(cd "$ROOT" && pwd -P)"

SLOT_DIR="${ROOT}/${SLOT_REL_DIR}"
SLOT_NAME="${SLOT_REL_DIR##*/}"

# The "//" anchor in Read/Edit rules REPLACES the leading slash:
# /home/me/x  ->  Read(//home/me/x).  Strip it here so we never emit "///".
ROOT_A="${ROOT#/}"
SLOT_A="${SLOT_DIR#/}"
[[ -d "$SLOT_DIR" ]] || { echo "No such slot dir: $SLOT_DIR" >&2; exit 2; }

# Refuse characters that would break JSON or gitignore-pattern matching.
if [[ "$ROOT$SLOT_REL_DIR" == *['"\\*?[]']* ]]; then
  echo "Path contains characters that need escaping in permission rules" >&2
  exit 2
fi

mkdir -p "$(dirname "$SETTINGS_FILE")"

# The implementation script must land flat at ${SLOT_DIR}/task_<id>.py. If the
# session creates a subdirectory and writes there instead, run_claude_task.sh
# reports "codegen produced no usable implementation script" and the task is
# scored as a failure even though the code was fine. Both routes are denied
# below: Bash mkdir, and Write/Edit into any subdirectory of the slot dir --
# blocking mkdir alone is not enough, because Write creates parent dirs itself.
# run_claude_task.sh already creates $SLOT_DIR, so nothing here needs mkdir.

# --- Build the read-deny list ----------------------------------------------
# Sandbox reads default to ALLOWED: the engine mounts "--ro-bind / /" and only
# masks what denyRead names, so anything unnamed here stays readable to the
# task's python/pytest process -- allowRead alone does NOT create an allowlist,
# it only re-exposes paths inside an already-masked region.
#
# Naming "/" is the engine's built-in way to flip reads to allowlist-only, but
# it expands to every entry in / and mounts a tmpfs over each, which aborts
# bwrap at startup on this host (/data points at an NFS mount we cannot
# traverse). Enumerate instead, and skip what bwrap cannot mount over.
#
# Symlinks are skipped on purpose: a tmpfs over /bin would mask /usr/bin rather
# than the link, and masking the directory each link resolves into (/usr,
# /data3, /scr1) already covers them.
DENY_READ_ENTRIES=()
for entry in /*; do
  case "${entry##*/}" in proc|dev|sys) continue ;; esac  # engine exempts these
  case "$entry" in *'"'*|*'\'*) continue ;; esac         # would break the JSON
  [[ -L "$entry" ]] && continue
  [[ -d "$entry" ]] || continue                          # /swap.img needs no mask
  ls "$entry" >/dev/null 2>&1 || continue                # unreachable -> bwrap aborts
  DENY_READ_ENTRIES+=("$entry")
done
[[ ${#DENY_READ_ENTRIES[@]} -gt 0 ]] || {
  echo "Refusing to emit an empty denyRead list" >&2; exit 1; }

DENY_READ_JSON=""; _sep=""
for entry in "${DENY_READ_ENTRIES[@]}"; do
  DENY_READ_JSON+="${_sep}        \"${entry}\""
  _sep=$',\n'
done

cat > "$SETTINGS_FILE" <<EOF
{
  "\$schema": "https://json.schemastore.org/claude-code-settings.json",

  "permissions": {
    "defaultMode": "dontAsk",

    "additionalDirectories": [],

    "allow": [
      "Read(//${ROOT_A}/api/schemas/shopping_admin_api_schema.json)",
      "Read(//${ROOT_A}/api/schemas/shopping_admin_extra_api_schema.json)",
      "Read(//${ROOT_A}/config/init_tokens/refresh_shopping_admin_tokens.py)",
      "Read(//${ROOT_A}/config/config.yaml)",
      "Read(//${ROOT_A}/agent/common/configurator.py)",
      "Read(//${ROOT_A}/agent/providers/*.py)",
      "Read(//${ROOT_A}/tools/**)",

      "Read(//${SLOT_A}/**)",
      "Edit(//${SLOT_A}/**)",

      "Bash(chmod +x ${SLOT_DIR}/*.py)",

      "Bash(${ROOT}/venv/bin/python -m py_compile ${SLOT_DIR}/*)",
      "Bash(${ROOT}/venv/bin/python -m pytest ${SLOT_DIR}/test_task_*.py*)",
      "Bash(${ROOT}/venv/bin/python -m pytest ${SLOT_DIR}/*)",
      "Bash(${ROOT}/venv/bin/python -m unittest ${SLOT_DIR}/test_task_*.py*)",
      "Bash(touch ${SLOT_DIR}/*)",

      "Bash(${ROOT}/venv/bin/python ${ROOT}/agent/baya_code_agent/codegen/query_schema.py *)",
      "Bash(./venv/bin/python agent/baya_code_agent/codegen/query_schema.py *)",
      "Read(//${ROOT_A}/agent/baya_code_agent/codegen/query_schema.py)",
      "Read(//${ROOT_A}/agent/baya_code_agent/codegen/schema_lib.py)"
    ],

    "deny": [
      "Read(**/.env)",
      "Read(**/.env.*)",
      "Read(**/.server_env)",
      "Read(**/.server_env.*)",
      "Read(**/CLAUDE.md)",
      "Read(**/CLAUDE.local.md)",
      "Read(//${ROOT_A}/.claude/**)",
      "Read(//${ROOT_A}/.git/**)",
      "Read(//${ROOT_A}/tests/**)",
      "Read(//${ROOT_A}/generated/.claude-settings/**)",
      "Read(~/.ssh/**)",
      "Read(~/.aws/**)",
      "Read(~/.config/gh/**)",
      "Read(~/.netrc)",
      "Read(~/.gnupg/**)",

      "Edit(//${ROOT_A}/.claude/**)",
      "Edit(//${ROOT_A}/.git/**)",
      "Edit(//${ROOT_A}/tests/**)",
      "Edit(//${ROOT_A}/config/**)",
      "Edit(//${ROOT_A}/api/**)",
      "Edit(//${ROOT_A}/agent/**)",
      "Edit(//${ROOT_A}/tools/**)",
      "Edit(//${ROOT_A}/venv/**)",

      "Edit(//${SLOT_A}/*/*/**)",
      "Bash(mkdir)",
      "Bash(mkdir *)",
      "Bash(install -d *)",

      "WebFetch",
      "WebSearch",
      "Bash(curl *)",
      "Bash(wget *)",
      "Bash(http *)",
      "Bash(httpie *)",
      "Bash(nc *)",
      "Bash(ssh *)",
      "Bash(scp *)",
      "Bash(git *)",
      "Bash(find *)",
      "Bash(env)",
      "Bash(env *)",
      "Bash(printenv)",
      "Bash(printenv *)",
      "Bash(set)",
      "Bash(export *)",
      "Bash(rm *)",
      "Bash(mv *)",
      "Bash(cp *)",
      "Bash(pip *)",
      "Bash(pip3 *)",
      "Bash(* -m pip *)",
      "Bash(* -c *)",
      "Bash(sudo *)",
      "Bash(chmod * ${ROOT}/config/*)",

      "Agent(Explore)"
    ]
  },

  "env": {
    "PYTEST_ADDOPTS": "-p no:cacheprovider",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONPYCACHEPREFIX": "${SLOT_DIR}/__pycache__",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"
  },

  "autoMemoryEnabled": false,
  "autoMemoryDirectory": "${ROOT}/generated/.claude-memory/${SLOT_NAME}",

  "claudeMdExcludes": [
    "**/CLAUDE.md",
    "**/CLAUDE.local.md",
    "**/.claude/CLAUDE.md",
    "**/.claude/rules/**",
    "**/AGENTS.md"
  ],

  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "allowUnsandboxedCommands": false,
    "autoAllowBashIfSandboxed": true,

    "filesystem": {
      "denyRead": [
${DENY_READ_JSON}
      ],
      "allowRead": [
        "/usr",
        "/etc",
        "${SLOT_DIR}",
        "${ROOT}/api/schemas",
        "${ROOT}/config/config.yaml",
        "${ROOT}/config/__init__.py",
        "${ROOT}/config/servers.py",
        "${ROOT}/config/init_tokens/refresh_shopping_admin_tokens.py",
        "${ROOT}/agent/common/configurator.py",
        "${ROOT}/agent/providers",
        "${ROOT}/tools",
        "${ROOT}/venv",
        "${ROOT}/agent/baya_code_agent/codegen/query_schema.py",
        "${ROOT}/agent/baya_code_agent/codegen/schema_lib.py"
      ],
      "allowWrite": [
        "${SLOT_DIR}"
      ],
      "denyWrite": [
        "${ROOT}/config",
        "${ROOT}/api",
        "${ROOT}/agent",
        "${ROOT}/tools",
        "${ROOT}/tests",
        "${ROOT}/.git",
        "${ROOT}/venv"
      ]
    },

    "network": {
      "allowedDomains": [],
      "allowLocalBinding": false
    }
  }
}
EOF

# --- Validate -------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
  python3 -m json.tool "$SETTINGS_FILE" >/dev/null \
    || { echo "Generated invalid JSON: $SETTINGS_FILE" >&2; exit 1; }
fi

echo "Wrote $SETTINGS_FILE"
echo "Run with:  cd '$ROOT' && claude --settings '$SETTINGS_FILE' -p '<task prompt>'"
