#!/usr/bin/env python3
"""Test pyright with pixi environment configuration."""

import asyncio
import json
import sys
from pathlib import Path
import tempfile
import shutil

sys.path.insert(0, str(Path(__file__).parent))

from pyright_mcp import PyrightClient, ensure_file_uri, read_pyright_config, get_python_interpreter

async def test_pixi_env():
    """Test pyright with pixi environment."""
    # Create a temporary project directory
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        
        # Create project structure
        src_dir = project_dir / "src"
        src_dir.mkdir()
        
        # Create a simple module
        module_file = src_dir / "mymodule.py"
        module_file.write_text("""
def greet(name: str) -> str:
    '''Greet someone by name.'''
    return f"Hello, {name}!"

class Calculator:
    def add(self, a: int, b: int) -> int:
        return a + b
""")
        
        # Create main file that imports the module
        main_file = project_dir / "main.py"
        main_file.write_text("""
from mymodule import greet, Calculator

# This would normally fail without proper configuration
message = greet("World")
calc = Calculator()
result = calc.add(1, 2)
print(f"{message} Result: {result}")
""")
        
        # Create pyrightconfig.json with pixi configuration
        configs = [
            # Config 1: Using venvPath + venv
            {
                "venvPath": ".pixi/envs",
                "venv": "dev",
                "extraPaths": ["src"]
            },
            # Config 2: Using full venv path
            {
                "venv": ".pixi/envs/dev",
                "extraPaths": ["src"]
            },
            # Config 3: Using pythonPath directly
            {
                "pythonPath": ".pixi/envs/dev/bin/python",
                "extraPaths": ["src"]
            }
        ]
        
        for i, config in enumerate(configs):
            print(f"\n{'='*60}")
            print(f"Testing configuration {i+1}: {json.dumps(config, indent=2)}")
            print('='*60)
            
            config_file = project_dir / "pyrightconfig.json"
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
            
            # Read the config
            loaded_config = read_pyright_config(project_dir)
            print(f"Loaded config: {loaded_config}")
            
            # Get Python interpreter
            python_path = get_python_interpreter(project_dir, loaded_config)
            print(f"Python interpreter: {python_path}")
            
            # Create pyright client
            client = PyrightClient(project_dir, loaded_config)
            
            try:
                print("Starting pyright...")
                await client.start()
                print("✓ Pyright started successfully")
                
                # Wait for analysis
                await asyncio.sleep(2.0)
                
                # Open the main file
                file_uri = ensure_file_uri(str(main_file))
                with open(main_file) as f:
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
                
                # Test hover on imported function
                print("\nTesting hover on 'greet' (imported function)...")
                response = await client.request("textDocument/hover", {
                    "textDocument": {"uri": file_uri},
                    "position": {"line": 1, "character": 18}  # On "greet" in import
                })
                
                if response and "contents" in response:
                    contents = response["contents"]
                    if isinstance(contents, dict) and "value" in contents:
                        print(f"✓ Hover successful: {contents['value'][:100]}...")
                    else:
                        print(f"✓ Hover response: {response}")
                else:
                    print("✗ No hover information (imports not resolved)")
                    
                # Test hover on Calculator
                print("\nTesting hover on 'Calculator' (imported class)...")
                response = await client.request("textDocument/hover", {
                    "textDocument": {"uri": file_uri},
                    "position": {"line": 5, "character": 7}  # On "Calculator()"
                })
                
                if response and "contents" in response:
                    print(f"✓ Got hover for Calculator")
                else:
                    print("✗ No hover for Calculator")
                    
            except Exception as e:
                print(f"✗ Error: {e}")
                import traceback
                traceback.print_exc()
            finally:
                await client.shutdown()
                print("Shutdown complete")

if __name__ == "__main__":
    import os
    os.environ["LOG_LEVEL"] = "INFO"
    asyncio.run(test_pixi_env())