"""Minimal MCP client for the Streamable HTTP transport.

Written against the MCP spec (2025-06-18) by hand, so every check in the
report maps to a specific request and response. No MCP SDK needed at runtime.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "mcp-preflight", "version": "0.1.0"}


class MCPError(Exception):
    """The server answered, but not the way the spec says it should."""


class AuthRequired(MCPError):
    def __init__(self, response: httpx.Response):
        self.status = response.status_code
        self.www_authenticate = response.headers.get("www-authenticate", "")
        super().__init__(f"HTTP {self.status}: {self.www_authenticate or 'no WWW-Authenticate header'}")


@dataclass
class Exchange:
    """One request/response pair, kept for the report."""

    method: str
    status: int
    content_type: str
    ms: float


@dataclass
class Session:
    server_info: dict = field(default_factory=dict)
    protocol_version: str = ""
    capabilities: dict = field(default_factory=dict)
    session_id: str | None = None
    exchanges: list[Exchange] = field(default_factory=list)


def parse_sse(text: str) -> list[dict]:
    """Return the JSON payloads of every `data:` event in an SSE body."""
    events, buf = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            buf.append(line[5:].lstrip(" "))
        elif line == "" and buf:
            try:
                events.append(json.loads("\n".join(buf)))
            except json.JSONDecodeError:
                pass
            buf = []
    return events


class StreamableHTTPClient:
    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.url = url
        self.http = httpx.Client(
            headers=headers or {}, timeout=timeout, transport=transport, follow_redirects=True
        )
        self.session = Session()
        self._next_id = 0

    # -- low level -------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self.session.session_id:
            h["Mcp-Session-Id"] = self.session.session_id
        if self.session.protocol_version:
            h["MCP-Protocol-Version"] = self.session.protocol_version
        return h

    def _send(self, method: str, params: dict | None = None, notify: bool = False):
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            self._next_id += 1
            payload["id"] = self._next_id

        t0 = time.perf_counter()
        try:
            r = self.http.post(self.url, json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            raise MCPError(f"{method}: {type(e).__name__}: {e}") from e
        ms = (time.perf_counter() - t0) * 1000
        ctype = r.headers.get("content-type", "").split(";")[0].strip()
        self.session.exchanges.append(Exchange(method, r.status_code, ctype, ms))

        if r.status_code in (401, 403):
            raise AuthRequired(r)
        if notify:
            if r.status_code not in (200, 202, 204):
                raise MCPError(f"{method}: expected 202 for a notification, got {r.status_code}")
            return None
        if r.status_code != 200:
            raise MCPError(f"{method}: HTTP {r.status_code}: {' '.join(r.text.split())[:120]}")

        if ctype == "text/event-stream":
            msgs = [m for m in parse_sse(r.text) if m.get("id") == payload["id"]]
            if not msgs:
                raise MCPError(f"{method}: SSE stream closed without a response for id {payload['id']}")
            msg = msgs[-1]
        elif ctype == "application/json":
            try:
                msg = r.json()
            except ValueError as e:
                raise MCPError(f"{method}: body is not JSON") from e
        else:
            raise MCPError(f"{method}: unexpected Content-Type {ctype!r}")

        if "error" in msg:
            err = msg["error"]
            raise MCPError(f"{method}: JSON-RPC error {err.get('code')}: {err.get('message')}")
        if "result" not in msg:
            raise MCPError(f"{method}: response has neither result nor error")
        return msg["result"], r

    # -- MCP methods -----------------------------------------------------
    def initialize(self) -> Session:
        result, resp = self._send(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        self.session.session_id = resp.headers.get("mcp-session-id")
        self.session.server_info = result.get("serverInfo", {})
        self.session.protocol_version = result.get("protocolVersion", "")
        self.session.capabilities = result.get("capabilities", {})
        self._send("notifications/initialized", notify=True)
        return self.session

    def list_tools(self, max_pages: int = 50) -> list[dict]:
        tools, cursor = [], None
        for _ in range(max_pages):
            result, _ = self._send("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
        raise MCPError(f"tools/list: still paginating after {max_pages} pages")

    def close(self) -> None:
        if self.session.session_id:
            try:
                self.http.delete(self.url, headers=self._headers())
            except httpx.HTTPError:
                pass
        self.http.close()
