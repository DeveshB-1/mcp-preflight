import pytest
from fakes import FakeServer, tool

from mcp_preflight.client import AuthRequired, MCPError, StreamableHTTPClient, parse_sse

URL = "https://mcp.example.com/mcp"


@pytest.mark.parametrize("sse", [True, False])
def test_initialize_and_list_over_sse_and_json(sse):
    srv = FakeServer(sse=sse)
    c = StreamableHTTPClient(URL, transport=srv.transport)
    s = c.initialize()
    assert s.session_id == "s-1"
    assert s.server_info["name"] == "fake"
    assert [t["name"] for t in c.list_tools()] == ["fetch_leave_balance"]
    # notifications/initialized went out, and later requests carry session + protocol headers
    methods = [r.content for r in srv.requests if r.method == "POST"]
    assert b"notifications/initialized" in methods[1]
    last = [r for r in srv.requests if r.method == "POST"][-1]
    assert last.headers["mcp-session-id"] == "s-1"
    assert last.headers["mcp-protocol-version"] == "2025-06-18"


def test_pagination_follows_next_cursor():
    srv = FakeServer(tools=[tool(f"fetch_item_{i}") for i in range(5)], page_size=2)
    c = StreamableHTTPClient(URL, transport=srv.transport)
    c.initialize()
    assert len(c.list_tools()) == 5


def test_auth_required_carries_www_authenticate():
    srv = FakeServer(auth_header=("authorization", "Bearer ok"), www_authenticate='Bearer resource_metadata="x"')
    c = StreamableHTTPClient(URL, transport=srv.transport)
    with pytest.raises(AuthRequired) as e:
        c.initialize()
    assert 'resource_metadata="x"' in e.value.www_authenticate


def test_non_mcp_endpoint_is_reported():
    import httpx
    t = httpx.MockTransport(lambda r: httpx.Response(501, text="<html>\n  Not Implemented </html>"))
    with pytest.raises(MCPError, match="HTTP 501: <html> Not Implemented"):
        StreamableHTTPClient(URL, transport=t).initialize()


def test_parse_sse_multiline_and_junk():
    text = "event: message\ndata: {\"id\": 1,\ndata:  \"result\": {}}\n\n: comment\ndata: not json\n\n"
    assert parse_sse(text) == [{"id": 1, "result": {}}]
