#!/usr/bin/env python3

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


def read_pyright_config(project_root: Path) -> Dict[str, Any]:
    """Read pyrightconfig.json if it exists."""
    config_path = project_root / "pyrightconfig.json"
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                logger.info(f"Loaded pyrightconfig.json: {config}")
                return config
        except Exception as e:
            logger.warning(f"Failed to read pyrightconfig.json: {e}")
    return {}


def get_python_interpreter(project_root: Path, config: Dict[str, Any]) -> Optional[str]:
    """Determine the Python interpreter path from config or environment."""
    # First check if pythonPath is explicitly set in config
    if "pythonPath" in config:
        python_path = config["pythonPath"]
        if not Path(python_path).is_absolute():
            python_path = str(project_root / python_path)
        if Path(python_path).exists():
            logger.info(f"Using Python interpreter from config: {python_path}")
            return python_path
        else:
            logger.warning(f"Python interpreter not found at: {python_path}")
    
    # Check for venv configuration
    if "venv" in config:
        venv_path = config["venv"]
        if not Path(venv_path).is_absolute():
            # Handle venvPath + venv combination
            if "venvPath" in config:
                venv_base = Path(config["venvPath"])
                if not venv_base.is_absolute():
                    venv_base = project_root / venv_base
                venv_path = str(venv_base / venv_path)
            else:
                venv_path = str(project_root / venv_path)
        
        # Try common Python locations in the venv
        for python_exe in ["bin/python", "bin/python3", "Scripts/python.exe", "Scripts/python3.exe"]:
            python_path = Path(venv_path) / python_exe
            if python_path.exists():
                logger.info(f"Using Python interpreter from venv: {python_path}")
                return str(python_path)
        
        logger.warning(f"Could not find Python interpreter in venv: {venv_path}")
    
    # Check for common virtual environment locations
    for venv_dir in [".venv", "venv", ".pixi/envs/default", ".pixi/envs/dev"]:
        venv_path = project_root / venv_dir
        if venv_path.exists():
            for python_exe in ["bin/python", "bin/python3", "Scripts/python.exe", "Scripts/python3.exe"]:
                python_path = venv_path / python_exe
                if python_path.exists():
                    logger.info(f"Found Python interpreter in {venv_dir}: {python_path}")
                    return str(python_path)
    
    return None


@asynccontextmanager
async def lifespan(mcp: FastMCP):
    """Manage the lifecycle of the pyright client"""
    global pyright, initialization_complete
    
    # Startup
    project_root = Path.cwd()
    logger.info(f"Starting MCP server in project: {project_root}")
    
    # Check if this is a Python project
    if not any((project_root / f).exists() for f in ["setup.py", "pyproject.toml", "requirements.txt", "pyrightconfig.json"]):
        logger.warning("No Python project files found. Consider creating pyrightconfig.json for better results.")
    
    # Read pyright configuration
    pyright_config = read_pyright_config(project_root)
    
    pyright = PyrightClient(project_root, pyright_config)
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


# Exceptions
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
    """Thread-based LSP client for pyright"""
    
    def __init__(self, project_root: Path, config: Optional[Dict[str, Any]] = None, pyright_path: Optional[str] = None):
        self.project_root = project_root
        self.config = config or {}
        self.pyright_path = pyright_path or self._find_pyright()
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self.notification_handlers: Dict[str, Callable] = {}
        self._initialized = False
        self._shutting_down = False
        self.request_timeout = float(os.environ.get("PYRIGHT_TIMEOUT", "60.0"))
        
        # Thread-based I/O
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._writer_lock = threading.Lock()
        self._message_queue = queue.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
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
            
        # Last resort: try node-based installation
        npm_prefix = subprocess.run(
            ["npm", "prefix", "-g"], 
            capture_output=True, 
            text=True
        ).stdout.strip()
        
        if npm_prefix:
            node_path = Path(npm_prefix) / "lib" / "node_modules" / "pyright" / "langserver.index.js"
            if node_path.exists():
                return f"node {node_path} --stdio"
                
        raise RuntimeError(
            "pyright not found. Install it with: pip install pyright"
        )
        
    async def start(self):
        """Start the pyright process and initialize communication"""
        if self.process:
            raise RuntimeError("Already started")
            
        # Store event loop for thread-to-async communication
        self._loop = asyncio.get_running_loop()
        
        logger.info(f"Starting pyright for project: {self.project_root}")
        logger.info(f"Using pyright command: {self.pyright_path}")
        
        # Start process with unbuffered output
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        
        try:
            self.process = subprocess.Popen(
                self.pyright_path.split(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.project_root),
                env=env,
                bufsize=0  # Unbuffered
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start pyright: {e}")
        
        # Start reader threads
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread.start()
        
        # Process messages from queue
        asyncio.create_task(self._process_messages())
        
        # Initialize LSP connection
        await self._initialize()
        
    def _reader_loop(self):
        """Read messages from stdout in a thread"""
        buffer = b""
        logger.debug("Reader thread started")
        
        while self.process and not self._shutting_down:
            try:
                # Read one byte at a time to avoid blocking
                byte = self.process.stdout.read(1)
                if not byte:
                    logger.debug("Reader thread: EOF")
                    break
                    
                buffer += byte
                
                # Check for complete message
                header_end = buffer.find(b"\r\n\r\n")
                if header_end == -1:
                    continue
                    
                # Parse header
                header = buffer[:header_end].decode('utf-8')
                content_length = None
                for line in header.split('\r\n'):
                    if line.startswith('Content-Length: '):
                        content_length = int(line[16:])
                        break
                        
                if content_length is None:
                    buffer = buffer[header_end + 4:]
                    continue
                    
                # Read content
                content_start = header_end + 4
                while len(buffer) < content_start + content_length:
                    chunk = self.process.stdout.read(
                        min(4096, content_start + content_length - len(buffer))
                    )
                    if not chunk:
                        return
                    buffer += chunk
                    
                # Extract message
                content = buffer[content_start:content_start + content_length]
                buffer = buffer[content_start + content_length:]
                
                try:
                    message = json.loads(content.decode('utf-8'))
                    # Put message in queue for async processing
                    method_or_id = message.get('method', f"response id={message.get('id')}")
                    logger.debug(f"Reader thread: queuing message {method_or_id}")
                    self._message_queue.put(message)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON: {e}")
                    
            except Exception as e:
                if not self._shutting_down:
                    logger.error(f"Error in reader thread: {e}")
                break
                
    def _stderr_loop(self):
        """Read stderr in a thread"""
        while self.process and self.process.stderr and not self._shutting_down:
            try:
                line = self.process.stderr.readline()
                if line:
                    decoded = line.decode().strip()
                    if "error" in decoded.lower() or "panic" in decoded.lower():
                        logger.error(f"pyright stderr: {decoded}")
                    else:
                        logger.info(f"pyright stderr: {decoded}")
                else:
                    break
            except Exception:
                break
                
    async def _process_messages(self):
        """Process messages from the queue"""
        logger.debug("Message processor started")
        while not self._shutting_down:
            try:
                # Use a simple approach - check if queue has items
                if not self._message_queue.empty():
                    message = self._message_queue.get_nowait()
                    logger.debug(f"Processing message from queue: {message.get('method', 'response')}")
                    await self._handle_message(message)
                else:
                    # Small sleep to avoid busy waiting
                    await asyncio.sleep(0.01)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                
    async def _handle_message(self, message: Dict[str, Any]):
        """Handle incoming LSP message"""
        logger.debug(f"Received: {message}")
        
        if 'id' in message and 'method' in message:
            # Request from server to client
            request_id = message['id']
            method = message['method']
            params = message.get('params', {})
            
            logger.debug(f"Server request: {method} (id={request_id})")
            
            # Handle workspace/configuration request
            if method == 'workspace/configuration':
                # Build response based on requested configuration sections
                result = []
                items = params.get('items', [])
                
                for item in items:
                    section = item.get('section', '')
                    config_response = {}
                    
                    if section == 'python':
                        # Provide Python interpreter path if we have it
                        python_path = get_python_interpreter(self.project_root, self.config)
                        if python_path:
                            config_response['defaultInterpreterPath'] = python_path
                            config_response['pythonPath'] = python_path
                    elif section == 'python.analysis':
                        # Provide analysis settings
                        if "extraPaths" in self.config:
                            extra_paths = self.config["extraPaths"]
                            # Convert relative paths to absolute
                            abs_paths = []
                            for path in extra_paths:
                                if not Path(path).is_absolute():
                                    abs_paths.append(str(self.project_root / path))
                                else:
                                    abs_paths.append(path)
                            config_response['extraPaths'] = abs_paths
                        if "typeCheckingMode" in self.config:
                            config_response['typeCheckingMode'] = self.config['typeCheckingMode']
                    elif section == 'pyright':
                        # Return the full pyright config if requested
                        config_response = self.config.copy()
                    
                    result.append(config_response)
                
                await self._send_message({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": result
                })
            else:
                # Send error response for unsupported methods
                await self._send_message({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not supported: {method}"
                    }
                })
        elif 'id' in message:
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
                    # Check if handler is async
                    if asyncio.iscoroutinefunction(handler):
                        await handler(params)
                    else:
                        handler(params)
                except Exception as e:
                    logger.error(f"Error in notification handler for {method}: {e}")
            else:
                logger.debug(f"Unhandled notification: {method}")
                
    async def _initialize(self):
        """Send LSP initialize request"""
        logger.debug("Sending initialize request...")
        
        # Build initialization options from config
        init_options = {"python": {"analysis": {}}}
        
        # Add analysis settings from config
        if "typeCheckingMode" in self.config:
            init_options["python"]["analysis"]["typeCheckingMode"] = self.config["typeCheckingMode"]
        else:
            init_options["python"]["analysis"]["typeCheckingMode"] = "basic"
            
        init_options["python"]["analysis"]["autoSearchPaths"] = True
        init_options["python"]["analysis"]["useLibraryCodeForTypes"] = True
        init_options["python"]["analysis"]["diagnosticMode"] = "workspace"
        
        # Add extraPaths if specified
        if "extraPaths" in self.config:
            extra_paths = self.config["extraPaths"]
            # Convert relative paths to absolute
            abs_paths = []
            for path in extra_paths:
                if not Path(path).is_absolute():
                    abs_paths.append(str(self.project_root / path))
                else:
                    abs_paths.append(path)
            init_options["python"]["analysis"]["extraPaths"] = abs_paths
        
        # Try to determine Python interpreter
        python_path = get_python_interpreter(self.project_root, self.config)
        if python_path:
            init_options["python"]["pythonPath"] = python_path
            logger.info(f"Using Python interpreter: {python_path}")
        
        # Add other config options
        if "pythonVersion" in self.config:
            init_options["python"]["pythonVersion"] = self.config["pythonVersion"]
        if "pythonPlatform" in self.config:
            init_options["python"]["pythonPlatform"] = self.config["pythonPlatform"]
        
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
                        "resolveSupport": {"properties": ["edit"]}
                    },
                    "publishDiagnostics": {"relatedInformation": True},
                    "callHierarchy": {},
                    "semanticTokens": {
                        "requests": {"full": True, "range": True},
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
            "initializationOptions": init_options
        })
        
        logger.info("pyright initialized successfully")
        
        # Send initialized notification
        await self.notify("initialized", {})
        
        self._initialized = True
        
    async def request(self, method: str, params: Any = None) -> Any:
        """Send request and wait for response"""
        if not self.process:
            raise RuntimeError("Not started")
            
        request_id = self.request_id
        self.request_id += 1
        
        logger.debug(f"Creating request {method} with id {request_id}")
        
        # Create future for response
        future = asyncio.Future()
        self.pending_requests[request_id] = future
        
        # Send request
        await self._send_message({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {}
        })
        
        # Wait for response with timeout
        try:
            logger.debug(f"Waiting for response to {method} (id={request_id})")
            result = await asyncio.wait_for(future, timeout=self.request_timeout)
            logger.debug(f"Got response for {method} (id={request_id}): {result}")
            return result
        except asyncio.TimeoutError:
            self.pending_requests.pop(request_id, None)
            logger.error(f"Request {method} (id={request_id}) timed out. Pending requests: {list(self.pending_requests.keys())}")
            raise LSPRequestError(f"Request {method} timed out after {self.request_timeout}s")
            
    async def notify(self, method: str, params: Any = None):
        """Send notification (no response expected)"""
        await self._send_message({
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        })
        
    async def _send_message(self, message: Dict[str, Any]):
        """Send message to pyright"""
        if not self.process or not self.process.stdin:
            raise RuntimeError("Process not running")
            
        content = json.dumps(message).encode('utf-8')
        header = f"Content-Length: {len(content)}\r\n\r\n".encode('utf-8')
        
        # Thread-safe write
        with self._writer_lock:
            self.process.stdin.write(header + content)
            self.process.stdin.flush()
            
        logger.debug(f"Sent: {message}")
        
    def on_notification(self, method: str, handler: Callable):
        """Register notification handler"""
        self.notification_handlers[method] = handler
        
    async def shutdown(self):
        """Shutdown the language server"""
        if not self.process:
            return
            
        logger.debug("Starting shutdown...")
        
        try:
            # Send shutdown request first
            shutdown_result = await self.request("shutdown", {})
            logger.debug(f"Shutdown response: {shutdown_result}")
            
            # Then mark as shutting down to stop message processing
            self._shutting_down = True
            
            # Send exit notification
            await self.notify("exit", {})
            
            # Give it a moment to exit cleanly
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            self._shutting_down = True
            
        # Terminate process if still running
        if self.process.poll() is None:
            logger.debug("Process still running, terminating...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.debug("Process didn't terminate, killing...")
                self.process.kill()
                self.process.wait()
                
        self.process = None
        self._initialized = False
        logger.debug("Shutdown complete")


# Helper functions
def ensure_file_uri(file_path: str) -> str:
    """Ensure file path is a proper file URI"""
    if file_path.startswith("file://"):
        return file_path
    
    path = Path(file_path).absolute()
    return f"file://{path}"


def ensure_pyright() -> PyrightClient:
    """Ensure pyright is initialized and return the client"""
    if not pyright:
        raise RuntimeError("pyright is not initialized")
    if not pyright._initialized:
        if initialization_complete:
            raise RuntimeError("pyright client is not properly initialized")
        else:
            raise RuntimeError("pyright is still initializing")
    return pyright


async def ensure_file_open(client: PyrightClient, file_path: str, file_uri: str) -> bool:
    """Ensure file is open in pyright."""
    if file_uri in opened_files:
        return True
        
    try:
        # Read file content
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Send didOpen notification
        await client.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_uri,
                "languageId": "python",
                "version": 1,
                "text": content
            }
        })
        
        opened_files.add(file_uri)
        return True
    except Exception as e:
        logger.error(f"Failed to open file {file_path}: {e}")
        return False


# Core Language Features
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
    
    if ctx:
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
async def completion(
    file_path: str, 
    line: int, 
    character: int, 
    ctx: Context,
    limit: int = 50,
    offset: int = 0
) -> Dict[str, Any]:
    """Get code completions at the specified position.
    
    Args:
        file_path: Path to the Python file
        line: Zero-based line number
        character: Zero-based character offset in the line
        limit: Maximum items to return (default: 50)
        offset: Number of items to skip for pagination (default: 0)
        
    Returns:
        Dictionary with:
        - items: List of completion items with absolute offsets
        - totalItems: Total number of completions found
        - offset: Current offset
        - limit: Current limit
        - hasMore: Whether there are more items
        - nextOffset: Offset for next page (if hasMore is True)
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    if ctx:
        await ctx.info(f"Getting completions at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    response = await client.request("textDocument/completion", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character}
    })
    
    # Response can be either CompletionList or CompletionItem[]
    completions = []
    if isinstance(response, dict) and "items" in response:
        completions = response["items"]
    elif isinstance(response, list):
        completions = response
    
    # Sort completions for stable ordering
    def sort_key(item):
        # Prioritize by sortText if available, otherwise by label
        return (item.get("sortText", item.get("label", "")), item.get("label", ""))
    
    completions.sort(key=sort_key)
    
    # Apply pagination
    total_items = len(completions)
    
    # Apply offset and limit
    start_idx = min(offset, total_items)
    end_idx = min(start_idx + limit, total_items)
    paginated_items = completions[start_idx:end_idx]
    
    # Check if there are more items
    has_more = end_idx < total_items
    
    # Add absolute offset to each item
    processed_items = []
    for i, item in enumerate(paginated_items):
        processed_item = {**item, "offset": start_idx + i}
        processed_items.append(processed_item)
    
    return {
        "items": processed_items,
        "totalItems": total_items,
        "offset": offset,
        "limit": limit,
        "hasMore": has_more,
        "nextOffset": end_idx if has_more else None
    }


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
    
    if ctx:
        await ctx.info(f"Finding definition at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    return await client.request("textDocument/definition", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character}
    })


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
    
    if ctx:
        await ctx.info(f"Finding type definition at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    return await client.request("textDocument/typeDefinition", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character}
    })


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
    
    if ctx:
        await ctx.info(f"Finding implementations at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    return await client.request("textDocument/implementation", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character}
    })


@mcp.tool
async def references(
    file_path: str, 
    line: int, 
    character: int, 
    include_declaration: bool = True,
    limit: int = 50,
    offset: int = 0
) -> Dict[str, Any]:
    """Find all references to the symbol at the specified position.
    
    Args:
        file_path: Path to the Python file
        line: Zero-based line number
        character: Zero-based character offset in the line
        include_declaration: Whether to include the declaration itself
        limit: Maximum items to return (default: 50)
        offset: Number of items to skip for pagination (default: 0)
        
    Returns:
        Dictionary with:
        - items: List of reference locations with absolute offsets
        - totalItems: Total number of references found
        - offset: Current offset
        - limit: Current limit
        - hasMore: Whether there are more items
        - nextOffset: Offset for next page (if hasMore is True)
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    # Get all references
    references = await client.request("textDocument/references", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character},
        "context": {"includeDeclaration": include_declaration}
    })
    
    # Handle empty response
    if not references:
        references = []
    
    # Sort references for stable ordering
    def sort_key(ref):
        return (ref.get("uri", ""), ref.get("range", {}).get("start", {}).get("line", 0), 
                ref.get("range", {}).get("start", {}).get("character", 0))
    
    references.sort(key=sort_key)
    
    # Apply pagination
    total_items = len(references)
    
    # Apply offset and limit
    start_idx = min(offset, total_items)
    end_idx = min(start_idx + limit, total_items)
    paginated_items = references[start_idx:end_idx]
    
    # Check if there are more items
    has_more = end_idx < total_items
    
    # Add absolute offset to each item
    processed_items = []
    for i, item in enumerate(paginated_items):
        processed_item = {**item, "offset": start_idx + i}
        processed_items.append(processed_item)
    
    return {
        "items": processed_items,
        "totalItems": total_items,
        "offset": offset,
        "limit": limit,
        "hasMore": has_more,
        "nextOffset": end_idx if has_more else None
    }


@mcp.tool
async def document_symbols(
    file_path: str, 
    ctx: Context,
    limit: int = 50,
    offset: int = 0
) -> Dict[str, Any]:
    """Get all symbols in a document (functions, classes, methods, etc.).
    
    Args:
        file_path: Path to the Python file
        limit: Maximum items to return (default: 50)
        offset: Number of items to skip for pagination (default: 0)
        
    Returns:
        Dictionary with:
        - items: List of symbols with absolute offsets
        - totalItems: Total number of symbols found
        - offset: Current offset
        - limit: Current limit
        - hasMore: Whether there are more items
        - nextOffset: Offset for next page (if hasMore is True)
    """
    client = ensure_pyright()
    file_uri = ensure_file_uri(file_path)
    
    if ctx:
        await ctx.info(f"Getting document symbols for {file_path}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    # Get all symbols
    symbols = await client.request("textDocument/documentSymbol", {
        "textDocument": {"uri": file_uri}
    })
    
    # Handle empty response
    if not symbols:
        symbols = []
    
    # Flatten hierarchical symbols if needed
    def flatten_symbols(symbols_list, parent_name=""):
        flattened = []
        for symbol in symbols_list:
            # Add the symbol itself
            symbol_copy = {**symbol}
            if parent_name:
                symbol_copy["containerName"] = parent_name
            flattened.append(symbol_copy)
            
            # Recursively add children
            if "children" in symbol and symbol["children"]:
                child_symbols = flatten_symbols(symbol["children"], symbol.get("name", ""))
                flattened.extend(child_symbols)
                
        return flattened
    
    # Check if symbols are hierarchical (DocumentSymbol) or flat (SymbolInformation)
    if symbols and "children" in symbols[0]:
        # Hierarchical - flatten for consistent pagination
        symbols = flatten_symbols(symbols)
    
    # Sort symbols for stable ordering
    def sort_key(symbol):
        # For DocumentSymbol
        if "range" in symbol:
            return (symbol.get("range", {}).get("start", {}).get("line", 0),
                    symbol.get("range", {}).get("start", {}).get("character", 0))
        # For SymbolInformation
        else:
            return (symbol.get("location", {}).get("range", {}).get("start", {}).get("line", 0),
                    symbol.get("location", {}).get("range", {}).get("start", {}).get("character", 0))
    
    symbols.sort(key=sort_key)
    
    # Apply pagination
    total_items = len(symbols)
    
    # Apply offset and limit
    start_idx = min(offset, total_items)
    end_idx = min(start_idx + limit, total_items)
    paginated_items = symbols[start_idx:end_idx]
    
    # Check if there are more items
    has_more = end_idx < total_items
    
    # Add absolute offset to each item
    processed_items = []
    for i, item in enumerate(paginated_items):
        processed_item = {**item, "offset": start_idx + i}
        processed_items.append(processed_item)
    
    return {
        "items": processed_items,
        "totalItems": total_items,
        "offset": offset,
        "limit": limit,
        "hasMore": has_more,
        "nextOffset": end_idx if has_more else None
    }


@mcp.tool
async def workspace_symbols(
    query: str, 
    ctx: Context,
    limit: int = 50,
    offset: int = 0
) -> Dict[str, Any]:
    """Search for symbols across the entire workspace.
    
    Args:
        query: Search query (can be partial name)
        limit: Maximum items to return (default: 50)
        offset: Number of items to skip for pagination (default: 0)
        
    Returns:
        Dictionary with:
        - items: List of matching symbols with absolute offsets
        - totalItems: Total number of symbols found
        - offset: Current offset
        - limit: Current limit
        - hasMore: Whether there are more items
        - nextOffset: Offset for next page (if hasMore is True)
    """
    client = ensure_pyright()
    
    if ctx:
        await ctx.info(f"Searching workspace for symbols matching '{query}'")
    
    # Get all symbols
    symbols = await client.request("workspace/symbol", {
        "query": query
    })
    
    # Handle empty response
    if not symbols:
        symbols = []
    
    # Sort symbols for stable ordering
    def sort_key(symbol):
        return (symbol.get("name", ""), symbol.get("location", {}).get("uri", ""),
                symbol.get("location", {}).get("range", {}).get("start", {}).get("line", 0))
    
    symbols.sort(key=sort_key)
    
    # Apply pagination
    total_items = len(symbols)
    
    # Apply offset and limit
    start_idx = min(offset, total_items)
    end_idx = min(start_idx + limit, total_items)
    paginated_items = symbols[start_idx:end_idx]
    
    # Check if there are more items
    has_more = end_idx < total_items
    
    # Add absolute offset to each item
    processed_items = []
    for i, item in enumerate(paginated_items):
        processed_item = {**item, "offset": start_idx + i}
        processed_items.append(processed_item)
    
    return {
        "items": processed_items,
        "totalItems": total_items,
        "offset": offset,
        "limit": limit,
        "hasMore": has_more,
        "nextOffset": end_idx if has_more else None
    }


# Code Intelligence
@mcp.tool
async def diagnostics(
    file_path: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> Dict[str, Any]:
    """Get current diagnostics (errors, warnings) for file(s).
    
    Args:
        file_path: Optional path to specific file. If None, returns all diagnostics.
        limit: Maximum items to return (default: 50)
        offset: Number of items to skip for pagination (default: 0)
        
    Returns:
        Dictionary with:
        - items: List of diagnostics with file URIs and absolute offsets
        - totalItems: Total number of diagnostics found
        - offset: Current offset
        - limit: Current limit
        - hasMore: Whether there are more items
        - nextOffset: Offset for next page (if hasMore is True)
    """
    # Collect all diagnostics
    all_diagnostics = []
    
    if file_path:
        file_uri = ensure_file_uri(file_path)
        # Ensure file is open
        client = ensure_pyright()
        await ensure_file_open(client, file_path, file_uri)
        
        # Get diagnostics for specific file
        file_diags = current_diagnostics.get(file_uri, [])
        for diag in file_diags:
            all_diagnostics.append({**diag, "uri": file_uri})
    else:
        # Get all diagnostics from all files
        for uri, diags in current_diagnostics.items():
            for diag in diags:
                all_diagnostics.append({**diag, "uri": uri})
    
    # Sort diagnostics for stable ordering
    def sort_key(diag):
        return (
            diag.get("uri", ""),
            diag.get("severity", 0),  # Errors first
            diag.get("range", {}).get("start", {}).get("line", 0),
            diag.get("range", {}).get("start", {}).get("character", 0)
        )
    
    all_diagnostics.sort(key=sort_key)
    
    # Apply pagination
    total_items = len(all_diagnostics)
    
    # Apply offset and limit
    start_idx = min(offset, total_items)
    end_idx = min(start_idx + limit, total_items)
    paginated_items = all_diagnostics[start_idx:end_idx]
    
    # Check if there are more items
    has_more = end_idx < total_items
    
    # Add absolute offset to each item
    processed_items = []
    for i, item in enumerate(paginated_items):
        processed_item = {**item, "offset": start_idx + i}
        processed_items.append(processed_item)
    
    return {
        "items": processed_items,
        "totalItems": total_items,
        "offset": offset,
        "limit": limit,
        "hasMore": has_more,
        "nextOffset": end_idx if has_more else None
    }


@mcp.tool
async def code_actions(
    file_path: str,
    start_line: int,
    start_char: int, 
    end_line: int,
    end_char: int,
    ctx: Context
) -> List[Dict[str, Any]]:
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
    
    if ctx:
        await ctx.info(f"Getting code actions for {file_path}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    # Get diagnostics for this range
    file_diagnostics = current_diagnostics.get(file_uri, [])
    
    return await client.request("textDocument/codeAction", {
        "textDocument": {"uri": file_uri},
        "range": {
            "start": {"line": start_line, "character": start_char},
            "end": {"line": end_line, "character": end_char}
        },
        "context": {
            "diagnostics": file_diagnostics
        }
    })


@mcp.tool
async def rename(
    file_path: str,
    line: int,
    character: int,
    new_name: str,
    ctx: Context
) -> Dict[str, Any]:
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
    
    if ctx:
        await ctx.info(f"Renaming symbol at {file_path}:{line}:{character} to '{new_name}'")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    # First check if rename is possible at this position
    prepare_result = await client.request("textDocument/prepareRename", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character}
    })
    
    if not prepare_result:
        return {"error": "Cannot rename at this position"}
    
    # Perform rename
    return await client.request("textDocument/rename", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character},
        "newName": new_name
    })


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
    
    if ctx:
        await ctx.info(f"Getting semantic tokens for {file_path}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    return await client.request("textDocument/semanticTokens/full", {
        "textDocument": {"uri": file_uri}
    })


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
    
    if ctx:
        await ctx.info(f"Getting signature help at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    return await client.request("textDocument/signatureHelp", {
        "textDocument": {"uri": file_uri},
        "position": {"line": line, "character": character}
    })


# Formatting
@mcp.tool
async def format_document(
    file_path: str,
    tab_size: int = 4,
    insert_spaces: bool = True
) -> List[Dict[str, Any]]:
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
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    return await client.request("textDocument/formatting", {
        "textDocument": {"uri": file_uri},
        "options": {
            "tabSize": tab_size,
            "insertSpaces": insert_spaces
        }
    })


@mcp.tool
async def format_range(
    file_path: str,
    start_line: int,
    start_char: int,
    end_line: int,
    end_char: int,
    tab_size: int = 4,
    insert_spaces: bool = True
) -> List[Dict[str, Any]]:
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
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    return await client.request("textDocument/rangeFormatting", {
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


# pyright Extensions
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
    
    if ctx:
        await ctx.info(f"Organizing imports in {file_path}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    # Execute organize imports command
    response = await client.request("workspace/executeCommand", {
        "command": "pyright.organizeimports",
        "arguments": [file_uri]
    })
    
    # Extract edits from response
    if isinstance(response, dict) and "changes" in response:
        return response["changes"].get(file_uri, [])
    return []


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
    
    if ctx:
        await ctx.info(f"Adding import for symbol at {file_path}:{line}:{character}")
    
    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)
    
    # Get code actions at this position
    actions = await client.request("textDocument/codeAction", {
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
    for action in actions:
        if action.get("kind") == "quickfix" and "import" in action.get("title", "").lower():
            # Return the edit from this action
            if "edit" in action:
                return action["edit"]
                
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
        "include": ["**/*.py"],
        "exclude": ["**/node_modules", "**/__pycache__", "**/.*"],
        "defineConstant": {"DEBUG": True},
        "typeCheckingMode": "basic",
        "pythonVersion": "3.10",
        "pythonPlatform": "Linux",
        "executionEnvironments": [
            {
                "root": "."
            }
        ]
    }
    
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
        
    if ctx:
        await ctx.info("Created pyrightconfig.json")
        
    return "Created pyrightconfig.json"


@mcp.tool
async def restart_server(ctx: Context) -> str:
    """Restart the pyright language server.
    
    Returns:
        Status message
    """
    global pyright
    
    if not pyright:
        return "pyright server is not running"
        
    if ctx:
        await ctx.info("Restarting pyright server...")
        
    # Shutdown existing server
    await pyright.shutdown()
    
    # Start new server
    project_root = pyright.project_root
    pyright = PyrightClient(project_root)
    pyright.on_notification("textDocument/publishDiagnostics", handle_diagnostics)
    
    await pyright.start()
    
    return "pyright server restarted successfully"


def main():
    """Main entry point for the pyright MCP server."""
    # Handle keyboard interrupt gracefully
    def signal_handler(sig, frame):
        logger.info("Received interrupt signal, shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Run the server
    mcp.run()


# Main entry point
if __name__ == "__main__":
    main()