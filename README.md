# mcp-trace

<!-- mcp-name: io.github.abhishekash/mcp-trace -->

[![CI](https://github.com/abhishekash/mcp-trace/actions/workflows/ci.yml/badge.svg)](https://github.com/abhishekash/mcp-trace/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Agents that can debug themselves.** An MCP server that exposes your agent runs — stored as plain OpenTelemetry JSONL span files — as queryable tools: runs, span trees, failures, tool health, security evidence, live activity, regressions, human-approval logs, token/cost usage.

The idea: observability shouldn't be a dashboard you read after the fact. It should be *tools your agent can call mid-run* — "why was I slow yesterday?", "what did the human deny me last time?", "which tool keeps timing out?" — or query interactively from Claude Desktop / pi / any MCP client.

Pairs with [agent-harness](https://github.com/abhishekash/agent-harness) (which writes the traces), but the reader is format-simple: any JSONL of OTel-shaped spans works.

## AI-native use cases

| Question during an agent run | MCP tool | Evidence returned |
|---|---|---|
| "Why did yesterday's run stall?" | `list_runs` → `slowest_spans` | Run IDs and the longest model or tool spans. |
| "Did the agent act after I denied the write?" | `approval_log` → `span_tree` | The recorded decision and subsequent execution path. |
| "How many model tokens did this run use?" | `token_usage` → `span_tree` | Run-level usage totals and the span tree for context. |
| "Which tool is failing or timing out?" | `failure_report` / `tool_stats` | Redacted diagnostics, error rates, denials, and p95 latency. |
| "Who/what was allowed to act?" | `security_audit` | Caller, tenant, server provenance, risk, and missing-evidence findings. |
| "What is happening in the run right now?" | `recent_activity` | Cursor-based polling of newly written spans. |
| "Did this model/version regress?" | `compare_runs` | Cost, latency, tokens, tool calls, and failure deltas. |

The tools read stored traces and can poll files being written, but they do not push events or infer intent from the final answer. See the [real trace fixture](examples/example_trace.jsonl) and the [tool descriptions](src/mcp_trace/server.py) for the exact query contract.

## Install & run

The `mcp-trace` name is occupied on PyPI by an unrelated project, so this server is published as `abhishekash-mcp-trace`; it exposes both the `abhishekash-mcp-trace` and `mcp-trace` commands.

```bash
uvx abhishekash-mcp-trace --trace-dir ./traces
# or, for local development:
git clone https://github.com/abhishekash/mcp-trace
cd mcp-trace && uv pip install -e .
mcp-trace --trace-dir ./traces
```

The package is published on [PyPI](https://pypi.org/project/abhishekash-mcp-trace/0.1.1/), and the validated [`server.json`](server.json) is live in the [official MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.abhishekash%2Fmcp-trace/versions/0.1.1).

## Client configuration

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "agent-traces": {
      "command": "uvx",
      "args": ["abhishekash-mcp-trace", "--trace-dir", "/path/to/traces"]
    }
  }
}
```

**pi** (`~/.pi/agent/settings.json`):

```json
{
  "mcpServers": {
    "agent-traces": {
      "command": "uvx",
      "args": ["abhishekash-mcp-trace", "--trace-dir", "/path/to/traces"]
    }
  }
}
```

**agent-harness** (mounted as gated tools):

```bash
harness run "Why was my last run slow?" --mcp "uvx abhishekash-mcp-trace --trace-dir ./traces"
```

## Tools

| Tool | Use it when |
|---|---|
| `list_runs` | Starting out — recent runs with task, model, duration, cost, decision counts |
| `run_summary` | One run at a glance (accepts trace-id prefix) |
| `span_tree` | "What did the agent actually do?" — nested shape of the run |
| `slowest_spans` | "Why was it slow?" — top-k spans by duration |
| `approval_log` | HITL audit — every approve/deny/**edit**, who decided, and *the rationale* |
| `token_usage` | Cost questions — aggregated across runs or per-run |
| `search_spans` | Find spans by tool name, file path, "denied", … |
| `failure_report` | Find actionable, bounded, redacted errors and failed tool calls. |
| `tool_stats` | Rank tools by volume, error rate, denials, and latency. |
| `security_audit` | Audit caller identity, tenant, server provenance, risk, and approval evidence. |
| `recent_activity` | Poll new spans with a cursor while a run is active. |
| `compare_runs` | Compare selected traces for regressions across models or versions. |

Tool descriptions are written as prompts (when-to-use, not just what-it-does) — descriptions are the interface for agent-called tools.

## Example session (real fixture trace)

```
> list_runs
[{ "trace_id": "f920798dd255…", "task": "Summarize the workspace's notes…",
   "tool_calls": 4, "human_decisions": 2, "stopped_reason": "completed" }]

> approval_log
[{ "tool": "write_file", "decision": "approve", "approver": "auto", … },
 { "tool": "run_shell",  "decision": "approve", "approver": "auto", … }]
```

## Design

```
traces/*.jsonl ──▶ mcp_trace.core (pure query functions, zero deps)
                          │
                   mcp_trace.server (thin MCPServer adapter, mcp 2.x)
                          │
                    stdio (NDJSON JSON-RPC)
```

- **core/server split**: all logic is pure functions over parsed spans; the MCP layer only parses args and JSON-encodes results. Tests hit both layers.
- **trace_id prefixes**: agents fumble full 32-char hex ids; every tool accepts prefixes.
- **bounded output**: diagnostics are truncated and obvious credentials are redacted before query results leave the server.
- **cursor polling**: `recent_activity` makes the snapshot reader useful while a run is still writing spans.
- The demo fixture ([`examples/example_trace.jsonl`](examples/example_trace.jsonl)) is a *real* agent-harness run, not hand-written.

## Research-driven gaps addressed

A small Reddit review surfaced the same production problems repeatedly: auth and
identity are unclear after the demo, versions and logs are hard to compare,
tool fleets become noisy and expensive, operators lack visibility into what is
happening, and raw logs are not a useful analysis surface. Examples:

- [ChatGPT + MCP gets painful after the demo](https://www.reddit.com/r/ChatGPTPro/comments/1uz6tzs/where_chatgpt_mcp_gets_painful_after_the_demo/) — auth, versions, logs, and safe tools.
- [MCP logging and correlation IDs](https://www.reddit.com/r/softwarearchitecture/comments/1r2wnfd/is_mcp_effectively_introducing_a_probabilistic/o51k4zq/) — preserve intent, tool arguments, results, and correlation IDs.
- [180 tools becomes a permission/context/debugging problem](https://www.reddit.com/r/ClaudeAI/comments/1tuqqpn/i_ship_ai_agents_in_production_the_mess_is_mcp/opbh78y/).
- [MCP security](https://www.reddit.com/r/cybersecurity/comments/1tgs4gg/mcp_security/) — identity, access, credentials, approved versions, and audit logs.
- [Raw logs are noisy and hard to query](https://www.reddit.com/r/ChatGPTPro/comments/1ur2tx4/i_made_my_codex_usage_tracker_more_agentnative/).

This update adds five focused query surfaces rather than pretending a
Dashboard solves those problems: `failure_report`, `tool_stats`,
`security_audit`, `recent_activity`, and `compare_runs`. Trace discovery is also
recursive, and query results redact obvious credential patterns.

## Honest limitations

- stdio transport only (Streamable HTTP plus authenticated remote access is the next transport boundary)
- live polling re-reads snapshots; it is not a push subscription
- security audit can only report identity/provenance that the trace producer records
- read-only tools; trace mutation (annotations) is roadmap

## License

MIT
