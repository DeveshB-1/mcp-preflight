"""A small HR/IT MCP server for the demo, built on the official MCP Python SDK.

It mixes tools written the way Leena's tool-description guide asks with tools
written the way they often are in practice. Run it, then point preflight at it:

    uv run python examples/hr_server.py --port 8941
    uv run mcp-preflight http://127.0.0.1:8941/mcp

--variant v2 simulates a later deploy: create_it_ticket is renamed and
submit_expense_report gains a required input. Use it to see drift findings.
--json-response makes the server answer with application/json instead of SSE.
"""

from __future__ import annotations

import argparse
import asyncio

from mcp.server.mcpserver import MCPServer


def build(variant: str = "v1") -> MCPServer:
    s = MCPServer(name="acme-hr-demo", version="0.1.0" if variant == "v1" else "0.2.0",
                  warn_on_duplicate_tools=False)

    @s.tool(description="Retrieves remaining leave balance for an employee from Workday. Use when an employee "
                        "asks about PTO, vacation days, sick leave or remaining holidays. Requires employee ID.")
    def fetch_leave_balance(employee_id: str) -> dict:
        return {"employee_id": employee_id, "pto_days": 11.5, "sick_days": 4}

    if variant == "v1":
        @s.tool(description="Creates a new incident ticket in ServiceNow for IT issues. Use for hardware problems, "
                            "VPN issues or password resets. Requires issue description and urgency level.")
        def create_it_ticket(issue_description: str, urgency: str) -> dict:
            return {"number": "INC0010042", "urgency": urgency}
    else:
        @s.tool(description="Creates a ServiceNow incident for IT issues. Use for hardware, VPN or password "
                            "problems. Requires issue description and urgency level.")
        def create_incident(issue_description: str, urgency: str) -> dict:
            return {"number": "INC0010042", "urgency": urgency}

    @s.tool(description="Gets employee.")
    def getEmployee(employee_id: str) -> dict:
        return {"employee_id": employee_id}

    @s.tool(description="Looks up an employee profile in Workday by employee ID. Use when the agent needs the "
                        "employee's manager, department or location.")
    def get_employee(employee_id: str) -> dict:
        return {"employee_id": employee_id, "manager": "E1001"}

    @s.tool(description="Handles Workday requests.")
    def workday_integration(payload: str) -> str:
        return "ok"

    @s.tool(description="Leave data.")
    def leave_data_tool(query: str) -> str:
        return "[]"

    if variant == "v1":
        @s.tool(description="Submits an expense report to SAP Concur for reimbursement. Use when an employee "
                            "wants to claim travel or meal costs.")
        def submit_expense_report(amount: float, currency: str, receipt_url: str) -> dict:
            return {"report_id": "EXP-77", "status": "submitted"}
    else:
        @s.tool(description="Submits an expense report to SAP Concur for reimbursement. Use when an employee "
                            "wants to claim travel or meal costs.")
        def submit_expense_report(amount: float, currency: str, receipt_url: str, cost_center: str) -> dict:
            return {"report_id": "EXP-77", "status": "submitted"}

    return s


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8941)
    p.add_argument("--variant", choices=["v1", "v2"], default="v1")
    p.add_argument("--json-response", action="store_true")
    a = p.parse_args()
    asyncio.run(build(a.variant).run_streamable_http_async(port=a.port, json_response=a.json_response))


if __name__ == "__main__":
    main()
