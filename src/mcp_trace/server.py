"""FastMCP stdio server: agents querying their own traces.

Tool design notes (these are prompts, not just schemas):
- every tool says *when* to use it, not just what it does
- results are compact JSON; trees are the only nested shape
- trace_dir comes from --trace-dir / MCP_TRACE_DIR / ./traces
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_trace import core

mcp = FastMCP(
    "mcp-trace",
    instructions=(
        "Query OpenTelemetry traces of agent runs (JSONL span files). "
        "Use list_runs to see recent runs, run_summary/span_tree to inspect one, "
        "slowest_spans for latency questions, approval_log for what humans "
        "approved/denied, token_usage for cost questions."
    ),
)

_trace_dir: Path = Path("./traces")


def _spans() -> list[dict]:
    try:
        return core.load_trace_dir(_trace_dir)
    except FileNotFoundError:
        return []


def _resolve(trace_id: str) -> list[dict]:
    spans = [s for s in _spans() if s["trace_id"].startswith(trace_id)]
    if not spans:
        known = ", ".join(t[:12] for t in core.group_traces(_spans())) or "(none)"
        raise ValueError(f"no trace matching {trace_id!r}; known prefixes: {known}")
    return spans


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


@mcp.tool()
def list_runs(limit: int = 10) -> str:
    """List recent agent runs: task, model, duration, tool use, human decisions, cost.

    Use first to find the trace_id for deeper inspection.
    """
    runs = core.list_runs(_spans(), limit=limit)
    if not runs:
        return f"No traces found in {_trace_dir}. Point --trace-dir at your harness traces."
    for r in runs:
        r["trace_id"] = r["trace_id"][:12] + "…"
    return _dump(runs)


@mcp.tool()
def run_summary(trace_id: str) -> str:
    """Compact summary of one run (accepts trace_id prefix)."""
    spans = _resolve(trace_id)
    return _dump(core.run_summary(spans[0]["trace_id"], spans))


@mcp.tool()
def span_tree(trace_id: str) -> str:
    """Nested span tree of a run — the shape of what the agent did, with durations."""
    spans = _resolve(trace_id)
    return _dump(core.span_tree(spans[0]["trace_id"], spans))


@mcp.tool()
def slowest_spans(trace_id: str | None = None, k: int = 5) -> str:
    """Top-k slowest spans. Use for 'why was this run slow?'. Omit trace_id to search all runs."""
    spans = _resolve(trace_id) if trace_id else _spans()
    return _dump(core.slowest_spans(spans, k=k))


@mcp.tool()
def approval_log(trace_id: str | None = None) -> str:
    """Human-in-the-loop audit: every approval/denial/edit, who decided, and the rationale.

    Use when asked 'why did the agent get to do X?' or 'what was denied?'.
    """
    spans = _resolve(trace_id) if trace_id else _spans()
    return _dump(core.approval_log(spans))


@mcp.tool()
def token_usage(trace_id: str | None = None) -> str:
    """Token and cost totals. Omit trace_id to aggregate across all runs."""
    spans = _resolve(trace_id) if trace_id else _spans()
    return _dump(core.token_usage(spans))


@mcp.tool()
def search_spans(needle: str, trace_id: str | None = None) -> str:
    """Substring search over span names/attributes, e.g. a tool name, file path, or 'denied'."""
    spans = _resolve(trace_id) if trace_id else _spans()
    return _dump(core.search_spans(spans, needle))


def main() -> None:
    global _trace_dir
    parser = argparse.ArgumentParser(description="MCP server for agent self-observability")
    parser.add_argument(
        "--trace-dir",
        default=os.environ.get("MCP_TRACE_DIR", "./traces"),
        help="directory of JSONL span files (default: $MCP_TRACE_DIR or ./traces)",
    )
    args = parser.parse_args()
    _trace_dir = Path(args.trace_dir).resolve()
    print(f"mcp-trace serving traces from {_trace_dir}", file=sys.stderr)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
