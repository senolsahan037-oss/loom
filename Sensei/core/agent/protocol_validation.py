import re

class ProtocolValidationError(Exception):
    """Raised when the LLM output violates the agent constraints protocol."""
    pass

def validate_protocol(output: str):
    """Enforces strict constraints: rejects code blocks, patches, and raw python definitions."""
    if not output:
        return

    # 1. Reject markdown code blocks (e.g. ```python or ```)
    if "```" in output or re.search(r"```[a-zA-Z0-9_-]*\n", output):
        raise ProtocolValidationError("Protocol Violation: Output contains disallowed markdown code blocks.")

    # 2. Reject git diff structures/patches
    if "diff --git" in output or "--- a/" in output or "+++ b/" in output:
        raise ProtocolValidationError("Protocol Violation: Output contains disallowed diff/patch segments.")

    # 3. Reject raw Python code declarations (def/class starting a line or code segment)
    lines = output.splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("def ") or stripped.startswith("class "):
            raise ProtocolValidationError(f"Protocol Violation: Output contains raw Python declarations ('{stripped}').")
