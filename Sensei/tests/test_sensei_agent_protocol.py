import pytest
from core.agent.protocol_validation import validate_protocol, ProtocolValidationError

def test_protocol_validation_allowed():
    # Regular text and explanations are allowed
    validate_protocol("This is a plan explaining how to structure the code.")
    validate_protocol("You can define a function using the def keyword or a class using the class keyword in python.")
    validate_protocol(None)
    validate_protocol("")

def test_protocol_validation_reject_code_blocks():
    # Markdown code blocks
    with pytest.raises(ProtocolValidationError):
        validate_protocol("Here is the code:\n```python\ndef test():\n    pass\n```")
        
    with pytest.raises(ProtocolValidationError):
        validate_protocol("```\nclass Dummy:\n    pass\n```")

def test_protocol_validation_reject_diff_blocks():
    # Git diff content
    with pytest.raises(ProtocolValidationError):
        validate_protocol("Here is the patch:\ndiff --git a/tools/sensei.py b/tools/sensei.py\n--- a/tools/sensei.py\n+++ b/tools/sensei.py")

def test_protocol_validation_reject_raw_definitions():
    # Raw Python definitions starting a line
    with pytest.raises(ProtocolValidationError):
        validate_protocol("We should create this class:\nclass MyNewClass:\n    pass")
        
    with pytest.raises(ProtocolValidationError):
        validate_protocol("def my_func():\n    return 42")
