from fakes import tool

from mcp_preflight.lint import leena_action_name, lint_tool, lint_tools, words_of


def codes(findings):
    return sorted(f.code for f in findings)


def test_action_name_title_cases_all_name_styles():
    assert leena_action_name("fetch_leave_balance") == "Fetch Leave Balance"
    assert leena_action_name("getEmployee") == "Get Employee"
    assert leena_action_name("create-it-ticket") == "Create It Ticket"
    assert words_of("hr.lookupPolicy") == ["hr", "lookup", "Policy"]


def test_guide_example_is_clean():
    t = tool("fetch_employee_leave_balance",
             "Retrieves remaining leave balance for an employee from Workday. Use when an employee asks "
             "about PTO, vacation days, sick leave, or remaining holidays. Requires employee ID.",
             required=("employee_id",))
    assert lint_tool(t) == []


def test_poor_examples_from_the_guide_are_flagged():
    t = tool("workday_integration", "Handles leave requests", required=("payload",))
    got = codes(lint_tool(t))
    for c in ("T004", "T005", "T006", "T007", "T011"):
        assert c in got
    assert "T008" in codes(lint_tool(tool("password_tool", "Resets a password. Use when locked out. Requires id.")))


def test_missing_description_and_bad_schema_are_errors():
    f = lint_tool({"name": "fetch_thing", "description": "", "inputSchema": {"type": "string"}})
    assert {x.code for x in f if x.level == "error"} == {"T001", "T002"}


def test_long_description_and_long_name():
    long = " ".join(["word"] * 51) + " use when asked id"
    assert "T003" in codes(lint_tool(tool("fetch_x", long)))
    assert "T010" in codes(lint_tool(tool("fetch_the_very_long_named_employee_record_thing")))
    assert "T009" in codes(lint_tool(tool("lookup")))


def test_acronym_mangled_by_title_case():
    f = [x for x in lint_tool(tool("create_it_ticket")) if x.code == "T015"]
    assert f and "Create It Ticket" in f[0].message


def test_param_descriptions_grouped_per_tool():
    t = tool("fetch_x", props={"a": {"type": "string"}, "b": {"type": "string"}}, required=())
    f = [x for x in lint_tool(t) if x.code == "T012"]
    assert len(f) == 1 and "'a'" in f[0].message and "'b'" in f[0].message


def test_collisions_and_near_duplicates_across_tools():
    f = lint_tools([tool("getEmployee"), tool("get_employee"), tool("fetch_leave_balances"),
                    tool("fetch_leave_balance")])
    assert [x.subject for x in f if x.code == "T013"] == ["get_employee"]
    assert any(x.code == "T014" for x in f)
