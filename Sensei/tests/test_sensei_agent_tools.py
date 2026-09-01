import pytest
from pathlib import Path
from core.agent.tools import (
    tool_registry,
    execution_registry,
    is_safe_path,
    get_safe_abs_path
)

def test_safe_path_check():
    # Relative path should resolve to safe location inside workspace
    assert is_safe_path("core/agent/context_node.py") is True
    assert is_safe_path("core/../core/agent") is True
    
    # Path traversal attempting to escape workspace root
    assert is_safe_path("../../../etc/passwd") is False
    assert is_safe_path("/etc/passwd") is False

def test_file_read_tool():
    reader = tool_registry.get("file_read")
    assert reader is not None
    
    # Read self
    content = reader.run(path="tests/test_sensei_agent_tools.py")
    assert "def test_file_read_tool" in content
    
    # Block path traversal
    with pytest.raises(ValueError):
        reader.run(path="../../../etc/passwd")

def test_repo_inspect_tool():
    inspector = tool_registry.get("repo_inspect")
    assert inspector is not None
    
    # Inspect agent dir
    content = inspector.run(path="core/agent")
    assert "context_node.py" in content
    assert "tools.py" in content
    
    # Block path traversal
    with pytest.raises(ValueError):
        inspector.run(path="../../../../")

def test_pytest_runner_tool():
    runner = execution_registry.get("pytest_runner")
    assert runner is not None
    
    # Check that pytest_runner is in execution registry, not tool registry
    assert tool_registry.get("pytest_runner") is None
    assert execution_registry.get("file_read") is None

def test_git_diff_tool():
    diff = tool_registry.get("git_diff")
    assert diff is not None
    res = diff.run()
    assert isinstance(res, str)
