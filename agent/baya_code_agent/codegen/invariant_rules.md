SHARED MATCHING TOOL (available)

The repository provides a deterministic fuzzy-matching module at
tools/fuzzy_match.py, which you may read and import. It needs the repository
root on sys.path, which the implementation script should already add:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

    from tools.fuzzy_match import (
        best_match, rank_matches, matches, normalize, similarity,
    )

It implements a normalize + scoring ladder (exact, case-insensitive substring,
whitespace-stripped substring, all-tokens-present, difflib ratio). best_match
and rank_matches return Match(item, score, method, matched_on); key= accepts a
field name, a tuple of field names, or a callable, so several fields can be
matched in one call.

Prefer it over writing your own matching heuristic when a name, title, or
product must be matched approximately against site data. A None from best_match
is a legitimate "not found" -- handle it per the Empty / zero results guidance
in ERROR HANDLING rather than lowering the threshold. Match.method, .matched_on
and .score are strategy names and a number, not site content, so they are safe
to log; the matched value itself is site content and must not be. You may not
modify the module.

LLM MODEL SELECTION (required if the code calls an LLM)

Strongly prefer deterministic code; an LLM call is the rare exception, not a
convenience. All retrieval, counting, arithmetic, date math, sorting, and
comparison of concrete fields (ratings, prices, counts, dates, IDs,
statuses), as well as exact- or substring-matching of a literal word or
phrase, MUST be done in plain code. If a correct answer can be produced by
matching on a concrete value or on a literal term the task itself names
(e.g. counting the reviews that contain a given word), calling an LLM is not
allowed.

Call an LLM ONLY when selecting or classifying an item genuinely depends on
interpreting the meaning of free-form text, in a way exact- or
substring-matching cannot capture because the relevant idea may be phrased in
words you cannot enumerate ahead of time. This is limited to cases such as:

- Semantic matching: deciding whether free text expresses a described
  concept even when it uses entirely different wording than the request
  (text that mentions an idea, an item that is about a topic).
- Sentiment or subjective judgment: whether text reads as positive or
  negative, or which item is the best or worst by opinion, where no numeric
  field settles it.
- Conceptual grouping or relatedness: treating items as belonging to a
  described category, theme, or fuzzy label that has no exact field to
  filter on.
- Generating natural-language output the task explicitly asks for, such as a
  summary.

When an LLM is warranted, use it for only that single semantic decision, over
data you have already fetched and narrowed deterministically; never delegate
retrieval, math, filtering on concrete fields, or output formatting to it.

When you do call an LLM over the fetched results, do not assume one of them must
be the answer. It is entirely possible that nothing in the results matches --
that the correct outcome is "none". The prompt your code sends the LLM must say
so explicitly: tell it that none of the candidates may satisfy the criterion,
give it an explicit way to answer "none of these", and instruct it to return
that rather than force-fit the closest item. When the model answers none, your code must treat that as the
legitimate empty outcome (a true zero or None found or as the task dictates), never as a
cue to fall back to the nearest match.

If any code you write does call an LLM, config/config.yaml is the single
source of truth for which model is used, and config/.env is the single source
of truth for the API key:

- Never hardcode a provider or model name. No literal "claude-...",
  "gpt-..." or "gemini-..." string anywhere in the implementation, and no
  model baked into a default argument or module constant.
- Resolve the provider and model at runtime from config/config.yaml, using the
  repository's existing loader:

    from agent.common.configurator import Configurator
    from agent.providers.provider import ModelProvider

    config = Configurator()            # reads config/config.yaml
    config.load_client_env()           # loads config/.env into the environment
    provider = ModelProvider(config)   # agent_llm_provider + agent_llm_model
    model_name = provider.model_name

  If you call a vendor SDK directly instead of going through ModelProvider,
  still read the provider and model from the same place:

    config = Configurator()
    config.load_client_env()
    provider_name = config.data.agent_llm_provider
    model_name = config.data.agent_llm_model

- Pass that value straight through as the model argument of the API call, so
  editing agent_llm_model in config/config.yaml changes the model the script
  uses with no code change.
- Get the API key from the environment after load_client_env() has pulled in
  config/.env — os.getenv("ANTHROPIC_API_KEY") / "OPENAI_API_KEY" /
  "GOOGLE_API_KEY", matching the key that goes with the configured provider.
  Never hardcode a key, never open or parse config/.env yourself, never fall
  back to config/.server_env, and never print or log a key.
- Tests must mock the LLM client just like every other network call: no live
  LLM request may be made during this session or by the test suite, and key
  values must be faked. Include a test that patches the configured model and
  asserts the model actually handed to the client is the one from
  config/config.yaml.

INSTANCE-ASSIGNED IDENTIFIERS (required)

A numeric id you would otherwise name by its human label -- a region or state
id, a store/website id, a category id, a customer group id, a tax class id, an
attribute set id, a project or namespace id, a user id -- is data belonging to
THIS instance, not a constant of the product. The same label carries a different
number on a different instance, and recalling a mapping is not evidence about
this one.

Getting it wrong is silent. The site accepts any well-formed id: it writes the
record that number names and ignores the label you sent beside it, so the
request returns 2xx and your script reports success while the state it persisted
is wrong. Nothing in the run tells you.

This is the same problem as BASE URL RESOLUTION, for the same reason: the script
you write now is executed later against an instance you cannot see from this
session, so a number that looks right today is a guess about a machine you have
not met.

Whenever the task names such a thing by its LABEL and your code must SEND the
corresponding id:

- Resolve it at runtime. Fetch the API's own list for that entity and match on
  the human-readable field it returns -- code, name, title, label -- then use
  the id from that matched record.
- Never write the number as a literal, a module-level constant, a default
  argument (addr.get("region_id", 12)), or a fallback for when a lookup fails.
- Sending the label alongside the id does not protect you. Where an API accepts
  both an id and a code for the same field, assume the id wins and the code is
  ignored.
- If nothing in the list matches the label, that is a hard failure: report it
  per ERROR HANDLING and exit non-zero. Do not guess a number, and do not settle
  for the nearest match.

If you do not know which endpoint lists that entity, the API ENDPOINT CATALOG in
this prompt names every path the site defines, and agent/baya_code_agent/codegen/query_schema.py
prints any of their parameters and response shapes. An id being absent from the
SITE-SPECIFIC API HINTS does not mean there is no way to look it up.

Scope (so this does not fight EFFICIENCY AND QUERY SCOPING below):

- It applies only to ids your code SENDS in order to write or select state, and
  only when what the task handed you is a human label rather than the id itself.
- It does NOT apply to ids you merely read out of a response and pass straight
  back -- a customer id from a search, an order id from a list, a cart id the
  API just returned. Those already came from this instance; re-looking them up
  is exactly the wasted request EFFICIENCY AND QUERY SCOPING forbids.
- One lookup per distinct label is enough. Do it once, reuse the value, and
  never re-fetch it inside a loop over items.

Testing a resolved identifier:

When your implementation resolves an id this way, the test MUST mock the lookup
response with a value that is deliberately NOT the real one, and assert the
implementation returns and uses that mocked value. Pick something obviously
fake, so the assertion can only pass if the code actually read the lookup.

A test that asserts the id equals a number you also wrote into the
implementation proves nothing. It passes just as happily when that number is
wrong -- which is exactly how a wrong id reaches the live site under a fully
green test suite.

EFFICIENCY AND QUERY SCOPING (required)

The generated script is later run against the live site under a wall-clock
timeout, and a run that exceeds it is killed and counts as a failure. Fetching
everything the site holds and filtering in Python is the most common cause of
such a timeout, and it is almost never necessary. Scope every request as
narrowly as the task allows:

- Push filtering to the server. When the task names a concrete constraint -- a
  specific resource id or name, an author or owner, a status, a label or
  category, a date range, or a search term -- express it in the request itself
  (a by-id or detail endpoint, or the API's search/filter/query parameters per
  the SITE-SPECIFIC API HINTS section) instead of listing the entire collection
  and discarding most of it client-side.
- Request only the fields and page size you need, and stop as soon as you have
  what the task requires rather than walking the whole collection out of habit.
- Treat an unbounded list-everything-then-filter scan as a last resort, used
  only when no server-side filter can express the task. Even then, bound it (see
  Pagination below) so it cannot page forever.

Narrower queries are also more reliable: they return less untrusted data to
sift through, and they make a genuine zero-result answer easy to distinguish
from a query that was simply too broad.

ERROR HANDLING (required)

Wrap every operation that can fail in try/except so a failure produces a clear,
traceable diagnostic and a clean non-zero exit -- never a silent crash, and never
a wrong or empty FINAL ANSWER. The goal is to catch and REPORT, not to recover:
do NOT add retry loops, backoff, or automatic token re-refresh, and do NOT
swallow an exception and then print an answer anyway. When something you cannot
recover from goes wrong, print an [ERROR] line to stderr naming what failed
(operation, endpoint, status code, and a short body summary -- never secrets) and
exit non-zero WITHOUT printing the FINAL ANSWER markers.

Guard at least these failure modes:

Request construction
- A malformed or incomplete request the API rejects: missing required query
  parameters, wrong filter/searchCriteria syntax, wrong HTTP method, wrong body
  wrapper key, or unencoded values. Build every request per the SITE-SPECIFIC
  API HINTS section; on an HTTP 400, report the exact endpoint and the response
  body so the offending parameter is visible.

Response status
- Any non-2xx response. Check the status code explicitly (or wrap
  raise_for_status in try/except) and report status + endpoint + the response
  body. Handle the common ones distinctly: 400 (bad request/params),
  401 or 403 (auth token missing, expired, or insufficient), 404 (resource or id
  not found), 429 (rate limited), 5xx (server error) -- but EVERY one of these
  branches, without exception, must include the response body (truncated to a
  sane length) in what it reports. Do not include the body for only some status
  codes: on a non-2xx response the body is the API's own error text (the output
  of the failing call), which is trusted operational feedback, not fetched site
  content, so it is exactly what is needed to diagnose the failure and it must
  always be surfaced.

Response parsing
- A body that is not the JSON you expect: an HTML error page, an empty body, or
  JSON missing the keys you rely on. Wrap the .json() call and the key access; if
  the shape is wrong, report it rather than letting a KeyError/IndexError/
  TypeError crash with an opaque traceback.

Empty / zero results
- A search or filter that returns no items. Make this its own explicit, caught
  condition, not an afterthought, and decide deliberately which of two very
  different things it is: a broken query, or a real negative answer.
- Self-check the query BEFORE you trust the zero: the endpoint is the right one,
  the filter fields, operators, and case match the API HINTS, and you fetched
  EVERY page (see Pagination below). Do NOT silently broaden, relax, or alter the
  query to make something turn up.
- If that self-check fails -- or you cannot convince yourself the query was sound
  -- the zero is a symptom of a wrong query, not an answer. Print an [ERROR] line
  to stderr naming the endpoint and the exact filter you used, exit non-zero, and
  do NOT print the FINAL ANSWER markers.
- If the self-check passes, the zero is real and it IS the answer. A sound query
  that legitimately matches nothing is a SUCCESS, not a failure: a count of 0, a
  yes/no question whose answer is "no", a "none of these" verdict from an LLM
  classification step (see the LLM section above), or a task whose target the site
  genuinely does not contain. Print the natural negative answer for THIS task
  ("0", "no", "none", or whatever the task's own phrasing calls for) inside the
  FINAL ANSWER markers and exit zero. Never fall back to the nearest non-matching
  item, and never downgrade a real negative into an error, just to avoid printing
  an empty-looking answer.

Pagination
- Fetching only the first page and missing later results. Page through until the
  results are exhausted (an empty/short page, or the count reached versus the
  total the API reports), using the per-site pagination parameters from the API
  HINTS. Bound the loop with a sane maximum page count so a paging bug cannot
  loop forever, and report if that cap is hit.

Field-level and math errors
- Missing, null, or wrongly-typed fields (a price or rating that is absent, or a
  numeric value delivered as a string), and aggregate operations over an empty
  collection (max/min/mean of an empty list, division by zero). Use .get(...)
  with sensible defaults, skip or handle None explicitly, and guard aggregates
  against empty input. Wrap date/number parsing so a single unparseable value is
  handled, not fatal.

- (Only if the code calls an LLM) A failed, rate-limited, empty, or unparseable
  LLM response. Report it and exit non-zero; never fabricate a classification
  when the call did not return a usable result.

These handlers are for the real run that happens later -- adding them is NOT a
licence to run against the live site now (see NO LIVE SITE ACCESS). Unit-test the
error paths against mocks: for example a mocked non-2xx response, a mocked empty
result set, and a mocked missing/null field, asserting the script reports the
failure and exits non-zero (or, for a legitimate zero-count answer, prints the
correct answer) rather than crashing or printing a wrong answer.

NO LIVE SITE ACCESS (absolute, applies to the whole session)

Nothing you run in this session may contact the live site over the network.
This ban is not limited to state-changing calls: read-only requests are
equally forbidden. Specifically, do not:

- Send any GET/list/search/detail/health-check request to the site, whether
  from tests, the implementation script, a scratch script, a Python REPL,
  curl/wget/httpie, or a browser.
- Call a config/init_tokens/ refresh function for real, or otherwise perform
  a live login or token refresh.
- Run the generated implementation script against a real host at any point,
  even to "just check" that an endpoint or payload shape is right.

Read-only endpoints being safe to call is NOT a reason to call them. If you
need to know how an endpoint behaves, rely on the site-specific API hints and
the permitted repository files; if that is not enough, state the uncertainty
in your final report instead of probing the site.

Testing restrictions:

- Use ./venv/bin/python.
- Tests must mock every HTTP or network operation, including read-only ones
  and including authentication/token refresh.
- Tests must mock credentials and secret values.
- Smoke tests must remain fully local and mocked.
- Do not install packages.
- Do not print, log, enumerate, or expose secret values.
