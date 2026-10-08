"""mcp-trace: MCP server over trace query core."""
from mcp_trace.core import (
    approval_log,
    group_traces,
    list_runs,
    load_spans,
    load_trace_dir,
    run_summary,
    search_spans,
    slowest_spans,
    span_tree,
    token_usage,
)

__version__ = "0.1.0"
__all__ = [
    "approval_log",
    "group_traces",
    "list_runs",
    "load_spans",
    "load_trace_dir",
    "run_summary",
    "search_spans",
    "slowest_spans",
    "span_tree",
    "token_usage",
    "__version__",
]
