"""
Pytest configuration and fixtures for pyright-mcp tests.
"""

import asyncio
import importlib.util
import json
import shutil

# Add src directory to path to import jons_mcp_pyright
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jons_mcp_pyright import PyrightClient, PyrightClientManager


def pyright_available() -> bool:
    """Return True when a Pyright language server can be started."""
    return importlib.util.find_spec("pyright") is not None or bool(
        shutil.which("pyright-langserver")
    )


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
    if not pyright_available():
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
async def pyright_manager(temp_python_project: Path) -> AsyncGenerator[PyrightClientManager, None]:
    """Create and start a PyrightClientManager for integration testing.

    This fixture sets up the manager in the server module so tools work correctly.
    """
    if not pyright_available():
        pytest.skip("pyright not found - install with 'pip install pyright'")

    from jons_mcp_pyright import server as server_module

    # Create and start the manager
    manager = PyrightClientManager(temp_python_project)

    try:
        await manager.start_root_client()
        # Give pyright a moment to analyze the project
        await asyncio.sleep(0.5)

        # Set up server module globals
        server_module.manager = manager
        server_module.initialization_complete = True

        yield manager
    finally:
        server_module.initialization_complete = False
        await manager.shutdown_all()
        server_module.manager = None


@pytest.fixture
def mock_lsp_messages() -> dict[str, Any]:
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
    from jons_mcp_pyright import server as server_module
    # Save original state
    original_complete = server_module.initialization_complete
    original_manager = server_module.manager

    # Set test state
    server_module.initialization_complete = True

    yield

    # Restore original state
    server_module.initialization_complete = original_complete
    server_module.manager = original_manager


@pytest.fixture
def multi_env_project(tmp_path: Path) -> Path:
    """Create a multi-environment Python project for testing.

    Structure:
    /root
        pyproject.toml
        pyrightconfig.json
        /src
            __init__.py
            main.py
        /packages
            /pkg-a
                pyproject.toml
                pyrightconfig.json
                /src
                    __init__.py
                    module_a.py
            /pkg-b
                pyproject.toml
                pyrightconfig.json
                /src
                    __init__.py
                    module_b.py
    """
    # Root project
    root_pyproject = tmp_path / "pyproject.toml"
    root_pyproject.write_text("""[project]
name = "root-project"
version = "0.1.0"
requires-python = ">=3.10"
""")

    root_pyrightconfig = tmp_path / "pyrightconfig.json"
    root_pyrightconfig.write_text(json.dumps({
        "include": ["src"],
        "typeCheckingMode": "basic",
        "pythonVersion": "3.10",
    }, indent=2))

    root_src = tmp_path / "src"
    root_src.mkdir()
    (root_src / "__init__.py").write_text('"""Root package."""\n')
    (root_src / "main.py").write_text('''"""Root main module."""

def root_func() -> str:
    """Function defined in root project."""
    return "root"

ROOT_CONSTANT = 42
''')

    # Package A
    pkg_a = tmp_path / "packages" / "pkg-a"
    pkg_a.mkdir(parents=True)

    pkg_a_pyproject = pkg_a / "pyproject.toml"
    pkg_a_pyproject.write_text("""[project]
name = "pkg-a"
version = "0.1.0"
requires-python = ">=3.10"
""")

    pkg_a_pyrightconfig = pkg_a / "pyrightconfig.json"
    pkg_a_pyrightconfig.write_text(json.dumps({
        "include": ["src"],
        "typeCheckingMode": "strict",
        "pythonVersion": "3.10",
    }, indent=2))

    pkg_a_src = pkg_a / "src"
    pkg_a_src.mkdir()
    (pkg_a_src / "__init__.py").write_text('"""Package A."""\n')
    (pkg_a_src / "module_a.py").write_text('''"""Module A."""

from typing import List

def func_a(items: List[str]) -> int:
    """Function in package A."""
    return len(items)

class ClassA:
    """Class defined in package A."""

    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self) -> str:
        """Greet using name."""
        return f"Hello from A, {self.name}!"

PKG_A_CONSTANT = "pkg_a"
''')

    # Package B
    pkg_b = tmp_path / "packages" / "pkg-b"
    pkg_b.mkdir(parents=True)

    pkg_b_pyproject = pkg_b / "pyproject.toml"
    pkg_b_pyproject.write_text("""[project]
name = "pkg-b"
version = "0.1.0"
requires-python = ">=3.10"
""")

    pkg_b_pyrightconfig = pkg_b / "pyrightconfig.json"
    pkg_b_pyrightconfig.write_text(json.dumps({
        "include": ["src"],
        "typeCheckingMode": "basic",
        "pythonVersion": "3.10",
    }, indent=2))

    pkg_b_src = pkg_b / "src"
    pkg_b_src.mkdir()
    (pkg_b_src / "__init__.py").write_text('"""Package B."""\n')
    (pkg_b_src / "module_b.py").write_text('''"""Module B."""

from typing import Dict

def func_b(data: Dict[str, int]) -> int:
    """Function in package B."""
    return sum(data.values())

class ClassB:
    """Class defined in package B."""

    def __init__(self, value: int) -> None:
        self.value = value

    def compute(self) -> int:
        """Compute double the value."""
        return self.value * 2

PKG_B_CONSTANT = "pkg_b"
''')

    return tmp_path


@pytest.fixture
async def multi_env_manager(multi_env_project: Path) -> AsyncGenerator[PyrightClientManager, None]:
    """Create PyrightClientManager for multi-environment testing.

    This discovers all three environments and sets up the server module.
    """
    if not pyright_available():
        pytest.skip("pyright not found - install with 'pip install pyright'")

    from jons_mcp_pyright import server as server_module

    # Create and start the manager (will discover all environments)
    manager = PyrightClientManager(multi_env_project)

    try:
        await manager.start_root_client()
        # Give pyright a moment to analyze
        await asyncio.sleep(1.0)

        # Set up server module globals
        server_module.manager = manager
        server_module.initialization_complete = True

        yield manager
    finally:
        server_module.initialization_complete = False
        await manager.shutdown_all()
        server_module.manager = None
