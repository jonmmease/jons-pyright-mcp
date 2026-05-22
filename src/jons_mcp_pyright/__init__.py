"""MCP server for Pyright LSP features."""

from .constants import (
    DEFAULT_PAGINATION_LIMIT,
    DEFAULT_PAGINATION_OFFSET,
    READ_BUFFER_SIZE,
    REQUEST_TIMEOUT,
    SHUTDOWN_TIMEOUT,
    LSPMethods,
)
from .environment import (
    EnvironmentState,
    discover_environments,
    get_environment_for_file,
)
from .exceptions import (
    DocumentSyncError,
    LSPRequestError,
    PathValidationError,
    Position,
    PyrightNotFoundError,
    PyrightNotInitializedError,
    Range,
)
from .lsp_client import PyrightClient
from .manager import PyrightClientManager
from .server import (
    ensure_pyright,
    ensure_pyright_indexed,
    get_manager,
    get_project_root,
    main,
    manager,
    mcp,
    resolve_file_for_tool,
)
from .tools import (
    definition,
    diagnostics,
    document_symbols,
    list_environments,
    preview_rename,
    references,
    restart_server,
    symbol_info,
    type_definition,
    type_info,
)
from .utils import apply_pagination, ensure_file_uri, resolve_project_file

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "DEFAULT_PAGINATION_LIMIT",
    "DEFAULT_PAGINATION_OFFSET",
    "LSPMethods",
    "READ_BUFFER_SIZE",
    "REQUEST_TIMEOUT",
    "SHUTDOWN_TIMEOUT",
    "DocumentSyncError",
    "LSPRequestError",
    "Position",
    "PathValidationError",
    "PyrightNotFoundError",
    "PyrightNotInitializedError",
    "Range",
    "PyrightClient",
    "PyrightClientManager",
    "EnvironmentState",
    "discover_environments",
    "get_environment_for_file",
    "ensure_pyright",
    "ensure_pyright_indexed",
    "get_manager",
    "get_project_root",
    "main",
    "manager",
    "mcp",
    "resolve_file_for_tool",
    "apply_pagination",
    "ensure_file_uri",
    "resolve_project_file",
    "symbol_info",
    "type_info",
    "definition",
    "type_definition",
    "references",
    "document_symbols",
    "diagnostics",
    "list_environments",
    "preview_rename",
    "restart_server",
]
