# mcp-preflight

A CLI that checks a remote MCP server before you paste its URL into Leena AI's **Custom MCP** connector.

Leena's agents act as the MCP client. An admin enters a URL and an auth type, Leena discovers the tools at runtime, and each tool becomes an action named after the tool in title case. That leaves 3 places a registration goes wrong after the form says "saved":

1. **Transport and auth.** The server has to speak Streamable HTTP, and for OAuth it has to publish metadata so Leena can find the authorize and token endpoints on its own (the admin only types a Client ID and Secret).
2. **Tool quality.** The agent picks actions by name and description. Leena's own guide, [Writing Effective Tool Descriptions](https://docs.leena.ai/docs/writing-effective-tool-descriptions), lists the rules: verb first, under 50 words, say when to use it, name the required inputs, no "handles" or "manages".
3. **Drift.** The docs say "the available actions can change whenever the server adds, removes, or updates its tools". An AOP that names a removed tool breaks quietly.

mcp-preflight checks all 3 from the command line, so it can run in CI on the MCP server's repo.

## Quickstart

```bash
uv sync
uv run mcp-preflight https://your-server.example.com/mcp

# custom auth, exactly as you'd paste it into Leena's "Authentication headers" field
uv run mcp-preflight https://your-server/mcp --headers-json '{"X-API-Key": "..."}'

# OAuth server: discovery runs without a token; add one to lint the tools too
uv run mcp-preflight https://your-server/mcp --header "Authorization: Bearer $TOKEN"

# drift: first run writes the snapshot, later runs diff against it
uv run mcp-preflight https://your-server/mcp --snapshot .preflight/tools.json
```

`--json` prints a machine-readable report. Exit code is 1 when any finding is at `--fail-on` level (default `error`).

## What it checks

| Code | Level | Check |
|---|---|---|
| X001-X006 | warn/error | HTTPS, `initialize` + `notifications/initialized` + paginated `tools/list` over Streamable HTTP (SSE or JSON responses), session id, `tools` capability, empty tool list |
| H001-H003 | error/info | Auth headers are a JSON object of strings (Leena's field takes "a JSON object"); server accepts them; what to pick when it needs auth |
| A001-A008 | info-error | OAuth discovery: `WWW-Authenticate` `resource_metadata` hint, RFC 9728 protected-resource metadata, RFC 8414 / OIDC metadata, origin fallback. Then: authorize + token endpoints, `authorization_code` and `refresh_token` grants, S256 PKCE, a token auth method that takes a client secret |
| T001-T015 | info-error | Tool lint from Leena's guide, plus 2 checks from how Leena names actions: names that differ only by case or separators (`getEmployee` vs `get_employee`) and acronyms that title case mangles (`create_it_ticket` becomes "Create It Ticket") |
| D001-D004 | info-error | Drift vs snapshot: tool removed, tool added, new required input, description or schema changed |
| L001 | warn | `initialize` + `tools/list` p50 over `--slow-ms` (default 3000) |

## Demo run (pasted from a real run, 2026-10-25)

### 1. A server built with the official MCP Python SDK

`examples/hr_server.py` is a 7-tool HR/IT server on `mcp` 2.3.0. Two tools follow Leena's guide; the rest are written the way tools often get written.

```
$ uv run python examples/hr_server.py &
$ uv run mcp-preflight http://127.0.0.1:8941/mcp --snapshot .preflight/tools.json
mcp-preflight  http://127.0.0.1:8941/mcp

server     acme-hr-demo 0.1.0
protocol   2025-06-18   session id: yes
auth       none
latency    initialize+tools/list p50 6.8 ms, max 7.8 ms over 3 runs (text/event-stream)

tools (7)  ->  Leena action name
  fetch_leave_balance    ->  Fetch Leave Balance
  create_it_ticket       ->  Create It Ticket
  getEmployee            ->  Get Employee
  get_employee           ->  Get Employee
  workday_integration    ->  Workday Integration
  leave_data_tool        ->  Leave Data Tool
  submit_expense_report  ->  Submit Expense Report

findings
  [x] T013 get_employee: Differs from 'getEmployee' only by case or separators, so both show up as 'Get Employee' (or close to it) in Leena. The agent can't tell them apart.
  [!] T004 workday_integration: Vague verb 'Handles' in description; say what it fetches, creates or updates.
  [!] T006 workday_integration: Name starts with the system name 'workday'; start with the action verb.
  [!] T007 leave_data_tool: Name doesn't start with an action verb ('leave').
  [!] T007 workday_integration: Name doesn't start with an action verb ('integration').
  [!] T008 leave_data_tool: Generic noun(s) ['data', 'tool'] in name add nothing; name the object.
  [!] T011 leave_data_tool: Required input 'query' isn't mentioned in the description.
  [!] T011 submit_expense_report: Required input 'amount' isn't mentioned in the description.
  [!] T011 submit_expense_report: Required input 'currency' isn't mentioned in the description.
  [!] T011 submit_expense_report: Required input 'receipt_url' isn't mentioned in the description.
  [!] T011 workday_integration: Required input 'payload' isn't mentioned in the description.
  [!] T015 create_it_ticket: Title case turns ['IT'] into ['It'] ('Create It Ticket'). Spell it out or check how the action reads in Leena.
  [i] D000 drift: Snapshot written to .preflight/tools.json.
  [i] T005 getEmployee: Description doesn't say when to use it (e.g. 'Use when an employee asks about PTO').
  ... (7 more T012 notes: parameters with no description)

verdict    NOT READY  (1 errors, 11 warnings, 11 notes)
```

Then a "v2 deploy" of the same server renames `create_it_ticket` to `create_incident` and adds a required `cost_center`:

```
$ uv run python examples/hr_server.py --variant v2 &
$ uv run mcp-preflight http://127.0.0.1:8941/mcp --snapshot .preflight/tools.json
  [x] D001 create_it_ticket: Tool removed since snapshot; any AOP that references this action will break.
  [!] D003 submit_expense_report: New required input(s) ['cost_center']; existing AOP steps may not supply them.
  [i] D002 create_incident: New tool since snapshot; it shows up as a new Leena action.
```

### 2. Real public MCP servers

DeepWiki (no auth):

```
server     DeepWiki 2.14.3
protocol   2025-06-18   session id: no (stateless)
latency    initialize+tools/list p50 1300.7 ms, max 1378.6 ms over 3 runs (text/event-stream)
tools (3): ask_wiki_question, read_wiki_contents, read_wiki_structure
verdict    READY  (0 errors, 0 warnings, 3 notes)
```

Atlassian Rovo (Leena lists it as an MCP integration) and Linear, both OAuth. No token used; this is discovery only:

```
mcp-preflight  https://mcp.atlassian.com/v1/mcp
oauth      via origin-fallback; auth server https://mcp.atlassian.com
             GET https://mcp.atlassian.com/.well-known/oauth-protected-resource/v1/mcp -> 404
             GET https://mcp.atlassian.com/.well-known/oauth-protected-resource -> 404
             GET https://mcp.atlassian.com/.well-known/oauth-authorization-server -> 200
             authorize https://mcp.atlassian.com/v1/authorize
             token     https://mcp.atlassian.com/v1/token

mcp-preflight  https://mcp.linear.app/mcp
oauth      via rfc9728; auth server https://mcp.linear.app
             GET https://mcp.linear.app/.well-known/oauth-protected-resource/mcp -> 200
             GET https://mcp.linear.app/.well-known/oauth-authorization-server -> 200
```

So the 2 servers need 2 different lookup paths to find the same kind of endpoint. Atlassian only works for a client that still does the older origin lookup.

## Tests

```
$ uv run pytest -q
.........................                                                [100%]
25 passed in 3.89s
```

- `tests/test_real_server.py` starts `examples/hr_server.py` (official MCP SDK) on a free port, in both SSE and JSON response modes, and runs the full preflight, including v1 to v2 drift.
- The rest use `httpx.MockTransport` fakes for cases a real server won't easily produce: 401 with a `resource_metadata` hint, wrong headers, pagination, a 501 HTML page, missing OAuth metadata, bad grant lists.

## What's real, what's assumed

- **Real:** the MCP client speaks the Streamable HTTP spec (2025-06-18) by hand, with no SDK at runtime, and it ran against the official SDK server plus 3 public servers above.
- **Not run against Leena itself.** I don't have a Leena workspace, so I never registered a server in the actual connector.
- **Assumed:** the exact title-casing Leena applies (the docs say "the tool's own name in title case"; camelCase splitting is my guess). The collision check ignores case and separators, so it fires either way.
- **Assumed:** the latency threshold. Leena's Custom MCP page doesn't publish a timeout, so 3000 ms is a default you can change.
- The lint rules are heuristics from Leena's guide. "Required input isn't mentioned" is a word match, so it can miss a synonym.

## Layout

```
src/mcp_preflight/client.py   Streamable HTTP MCP client (initialize, tools/list, SSE parsing)
src/mcp_preflight/oauth.py    RFC 9728 / RFC 8414 / OIDC discovery and metadata checks
src/mcp_preflight/lint.py     tool rules from Leena's description guide
src/mcp_preflight/drift.py    tool-list snapshot and diff
src/mcp_preflight/run.py      runs the checks, builds the report
src/mcp_preflight/cli.py      text / JSON output, exit codes
examples/hr_server.py         demo server on the official MCP Python SDK
```
