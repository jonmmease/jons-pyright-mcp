#!/usr/bin/env python3
"""Test with a real project that has pixi setup."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pyright_mcp import PyrightClient, ensure_file_uri, read_pyright_config, get_python_interpreter

async def test_real_pixi():
    """Test with hex-sl project which uses pixi."""
    project_dir = Path("/Users/jonmmease/VegaFusion/projects/hex-sl")
    
    if not project_dir.exists():
        print(f"Project directory not found: {project_dir}")
        print("Please provide a path to a project with pixi environment")
        return
        
    print(f"Testing in project: {project_dir}")
    
    # Read pyright config if it exists
    config = read_pyright_config(project_dir)
    print(f"Loaded config: {config}")
    
    # Get Python interpreter
    python_path = get_python_interpreter(project_dir, config)
    print(f"Python interpreter: {python_path}")
    
    # If no config exists, create one for pixi
    if not config and (project_dir / ".pixi").exists():
        print("\nNo pyrightconfig.json found, creating one for pixi environment...")
        config = {
            "venvPath": ".pixi/envs",
            "venv": "default",
            "extraPaths": ["src"]
        }
        
        # Check which pixi env exists
        pixi_envs = project_dir / ".pixi" / "envs"
        if pixi_envs.exists():
            envs = [d.name for d in pixi_envs.iterdir() if d.is_dir()]
            if envs:
                print(f"Found pixi environments: {envs}")
                if "dev" in envs:
                    config["venv"] = "dev"
                elif envs:
                    config["venv"] = envs[0]
    
    # Create client
    client = PyrightClient(project_dir, config)
    
    try:
        print("\nStarting pyright...")
        await client.start()
        print("✓ Pyright started successfully")
        
        # Wait for initial analysis
        print("Waiting for analysis...")
        await asyncio.sleep(3.0)
        
        # Find a Python file to test
        test_file = None
        for pattern in ["src/**/*.py", "**/__init__.py", "*.py"]:
            files = list(project_dir.glob(pattern))
            if files:
                test_file = files[0]
                break
                
        if not test_file:
            print("No Python files found to test")
            return
            
        print(f"\nTesting with file: {test_file}")
        
        # Open the file
        file_uri = ensure_file_uri(str(test_file))
        with open(test_file) as f:
            content = f.read()
            
        await client.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_uri,
                "languageId": "python",
                "version": 1,
                "text": content
            }
        })
        
        # Wait for analysis
        await asyncio.sleep(1.0)
        
        # Find something to hover over (look for an import or variable)
        lines = content.split('\n')
        hover_pos = None
        
        # Look for imports
        for i, line in enumerate(lines):
            if line.strip().startswith(('import ', 'from ')):
                # Find first identifier after import/from
                if 'import ' in line:
                    idx = line.find('import ') + 7
                    if idx < len(line) and line[idx].isalpha():
                        hover_pos = (i, idx)
                        print(f"Testing hover on line {i+1}: {line.strip()}")
                        break
                elif 'from ' in line:
                    idx = line.find('from ') + 5
                    if idx < len(line) and line[idx].isalpha():
                        hover_pos = (i, idx)
                        print(f"Testing hover on line {i+1}: {line.strip()}")
                        break
        
        if hover_pos:
            line, char = hover_pos
            response = await client.request("textDocument/hover", {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": char}
            })
            
            if response and "contents" in response:
                contents = response["contents"]
                if isinstance(contents, dict) and "value" in contents:
                    print(f"✓ Hover successful: {contents['value'][:200]}...")
                else:
                    print(f"✓ Hover response: {response}")
            else:
                print("✗ No hover information available")
                
        # Get diagnostics
        print("\nChecking diagnostics...")
        from pyright_mcp import diagnostics
        diag_result = await diagnostics.fn()
        if diag_result:
            total_errors = sum(len(diags) for diags in diag_result.values())
            print(f"Found {total_errors} total diagnostics across {len(diag_result)} files")
            if total_errors > 0:
                # Show first few
                shown = 0
                for file_uri, diags in diag_result.items():
                    for diag in diags[:2]:  # Show first 2 per file
                        print(f"  - {diag.get('message', 'Unknown error')}")
                        shown += 1
                        if shown >= 5:
                            break
                    if shown >= 5:
                        break
        else:
            print("No diagnostics found")
            
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\nShutting down...")
        await client.shutdown()
        print("Shutdown complete")

if __name__ == "__main__":
    import os
    os.environ["LOG_LEVEL"] = "INFO"
    asyncio.run(test_real_pixi())