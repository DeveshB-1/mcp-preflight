"""Lint MCP tools against Leena AI's "Writing Effective Tool Descriptions" guide.

Leena turns each discovered tool into an action named after the tool "in title
case", and its agents pick actions by reading names and descriptions. Each rule
below cites the guideline it comes from (docs.leena.ai/docs/writing-effective-tool-descriptions).
"""

from __future__ import annotations

import difflib
import re

from .findings import Finding

ACTION_VERBS = {
    "add", "apply", "approve", "assign", "book", "calculate", "cancel", "check", "close", "compare",
    "convert", "create", "delete", "download", "edit", "enroll", "escalate", "export", "fetch", "file",
    "find", "generate", "get", "grant", "import", "list", "log", "lookup", "look", "mark", "move",
    "notify", "open", "post", "query", "raise", "read", "register", "reject", "remove", "request",
    "reset", "resolve", "retrieve", "revoke", "run", "schedule", "search", "send", "set", "start",
    "stop", "submit", "summarize", "sync", "transfer", "unlock", "update", "upload", "validate",
    "verify", "write", "ask", "answer", "reply", "share", "invite", "provision", "deprovision",
    "suspend", "restore", "refresh", "count", "describe", "show", "view", "upsert", "attach",
}
GENERIC_NOUNS = {"tool", "tools", "helper", "data", "stuff", "util", "utils", "misc", "thing", "handler"}
SYSTEM_NAMES = {
    "servicenow", "workday", "jira", "slack", "sap", "salesforce", "zendesk", "hubspot", "confluence",
    "atlassian", "okta", "successfactors", "gmail", "google", "teams", "outlook", "freshservice",
}
# Short acronyms that title case mangles into ordinary words ("it" -> "It", "hr" -> "Hr").
ACRONYMS = {"it", "hr", "pto", "vpn", "sso", "mfa", "api", "url", "sap", "aws", "gcp", "kb", "faq", "ooo", "pii"}
VAGUE_VERBS = re.compile(r"\b(handles?|manages?|deals with|takes care of|is used for stuff)\b", re.IGNORECASE)
WHEN_CUE = re.compile(r"\b(use (it |this )?(when|for|if|to|in|within)|when (an?|the) )", re.IGNORECASE)
MAX_DESC_WORDS = 50


def words_of(name: str) -> list[str]:
    """Split snake_case, kebab-case, dotted and camelCase names into words."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return [w for w in re.split(r"[\s_\-./:]+", spaced) if w]


def leena_action_name(name: str) -> str:
    """Approximate how Leena titles an action: the tool name in title case."""
    return " ".join(w.capitalize() for w in words_of(name))


def lint_tool(tool: dict) -> list[Finding]:
    name = tool.get("name", "")
    desc = (tool.get("description") or "").strip()
    words = [w.lower() for w in words_of(name)]
    out: list[Finding] = []

    def add(level: str, code: str, msg: str) -> None:
        out.append(Finding(level, code, name, msg))

    schema = tool.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        add("error", "T001", "inputSchema must be a JSON Schema with type 'object'.")
        schema = {}

    if not desc:
        add("error", "T002", "No description. Leena's agent picks actions by description.")
    else:
        n = len(desc.split())
        if n > MAX_DESC_WORDS:
            add("warn", "T003", f"Description is {n} words; the guide says keep it under {MAX_DESC_WORDS}.")
        m = VAGUE_VERBS.search(desc)
        if m:
            add("warn", "T004", f"Vague verb '{m.group(0)}' in description; say what it fetches, creates or updates.")
        if not WHEN_CUE.search(desc):
            add("info", "T005", "Description doesn't say when to use it (e.g. 'Use when an employee asks about PTO').")

    if words and words[0] in SYSTEM_NAMES:
        add("warn", "T006", f"Name starts with the system name '{words[0]}'; start with the action verb.")
        verb_words = words[1:]
    else:
        verb_words = words
    if verb_words and verb_words[0] not in ACTION_VERBS:
        add("warn", "T007", f"Name doesn't start with an action verb ('{verb_words[0]}').")
    generic = sorted(set(words) & GENERIC_NOUNS)
    if generic:
        add("warn", "T008", f"Generic noun(s) {generic} in name add nothing; name the object.")
    if len(words) == 1:
        add("warn", "T009", "One-word name; say the action and the object (e.g. fetch_leave_balance).")
    elif len(words) > 6:
        add("warn", "T010", f"Name is {len(words)} words; the guide suggests 3 to 6.")

    props = schema.get("properties") or {}
    for req in schema.get("required") or []:
        req_words = [w.lower() for w in words_of(req)]
        keep = [w for w in req_words if len(w) > 2] or req_words  # skip "of"/"to", but keep a bare "id"
        if desc and not any(re.search(rf"\b{re.escape(w)}", desc, re.IGNORECASE) for w in keep):
            add("warn", "T011", f"Required input '{req}' isn't mentioned in the description.")
    bare = [p for p, ps in props.items() if isinstance(ps, dict) and not ps.get("description")]
    if bare:
        add("info", "T012", f"Parameter(s) {bare} have no description; Leena's agent reads the schema at runtime.")

    lost = [w for w in words if w in ACRONYMS]
    if lost:
        add("warn", "T015", f"Title case turns {[w.upper() for w in lost]} into {[w.capitalize() for w in lost]} "
                            f"('{leena_action_name(name)}'). Spell it out or check how the action reads in Leena.")
    return out


def lint_tools(tools: list[dict]) -> list[Finding]:
    out: list[Finding] = []
    seen: dict[str, str] = {}
    for t in tools:
        out += lint_tool(t)
        name = t.get("name", "")
        key = "".join(w.lower() for w in words_of(name))  # ignores case and separators
        if key in seen:
            out.append(Finding(
                "error", "T013", name,
                f"Differs from '{seen[key]}' only by case or separators, so both show up as "
                f"'{leena_action_name(name)}' (or close to it) in Leena. The agent can't tell them apart.",
            ))
        else:
            seen[key] = name

    titles = list(seen)
    for i, a in enumerate(titles):
        for b in titles[i + 1:]:
            if difflib.SequenceMatcher(None, a, b).ratio() >= 0.9:
                out.append(Finding(
                    "warn", "T014", seen[b],
                    f"Name is nearly identical to '{seen[a]}'; the guide says make tools distinct.",
                ))
    return out
