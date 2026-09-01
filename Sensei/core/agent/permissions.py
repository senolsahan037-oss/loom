class PermissionsManager:
    """Manages execution permissions for tools and files based on agent mode."""
    def __init__(self, mode: str):
        self.mode = mode.lower()
        self.valid_modes = {"ask", "analyze", "plan", "review", "test", "tools"}
        if self.mode not in self.valid_modes:
            raise ValueError(f"Invalid mode: {self.mode}")
            
    def is_write_allowed(self) -> bool:
        # Patch and write operations are strictly disabled across all modes
        return False
        
    def is_read_allowed(self) -> bool:
        # Read-only operations are allowed in all valid modes
        return True
        
    def is_tool_allowed(self, tool_name: str) -> bool:
        # Any file write or raw shell execution tools are globally denied
        if tool_name in ["file_write", "shell_command"]:
            return False
            
        if self.mode == "review":
            # review mode only allows git diff inspect tool
            return tool_name == "git_diff"
            
        if self.mode == "test":
            # test mode only allows running pytest execution tool
            return tool_name == "pytest_runner"
            
        if self.mode == "ask":
            # ask mode only allows read-only workspace inspection
            return tool_name in ["file_read", "repo_inspect"]
            
        # analyze, plan, and tools modes allow all read-only inspection/execution tools
        return tool_name in ["file_read", "repo_inspect", "git_diff", "pytest_runner"]
