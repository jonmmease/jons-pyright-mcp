#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fastmcp>=0.3.0",
#   "pyright>=1.1.0",
# ]
# ///

"""
FastMCP server that exposes pyright LSP features through MCP tools.

This server manages pyright as a subprocess and translates between MCP and LSP protocols.
It assumes it's launched from a Python project's root directory and analyzes that specific project.
"""

import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import threading
import queue
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Union, Set
from dataclasses import dataclass
from enum import Enum

from contextlib import asynccontextmanager
from fastmcp import FastMCP, Context

# Configure logging
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global pyright client instance
pyright: Optional['PyrightClient'] = None

# Store diagnostics from pyright
current_diagnostics: Dict[str, List[Dict[str, Any]]] = {}

# Track opened files
opened_files: Set[str] = set()

# Track initialization state
initialization_complete = False


async def handle_diagnostics(params: Dict[str, Any]):
    """Handle diagnostics notification from pyright"""
    uri = params.get("uri", "")
    diagnostics = params.get("diagnostics", [])
    current_diagnostics[uri] = diagnostics
    logger.info(f"Received {len(diagnostics)} diagnostics for {uri}")


@asynccontextmanager
async def lifespan(mcp: FastMCP):
    """Manage the lifecycle of the pyright client"""
    global pyright, initialization_complete
    
    # Startup
    project_root = Path.cwd()
    logger.info(f"Starting MCP server in project: {project_root}")
    
    # Check if this is a Python project
    if not any((project_root / f).exists() for f in ["setup.py", "pyproject.toml", "requirements.txt"]):
        logger.warning("No Python project files found. Consider creating pyrightconfig.json for better results.")
    
    pyright = PyrightClient(project_root)
    pyright.on_notification("textDocument/publishDiagnostics", handle_diagnostics)
    
    try:
        await pyright.start()
        # Give pyright more time to analyze the project initially
        logger.info("Waiting for pyright to analyze the project...")
        await asyncio.sleep(2.0)
        initialization_complete = True
        logger.info("Pyright initialization complete")
    except Exception as e:
        logger.error(f"Failed to start pyright: {e}")
        raise
    
    yield
    
    # Shutdown
    initialization_complete = False
    if pyright:
        await pyright.shutdown()
        pyright = None


# Create FastMCP server instance with lifespan
mcp = FastMCP(
    name="pyright-mcp",
    lifespan=lifespan
)


class LSPRequestError(Exception):
    """Raised when an LSP request fails"""
    pass


@dataclass
class Position:
    """LSP position in a text document"""
    line: int
    character: int
    
    def to_dict(self) -> Dict[str, int]:
        return {"line": self.line, "character": self.character}


@dataclass
class Range:
    """LSP range in a text document"""
    start: Position
    end: Position
    
    def to_dict(self) -> Dict[str, Any]:
        return {"start": self.start.to_dict(), "end": self.end.to_dict()}


class PyrightClient:
    """AsyncIO-based LSP client for pyright"""
    
    def __init__(self, project_root: Path, pyright_path: Optional[str] = None):
        self.project_root = project_root
        self.pyright_path = pyright_path or self._find_pyright()
        self.process: Optional[asyncio.subprocess.Process] = None
        self.request_id = 0
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self.notification_handlers: Dict[str, Callable] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._initialized = False
        self._shutting_down = False
        # Increase timeout for large projects
        self.request_timeout = float(os.environ.get("PYRIGHT_TIMEOUT", "60.0"))
        
    def _find_pyright(self) -> str:
        """Find pyright executable"""
        # Check environment variable first
        if env_path := os.environ.get("PYRIGHT_PATH"):
            return env_path
            
        # Try to find pyright-langserver from the pyright package
        try:
            # When installed via pip install pyright, it provides pyright.langserver
            import pyright
            # The pyright package includes node and the langserver
            # We can run it directly via the pyright.langserver module with --stdio flag
            return sys.executable + " -m pyright.langserver --stdio"
        except ImportError:
            pass
            
        # Check if pyright-langserver is on PATH
        if path := shutil.which("pyright-langserver"):
            return path
            
        # Check if pyright is on PATH (CLI version)
        if path := shutil.which("pyright"):
            # Try to use it with --langserver flag
            return f"{path} --langserver"
            
        raise RuntimeError(
            "pyright not found. Please install it with 'pip install pyright' or set PYRIGHT_PATH"
        )
        
    async def start(self):
        """Start pyright subprocess"""
        if self.process:
            return
            
        logger.info(f"Starting pyright for project: {self.project_root}")
        logger.info(f"Using pyright command: {self.pyright_path}")
        
        try:
            # Split the command if it contains spaces
            cmd_parts = self.pyright_path.split()
            
            self.process = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.project_root)
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start pyright: {e}")
            
        # Start background tasks
        self._reader_task = asyncio.create_task(self._read_loop())
        asyncio.create_task(self._stderr_reader())
        
        # Initialize LSP connection
        await self._initialize()
        
    async def _initialize(self):
        """Send LSP initialize request"""
        response = await self.request("initialize", {
            "processId": os.getpid(),
            "clientInfo": {
                "name": "pyright-mcp",
                "version": "0.1.0"
            },
            "rootUri": f"file://{self.project_root.absolute()}",
            "capabilities": {
                "textDocument": {
                    "hover": {
                        "contentFormat": ["plaintext", "markdown"]
                    },
                    "completion": {
                        "completionItem": {
                            "snippetSupport": True,
                            "resolveSupport": {
                                "properties": ["documentation", "detail", "additionalTextEdits"]
                            }
                        }
                    },
                    "signatureHelp": {
                        "signatureInformation": {
                            "documentationFormat": ["plaintext", "markdown"],
                            "parameterInformation": {
                                "labelOffsetSupport": True
                            }
                        }
                    },
                    "definition": {"linkSupport": True},
                    "typeDefinition": {"linkSupport": True},
                    "implementation": {"linkSupport": True},
                    "references": {},
                    "documentHighlight": {},
                    "documentSymbol": {
                        "hierarchicalDocumentSymbolSupport": True
                    },
                    "formatting": {},
                    "rangeFormatting": {},
                    "rename": {"prepareSupport": True},
                    "codeAction": {
                        "codeActionLiteralSupport": {
                            "codeActionKind": {
                                "valueSet": [
                                    "quickfix",
                                    "refactor",
                                    "refactor.extract",
                                    "refactor.inline",
                                    "refactor.rewrite",
                                    "source",
                                    "source.organizeImports"
                                ]
                            }
                        },
                        "resolveSupport": {
                            "properties": ["edit"]
                        }
                    },
                    "publishDiagnostics": {
                        "relatedInformation": True
                    },
                    "callHierarchy": {},
                    "semanticTokens": {
                        "requests": {
                            "full": True,
                            "range": True
                        },
                        "tokenTypes": [],
                        "tokenModifiers": [],
                        "formats": ["relative"]
                    }
                },
                "workspace": {
                    "applyEdit": True,
                    "symbol": {},
                    "executeCommand": {},
                    "workspaceFolders": True,
                    "configuration": True
                }
            },
            "initializationOptions": {
                "python": {
                    "analysis": {
                        "autoSearchPaths": True,
                        "useLibraryCodeForTypes": True,
                        "diagnosticMode": "workspace",
                        "typeCheckingMode": "basic"
                    }
                }
            }
        })
        
        logger.info("pyright initialized successfully")
        self._initialized = True
        
        # Send initialized notification
        await self.notify("initialized", {})
        
        return response
        
    async def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Send request and wait for response"""
        if self._shutting_down:
            raise LSPRequestError("Client is shutting down")
            
        self.request_id += 1
        request_id = self.request_id
        
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {}
        }
        
        # Create future for response
        future = asyncio.Future()
        self.pending_requests[request_id] = future
        
        # Send request
        await self._send_message(message)
        
        # Wait for response with timeout
        try:
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except asyncio.TimeoutError:
            self.pending_requests.pop(request_id, None)
            raise LSPRequestError(f"Request {method} timed out after {self.request_timeout}s")
            
    async def notify(self, method: str, params: Optional[Dict[str, Any]] = None):
        """Send notification (no response expected)"""
        if self._shutting_down:
            return
            
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }
        await self._send_message(message)
        
    async def _send_message(self, message: Dict[str, Any]):
        """Send LSP message with proper headers"""
        if not self.process or not self.process.stdin:
            raise LSPRequestError("Process not started")
            
        content = json.dumps(message, separators=(',', ':'))
        content_bytes = content.encode('utf-8')
        
        header = f"Content-Length: {len(content_bytes)}\r\n\r\n"
        
        self.process.stdin.write(header.encode('utf-8'))
        self.process.stdin.write(content_bytes)
        await self.process.stdin.drain()
        
        logger.debug(f"Sent: {message}")
        
    async def _read_loop(self):
        """Read messages from pyright"""
        reader = self.process.stdout
        
        while self.process and reader and not self._shutting_down:
            try:
                # Read headers line by line until we get empty line
                headers = []
                while True:
                    line = await reader.readline()
                    if not line:
                        return  # EOF
                    
                    line = line.decode('utf-8').rstrip('\r\n')
                    if not line:
                        break  # Empty line, headers done
                    headers.append(line)
                
                # Parse Content-Length
                content_length = None
                for header in headers:
                    if header.startswith('Content-Length: '):
                        content_length = int(header[16:])
                        break
                
                if content_length is None:
                    logger.error("No Content-Length header found")
                    continue
                
                # Read exact content length
                content = await reader.readexactly(content_length)
                
                # Parse JSON
                try:
                    message = json.loads(content.decode('utf-8'))
                    await self._handle_message(message)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON: {e}")
                    
            except asyncio.IncompleteReadError:
                # Connection closed
                break
            except Exception as e:
                logger.error(f"Error in read loop: {e}", exc_info=True)
                break
                
    def _parse_message(self, buffer: bytes) -> tuple[Optional[Dict], bytes]:
        """Parse LSP message from buffer"""
        # Look for Content-Length header
        header_end = buffer.find(b"\r\n\r\n")
        if header_end == -1:
            return None, buffer
            
        header = buffer[:header_end].decode('utf-8')
        content_start = header_end + 4
        
        # Extract content length
        content_length = None
        for line in header.split('\r\n'):
            if line.startswith('Content-Length: '):
                content_length = int(line[16:])
                break
                
        if content_length is None:
            return None, buffer
            
        # Check if we have complete content
        if len(buffer) < content_start + content_length:
            return None, buffer
            
        # Extract and parse content
        content = buffer[content_start:content_start + content_length]
        try:
            message = json.loads(content.decode('utf-8'))
            remaining = buffer[content_start + content_length:]
            return message, remaining
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e}")
            return None, buffer[content_start + content_length:]
            
    async def _handle_message(self, message: Dict[str, Any]):
        """Handle incoming LSP message"""
        logger.debug(f"Received: {message}")
        
        if 'id' in message:
            # Response to our request
            request_id = message['id']
            future = self.pending_requests.pop(request_id, None)
            
            if future and not future.done():
                if 'error' in message:
                    error = message['error']
                    future.set_exception(
                        LSPRequestError(f"{error.get('message', 'Unknown error')} (code: {error.get('code')})")
                    )
                else:
                    future.set_result(message.get('result'))
        else:
            # Server notification
            method = message.get('method', '')
            params = message.get('params', {})
            
            handler = self.notification_handlers.get(method)
            if handler:
                try:
                    await handler(params)
                except Exception as e:
                    logger.error(f"Error in notification handler for {method}: {e}")
            else:
                logger.debug(f"Unhandled notification: {method}")
                
    async def _stderr_reader(self):
        """Read stderr output from pyright"""
        while self.process and self.process.stderr and not self._shutting_down:
            try:
                line = await self.process.stderr.readline()
                if line:
                    decoded = line.decode().strip()
                    # Log at INFO level so we can see errors during testing
                    if "error" in decoded.lower() or "panic" in decoded.lower():
                        logger.error(f"pyright stderr: {decoded}")
                    else:
                        logger.info(f"pyright stderr: {decoded}")
                else:
                    break
            except Exception:
                break
                
    def on_notification(self, method: str, handler: Callable):
        """Register notification handler"""
        self.notification_handlers[method] = handler
        
    async def shutdown(self):
        """Properly shutdown pyright"""
        if not self.process or self._shutting_down:
            return
            
        self._shutting_down = True
        
        try:
            # Send shutdown request
            await self.request("shutdown")
            
            # Send exit notification
            await self.notify("exit")
            
            # Wait for process to exit
            await asyncio.wait_for(self.process.wait(), timeout=5.0)
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            if self.process:
                self.process.terminate()
                await self.process.wait()
                
        finally:
            # Cancel reader task
            if self._reader_task:
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except asyncio.CancelledError:
                    pass
                    
            self.process = None
            self._initialized = False
            self._shutting_down = False




def ensure_file_uri(file_path: str) -> str:
    """Convert file path to proper file URI"""
    if file_path.startswith("file://"):
        return file_path
    
    path = Path(file_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    
    return f"file://{path.absolute()}"


def ensure_pyright() -> PyrightClient:
    """Ensure pyright is initialized"""
    if not pyright or not pyright._initialized:
        raise RuntimeError("pyright is not initialized")
    if not initialization_complete:
        raise RuntimeError("pyright is still initializing, please try again in a few seconds")
    return pyright


async def ensure_file_open(client: PyrightClient, file_path: str, file_uri: str) -> bool:
    """Ensure file is opened in pyright"""
    global opened_files
    
    if file_uri in opened_files:
        return True
        
    # Open the file in pyright if it exists
    file_path_obj = Path(file_path)
    if not file_path_obj.is_absolute():
        file_path_obj = Path.cwd() / file_path_obj
    
    if file_path_obj.exists():
        try:
            content = file_path_obj.read_text()
            await client.notify("textDocument/didOpen", {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": "python",
                    "version": 1,
                    "text": content
                }
            })
            opened_files.add(file_uri)
            # Give pyright a moment to process the file
            await asyncio.sleep(0.2)
            return True
        except Exception as e:
            logger.warning(f"Could not open file {file_path}: {e}")
            return False
    return False


# MCP Tools - Core Language Features

@mcp.tool
async def hover(file_path: str, line: int, character: int, ctx: Context) -> Dict[str, Any]:
    """Get hover information at the specified position in a Python file.
    
    Args:
        file_path: Path to the Python file (absolute or relative)
        line: Zero-based line number
        character: Zero-based character offset in the line
        
    Returns:
        Hover information including type info, documentation, etc.
    """
    try:
        client = ensure_pyright()
    except RuntimeError as e:
        if "still initializing" in str(e):
            return {"error": "Pyright is still initializing. Please try again in a few seconds."}
        return {"error": f"Pyright error: {e}"}
    
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Getting hover info at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    try:
        response = await client.request("textDocument/hover", {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character}
        })
    except LSPRequestError as e:
        if "timed out" in str(e):
            return {"error": "Request timed out. The file might be too large or pyright is still analyzing. Please try again."}
        return {"error": f"LSP error: {e}"}
    
    if not response:
        return {"contents": "No hover information available"}
        
    return response


@mcp.tool
async def completion(file_path: str, line: int, character: int, ctx: Context) -> List[Dict[str, Any]]:
    """Get code completions at the specified position.
    
    Args:
        file_path: Path to the Python file
        line: Zero-based line number
        character: Zero-based character offset in the line
        
    Returns:
        List of completion items with labels, kinds, and documentation
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Getting completions at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("textDocument/completion", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character}
    })
    
    # Handle both array and CompletionList responses
    if isinstance(response, list):
        return response
    elif isinstance(response, dict) and "items" in response:
        return response["items"]
    else:
        return []


@mcp.tool
async def definition(file_path: str, line: int, character: int, ctx: Context) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """Go to definition of the symbol at the specified position.
    
    Args:
        file_path: Path to the Python file
        line: Zero-based line number
        character: Zero-based character offset in the line
        
    Returns:
        Location(s) of the definition
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Finding definition at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("textDocument/definition", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character}
    })
    
    return response or {"message": "No definition found"}


@mcp.tool
async def type_definition(file_path: str, line: int, character: int, ctx: Context) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """Go to type definition of the symbol at the specified position.
    
    Args:
        file_path: Path to the Python file
        line: Zero-based line number
        character: Zero-based character offset in the line
        
    Returns:
        Location(s) of the type definition
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Finding type definition at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("textDocument/typeDefinition", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character}
    })
    
    return response or {"message": "No type definition found"}


@mcp.tool
async def implementation(file_path: str, line: int, character: int, ctx: Context) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """Find implementations of the class/protocol at the specified position.
    
    Args:
        file_path: Path to the Python file
        line: Zero-based line number
        character: Zero-based character offset in the line
        
    Returns:
        Location(s) of implementations
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Finding implementations at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("textDocument/implementation", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character}
    })
    
    return response or {"message": "No implementations found"}


@mcp.tool
async def references(file_path: str, line: int, character: int, include_declaration: bool = True, ctx: Context = None) -> List[Dict[str, Any]]:
    """Find all references to the symbol at the specified position.
    
    Args:
        file_path: Path to the Python file
        line: Zero-based line number
        character: Zero-based character offset in the line
        include_declaration: Whether to include the declaration itself
        
    Returns:
        List of reference locations
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    if ctx:
        await ctx.info(f"Finding references at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("textDocument/references", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character},
        "context": {"includeDeclaration": include_declaration}
    })
    
    return response or []


@mcp.tool
async def document_symbols(file_path: str, ctx: Context) -> List[Dict[str, Any]]:
    """Get all symbols in a document (functions, classes, methods, etc.).
    
    Args:
        file_path: Path to the Python file
        
    Returns:
        Hierarchical list of symbols in the document
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Getting document symbols for {file_path}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("textDocument/documentSymbol", {
        "textDocument": {"uri": file_uri}
    })
    
    return response or []


@mcp.tool
async def workspace_symbols(query: str, ctx: Context) -> List[Dict[str, Any]]:
    """Search for symbols across the entire workspace.
    
    Args:
        query: Search query (can be partial name)
        
    Returns:
        List of matching symbols with their locations
    """
    client = ensure_pyright()
    
    await ctx.info(f"Searching workspace symbols: {query}")
    
    response = await client.request("workspace/symbol", {
        "query": query
    })
    
    return response or []


# MCP Tools - Code Intelligence

@mcp.tool
async def diagnostics(file_path: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Get current diagnostics (errors, warnings) for file(s).
    
    Args:
        file_path: Optional path to specific file. If None, returns all diagnostics.
        
    Returns:
        Dictionary mapping file URIs to their diagnostics
    """
    if file_path:
        file_uri = ensure_file_uri(file_path)
        # Ensure file is open to get latest diagnostics
        client = ensure_pyright()
        await ensure_file_open(client, file_path, file_uri)
        # Wait a bit for diagnostics to update
        await asyncio.sleep(0.5)
        return {file_uri: current_diagnostics.get(file_uri, [])}
    else:
        return current_diagnostics


@mcp.tool
async def code_actions(file_path: str, start_line: int, start_char: int, end_line: int, end_char: int, ctx: Context) -> List[Dict[str, Any]]:
    """Get available code actions (fixes, refactorings) for a range.
    
    Args:
        file_path: Path to the Python file
        start_line: Start line (zero-based)
        start_char: Start character (zero-based)
        end_line: End line (zero-based)
        end_char: End character (zero-based)
        
    Returns:
        List of available code actions
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Getting code actions for {file_path}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    # Get diagnostics for this range
    file_diagnostics = current_diagnostics.get(file_uri, [])
    
    response = await client.request("textDocument/codeAction", {
        "textDocument": {"uri": file_uri},
        "range": {
            "start": {"line": start_line, "character": start_char},
            "end": {"line": end_line, "character": end_char}
        },
        "context": {
            "diagnostics": file_diagnostics
        }
    })
    
    return response or []


@mcp.tool
async def rename(file_path: str, line: int, character: int, new_name: str, ctx: Context) -> Dict[str, Any]:
    """Rename a symbol and all its references.
    
    Args:
        file_path: Path to the Python file
        line: Zero-based line number
        character: Zero-based character offset in the line
        new_name: New name for the symbol
        
    Returns:
        WorkspaceEdit with all changes needed
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Renaming symbol at {file_path}:{line}:{character} to '{new_name}'")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    # First check if rename is valid
    try:
        prepare_result = await client.request("textDocument/prepareRename", {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character}
        })
        
        if not prepare_result:
            return {"error": "Cannot rename at this position"}
            
    except LSPRequestError:
        return {"error": "Cannot rename at this position"}
    
    # Perform rename
    response = await client.request("textDocument/rename", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character},
        "newName": new_name
    })
    
    return response or {"changes": {}}


@mcp.tool
async def semantic_tokens(file_path: str, ctx: Context) -> Dict[str, Any]:
    """Get semantic tokens for syntax highlighting.
    
    Args:
        file_path: Path to the Python file
        
    Returns:
        Semantic tokens data
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Getting semantic tokens for {file_path}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("textDocument/semanticTokens/full", {
        "textDocument": {"uri": file_uri}
    })
    
    return response or {"data": []}


@mcp.tool
async def signature_help(file_path: str, line: int, character: int, ctx: Context) -> Dict[str, Any]:
    """Get signature help for function calls.
    
    Args:
        file_path: Path to the Python file
        line: Zero-based line number
        character: Zero-based character offset in the line
        
    Returns:
        Signature information with parameters
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Getting signature help at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("textDocument/signatureHelp", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character}
    })
    
    return response or {"signatures": []}


# MCP Tools - Formatting

@mcp.tool
async def format_document(file_path: str, tab_size: int = 4, insert_spaces: bool = True, ctx: Context = None) -> List[Dict[str, Any]]:
    """Format an entire Python document.
    
    Args:
        file_path: Path to the Python file
        tab_size: Size of a tab in spaces
        insert_spaces: Use spaces instead of tabs
        
    Returns:
        List of text edits to apply
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    if ctx:
        await ctx.info(f"Formatting {file_path}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("textDocument/formatting", {
        "textDocument": {"uri": file_uri},
        "options": {
            "tabSize": tab_size,
            "insertSpaces": insert_spaces
        }
    })
    
    return response or []


@mcp.tool
async def format_range(file_path: str, start_line: int, start_char: int, end_line: int, end_char: int, 
                      tab_size: int = 4, insert_spaces: bool = True, ctx: Context = None) -> List[Dict[str, Any]]:
    """Format a range in a Python document.
    
    Args:
        file_path: Path to the Python file
        start_line: Start line (zero-based)
        start_char: Start character (zero-based)
        end_line: End line (zero-based)
        end_char: End character (zero-based)
        tab_size: Size of a tab in spaces
        insert_spaces: Use spaces instead of tabs
        
    Returns:
        List of text edits to apply
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    if ctx:
        await ctx.info(f"Formatting range in {file_path}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("textDocument/rangeFormatting", {
        "textDocument": {"uri": file_uri},
        "range": {
            "start": {"line": start_line, "character": start_char},
            "end": {"line": end_line, "character": end_char}
        },
        "options": {
            "tabSize": tab_size,
            "insertSpaces": insert_spaces
        }
    })
    
    return response or []


@mcp.tool
async def organize_imports(file_path: str, ctx: Context) -> List[Dict[str, Any]]:
    """Organize imports in a Python file according to PEP 8.
    
    Args:
        file_path: Path to the Python file
        
    Returns:
        List of text edits to apply
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Organizing imports in {file_path}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("workspace/executeCommand", {
        "command": "pyright.organizeimports",
        "arguments": [file_uri]
    })
    
    # The response might be a WorkspaceEdit or direct edits
    if isinstance(response, dict) and "changes" in response:
        # Extract edits for the file
        return response["changes"].get(file_uri, [])
    elif isinstance(response, list):
        return response
    else:
        return []


# MCP Tools - pyright Extensions

@mcp.tool
async def add_import(file_path: str, line: int, character: int, ctx: Context) -> Dict[str, Any]:
    """Add missing import for symbol at position.
    
    Args:
        file_path: Path to the Python file
        line: Zero-based line number
        character: Zero-based character offset in the line
        
    Returns:
        WorkspaceEdit with import statement added
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    await ctx.info(f"Adding import at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    # Get code actions at position
    response = await client.request("textDocument/codeAction", {
        "textDocument": {"uri": file_uri},
        "range": {
            "start": {"line": line, "character": character},
            "end": {"line": line, "character": character}
        },
        "context": {
            "diagnostics": current_diagnostics.get(file_uri, [])
        }
    })
    
    # Find add import action
    for action in response or []:
        if action.get("kind") == "quickfix" and "import" in action.get("title", "").lower():
            return action.get("edit", {})
    
    return {"error": "No import action available"}


@mcp.tool
async def create_config(ctx: Context) -> str:
    """Create a pyrightconfig.json in the current directory.
    
    Returns:
        Success message or error
    """
    config_path = Path.cwd() / "pyrightconfig.json"
    
    if config_path.exists():
        return "pyrightconfig.json already exists"
    
    config = {
        "$schema": "https://raw.githubusercontent.com/microsoft/pyright/main/packages/vscode-pyright/schemas/pyrightconfig.schema.json",
        "include": ["src"],
        "exclude": [
            "**/node_modules",
            "**/__pycache__",
            "**/.*"
        ],
        "typeCheckingMode": "basic",
        "pythonVersion": "3.10",
        "venvPath": ".",
        "venv": ".venv"
    }
    
    try:
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        return "Created pyrightconfig.json"
    except Exception as e:
        return f"Failed to create config: {e}"


@mcp.tool
async def restart_server(ctx: Context) -> str:
    """Restart the pyright language server.
    
    Returns:
        Status message
    """
    global pyright, opened_files, initialization_complete
    
    if not pyright:
        return "pyright server is not running"
    
    await ctx.info("Restarting pyright server")
    
    try:
        # Mark as not initialized
        initialization_complete = False
        
        # Clear opened files cache
        opened_files.clear()
        
        # Shutdown existing server
        await pyright.shutdown()
        
        # Start new server
        pyright = PyrightClient(Path.cwd())
        pyright.on_notification("textDocument/publishDiagnostics", handle_diagnostics)
        await pyright.start()
        
        # Give pyright time to analyze
        await asyncio.sleep(2.0)
        initialization_complete = True
        
        return "pyright server restarted successfully"
    except Exception as e:
        return f"Failed to restart server: {e}"


# Signal handling for graceful shutdown
def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)


# Main entry point
if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the MCP server
    mcp.run()