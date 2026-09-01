import pytest
from core.agent.permissions import PermissionsManager

def test_permissions_modes_invalid():
    with pytest.raises(ValueError):
        PermissionsManager("invalid_mode")

def test_permissions_write_never_allowed():
    for mode in ["ask", "analyze", "plan", "review", "test", "tools"]:
        perms = PermissionsManager(mode)
        assert perms.is_write_allowed() is False

def test_permissions_read_always_allowed():
    for mode in ["ask", "analyze", "plan", "review", "test", "tools"]:
        perms = PermissionsManager(mode)
        assert perms.is_read_allowed() is True

def test_permissions_tools_restrictions():
    # 1. Ask mode allowed tools
    perms_ask = PermissionsManager("ask")
    assert perms_ask.is_tool_allowed("file_read") is True
    assert perms_ask.is_tool_allowed("repo_inspect") is True
    assert perms_ask.is_tool_allowed("git_diff") is False
    assert perms_ask.is_tool_allowed("pytest_runner") is False
    assert perms_ask.is_tool_allowed("file_write") is False
    assert perms_ask.is_tool_allowed("shell_command") is False

    # 2. Review mode allowed tools
    perms_review = PermissionsManager("review")
    assert perms_review.is_tool_allowed("git_diff") is True
    assert perms_review.is_tool_allowed("file_read") is False
    assert perms_review.is_tool_allowed("pytest_runner") is False

    # 3. Test mode allowed tools
    perms_test = PermissionsManager("test")
    assert perms_test.is_tool_allowed("pytest_runner") is True
    assert perms_test.is_tool_allowed("file_read") is False
    assert perms_test.is_tool_allowed("git_diff") is False

    # 4. Plan/Tools/Analyze modes allow all read-only inspection/execution tools
    for mode in ["plan", "tools", "analyze"]:
        perms = PermissionsManager(mode)
        assert perms.is_tool_allowed("file_read") is True
        assert perms.is_tool_allowed("repo_inspect") is True
        assert perms.is_tool_allowed("git_diff") is True
        assert perms.is_tool_allowed("pytest_runner") is True
        assert perms.is_tool_allowed("file_write") is False
        assert perms.is_tool_allowed("shell_command") is False
