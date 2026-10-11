"""mcp-trace: MCP server over trace query core."""
from mcp_trace.core import (
    approval_log,
    compare_runs,
    failure_report,
    group_traces,
    list_runs,
    load_spans,
    load_trace_dir,
    recent_activity,
    run_summary,
    search_spans,
    security_audit,
    slowest_spans,
    span_tree,
    token_usage,
    tool_stats,
)

__version__ = "0.1.1"
__all__ = [
    "approval_log",
    "compare_runs",
    "failure_report",
    "group_traces",
    "list_runs",
    "load_spans",
    "load_trace_dir",
    "recent_activity",
    "run_summary",
    "search_spans",
    "security_audit",
    "slowest_spans",
    "span_tree",
    "token_usage",
    "tool_stats",
    "__version__",
]
