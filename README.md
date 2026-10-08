# mcp-trace

<!-- mcp-name: io.github.abhishekash/mcp-trace -->

[![CI](https://github.com/abhishekash/mcp-trace/actions/workflows/ci.yml/badge.svg)](https://github.com/abhishekash/mcp-trace/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Agents that can debug themselves.** An MCP server that exposes your agent runs — stored as plain OpenTelemetry JSONL span files — as queryable tools: runs, span trees, slow spans, human-approval logs, token/cost usage.

The idea: observability shouldn't be a dashboard you read after the fact. It should be *tools your agent can call mid-run* — "why was I slow yesterday?", "what did the human deny me last time?", "which tool keeps timing out?" — or query interactively from Claude Desktop / pi / any MCP client.

Pairs with [agent-harness](https://github.com/abhishekash/agent-harness) (which writes the traces), but the reader is format-simple: any JSONL of OTel-shaped spans works.

## Install & run

This repository is not claiming a PyPI release yet. The `mcp-trace` name is already occupied on PyPI by an unrelated project, so the planned distribution name is `abhishekash-mcp-trace`; it still exposes the `mcp-trace` command. Until publication, run this repository directly from GitHub:

```bash
uvx --from git+https://github.com/abhishekash/mcp-trace.git mcp-trace --trace-dir ./traces
# after PyPI publication:
uvx abhishekash-mcp-trace --trace-dir ./traces
# or, for local development:
git clone https://github.com/abhishekash/mcp-trace
cd mcp-trace && uv pip install -e .
mcp-trace --trace-dir ./traces
```

The registry manifest is checked in at [`server.json`](server.json), pointing to the unique PyPI distribution `abhishekash-mcp-trace`. The package has passed local `uv build` and `twine check`; PyPI publication is wired through GitHub trusted publishing in [`.github/workflows/publish-pypi.yml`](.github/workflows/publish-pypi.yml).

## Client configuration

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "agent-traces": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/abhishekash/mcp-trace.git", "mcp-trace", "--trace-dir", "/path/to/traces"]
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
      "args": ["--from", "git+https://github.com/abhishekash/mcp-trace.git", "mcp-trace", "--trace-dir", "/path/to/traces"]
    }
  }
}
```

**agent-harness** (mounted as gated tools):

```bash
harness run "Why was my last run slow?" --mcp "uvx --from git+https://github.com/abhishekash/mcp-trace.git mcp-trace --trace-dir ./traces"
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
- The demo fixture ([`examples/example_trace.jsonl`](examples/example_trace.jsonl)) is a *real* agent-harness run, not hand-written.

## Honest limitations

- stdio transport only (no Streamable HTTP yet)
- non-recursive trace-dir scan; very large dirs should use per-file loading
- no span streaming/watching — snapshots at call time
- v0.1: read-only tools; trace *mutation* (annotations) is roadmap

## License

MIT
