"""
Pytest configuration and fixtures for pyright-mcp tests.
"""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Dict, Any
import pytest

# Add parent directory to path to import pyright_mcp
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from pyright_mcp import PyrightClient


@pytest.fixture
def temp_python_project(tmp_path: Path) -> Path:
    """Create a temporary Python project for testing."""
    # Create pyproject.toml
    pyproject_toml = tmp_path / "pyproject.toml"
    pyproject_toml.write_text("""[project]
name = "test_project"
version = "0.1.0"
requires-python = ">=3.10"

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"
""")
    
    # Create src directory
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    
    # Create __init__.py
    init_py = src_dir / "__init__.py"
    init_py.write_text('"""Test package."""\n')
    
    # Create main.py
    main_py = src_dir / "main.py"
    main_py.write_text('''"""Main module for testing."""

def greet(name: str) -> str:
    """Greet someone by name.
    
    Args:
        name: The name to greet
        
    Returns:
        A greeting message
    """
    return f"Hello, {name}!"


def add(a: int, b: int) -> int:
    """Add two numbers.
    
    Args:
        a: First number
        b: Second number
        
    Returns:
        Sum of a and b
    """
    return a + b


class Calculator:
    """A simple calculator class."""
    
    def __init__(self, initial_value: float = 0.0):
        """Initialize calculator with a value.
        
        Args:
            initial_value: Starting value
        """
        self.value = initial_value
    
    def add(self, x: float) -> None:
        """Add to the current value.
        
        Args:
            x: Value to add
        """
        self.value += x
    
    def multiply(self, x: float) -> None:
        """Multiply the current value.
        
        Args:
            x: Value to multiply by
        """
        self.value *= x
    
    def get_value(self) -> float:
        """Get the current value.
        
        Returns:
            The current value
        """
        return self.value


if __name__ == "__main__":
    print(greet("World"))
    calc = Calculator(10)
    calc.add(5)
    print(f"Calculator value: {calc.get_value()}")
''')
    
    # Create utils.py
    utils_py = src_dir / "utils.py"
    utils_py.write_text('''"""Utility functions."""

from typing import List, Optional, Protocol


class Processor(Protocol):
    """Protocol for data processors."""
    
    def process(self, data: str) -> str:
        """Process the data."""
        ...


class UppercaseProcessor:
    """Processor that converts to uppercase."""
    
    def process(self, data: str) -> str:
        """Convert data to uppercase.
        
        Args:
            data: Input string
            
        Returns:
            Uppercase string
        """
        return data.upper()


def filter_items(items: List[str], prefix: Optional[str] = None) -> List[str]:
    """Filter items by prefix.
    
    Args:
        items: List of items to filter
        prefix: Optional prefix to filter by
        
    Returns:
        Filtered list of items
    """
    if prefix is None:
        return items
    return [item for item in items if item.startswith(prefix)]


def parse_number(value: str) -> int | float:
    """Parse a string to a number.
    
    Args:
        value: String representation of a number
        
    Returns:
        Parsed number (int or float)
        
    Raises:
        ValueError: If value cannot be parsed
    """
    try:
        # Try to parse as int first
        return int(value)
    except ValueError:
        # Fall back to float
        return float(value)
''')
    
    # Create test_main.py
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    
    test_main_py = test_dir / "test_main.py"
    test_main_py.write_text('''"""Tests for main module."""

import pytest
from src.main import greet, add, Calculator


def test_greet():
    """Test the greet function."""
    assert greet("Alice") == "Hello, Alice!"
    assert greet("Bob") == "Hello, Bob!"


def test_add():
    """Test the add function."""
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(0, 0) == 0


class TestCalculator:
    """Test the Calculator class."""
    
    def test_init(self):
        """Test calculator initialization."""
        calc = Calculator()
        assert calc.get_value() == 0.0
        
        calc2 = Calculator(10.5)
        assert calc2.get_value() == 10.5
    
    def test_add(self):
        """Test calculator addition."""
        calc = Calculator(5)
        calc.add(3)
        assert calc.get_value() == 8
    
    def test_multiply(self):
        """Test calculator multiplication."""
        calc = Calculator(4)
        calc.multiply(3)
        assert calc.get_value() == 12
''')
    
    # Create pyrightconfig.json
    pyrightconfig = tmp_path / "pyrightconfig.json"
    pyrightconfig.write_text(json.dumps({
        "include": ["src", "tests"],
        "exclude": ["**/__pycache__"],
        "typeCheckingMode": "strict",
        "pythonVersion": "3.10",
        "venvPath": ".",
        "venv": ".venv"
    }, indent=2))
    
    return tmp_path


@pytest.fixture
async def pyright_client(temp_python_project: Path) -> AsyncGenerator[PyrightClient, None]:
    """Create and start a pyright client for testing."""
    # Check if pyright is available
    try:
        import pyright
        has_pyright = True
    except ImportError:
        has_pyright = False
    
    if not has_pyright and not shutil.which("pyright-langserver"):
        pytest.skip("pyright not found - install with 'pip install pyright'")
    
    client = PyrightClient(temp_python_project)
    
    try:
        await client.start()
        # Give pyright a moment to analyze the project
        await asyncio.sleep(0.5)
        yield client
    finally:
        await client.shutdown()


@pytest.fixture
def mock_lsp_messages() -> Dict[str, Any]:
    """Mock LSP messages for testing."""
    return {
        "initialize_response": {
            "capabilities": {
                "textDocumentSync": 2,
                "hoverProvider": True,
                "completionProvider": {
                    "resolveProvider": True,
                    "triggerCharacters": [".", "(", "[", ",", " "]
                },
                "signatureHelpProvider": {
                    "triggerCharacters": ["(", ","]
                },
                "definitionProvider": True,
                "typeDefinitionProvider": True,
                "implementationProvider": True,
                "referencesProvider": True,
                "documentSymbolProvider": True,
                "workspaceSymbolProvider": True,
                "codeActionProvider": {
                    "codeActionKinds": ["quickfix", "source.organizeImports"]
                },
                "renameProvider": {
                    "prepareProvider": True
                },
                "documentFormattingProvider": True,
                "documentRangeFormattingProvider": True,
                "semanticTokensProvider": {
                    "legend": {
                        "tokenTypes": [],
                        "tokenModifiers": []
                    },
                    "full": True,
                    "range": True
                }
            }
        },
        "hover_response": {
            "contents": {
                "kind": "markdown",
                "value": "```python\ndef add(a: int, b: int) -> int\n```\n\nAdd two numbers."
            },
            "range": {
                "start": {"line": 14, "character": 4},
                "end": {"line": 14, "character": 7}
            }
        },
        "completion_response": {
            "items": [
                {
                    "label": "print",
                    "kind": 3,  # Function
                    "detail": "def print(*values, sep=' ', end='\\n', file=None, flush=False)",
                    "documentation": {
                        "kind": "markdown",
                        "value": "Print objects to the text stream file."
                    }
                },
                {
                    "label": "len",
                    "kind": 3,
                    "detail": "def len(__obj: Sized) -> int",
                    "documentation": {
                        "kind": "markdown",
                        "value": "Return the length of an object."
                    }
                }
            ]
        },
        "definition_response": {
            "uri": "file:///test/src/main.py",
            "range": {
                "start": {"line": 14, "character": 0},
                "end": {"line": 25, "character": 15}
            }
        },
        "diagnostics_notification": {
            "uri": "file:///test/src/main.py",
            "diagnostics": [
                {
                    "range": {
                        "start": {"line": 10, "character": 4},
                        "end": {"line": 10, "character": 11}
                    },
                    "severity": 1,  # Error
                    "code": "reportUndefinedVariable",
                    "source": "Pyright",
                    "message": '"unknown" is not defined'
                }
            ]
        }
    }


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def mock_initialization_state():
    """Mock the initialization state for tests."""
    import pyright_mcp
    # Save original state
    original_complete = getattr(pyright_mcp, 'initialization_complete', False)
    original_pyright = pyright_mcp.pyright
    original_opened_files = getattr(pyright_mcp, 'opened_files', set())
    
    # Set test state
    pyright_mcp.initialization_complete = True
    pyright_mcp.opened_files = set()
    
    yield
    
    # Restore original state
    pyright_mcp.initialization_complete = original_complete
    pyright_mcp.pyright = original_pyright
    pyright_mcp.opened_files = original_opened_files