import json

import httpx
from fakes import FakeServer, tool

from mcp_preflight import drift
from mcp_preflight.cli import main
from mcp_preflight.oauth import check_as_metadata, discover
from mcp_preflight.run import parse_headers_json, run_preflight

URL = "https://mcp.example.com/mcp"
AS = {"issuer": "https://auth.example.com", "authorization_endpoint": "https://auth.example.com/authorize",
      "token_endpoint": "https://auth.example.com/token", "grant_types_supported": ["authorization_code", "refresh_token"],
      "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["client_secret_post"]}


def test_rfc9728_discovery_via_header_hint():
    srv = FakeServer(wellknown={
        "https://mcp.example.com/prm": {"resource": URL, "authorization_servers": ["https://auth.example.com"]},
        "https://auth.example.com/.well-known/oauth-authorization-server": AS,
    })
    d = discover(URL, 'Bearer resource_metadata="https://mcp.example.com/prm"', httpx.Client(transport=srv.transport))
    assert d.source == "rfc9728" and d.as_metadata == AS and d.findings == []


def test_origin_fallback_is_noted():
    srv = FakeServer(wellknown={"https://mcp.example.com/.well-known/oauth-authorization-server": AS})
    d = discover(URL, "Bearer", httpx.Client(transport=srv.transport))
    assert d.source == "origin-fallback"
    assert [f.code for f in d.findings] == ["A001"]
    assert [u for u, _ in d.steps][:2] == ["https://mcp.example.com/.well-known/oauth-protected-resource/mcp",
                                          "https://mcp.example.com/.well-known/oauth-protected-resource"]


def test_no_metadata_is_an_error():
    d = discover(URL, "", httpx.Client(transport=FakeServer().transport))
    assert any(f.code == "A002" and f.level == "error" for f in d.findings)


def test_as_metadata_checks():
    bad = {"authorization_endpoint": "x", "grant_types_supported": ["client_credentials"],
           "token_endpoint_auth_methods_supported": ["none"]}
    got = {f.code for f in check_as_metadata(bad)}
    assert {"A003", "A004", "A005", "A006", "A007"} <= got


def test_headers_json_matches_leena_field():
    assert parse_headers_json('{"X-API-Key": "k"}') == ({"X-API-Key": "k"}, [])
    assert parse_headers_json("{'X-API-Key': 'k'}")[1][0].code == "H001"
    assert parse_headers_json('{"X-Retries": 3}')[1][0].code == "H001"


def test_run_with_wrong_header_is_error_and_right_header_lints():
    srv = FakeServer(auth_header=("x-api-key", "good"),
                     wellknown={"https://mcp.example.com/.well-known/oauth-authorization-server": AS})
    bad = run_preflight(URL, headers={"X-API-Key": "nope"}, runs=1, transport=srv.transport)
    assert bad.verdict == "NOT READY" and any(f.code == "H002" for f in bad.findings)
    good = run_preflight(URL, headers={"X-API-Key": "good"}, runs=1, transport=srv.transport)
    assert good.verdict == "READY" and len(good.tools) == 1


def test_drift_removed_tool_and_new_required_input(tmp_path):
    snap = tmp_path / "tools.json"
    v1 = FakeServer(tools=[tool("create_ticket"), tool("submit_expense", required=("amount",))])
    run_preflight(URL, runs=1, snapshot_path=snap, transport=v1.transport)
    v2 = FakeServer(tools=[tool("create_incident"), tool("submit_expense", required=("amount", "cost_center"))])
    rep = run_preflight(URL, runs=1, snapshot_path=snap, transport=v2.transport)
    got = {(f.code, f.subject) for f in rep.findings if f.code.startswith("D")}
    assert got == {("D001", "create_ticket"), ("D002", "create_incident"), ("D003", "submit_expense")}
    assert drift.diff(drift.load(snap), drift.load(snap)) == []


def test_cli_json_output_and_exit_codes(capsys, monkeypatch):
    srv = FakeServer(tools=[tool("getEmployee"), tool("get_employee")])
    from mcp_preflight import cli
    real = cli.run_preflight
    monkeypatch.setattr(cli, "run_preflight", lambda *a, **k: real(*a, transport=srv.transport, **k))
    assert main([URL, "--json", "--runs", "1"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "NOT READY"
    assert [t["leena_action"] for t in out["tools"]] == ["Get Employee", "Get Employee"]
    assert main([URL, "--headers-json", "not json"]) == 1
