# Contributing to mcp-trace

## Development setup

```bash
uv venv
uv pip install -e ".[dev]"
pytest -q
```

The core query engine is dependency-light and should remain pure. Server tests exercise the MCP 2.x protocol adapter as well as the core functions.

## Pull requests

- Add a deterministic fixture for new query behavior.
- Test the tool through `MCPServer.call_tool`, not only by calling the underlying function.
- Keep tool descriptions phrased as when-to-use prompts.
- Preserve trace-id prefix support and compact output defaults.
- Update the README's tool table and limitations when behavior changes.
