"""A scriptable fake MCP server for httpx.MockTransport."""

from __future__ import annotations

import json

import httpx


def tool(name: str, description: str = "Fetches a thing. Use when asked. Requires id.", required=("id",), props=None):
    props = props if props is not None else {r: {"type": "string", "description": r} for r in required}
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props, "required": list(required)}}


class FakeServer:
    def __init__(self, tools=None, sse=True, page_size=None, auth_header=None, www_authenticate="",
                 wellknown=None, protocol="2025-06-18"):
        self.tools = tools if tools is not None else [tool("fetch_leave_balance")]
        self.sse = sse
        self.page_size = page_size
        self.auth_header = auth_header  # (name, value) the server requires, or None
        self.www_authenticate = www_authenticate
        self.wellknown = wellknown or {}  # url -> dict body
        self.protocol = protocol
        self.requests: list[httpx.Request] = []

    def _reply(self, req_id, result):
        msg = {"jsonrpc": "2.0", "id": req_id, "result": result}
        if self.sse:
            body = f"event: message\ndata: {json.dumps(msg)}\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream",
                                                            "mcp-session-id": "s-1"})
        return httpx.Response(200, json=msg, headers={"mcp-session-id": "s-1"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            url = str(request.url)
            return httpx.Response(200, json=self.wellknown[url]) if url in self.wellknown else httpx.Response(404)
        if request.method == "DELETE":
            return httpx.Response(200)
        if self.auth_header and request.headers.get(self.auth_header[0]) != self.auth_header[1]:
            return httpx.Response(401, headers={"www-authenticate": self.www_authenticate})

        body = json.loads(request.content)
        method = body["method"]
        if "id" not in body:
            return httpx.Response(202)
        if method == "initialize":
            return self._reply(body["id"], {"protocolVersion": self.protocol, "capabilities": {"tools": {}},
                                            "serverInfo": {"name": "fake", "version": "1"}})
        if method == "tools/list":
            if not self.page_size:
                return self._reply(body["id"], {"tools": self.tools})
            start = int((body.get("params") or {}).get("cursor") or 0)
            page = self.tools[start:start + self.page_size]
            result = {"tools": page}
            if start + self.page_size < len(self.tools):
                result["nextCursor"] = str(start + self.page_size)
            return self._reply(body["id"], result)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "error": {"code": -32601, "message": "Method not found"}})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
