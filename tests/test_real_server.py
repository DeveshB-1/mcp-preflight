"""End to end against a real server built with the official MCP Python SDK."""

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("mcp.server.mcpserver")

from mcp_preflight.run import run_preflight

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "hr_server.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    procs = []

    def start(*args):
        port = _free_port()
        p = subprocess.Popen([sys.executable, str(EXAMPLE), "--port", str(port), *args],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        url = f"http://127.0.0.1:{port}/mcp"
        for _ in range(60):
            try:
                httpx.get(url, timeout=0.5)
                return url
            except httpx.HTTPError:
                time.sleep(0.2)
        raise RuntimeError("example server did not start")

    yield start
    for p in procs:
        p.terminate()
        p.wait(timeout=10)


@pytest.mark.parametrize("mode", [[], ["--json-response"]])
def test_sdk_server_end_to_end(server, mode):
    rep = run_preflight(server(*mode), runs=2)
    assert rep.server_info["name"] == "acme-hr-demo"
    assert len(rep.tools) == 7
    by_code = {}
    for f in rep.findings:
        by_code.setdefault(f.code, set()).add(f.subject)
    assert by_code["T013"] == {"get_employee"}
    assert by_code["T015"] == {"create_it_ticket"}
    assert "fetch_leave_balance" not in set().union(*(v for k, v in by_code.items() if k != "T012"))
    assert rep.verdict == "NOT READY"


def test_sdk_server_drift_v1_to_v2(server, tmp_path):
    snap = tmp_path / "tools.json"
    run_preflight(server(), runs=1, snapshot_path=snap)
    rep = run_preflight(server("--variant", "v2"), runs=1, snapshot_path=snap)
    drift = {(f.code, f.subject) for f in rep.findings if f.code.startswith("D")}
    assert drift == {("D001", "create_it_ticket"), ("D002", "create_incident"), ("D003", "submit_expense_report")}
