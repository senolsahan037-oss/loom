import pytest
import sys
from io import StringIO
from unittest.mock import MagicMock, patch
from core.agent.runtime import SenseiAgent
from core.agent.providers.base import LLMProviderBase
from core.agent.protocol_validation import ProtocolValidationError
from tools.sensei import main as cli_main

class MockProvider(LLMProviderBase):
    def __init__(self, response_text: str):
        self.response_text = response_text
        self.last_prompt = None
        self.last_system_instruction = None

    def ask(self, prompt: str, system_instruction: str = None) -> str:
        self.last_prompt = prompt
        self.last_system_instruction = system_instruction
        return self.response_text


def test_runtime_flow_success():
    provider = MockProvider("This is a valid plan without code.")
    agent = SenseiAgent("plan", provider=provider)
    res = agent.run("implement context selector", with_context=False)
    assert res == "This is a valid plan without code."
    assert provider.last_prompt == "implement context selector"
    assert "You are Sensei agent." in provider.last_system_instruction

def test_runtime_flow_violates_protocol():
    provider = MockProvider("```python\ndef bad(): pass\n```")
    agent = SenseiAgent("plan", provider=provider)
    with pytest.raises(ProtocolValidationError):
        agent.run("implement context selector", with_context=False)

def test_runtime_with_context():
    from core.agent.context_node import build_index
    build_index()
    provider = MockProvider("Valid response.")
    agent = SenseiAgent("plan", provider=provider)
    # This should run select_context internally and append context to the prompt
    res = agent.run("change midi runtime settings", with_context=True)
    assert "=== Context ===" in provider.last_prompt
    assert "core/midi_runtime.py" in provider.last_prompt

def test_cli_tools_list():
    test_args = ["tools/sensei.py", "tools", "list"]
    with patch.object(sys, "argv", test_args), \
         patch("sys.stdout", new_callable=StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc_info:
            cli_main()
        assert exc_info.value.code == 0
        stdout_output = mock_stdout.getvalue()
        assert "=== Available Tools ===" in stdout_output
        assert "file_read" in stdout_output
        assert "pytest_runner" in stdout_output

def test_cli_tools_info():
    test_args = ["tools/sensei.py", "tools", "info", "file_read"]
    with patch.object(sys, "argv", test_args), \
         patch("sys.stdout", new_callable=StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc_info:
            cli_main()
        assert exc_info.value.code == 0
        stdout_output = mock_stdout.getvalue()
        assert "Tool Name: file_read" in stdout_output
        assert "Status: Allowed" in stdout_output

def test_cli_tools_run():
    test_args = ["tools/sensei.py", "tools", "run", "repo_inspect", "--path", "core/agent"]
    with patch.object(sys, "argv", test_args), \
         patch("sys.stdout", new_callable=StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc_info:
            cli_main()
        assert exc_info.value.code == 0
        stdout_output = mock_stdout.getvalue()
        assert "context_node.py" in stdout_output
        assert "runtime.py" in stdout_output
