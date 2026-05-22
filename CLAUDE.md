# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Overview

`jons-mcp-pyright` is a FastMCP stdio server that exposes Pyright LSP features
as MCP tools. It manages one or more Pyright subprocesses, routes files to
discovered Python project roots, keeps opened documents synced with disk, and
rejects file inputs outside the configured project root before any LSP or
filesystem side effect.

## Commands

```bash
uv sync --extra dev
uv run jons-mcp-pyright /path/to/python/project
uv run pytest
uv run pytest --cov=src/jons_mcp_pyright --cov-report=term-missing
uv run ruff check .
uv run mypy src
uv build --wheel --out-dir /tmp/jons-mcp-pyright-wheel
```

Run a focused test:

```bash
uv run pytest tests/test_mcp_tools.py::TestCoreLanguageFeatures::test_symbol_info
```

## Package And Entry Point

- Package name: `jons-mcp-pyright`
- Import package: `jons_mcp_pyright`
- Console command: `jons-mcp-pyright`
- Source layout: packages are discovered under `src/`; do not reintroduce a
  packaged top-level `src` module.

The project is not published on PyPI. Documentation should use GitHub/local
install examples, not `pip install` or `uv add` package-name instructions.

## Architecture

```text
src/jons_mcp_pyright/
├── constants.py
├── environment.py      # project-root and environment discovery
├── exceptions.py
├── lsp_client.py       # Pyright subprocess and JSON-RPC/LSP framing
├── manager.py          # multi-environment routing, diagnostics, restarts
├── server.py           # FastMCP server, lifespan, path resolver entrypoint
├── utils.py            # path validation, file URIs, pagination, response helpers
└── tools/
    ├── extensions.py
    ├── intelligence.py
    └── language.py
```

## Safety Rules

- Resolve every user-supplied file path through the project-root resolver.
- Relative paths are relative to the configured project root, not process cwd.
- Reject outside-root absolute paths, outside-root `file://` URIs, `..` escapes,
  symlink escapes, missing files, and directories before Pyright is touched.
- LSP-returned external locations may be returned to the caller, but do not open
  or read external files for enrichment.
- Keep stdout protocol-clean; logs and startup errors go to stderr.
- `preview_rename` returns a sorted edit preview only; it must not write files.
- Public `line` and `character` inputs and returned ranges are one-based.
- Call `type_info` on value references when member discovery is desired.
- `references` and `preview_rename` are scoped to the active Pyright
  workspace/environment for the input file, not every environment in a monorepo.
- `preview_rename` should supplement Pyright rename edits with same-workspace
  reference ranges so import callers are included in the preview.
- Discovered uv workspace member projects should route to the enclosing
  `[tool.uv.workspace]` root so shared workspace dependencies resolve.

## Public Tools

`symbol_info`, `type_info`, `definition`, `type_definition`, `references`,
`document_symbols`, `diagnostics`, `preview_rename`, `list_environments`, and
`restart_server`.

Navigation tools return `items` and `totalItems`. Paginated tools return
`items`, `totalItems`, `offset`, `limit`, `hasMore`, and `nextOffset`. Errors
use `error.code`, `error.message`, and `error.retryable`.
