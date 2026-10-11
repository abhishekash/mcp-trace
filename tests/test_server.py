"""Server-level tests: tools callable via the FastMCP app's tool manager."""
import json

import pytest

import mcp_trace.server as server


@pytest.fixture(autouse=True)
def point_at_fixtures(tmp_path):
    import shutil

    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    shutil.copy("tests/fixtures/demo_trace.jsonl", trace_dir / "demo.jsonl")
    server._trace_dir = trace_dir
    yield


@pytest.mark.anyio
async def test_tools_registered():
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "list_runs",
        "run_summary",
        "span_tree",
        "slowest_spans",
        "approval_log",
        "token_usage",
        "search_spans",
        "failure_report",
        "tool_stats",
        "security_audit",
        "recent_activity",
        "compare_runs",
    }
    # descriptions should say *when* to use the tool, not just what it does
    slowest = next(t for t in tools if t.name == "slowest_spans")
    assert "slow" in slowest.description.lower()


@pytest.mark.anyio
async def test_list_runs_tool():
    result = await server.mcp.call_tool("list_runs", {"limit": 5})
    data = json.loads(result.content[0].text)
    assert len(data) == 1
    assert data[0]["model"] == "scripted"


@pytest.mark.anyio
async def test_run_summary_accepts_prefix():
    result = await server.mcp.call_tool("run_summary", {"trace_id": "f920798d"})
    data = json.loads(result.content[0].text)
    assert data["tool_calls"] == 4


@pytest.mark.anyio
async def test_unknown_trace_errors():
    with pytest.raises(Exception):
        await server.mcp.call_tool("run_summary", {"trace_id": "deadbeef"})


@pytest.mark.anyio
async def test_approval_log_tool():
    result = await server.mcp.call_tool("approval_log", {})
    data = json.loads(result.content[0].text)
    assert len(data) == 2
    assert {d["tool"] for d in data} == {"write_file", "run_shell"}


@pytest.mark.anyio
async def test_span_tree_tool():
    result = await server.mcp.call_tool("span_tree", {"trace_id": "f920798d"})
    tree = json.loads(result.content[0].text)
    assert tree["name"] == "agent.run"
    assert len(tree["children"]) == 5


@pytest.mark.anyio
async def test_failure_report_tool():
    result = await server.mcp.call_tool("failure_report", {"trace_id": "f920798d"})
    assert json.loads(result.content[0].text) == []


@pytest.mark.anyio
async def test_tool_stats_and_security_audit_tools():
    stats = await server.mcp.call_tool("tool_stats", {"trace_id": "f920798d"})
    stats_data = json.loads(stats.content[0].text)
    assert len(stats_data) == 4

    audit = await server.mcp.call_tool("security_audit", {"trace_id": "f920798d"})
    audit_data = json.loads(audit.content[0].text)
    assert audit_data["missing_identity"] == 4


@pytest.mark.anyio
async def test_recent_activity_and_compare_tools():
    activity = await server.mcp.call_tool("recent_activity", {"limit": 2})
    activity_data = json.loads(activity.content[0].text)
    assert activity_data["count"] == 2
    compared = await server.mcp.call_tool(
        "compare_runs", {"trace_ids": ["f920798d", "f920798d"]}
    )
    compared_data = json.loads(compared.content[0].text)
    assert len(compared_data["runs"]) == 2
