"""Trace query engine over JSONL span files.

Pure functions — no MCP or OTel imports — so the query logic is testable
offline and reusable. The MCP server (server.py) is a thin adapter over this.

Span format is the agent-harness contract: one JSON object per line with
trace_id, span_id, parent_span_id, name, start/end unix nanos, status,
attributes, events.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# loading


def load_spans(path: str | Path) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                span = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: invalid span JSON: {e}") from e
            span["_source_file"] = str(path)
            spans.append(span)
    return spans


def load_trace_dir(trace_dir: str | Path) -> list[dict[str, Any]]:
    """Load every *.jsonl span file under a directory (non-recursive)."""
    root = Path(trace_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"trace dir not found: {root}")
    spans: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.jsonl")):
        spans.extend(load_spans(path))
    return spans


def group_traces(spans: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    traces: dict[str, list[dict[str, Any]]] = {}
    for s in spans:
        traces.setdefault(s["trace_id"], []).append(s)
    for tid in traces:
        traces[tid].sort(key=lambda s: (s["start_unix_nano"], s["end_unix_nano"]))
    return traces


# ---------------------------------------------------------------------------
# queries


def _duration_ms(span: dict[str, Any]) -> float:
    return max(0, span["end_unix_nano"] - span["start_unix_nano"]) / 1e6


def _root(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    ids = {s["span_id"] for s in spans}
    for s in spans:
        if s["parent_span_id"] is None or s["parent_span_id"] not in ids:
            return s
    return None


def run_summary(trace_id: str, spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact summary of one run (one trace)."""
    root = _root(spans)
    attrs = (root or {}).get("attributes", {})
    started = min(s["start_unix_nano"] for s in spans)
    ended = max(s["end_unix_nano"] for s in spans)
    llm_spans = [s for s in spans if s["name"] == "llm.complete"]
    tool_spans = [s for s in spans if s["name"] == "tool.call"]
    decisions = [
        e for s in spans for e in s.get("events", []) if e["name"] == "hitl.decision"
    ]
    return {
        "trace_id": trace_id,
        "task": attrs.get("agent.task"),
        "model": attrs.get("llm.model"),
        "started_unix_nano": started,
        "duration_ms": round((ended - started) / 1e6, 2),
        "spans": len(spans),
        "llm_calls": len(llm_spans),
        "tool_calls": len(tool_spans),
        "tools_used": sorted({s["attributes"].get("tool.name", "?") for s in tool_spans}),
        "human_decisions": len(decisions),
        "denials": sum(1 for e in decisions if e["attributes"].get("hitl.decision") == "deny"),
        "input_tokens": attrs.get("agent.usage.input_tokens"),
        "output_tokens": attrs.get("agent.usage.output_tokens"),
        "cost_usd": attrs.get("agent.cost_usd"),
        "stopped_reason": attrs.get("agent.stopped_reason"),
        "source": (root or spans[0]).get("_source_file"),
    }


def list_runs(spans: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    """Newest-first summaries of every trace."""
    traces = group_traces(spans)
    summaries = [run_summary(tid, t) for tid, t in traces.items()]
    summaries.sort(key=lambda s: s["started_unix_nano"], reverse=True)
    return summaries[:limit]


def span_tree(trace_id: str, spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Nested span tree with durations — the shape of the run."""
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for s in spans:
        by_parent.setdefault(s["parent_span_id"], []).append(s)
    for children in by_parent.values():
        children.sort(key=lambda s: s["start_unix_nano"])

    def node(span: dict[str, Any]) -> dict[str, Any]:
        events = [e["name"] for e in span.get("events", [])]
        out = {
            "name": span["name"],
            "duration_ms": round(_duration_ms(span), 2),
            "status": span["status"],
            "events": events,
            "children": [node(c) for c in by_parent.get(span["span_id"], [])],
        }
        attrs = span["attributes"]
        keep = {
            k: v
            for k, v in attrs.items()
            if k.startswith(("tool.", "llm.", "hitl.", "agent.stopped"))
        }
        if keep:
            out["attributes"] = keep
        return out

    root = _root(spans)
    return node(root) if root else {"error": f"no root span in trace {trace_id}"}


def slowest_spans(spans: list[dict[str, Any]], k: int = 5, name_filter: str | None = None) -> list[dict[str, Any]]:
    """Top-k slowest spans — 'why was this run slow?' in one call."""
    pool = [s for s in spans if name_filter is None or name_filter in s["name"]]
    pool.sort(key=_duration_ms, reverse=True)
    return [
        {
            "name": s["name"],
            "duration_ms": round(_duration_ms(s), 2),
            "tool": s["attributes"].get("tool.name"),
            "trace_id": s["trace_id"],
        }
        for s in pool[:k]
    ]


def approval_log(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every human decision in a run: what was gated, who decided, why."""
    out: list[dict[str, Any]] = []
    for s in sorted(spans, key=lambda s: s["start_unix_nano"]):
        tool = s["attributes"].get("tool.name", s["name"])
        for e in s.get("events", []):
            if e["name"] != "hitl.decision":
                continue
            a = e["attributes"]
            out.append(
                {
                    "tool": tool,
                    "decision": a.get("hitl.decision"),
                    "approver": a.get("hitl.approver"),
                    "rationale": a.get("hitl.rationale"),
                    "edited": bool(a.get("hitl.edited")),
                    "trace_id": s["trace_id"],
                }
            )
    return out


def token_usage(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate token + cost accounting across llm.complete spans."""
    in_tok = out_tok = 0
    cost = 0.0
    calls = 0
    for s in spans:
        if s["name"] != "llm.complete":
            continue
        calls += 1
        in_tok += int(s["attributes"].get("llm.usage.input_tokens", 0))
        out_tok += int(s["attributes"].get("llm.usage.output_tokens", 0))
        cost += float(s["attributes"].get("llm.cost_usd", 0.0))
    return {
        "llm_calls": calls,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
        "cost_usd": round(cost, 8),
    }


def search_spans(spans: list[dict[str, Any]], needle: str) -> list[dict[str, Any]]:
    """Substring search over span names and attribute values."""
    needle_l = needle.lower()
    hits = []
    for s in spans:
        haystacks = [s["name"]] + [str(v) for v in s["attributes"].values()]
        if any(needle_l in h.lower() for h in haystacks):
            hits.append(
                {
                    "trace_id": s["trace_id"],
                    "name": s["name"],
                    "duration_ms": round(_duration_ms(s), 2),
                    "matched": [h for h in haystacks if needle_l in h.lower()][:3],
                }
            )
    return hits
