# jons-mcp-pyright

A FastMCP stdio server that exposes Pyright language intelligence through MCP.
It starts Pyright as a subprocess, keeps LSP document state fresh with disk
contents, and provides root-bound tools for navigating and understanding Python
projects.

This project is not published on PyPI. Install or run it from GitHub or a local
checkout.

## Installation

Run directly from GitHub:

```bash
uvx --from git+https://github.com/jonmmease/jons-pyright-mcp.git jons-mcp-pyright /path/to/python/project
```

Run from a local checkout:

```bash
git clone https://github.com/jonmmease/jons-pyright-mcp.git
cd jons-pyright-mcp
uv sync --extra dev
uv run jons-mcp-pyright /path/to/python/project
```

The command is `jons-mcp-pyright`. The final optional argument is the target
project root. If omitted, the server uses its current working directory.

## MCP Client Setup

### Claude Code

From the Python project you want Pyright to analyze:

```bash
claude mcp add --scope project jons-mcp-pyright -- \
  uvx --from git+https://github.com/jonmmease/jons-pyright-mcp.git \
  jons-mcp-pyright "$(pwd)"
```

For a local server checkout:

```bash
claude mcp add --scope project jons-mcp-pyright -- \
  uv run --project /path/to/jons-pyright-mcp \
  jons-mcp-pyright "$(pwd)"
```

### Codex CLI

```bash
codex mcp add jons-mcp-pyright -- \
  uvx --from git+https://github.com/jonmmease/jons-pyright-mcp.git \
  jons-mcp-pyright /path/to/python/project
```

### `.mcp.json`

```json
{
  "mcpServers": {
    "jons-mcp-pyright": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/jonmmease/jons-pyright-mcp.git",
        "jons-mcp-pyright",
        "/absolute/path/to/python/project"
      ]
    }
  }
}
```

### Codex TOML

```toml
[mcp_servers.jons-mcp-pyright]
command = "uvx"
args = [
  "--from",
  "git+https://github.com/jonmmease/jons-pyright-mcp.git",
  "jons-mcp-pyright",
  "/absolute/path/to/python/project",
]
```

## Project Root Semantics

The configured project root is the filesystem boundary for all MCP tool file
inputs.

- Relative paths resolve from the configured project root, not the MCP process
  cwd.
- Absolute paths and `file://` URIs must resolve inside the configured root.
- `..` escapes, symlink escapes, missing files, and directories are rejected
  before Pyright or the filesystem is touched.
- LSP responses may include external locations, but the server does not open or
  read external files for enrichment.

Pyright reads `pyrightconfig.json` and Python project metadata from the selected
root and discovered nested project roots. Virtual environments are detected from
common names such as `.venv`, `venv`, `.env`, and Pixi environments under
`.pixi/envs/<name>`.

## Prerequisites

- Python 3.10 or newer.
- `uv` for the documented install commands.
- A Python target project. For best results, include `pyrightconfig.json` or
  `pyproject.toml`.
- Target-project dependencies should be installed in the environment Pyright
  should analyze. The server can also be pointed at a specific Pyright command
  with `PYRIGHT_PATH`.

## Tools

Navigation and discovery:

- `document_symbols`
- `definition`
- `type_definition`
- `implementation`
- `references`

Understanding code:

- `symbol_info`
- `type_info`

Code intelligence and refactoring:

- `diagnostics`
- `preview_rename`

Server management:

- `list_environments`
- `restart_server`

All public `line` and `character` inputs and returned ranges are one-based.
`preview_rename` is preview-only: it returns sorted text edits and never writes
files. `type_info` works best when called on a value reference such as `obj` in
`obj.method()`, rather than on a class or variable declaration, when member
discovery is desired.

Paginated tools return `items`, `totalItems`, `offset`, `limit`, `hasMore`, and
`nextOffset`. Navigation tools return `items` and `totalItems`. Errors use a
structured `error.code`, `error.message`, and `error.retryable` shape.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run pytest --cov=src/jons_mcp_pyright --cov-report=term-missing
uv run ruff check .
uv run mypy src
uv build --wheel --out-dir /tmp/jons-mcp-pyright-wheel
```

Inspect a built wheel:

```bash
uv run python -m zipfile -l /tmp/jons-mcp-pyright-wheel/jons_mcp_pyright-0.1.0-py3-none-any.whl
```

The wheel should contain top-level `jons_mcp_pyright`, not a packaged top-level
`src` module, and should not include build artifacts or `node_modules`.
