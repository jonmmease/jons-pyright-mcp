# Troubleshooting pyright-mcp

## Issue: Hover requests timeout when using pyright-mcp

### Summary
When using pyright-mcp from Claude Code, hover requests (and likely other LSP requests) timeout after 30-60 seconds. The pyright language server starts successfully but doesn't respond to requests.

### Root Cause
The issue appears to be related to how Python's asyncio subprocess module handles stdio communication with the pyright language server on macOS. Specifically:

1. The asyncio `StreamReader.read()` method can block indefinitely when reading from pyright's stdout
2. Pyright may be buffering its output when connected to pipes (not a terminal)
3. The combination causes the reader task to hang, preventing messages from being processed

### Testing Results
- Synchronous subprocess communication with pyright works correctly
- Thread-based I/O handling works correctly  
- The issue only occurs with asyncio subprocess pipes
- The problem affects both simple test cases and the actual hex-sl project

### Potential Solutions

1. **Use unbuffered I/O (recommended)**
   - Set `PYTHONUNBUFFERED=1` environment variable when starting pyright
   - Pass `-u` flag to Python when running pyright

2. **Use thread-based I/O instead of asyncio**
   - Replace the asyncio reader task with a thread that reads from stdout
   - Use a queue to pass messages back to the async context

3. **Use a different subprocess backend**
   - Consider using `pexpect` or similar libraries that handle subprocess I/O differently

4. **Force line buffering**
   - Use `stdbuf -oL` on systems where it's available

### Temporary Workaround
For immediate use, you can increase the timeout:
```bash
export PYRIGHT_TIMEOUT=120  # 2 minutes
```

However, this doesn't fix the underlying issue - requests will still timeout, just after a longer period.

### Next Steps
The most reliable solution would be to rewrite the subprocess communication to use threads instead of asyncio, as demonstrated in `test_threads.py`. This approach has been proven to work correctly with pyright.