import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any

from core.agent.context_node import get_workspace_root

def is_safe_path(path_str: str) -> bool:
    """Verifies that the target path does not escape the workspace root directory."""
    root = get_workspace_root().resolve()
    try:
        p = Path(path_str)
        if not p.is_absolute():
            p = root / p
        resolved = p.resolve()
        # If it is inside the workspace, it starts with root's path
        return resolved.as_posix().startswith(root.as_posix())
    except Exception:
        return False

def get_safe_abs_path(path_str: str) -> Path:
    """Returns absolute path if safe, otherwise raises ValueError."""
    if not is_safe_path(path_str):
        raise ValueError(f"Security Violation: Path traversal or accessing path outside workspace is blocked: {path_str}")
    root = get_workspace_root().resolve()
    p = Path(path_str)
    if not p.is_absolute():
        p = root / p
    return p.resolve()


class BaseTool:
    """Base class for all agent tools."""
    name: str
    description: str

    def run(self, **kwargs) -> str:
        raise NotImplementedError()


class FileReadTool(BaseTool):
    name = "file_read"
    description = "Reads content from a specified workspace file. Path traversal outside workspace is blocked."

    def run(self, path: str = None, **kwargs) -> str:
        if not path:
            raise ValueError("Parameter 'path' is required.")
        abs_path = get_safe_abs_path(path)
        if not abs_path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            return f"Error reading file {path}: {e}"


class RepoInspectTool(BaseTool):
    name = "repo_inspect"
    description = "Lists files and subdirectories in a directory. Path traversal outside workspace is blocked."

    def run(self, path: str = ".", **kwargs) -> str:
        abs_path = get_safe_abs_path(path)
        if not abs_path.is_dir():
            raise FileNotFoundError(f"Directory not found: {path}")
        
        try:
            items = sorted(os.listdir(abs_path))
            lines = []
            for item in items:
                item_path = abs_path / item
                # Determine type
                suffix = "/" if item_path.is_dir() else ""
                lines.append(f"{item}{suffix}")
            return "\n".join(lines) if lines else "[Empty Directory]"
        except Exception as e:
            return f"Error inspecting directory {path}: {e}"


class GitDiffTool(BaseTool):
    name = "git_diff"
    description = "Displays the git diff for the workspace changes."

    def run(self, **kwargs) -> str:
        root = get_workspace_root()
        try:
            output = subprocess.check_output(
                "git diff -- .",
                shell=True,
                cwd=root,
                text=True,
                stderr=subprocess.STDOUT
            )
            return output if output.strip() else "[No changes in git diff]"
        except Exception as e:
            return f"Error executing git diff: {e}"


class PytestRunnerTool(BaseTool):
    name = "pytest_runner"
    description = "Runs the pytest test suite for a specific file or the entire repository."

    def run(self, file: str = None, **kwargs) -> str:
        root = get_workspace_root()
        cmd = "pytest -q"
        if file:
            # Block traversal for safety
            safe_file = get_safe_abs_path(file)
            cmd = f"pytest -q {safe_file.as_posix()}"
            
        try:
            output = subprocess.check_output(
                cmd,
                shell=True,
                cwd=root,
                text=True,
                stderr=subprocess.STDOUT
            )
            return output
        except subprocess.CalledProcessError as cpe:
            # Pytest returns exit code > 0 if tests fail; return output anyway
            return cpe.output
        except Exception as e:
            return f"Error executing pytest: {e}"


class ToolRegistry:
    """Registry for inspection/read tools."""
    def __init__(self):
        self._tools = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        return self._tools.get(name)

    def list_tools(self) -> List[BaseTool]:
        return list(self._tools.values())


class ExecutionRegistry:
    """Registry for execution/runner tools."""
    def __init__(self):
        self._tools = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        return self._tools.get(name)

    def list_tools(self) -> List[BaseTool]:
        return list(self._tools.values())


# Instantiate global registries
tool_registry = ToolRegistry()
execution_registry = ExecutionRegistry()

# Register tools under their respective registries
tool_registry.register(FileReadTool())
tool_registry.register(RepoInspectTool())
tool_registry.register(GitDiffTool())

execution_registry.register(PytestRunnerTool())
