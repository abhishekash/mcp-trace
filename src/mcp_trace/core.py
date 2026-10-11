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
    """Load every JSONL span file below a trace directory."""
    root = Path(trace_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"trace dir not found: {root}")
    spans: list[dict[str, Any]] = []
    for path in sorted(p for p in root.rglob("*.jsonl") if p.is_file()):
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


_SECRET_PATTERNS = (
    (r"(?i)(authorization\s*:\s*bearer\s+)[^\s,]+", r"\1<redacted>"),
    (r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*[\"']?)[^\s,\"']+", r"\1<redacted>"),
    (r"(?i)\b(?:sk|ghp|github_pat|xoxb|xoxp)-[A-Za-z0-9_\-]{12,}\b", "<redacted>"),
)


def _redact_text(value: Any, limit: int = 500) -> str:
    import re

    text = str(value)
    for pattern, replacement in _SECRET_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text[:limit]


def _status_code(span: dict[str, Any]) -> str:
    status = span.get("status") or {}
    code = status.get("code", "") if isinstance(status, dict) else ""
    return str(code).upper()


def _span_error(span: dict[str, Any]) -> bool:
    attrs = span.get("attributes", {})
    result_status = str(attrs.get("tool.result_status", "")).lower()
    stopped = str(attrs.get("agent.stopped_reason", "")).lower()
    events = span.get("events", []) or []
    return bool(
        _status_code(span) in {"ERROR", "2"}
        or result_status in {"error", "denied"}
        or attrs.get("tool.error")
        or stopped not in {"", "completed"}
        or any(str(event.get("name", "")).lower() in {"exception", "error"} for event in events)
    )


def _span_error_detail(span: dict[str, Any]) -> str | None:
    attrs = span.get("attributes", {})
    for key in (
        "tool.error_message",
        "error.message",
        "exception.message",
        "llm.error",
        "agent.error",
        "tool.error",
        "agent.stopped_reason",
    ):
        if attrs.get(key) and not (
            key == "agent.stopped_reason" and str(attrs[key]).lower() == "completed"
        ):
            return _redact_text(attrs[key])
    for event in span.get("events", []) or []:
        event_attrs = event.get("attributes", {}) or {}
        for key in ("exception.message", "error.message"):
            if event_attrs.get(key):
                return _redact_text(event_attrs[key])
    return None


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * 0.95))]


def _trace_groups(spans: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return group_traces(spans)


def _resolve_trace_prefix(spans: Iterable[dict[str, Any]], prefix: str) -> tuple[str, list[dict[str, Any]]]:
    groups = _trace_groups(spans)
    matches = [(trace_id, trace_spans) for trace_id, trace_spans in groups.items() if trace_id.startswith(prefix)]
    if not matches:
        raise ValueError(f"no trace matching {prefix!r}")
    if len(matches) > 1:
        raise ValueError(f"trace prefix {prefix!r} is ambiguous")
    return matches[0]


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
            out["attributes"] = {
                key: _redact_text(value) if isinstance(value, str) else value
                for key, value in keep.items()
            }
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
                    "rationale": _redact_text(a.get("hitl.rationale", "")),
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


def failure_report(
    spans: list[dict[str, Any]],
    *,
    trace_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return actionable failed/error spans instead of raw JSONL.

    This deliberately reports bounded, redacted diagnostics. It does not copy
    normal tool output into the result, which keeps an observability query from
    becoming a secret-exfiltration path.
    """
    pool = spans if trace_id is None else _resolve_trace_prefix(spans, trace_id)[1]
    failures = [span for span in pool if _span_error(span)]
    failures.sort(key=lambda span: span.get("start_unix_nano", 0), reverse=True)
    out = []
    for span in failures[: max(0, limit)]:
        attrs = span.get("attributes", {})
        out.append(
            {
                "trace_id": span.get("trace_id"),
                "span_id": span.get("span_id"),
                "name": span.get("name"),
                "duration_ms": round(_duration_ms(span), 2),
                "tool": attrs.get("tool.name"),
                "status": _status_code(span) or "UNSET",
                "result_status": attrs.get("tool.result_status"),
                "detail": _span_error_detail(span),
                "events": [event.get("name") for event in span.get("events", []) or []],
            }
        )
    return out


def tool_stats(
    spans: list[dict[str, Any]],
    *,
    trace_id: str | None = None,
    tool_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Aggregate tool health: volume, latency, errors, and denials."""
    pool = spans if trace_id is None else _resolve_trace_prefix(spans, trace_id)[1]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for span in pool:
        name = span.get("attributes", {}).get("tool.name")
        if span.get("name") != "tool.call" or not name:
            continue
        if tool_name and name != tool_name:
            continue
        grouped.setdefault(str(name), []).append(span)

    rows = []
    for name, calls in grouped.items():
        durations = [_duration_ms(span) for span in calls]
        errors = [span for span in calls if _span_error(span)]
        denials = [
            event
            for span in calls
            for event in span.get("events", []) or []
            if event.get("name") == "hitl.decision"
            and event.get("attributes", {}).get("hitl.decision") == "deny"
        ]
        last_error = next((_span_error_detail(span) for span in calls if _span_error_detail(span)), None)
        rows.append(
            {
                "tool": name,
                "calls": len(calls),
                "errors": len(errors),
                "error_rate": round(len(errors) / len(calls), 4),
                "denials": len(denials),
                "mean_duration_ms": round(sum(durations) / len(durations), 2),
                "p95_duration_ms": round(_p95(durations), 2),
                "last_error": last_error,
                "traces": sorted({span.get("trace_id") for span in calls}),
            }
        )
    rows.sort(key=lambda row: (-row["errors"], -row["mean_duration_ms"], row["tool"]))
    return rows[: max(0, limit)]


def security_audit(
    spans: list[dict[str, Any]],
    *,
    trace_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Summarize identity, provenance, authorization, and dangerous actions.

    Missing identity/provenance is returned explicitly as a finding. The trace
    reader cannot infer who authorized a call when the producer did not record
    it.
    """
    pool = spans if trace_id is None else _resolve_trace_prefix(spans, trace_id)[1]
    calls = [span for span in pool if span.get("name") == "tool.call"]
    identity_keys = ("agent.identity", "auth.subject", "user.id", "mcp.client_id")
    tenant_keys = ("auth.tenant", "tenant.id", "agent.tenant")
    server_keys = ("mcp.server", "mcp.server.name", "mcp.server.version")
    records = []
    missing_identity = 0
    missing_provenance = 0
    denied = 0
    for index, span in enumerate(sorted(calls, key=lambda item: item.get("start_unix_nano", 0))):
        attrs = span.get("attributes", {})
        identity = next((attrs[key] for key in identity_keys if attrs.get(key)), None)
        tenant = next((attrs[key] for key in tenant_keys if attrs.get(key)), None)
        server = next((attrs[key] for key in server_keys if attrs.get(key)), None)
        decision_events = [
            event for event in span.get("events", []) or [] if event.get("name") == "hitl.decision"
        ]
        decision = decision_events[-1].get("attributes", {}).get("hitl.decision") if decision_events else None
        if not identity:
            missing_identity += 1
        if not server:
            missing_provenance += 1
        if decision == "deny":
            denied += 1
        if index >= max(0, limit):
            continue
        records.append(
            {
                "trace_id": span.get("trace_id"),
                "span_id": span.get("span_id"),
                "tool": attrs.get("tool.name"),
                "risk": attrs.get("tool.risk"),
                "identity": _redact_text(identity) if identity else None,
                "tenant": _redact_text(tenant) if tenant else None,
                "server": _redact_text(server) if server else None,
                "decision": decision,
                "result_status": attrs.get("tool.result_status"),
            }
        )
    return {
        "calls": len(calls),
        "denied": denied,
        "missing_identity": missing_identity,
        "missing_server_provenance": missing_provenance,
        "findings": [
            finding
            for finding in (
                "tool calls lack recorded caller identity" if missing_identity else None,
                "tool calls lack recorded MCP server provenance" if missing_provenance else None,
            )
            if finding
        ],
        "records": records,
    }


def recent_activity(
    spans: list[dict[str, Any]],
    *,
    cursor: str | None = None,
    trace_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return an incremental, redacted span feed for polling a live directory."""
    pool = spans if trace_id is None else _resolve_trace_prefix(spans, trace_id)[1]
    after = (0, "")
    if cursor:
        try:
            start, span_id = cursor.split(":", 1)
            after = (int(start), span_id)
        except ValueError as exc:
            raise ValueError("cursor must be '<start_unix_nano>:<span_id>'") from exc
    ordered = sorted(
        (span for span in pool if (span.get("start_unix_nano", 0), span.get("span_id", "")) > after),
        key=lambda span: (span.get("start_unix_nano", 0), span.get("span_id", "")),
    )
    selected = ordered[: max(0, limit)]
    records = []
    for span in selected:
        attrs = span.get("attributes", {})
        records.append(
            {
                "trace_id": span.get("trace_id"),
                "span_id": span.get("span_id"),
                "name": span.get("name"),
                "start_unix_nano": span.get("start_unix_nano"),
                "duration_ms": round(_duration_ms(span), 2),
                "status": _status_code(span) or "UNSET",
                "tool": attrs.get("tool.name"),
                "result_status": attrs.get("tool.result_status"),
                "error": _span_error_detail(span),
                "events": [event.get("name") for event in span.get("events", []) or []],
            }
        )
    next_cursor = cursor
    if selected:
        last = selected[-1]
        next_cursor = f"{last.get('start_unix_nano', 0)}:{last.get('span_id', '')}"
    return {"next_cursor": next_cursor, "count": len(records), "spans": records}


def compare_runs(spans: list[dict[str, Any]], trace_ids: list[str]) -> dict[str, Any]:
    """Compare selected runs for latency, cost, tokens, tools, and failures."""
    if not trace_ids:
        raise ValueError("trace_ids must contain at least one trace-id prefix")
    runs = []
    for prefix in trace_ids:
        full_id, trace_spans = _resolve_trace_prefix(spans, prefix)
        summary = run_summary(full_id, trace_spans)
        usage = token_usage(trace_spans)
        runs.append(
            {
                "trace_id": full_id,
                "task": summary.get("task"),
                "model": summary.get("model"),
                "stopped_reason": summary.get("stopped_reason"),
                "duration_ms": summary.get("duration_ms", 0),
                "cost_usd": usage["cost_usd"],
                "total_tokens": usage["total_tokens"],
                "tool_calls": summary.get("tool_calls", 0),
                "failures": len(failure_report(trace_spans)),
            }
        )
    baseline = runs[0]
    numeric = ("duration_ms", "cost_usd", "total_tokens", "tool_calls", "failures")
    for run in runs:
        run["delta_vs_first"] = {
            key: round(float(run[key]) - float(baseline[key]), 8) for key in numeric
        }
    return {"baseline": baseline["trace_id"], "runs": runs}


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
                    "matched": [_redact_text(h) for h in haystacks if needle_l in h.lower()][:3],
                }
            )
    return hits
