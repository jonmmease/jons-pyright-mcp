"""Thread-based PyrightClient implementation for reliable subprocess communication."""

import asyncio
import json
import logging
import os
import shutil
import sys
import subprocess
import threading
import queue
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable

logger = logging.getLogger(__name__)


class LSPRequestError(Exception):
    """Error from LSP request"""
    pass


class ThreadedPyrightClient:
    """Thread-based LSP client for pyright"""
    
    def __init__(self, project_root: Path, pyright_path: Optional[str] = None):
        self.project_root = project_root
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
        
        self.process = subprocess.Popen(
            self.pyright_path.split(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.project_root),
            env=env,
            bufsize=0  # Unbuffered
        )
        
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
        
        while self.process and not self._shutting_down:
            try:
                # Read one byte at a time to avoid blocking
                byte = self.process.stdout.read(1)
                if not byte:
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
        while not self._shutting_down:
            try:
                # Check for messages with timeout
                message = await asyncio.get_running_loop().run_in_executor(
                    None, self._message_queue.get, True, 0.1
                )
                await self._handle_message(message)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                
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
        
        # Send initialized notification
        await self.notify("initialized", {})
        
        self._initialized = True
        
    async def request(self, method: str, params: Any = None) -> Any:
        """Send request and wait for response"""
        if not self.process:
            raise RuntimeError("Not started")
            
        request_id = self.request_id
        self.request_id += 1
        
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
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except asyncio.TimeoutError:
            self.pending_requests.pop(request_id, None)
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
            
        self._shutting_down = True
        
        try:
            # Send shutdown request
            await self.request("shutdown", {})
            
            # Send exit notification
            await self.notify("exit", {})
            
            # Give it a moment to exit cleanly
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            
        # Terminate process if still running
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                
        self.process = None
        self._initialized = False