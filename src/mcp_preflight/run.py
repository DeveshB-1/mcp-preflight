"""Run every preflight check against one MCP server URL."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import drift, lint, oauth
from .client import PROTOCOL_VERSION, AuthRequired, MCPError, StreamableHTTPClient
from .findings import Finding, worst

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass
class Report:
    url: str
    auth: str = "none"  # none | headers | oauth-required
    server_info: dict = field(default_factory=dict)
    protocol_version: str = ""
    session_id: bool = False
    latency_ms: dict = field(default_factory=dict)
    tools: list[dict] = field(default_factory=list)
    discovery: oauth.Discovery | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.auth == "oauth-required" and worst(self.findings) != "error":
            return "OAUTH DISCOVERY OK, TOOLS NOT CHECKED (needs a token)"
        return {"info": "READY", "warn": "READY WITH WARNINGS", "error": "NOT READY"}[worst(self.findings)]

    def as_dict(self) -> dict:
        d = {
            "url": self.url, "verdict": self.verdict, "auth": self.auth, "server_info": self.server_info,
            "protocol_version": self.protocol_version, "session_id": self.session_id,
            "latency_ms": self.latency_ms,
            "tools": [{"name": t["name"], "leena_action": lint.leena_action_name(t["name"])} for t in self.tools],
            "findings": [f.as_dict() for f in self.findings],
        }
        if self.discovery:
            d["oauth"] = {
                "source": self.discovery.source, "auth_server": self.discovery.auth_server,
                "authorization_endpoint": (self.discovery.as_metadata or {}).get("authorization_endpoint"),
                "token_endpoint": (self.discovery.as_metadata or {}).get("token_endpoint"),
                "steps": self.discovery.steps,
            }
        return d


def parse_headers_json(text: str) -> tuple[dict[str, str], list[Finding]]:
    """Leena's form takes custom auth headers as 'a JSON object'. Check it the same way."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        return {}, [Finding("error", "H001", "headers", f"Headers are not valid JSON: {e.msg} at char {e.pos}.")]
    if not isinstance(obj, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in obj.items()):
        return {}, [Finding("error", "H001", "headers", 'Headers must be a JSON object of strings, e.g. {"X-API-Key": "..."}.')]
    return obj, []


def run_preflight(
    url: str,
    headers: dict[str, str] | None = None,
    runs: int = 3,
    slow_ms: float = 3000,
    snapshot_path: Path | None = None,
    update_snapshot: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> Report:
    rep = Report(url=url, auth="headers" if headers else "none")
    host = urlsplit(url).hostname or ""
    if urlsplit(url).scheme != "https" and host not in LOCAL_HOSTS:
        rep.findings.append(Finding("warn", "X001", "transport",
                                    "URL is not HTTPS; Leena reaches the server over the internet."))

    timings: list[float] = []
    for i in range(max(1, runs)):
        c = StreamableHTTPClient(url, headers=headers, transport=transport)
        try:
            c.initialize()
            tools = c.list_tools()
        except AuthRequired as e:
            rep.auth = "oauth-required" if not headers else rep.auth
            if headers:
                rep.findings.append(Finding("error", "H002", "headers",
                                            f"Server rejected the supplied headers ({e.status})."))
            else:
                rep.findings.append(Finding("info", "H003", "headers",
                    "Server needs auth. Pick Admin or User OAuth 2.0 in Leena, or Custom authentication "
                    "if it takes an API key. Re-run with --header 'Authorization: Bearer <token>' to lint tools."))
            http = httpx.Client(timeout=15.0, transport=transport, follow_redirects=True)
            try:
                rep.discovery = oauth.discover(url, e.www_authenticate, http)
            finally:
                http.close()
            rep.findings += rep.discovery.findings
            c.close()
            return rep
        except MCPError as e:
            rep.findings.append(Finding("error", "X005", "transport", str(e)))
            c.close()
            return rep
        s = c.session
        timings.append(sum(x.ms for x in s.exchanges))
        c.close()
        if i == 0:
            rep.server_info, rep.protocol_version = s.server_info, s.protocol_version
            rep.session_id = bool(s.session_id)
            rep.tools = tools
            if s.protocol_version != PROTOCOL_VERSION:
                rep.findings.append(Finding("info", "X002", "transport",
                    f"Server negotiated protocol {s.protocol_version or '?'} (client asked for {PROTOCOL_VERSION})."))
            if "tools" not in s.capabilities:
                rep.findings.append(Finding("warn", "X003", "transport",
                                            "Server doesn't advertise the 'tools' capability."))
            ctypes = {x.method: x.content_type for x in s.exchanges}
            rep.latency_ms["content_type"] = ctypes.get("tools/list", "")

    rep.latency_ms.update({"runs": len(timings), "p50": round(statistics.median(timings), 1),
                           "max": round(max(timings), 1)})
    if rep.latency_ms["p50"] > slow_ms:
        rep.findings.append(Finding("warn", "L001", "latency",
            f"initialize + tools/list p50 is {rep.latency_ms['p50']} ms (threshold {slow_ms} ms). "
            "Leena doesn't publish a connector timeout, so slow discovery is a risk."))

    if not rep.tools:
        rep.findings.append(Finding("warn", "X006", "transport", "Server lists 0 tools; Leena will show no actions."))
    rep.findings += lint.lint_tools(rep.tools)

    if snapshot_path:
        snap = drift.snapshot(rep.tools)
        old = drift.load(snapshot_path)
        if old is None or update_snapshot:
            drift.save(snapshot_path, snap)
            rep.findings.append(Finding("info", "D000", "drift", f"Snapshot written to {snapshot_path}."))
        else:
            rep.findings += drift.diff(old, snap)
    return rep
