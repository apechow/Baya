#!/usr/bin/env bash
set -euo pipefail

# Assemble the invariant half of the codegen prompt for one (site, slot) pair.
#
# Two callers, two delivery mechanisms, same requirement:
#   - run_claude_task.sh passes this to `claude --append-system-prompt-file`,
#     landing it in the cached system prompt.
#   - run_antigravity_task.sh stages it as <slot>/AGENTS.md and binds it at
#     $ROOT/AGENTS.md, which agy discovers by walking up from the working
#     directory and applies as a workspace rule. (agy has no
#     --append-system-prompt-file.) Both callers run the session with the repo
#     root as its working directory, so the third argument stays repo-relative
#     and every relative path emitted below resolves for either one.
#
# Either way, prompt caching keys on an exact prefix, so this file MUST be
# byte-identical for every task run against the same site and slot -- that
# identity is the whole point. Never emit a timestamp, a task id, or anything
# else that varies per task, or every run becomes a cold prefix again (measured
# on the Claude path: ~26k tokens re-created instead of read).
#
# The per-task half (the task text, the exact filenames, FINAL ANSWER, PYTHON
# RUN LOGGING, and the replan block) stays in the caller's -p argument.

[[ $# -eq 4 || $# -eq 5 ]] || {
  echo "Usage: $0 <root> <site> <slot_relative_dir> <out_file> [write_dir_mode]" >&2
  exit 2
}

ROOT="$1"
SITE="$2"
SLOT_REL_DIR="$3"
OUT_FILE="$4"
# How the OUTPUT AND EXECUTION RESTRICTIONS section names the write location:
#   explicit (default) -- bake $SLOT_REL_DIR into the invariant, exactly as the
#     Claude path has always done; its emitted prompt stays byte-identical.
#   deferred           -- name NO concrete directory here, so the invariant is
#     byte-identical PER SITE (no slot, no task in it). The antigravity path uses
#     this: agy writes straight into generated/<site>/task_<id>/, and that
#     concrete path is stated in the caller's per-task -p prompt instead, keeping
#     this cached invariant from going cold every task.
WRITE_DIR_MODE="${5:-explicit}"
if [[ "$WRITE_DIR_MODE" != "explicit" && "$WRITE_DIR_MODE" != "deferred" ]]; then
  echo "Error: write_dir_mode must be 'explicit' or 'deferred' (got '$WRITE_DIR_MODE')" >&2
  exit 2
fi

ROOT="$(cd "$ROOT" && pwd -P)"

INVARIANT_RULES="$ROOT/agent/baya_code_agent/codegen/invariant_rules.md"
API_HINTS_LOOKUP="$ROOT/agent/baya_code_agent/codegen/lookup_api_hints.py"
IDENTITY_LOOKUP="$ROOT/agent/baya_code_agent/codegen/lookup_identity.py"
ENDPOINT_CATALOG_GEN="$ROOT/agent/baya_code_agent/codegen/build_endpoint_catalog.py"

if [[ ! -s "$INVARIANT_RULES" ]]; then
  echo "Error: missing or empty $INVARIANT_RULES" >&2
  exit 2
fi

if ! API_HINTS="$("$ROOT/venv/bin/python" "$API_HINTS_LOOKUP" "$SITE")"; then
  echo "Error: API hints lookup failed for site '$SITE'" >&2
  exit 1
fi

# Tells the generated script WHO the authenticated user is (gitlab->byteblaze,
# shopping->emma.lopez@gmail.com, sourced from config.servers + config/.env), so
# first-person tasks ("my orders", "issues assigned to me") resolve to the right
# account instead of the model guessing from the operator's own environment.
# Empty output is expected for sites with no first-person identity.
if ! IDENTITY_SECTION="$("$ROOT/venv/bin/python" "$IDENTITY_LOOKUP" "$SITE")"; then
  echo "Error: identity lookup failed for site '$SITE'" >&2
  exit 1
fi
# Every path the site's schema file(s) define. The SITE-SPECIFIC API HINTS
# section is a partial guide, and codegen otherwise has no way to learn that an
# endpoint exists short of reading a 1.3MB schema file -- WebArena shopping task
# 574 hardcoded a region id (18, which is Florida, not Georgia) it could have
# looked up via an endpoint sitting unread in the schema. Deterministic by
# construction (see api-ordering notes in agent/baya_code_agent/codegen/schema_lib.py), so the emitted
# prompt stays byte-identical per site and the cached prefix survives.
if ! ENDPOINT_CATALOG="$("$ROOT/venv/bin/python" "$ENDPOINT_CATALOG_GEN" "$SITE")"; then
  echo "Error: endpoint catalog generation failed for site '$SITE'" >&2
  exit 1
fi
# Unlike IDENTITY_SECTION, an empty catalog is always a bug: it means schema
# resolution silently found nothing. Failing here is far cheaper than shipping a
# prompt that quietly lost several hundred endpoint lines.
if [[ -z "$ENDPOINT_CATALOG" ]]; then
  echo "Error: endpoint catalog is empty for site '$SITE'" >&2
  exit 1
fi

if [[ -n "$IDENTITY_SECTION" ]]; then
  # Leading blank line separates it from API HINTS; kept empty (no separator)
  # when there is no identity section so no blank gap opens.
  IDENTITY_BLOCK="
${IDENTITY_SECTION}
"
else
  IDENTITY_BLOCK=""
fi

SITE_UPPER="${SITE^^}"
DOMAIN_ENV_VAR="${SITE_UPPER}_DOMAIN"
TOKEN_ENV_VAR="${SITE_UPPER}_TOKEN"
EXTRA_DOMAIN_ENV_VAR="${SITE_UPPER}_EXTRA_DOMAIN"

# Some sites have a companion "extra" API server carrying endpoints the site's
# own API does not expose (gitlab -> the RSS/feed token; shopping -> fuzzy
# product search). Its schema is already readable -- every site settings
# generator allow-lists it -- but a path in a permission rule is not a
# statement that the server exists, so codegen never went looking: WebArena
# gitlab task 259 spent five replans on REST and GraphQL endpoints that do not
# exist on this instance while GET /rss_token sat unread. Naming the file, and
# saying when to read it, is the whole fix.
#
# Conditional because not every site has one. A file-existence test is
# deterministic, so the emitted prompt stays byte-identical per site and the
# cached prefix survives.
#
# reddit is the odd case: it has a SERVER_URLS["reddit_extra"] entry but no
# *_extra_api_schema.json, because for reddit the companion server IS the main
# schema -- reddit_api_schema.json declares host 127.0.0.1:7791, Postmill itself
# having no API. So this block correctly stays empty for reddit, and the reddit
# branches below (SCHEMA_INTRO, BASE_URL_BULLETS) point its scripts at
# ${EXTRA_DOMAIN_ENV_VAR} instead.
EXTRA_SCHEMA="$ROOT/api/schemas/${SITE}_extra_api_schema.json"
if [[ -s "$EXTRA_SCHEMA" ]]; then
  # The doubled $$ escapes survive the unquoted heredoc below as literal "$",
  # so the reader sees $GITLAB_DOMAIN rather than its expansion.
  EXTRA_SCHEMA_BLOCK="
- api/schemas/${SITE}_extra_api_schema.json — the ${SITE} Extra API: a small
  companion server providing endpoints the site's own API does not expose. This
  file is only a few KB; read it in full whenever the task needs something the
  site's own API has no endpoint for, BEFORE concluding the task cannot be done.
  An endpoint being absent from the main schema does not mean it is unavailable.

  The Extra API runs on a DIFFERENT host from the site. Resolve it from the
  ${EXTRA_DOMAIN_ENV_VAR} environment variable, falling back to
  config.servers.SERVER_URLS[\"${SITE}_extra\"]. This is the one exception to
  BASE URL RESOLUTION below: requesting an Extra API path against
  \$${DOMAIN_ENV_VAR} hits the site itself and returns a 404 page.

  This server is shared by every eval worker. Whenever an Extra API endpoint
  accepts a base_url parameter, ALWAYS send it, set to your resolved
  \$${DOMAIN_ENV_VAR} — otherwise the call acts on the server's own default
  instance rather than the one this task is graded on.
"
else
  EXTRA_SCHEMA_BLOCK=""
fi

# Only gitlab gets a token injected into the script's environment by the eval
# runner (both eval/claude_code_agent_runner.py and eval/antigravity_agent_runner.py
# set GITLAB_TOKEN when a GLPAT is available). No other site has such a variable,
# so telling its scripts to read
# one would be a dead branch that never fires -- they keep the original
# always-refresh instruction.
if [[ "$SITE" == "gitlab" ]]; then
  AUTH_SECTION="AUTHENTICATION (required)

A ready-to-use auth token is normally supplied at runtime in the
${TOKEN_ENV_VAR} environment variable. Minting a token is expensive -- it drives
a real browser login -- so resolve the token in exactly this order and do not
mint one unnecessarily:

1. Read ${TOKEN_ENV_VAR} from the environment. If it is set and non-empty, use
   that value as-is and do not call any refresh function.
2. Only if ${TOKEN_ENV_VAR} is unset or empty, fall back to the site's refresh
   function in config/init_tokens/ to obtain one, passing the resolved base URL
   explicitly as its base_url argument (see BASE URL RESOLUTION).

Concretely:

    import os

    token = os.environ.get(\"${TOKEN_ENV_VAR}\", \"\").strip()
    if not token:
        from config.init_tokens.refresh_gitlab_token import refresh_token
        token = refresh_token(base_url=base_url)

Both paths must produce identical behaviour downstream: nothing after this point
may care which one supplied the token.

Do not treat a supplied token as guaranteed valid. If a request comes back 401
or 403, report it per ERROR HANDLING and exit non-zero. Do NOT silently re-mint
a token and retry.

Never read tokens from config/.server_env (do not open, parse, or import that
file), and never fall back to it."
elif [[ "$SITE" == "shopping_admin" ]]; then
  # The generic branch below says only "the site's refresh function in
  # config/init_tokens/", which works for gitlab and shopping because the module
  # name is guessable from the site name. It is not enough here, for two
  # reasons. First, a codegen session cannot discover the module: on the Claude
  # path the settings sandbox denies reads on the repo root and Bash(find *), and
  # on the antigravity path agent/baya_code_agent/sandbox/antigravity_sandbox.sh does not mount the
  # repo at all beyond a narrow allow-list -- either way an unnamed module is
  # unreachable. Second, and worse, the neighbouring
  # refresh_shopping_tokens.py defaults its base_url to port 7770 -- the
  # storefront, a SEPARATE Magento install with a separate catalog database --
  # so a wrong guess there does not fail loudly, it silently reads and writes
  # the wrong catalog. Both are worth spending prompt tokens to prevent.
  AUTH_SECTION="AUTHENTICATION (required)

Obtain the admin Bearer token at runtime from this exact module:

    from config.init_tokens.refresh_shopping_admin_tokens import refresh_tokens

    token = refresh_tokens(base_url=base_url)
    headers = {\"Authorization\": f\"Bearer {token}\"}

where base_url is the value resolved in BASE URL RESOLUTION below.

Two things about this are easy to get wrong, and neither fails loudly:

- ALWAYS pass base_url explicitly. This site (the Luma admin panel) and the
  shopping storefront are two separate Magento installations with separate
  catalog databases, and a token issued by one is not valid for the other.
  Every refresh function in config/init_tokens/ carries a single-instance
  default that is NOT necessarily this instance, so relying on a default can
  authenticate you against the wrong database -- which returns 200s carrying
  the wrong catalog rather than an error you would notice.
- Pass the BARE instance root, exactly as resolved from
  \$${DOMAIN_ENV_VAR} -- e.g. http://localhost:7780, with no /admin suffix.
  Magento serves REST from <root>/rest/V1/..., so a base_url ending in /admin
  produces 404s on every call.

Do not use config/init_tokens/refresh_shopping_tokens.py directly; it defaults
to the storefront instance. Use the shopping_admin module named above.

Do not treat the token as guaranteed valid. If a request comes back 401 or 403,
report it per ERROR HANDLING and exit non-zero. Do NOT silently re-mint a token
and retry.

Never read tokens from config/.server_env (do not open, parse, or import that
file), and never fall back to it."
elif [[ "$SITE" == "reddit" ]]; then
  # The generic branch below tells the script to call a refresh function in
  # config/init_tokens/. That is wrong here and would fail twice over: the API
  # server the script talks to (see AVAILABLE API SCHEMAS) drives its own
  # logged-in browser session and re-authenticates on demand, and
  # refresh_reddit_session.py is deliberately outside the settings sandbox's
  # allowRead set (see agent/baya_code_agent/sandbox/claude_settings/reddit_settings.sh), so importing
  # it does not even resolve.
  AUTH_SECTION="AUTHENTICATION

This site needs NO authentication work from your script, and none is available
to it.

The Reddit API server named in AVAILABLE API SCHEMAS below drives a logged-in
browser session of its own and refreshes it when it expires, so every request
you send it already acts as the site's authenticated user. Therefore:

- Send no Authorization header, no API key, no Bearer token and no PHPSESSID
  cookie. There is no token to obtain and nothing to pass.
- Do NOT import or call anything under config/init_tokens/. In particular do not
  call refresh_reddit_session; it is not readable from this session, and a script
  that imports it will fail to run.
- Never read tokens from config/.server_env (do not open, parse, or import that
  file), and never fall back to it.

A 401 or 403 from the API server is still a hard failure: report it per ERROR
HANDLING and exit non-zero. Do NOT try to log in yourself and retry."
else
  AUTH_SECTION="AUTHENTICATION

Always obtain auth tokens at runtime by calling the site's refresh function in
config/init_tokens/. Never read tokens from config/.server_env (do not open,
parse, or import that file), and never fall back to it."
fi

# The opening lines of the AVAILABLE API SCHEMAS bullet, and the bullets of BASE
# URL RESOLUTION, are hoisted out of the heredocs below because reddit needs
# different text there and a heredoc cannot branch. Both arms are deterministic
# per site, so the emitted prompt stays byte-identical across tasks; the non-reddit
# arms are the original text character-for-character, so the other three sites'
# prompts do not change at all.
#
# The reddit case: reddit_api_schema.json documents a companion FastAPI server on
# ${EXTRA_DOMAIN_ENV_VAR} (host 127.0.0.1:7791) that drives a headless browser
# against Postmill. So the schema is NOT served from the site's base URL, and the
# generic "route every outbound request through the resolved base URL" rule would
# send every API call to Postmill, which answers with an HTML 404 page.
if [[ "$SITE" == "reddit" ]]; then
  SCHEMA_INTRO="- api/schemas/reddit_api_schema.json — the Reddit API. Unlike every other site
  here, this is NOT the site's own API: Postmill has none. It is a companion
  FastAPI server that drives a headless browser against the site for you, so
  there are no CSRF tokens, no session cookies and no form scraping. Send every
  API request to it, resolved from the ${EXTRA_DOMAIN_ENV_VAR} environment
  variable, falling back to config.servers.SERVER_URLS[\"${SITE}_extra\"].
  Sending an API path to \$${DOMAIN_ENV_VAR} instead reaches the Postmill site,
  which serves HTML and returns a 404 page. This file is large; do NOT read it in"
  BASE_URL_BULLETS="- Check the environment variable ${DOMAIN_ENV_VAR} first; if unset or empty,
  fall back to config.servers.SERVER_URLS[\"${SITE}\"]. This is the Postmill site
  itself — the instance this task is graded on.
- reddit is the one site whose API traffic does NOT go to that base URL. Send API
  requests to the Reddit API server instead (see AVAILABLE API SCHEMAS above). That
  server is shared across Postmill instances, so every request MUST carry a base_url
  field set to the resolved \$${DOMAIN_ENV_VAR} (the Postmill instance this task is
  graded on): put it in the JSON body for POST endpoints and pass it as a query
  parameter for GET endpoints. Omitting base_url makes the server act on its own
  default instance, which is not necessarily the one you are graded on.
- Use the resolved \$${DOMAIN_ENV_VAR} only for site URLs: derive its host from
  the resolved value whenever you must build a /f/<forum>/... or /u/<user> URL
  yourself, rather than writing a host and port into the source.
- Post and comment URLs that come back FROM the API (e.g. in /user_posts or
  /search responses) are already correct — pass them back to endpoints such as
  POST /vote_post exactly as returned, and never rewrite their host."
else
  SCHEMA_INTRO="- api/schemas/${SITE}_api_schema.json — the site's own API, served from the base
  URL resolved in BASE URL RESOLUTION. This file is large; do NOT read it in"
  BASE_URL_BULLETS="- Check the environment variable ${DOMAIN_ENV_VAR} first; if unset or empty,
  fall back to config.servers.SERVER_URLS[\"${SITE}\"].
- Route every outbound request, and every call to a config/init_tokens/
  refresh function (per the AUTHENTICATION section), through this same
  resolved base URL — pass it explicitly as that function's base_url
  argument rather than relying on the function's own default."
fi

# Written to a temp file and moved into place, so a concurrent run for another
# task on this slot never reads a half-written prompt.
TMP_FILE="${OUT_FILE}.$$.tmp"
mkdir -p "$(dirname "$OUT_FILE")"
trap 'rm -f "$TMP_FILE"' EXIT

{
  cat <<EOF
SITE-SPECIFIC API HINTS

${API_HINTS}
${IDENTITY_BLOCK}
${AUTH_SECTION}

AVAILABLE API SCHEMAS

These OpenAPI/Swagger files describe the endpoints available for this site.

${SCHEMA_INTRO}
  full. The SITE-SPECIFIC API HINTS section above is a partial guide, not a
  complete inventory: it covers what comes up most often and says nothing about
  the rest, so an endpoint's absence from the hints tells you nothing about
  whether it exists. Two things give you the rest without opening the file:

  - The API ENDPOINT CATALOG below lists every path the schema defines, and the
    HTTP methods each one accepts.
  - agent/baya_code_agent/codegen/query_schema.py prints the full detail for a path from that list —
    description, parameters, request body shape (nested) and response shape:

        ./venv/bin/python agent/baya_code_agent/codegen/query_schema.py ${SITE} --search <term>
        ./venv/bin/python agent/baya_code_agent/codegen/query_schema.py ${SITE} --endpoint "GET /some/path"

  Both are available to you and neither is a required step. Reach for them when
  the hints do not settle a question, rather than guessing or opening the schema.
${EXTRA_SCHEMA_BLOCK}
API ENDPOINT CATALOG (reference)

Every path this site's schema file(s) define, grouped by file, with the HTTP
methods each path accepts. This list is complete. It carries no descriptions and
no parameters — for those, run agent/baya_code_agent/codegen/query_schema.py on a path from this list.

EOF

  # Emitted outside the heredoc: the heredoc above is unquoted (so \${SITE} and
  # friends expand), and schema-derived text must never be run through shell
  # expansion. Today no path contains \$, a backtick or a backslash; this keeps
  # that from becoming a silent requirement on every future schema.
  printf '%s\n' "$ENDPOINT_CATALOG"

  cat <<EOF

BASE URL RESOLUTION (required, multi-docker compatible)

This task's implementation script may later be executed against a different
site instance (a different host/port) than whatever is reachable during this
coding session, so it must resolve its base URL at runtime instead of ever
hardcoding one:

${BASE_URL_BULLETS}

EOF

  cat "$INVARIANT_RULES"

  if [[ "$WRITE_DIR_MODE" == "deferred" ]]; then
    # No concrete directory named here -- the caller's per-task -p prompt gives
    # the exact generated/<site>/task_<id>/ path. Keeping it out of this file is
    # what makes the invariant byte-identical per site (see WRITE_DIR_MODE above).
    cat <<EOF

OUTPUT AND EXECUTION RESTRICTIONS

Create every generated implementation file and test file only inside the working
directory named in the task message, and nowhere else.

Do not create, modify, rename, move, or delete files outside that directory, and
do not create subdirectories inside it -- the files must sit flat in it.

Use only the repository files explicitly permitted by the site-specific
settings and task instructions.

If required information is unavailable from the permitted sources, report
what is missing instead of searching elsewhere.

The exact filenames to use are given in the task message. Use those names
verbatim; never invent a descriptive filename derived from the task content.

Any path the implementation or its tests resolve relative to themselves MUST go
through Path(__file__).parent / Path(__file__).resolve().parents[N] rather than
an absolute directory written into the source: the script is later executed with
the repository root as its working directory, so a directory written into the
source now may not resolve the same way when the script is actually run.
EOF
  else
    cat <<EOF

OUTPUT AND EXECUTION RESTRICTIONS

Create every generated implementation file and test file only inside:

${SLOT_REL_DIR}/

Do not create, modify, rename, move, or delete files outside that directory.

Use only the repository files explicitly permitted by the site-specific
settings and task instructions.

If required information is unavailable from the permitted sources, report
what is missing instead of searching elsewhere.

The exact filenames to use are given in the task message. Use those names
verbatim; never invent a descriptive filename derived from the task content.

This directory is a working area, not where the files end up. They are moved to
their permanent location after this session ends, so any path the implementation
or its tests resolve relative to themselves MUST go through
Path(__file__).parent / Path(__file__).resolve().parents[N] rather than an
absolute directory written into the source. An absolute path captured now will
not be valid when the script is actually run.
EOF
  fi
} > "$TMP_FILE"

mv -f "$TMP_FILE" "$OUT_FILE"
trap - EXIT
