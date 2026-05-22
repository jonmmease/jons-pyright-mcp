# AGENTS.md

Guidance for Codex and other coding agents working in this repository.

## Project Overview

`jons-mcp-pyright` is a FastMCP stdio server for Pyright. It exposes Python code
intelligence through MCP tools, manages Pyright subprocess lifecycles, supports
multi-environment monorepos, and enforces a configured project-root filesystem
boundary for all user-supplied file paths.

## Development Commands

```bash
uv sync --extra dev
uv run jons-mcp-pyright /path/to/python/project
uv run pytest
uv run pytest --cov=src/jons_mcp_pyright --cov-report=term-missing
uv run ruff check .
uv run mypy src
uv build --wheel --out-dir /tmp/jons-mcp-pyright-wheel
```

## Architecture

- `lsp_client.py`: Pyright subprocess management and LSP JSON-RPC framing.
- `manager.py`: environment discovery, file-to-environment routing, diagnostics,
  client restart, and document state tracking.
- `server.py`: FastMCP setup, lifespan, CLI entry point, and project-root path
  resolver entrypoint.
- `utils.py`: file URI conversion, root-bound path validation, pagination, and
  tool response helpers.
- `tools/language.py`: symbol, navigation, references, document symbols, and
  `type_info`.
- `tools/intelligence.py`: diagnostics and rename previews.
- `tools/extensions.py`: environment listing and restart.

## Invariants

- The console command is `jons-mcp-pyright`; the import package is
  `jons_mcp_pyright`.
- The project uses a `src/` layout. Do not package a top-level `src` module.
- The package is not published on PyPI; docs should use GitHub or local checkout
  install examples.
- All file-taking tools must validate paths before LSP requests, notifications,
  or filesystem reads.
- Relative paths resolve from the configured project root, never process cwd.
- Out-of-root absolute paths, `file://` URIs, `..` escapes, symlink escapes,
  missing files, and directories are rejected.
- External LSP locations may be returned but must not be opened or read.
- Stdout must remain MCP-protocol clean; logs go to stderr.
- Public `line` and `character` inputs and returned ranges are one-based.
- `preview_rename` returns a sorted edit preview and does not write files.
- Call `type_info` on value references when member discovery is desired.
- `references` and `preview_rename` are scoped to the active Pyright
  workspace/environment for the input file, not every environment in a monorepo.
- `preview_rename` should supplement Pyright rename edits with same-workspace
  reference ranges so import callers are included in the preview.
- Discovered uv workspace member projects should route to the enclosing
  `[tool.uv.workspace]` root so shared workspace dependencies resolve.

## MCP Tools

`symbol_info`, `type_info`, `definition`, `type_definition`, `references`,
`document_symbols`, `diagnostics`, `preview_rename`, `list_environments`, and
`restart_server`.

Tool errors use `error.code`, `error.message`, and `error.retryable`.
