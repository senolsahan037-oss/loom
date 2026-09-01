import json
import os
import sys
import time
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add root folder to sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent.context_node import (
    build_index,
    get_index_path,
    get_index_status,
    get_index_summary,
    select_context,
    should_exclude,
    tokenize_task
)
from tools.sensei import main as cli_main


@pytest.fixture(autouse=True)
def mock_index_path(tmp_path, monkeypatch):
    """Mocks the index file path to point to a temporary folder to avoid modifying real index."""
    mock_index_path = tmp_path / "mock_agent_index.json"
    monkeypatch.setattr("core.agent.context_node.get_index_path", lambda: mock_index_path)
    return mock_index_path


def test_should_exclude():
    root = Path("/workspace")
    
    # Excluded directories
    assert should_exclude(Path("/workspace/.git/config"), root) is True
    assert should_exclude(Path("/workspace/.venv/bin/python"), root) is True
    assert should_exclude(Path("/workspace/venv/lib/site-packages"), root) is True
    assert should_exclude(Path("/workspace/__pycache__/utils.cpython-310.pyc"), root) is True
    assert should_exclude(Path("/workspace/node_modules/lodash/package.json"), root) is True
    assert should_exclude(Path("/workspace/.pytest_cache/v/cache/lastfailed"), root) is True
    assert should_exclude(Path("/workspace/exports/my_song.mid"), root) is True
    assert should_exclude(Path("/workspace/data/raw/dataset.db"), root) is True
    
    # Excluded extensions
    assert should_exclude(Path("/workspace/core/cache.sqlite"), root) is True
    assert should_exclude(Path("/workspace/core/dataset.db"), root) is True
    assert should_exclude(Path("/workspace/core/song.pkl"), root) is True
    assert should_exclude(Path("/workspace/core/preset.adg"), root) is True
    assert should_exclude(Path("/workspace/core/clip.alc"), root) is True
    assert should_exclude(Path("/workspace/core/groove.agr"), root) is True
    assert should_exclude(Path("/workspace/core/icon.png"), root) is True
    assert should_exclude(Path("/workspace/core/song.wav"), root) is True
    
    # Allowed files
    assert should_exclude(Path("/workspace/core/agent/context_node.py"), root) is False
    assert should_exclude(Path("/workspace/README.md"), root) is False
    assert should_exclude(Path("/workspace/docs/architecture.md"), root) is False


def test_build_index(mock_index_path):
    assert not mock_index_path.exists()
    
    index = build_index()
    
    assert mock_index_path.exists()
    assert isinstance(index, dict)
    
    # Verify index contains python files and readme
    assert "README.md" in index
    assert "tools/sensei.py" in index
    assert "core/agent/context_node.py" in index
    
    # Verify metadata fields
    meta = index["core/agent/context_node.py"]
    assert "size" in meta
    assert "mtime" in meta
    assert "symbols" in meta
    assert "summary" in meta
    assert "dependencies" in meta
    
    # Verify symbol detection worked
    assert any("def build_index" in sym for sym in meta["symbols"])
    assert any("class" not in sym for sym in meta["symbols"])  # context_node has functions, not classes
    
    # Verify markdown summary detection worked for README.md
    readme_meta = index["README.md"]
    assert "Sensei" in readme_meta["summary"]


def test_get_index_status(mock_index_path):
    status_missing = get_index_status()
    assert status_missing["exists"] is False
    
    build_index()
    
    status_present = get_index_status()
    assert status_present["exists"] is True
    assert status_present["file_count"] > 0
    assert status_present["size"] > 0
    assert status_present["last_modified"] > 0.0


def test_get_index_summary():
    build_index()
    summary = get_index_summary()
    assert "=== Repo Index Summary ===" in summary
    assert "core/agent/context_node.py" in summary
    assert "def should_exclude" in summary


def test_agent_task_selection():
    build_index()
    
    # Task mentions "context node" -> agent task
    selected, context, reason, meta = select_context("Implement local context node", "plan")
    
    # Always include files for agent tasks: tools/sensei.py, core/agent/*
    assert "tools/sensei.py" in selected
    assert "core/agent/context_node.py" in selected
    assert "always included for agent tasks" in reason
    
    # Verify details in returned metadata
    assert "tools/sensei.py" in meta
    assert meta["tools/sensei.py"]["reason"] == "always included for agent tasks"
    assert meta["tools/sensei.py"]["truncated"] is False


def test_docs_task_selection():
    build_index()
    
    # Non-docs task should exclude docs
    selected, _, _, _ = select_context("implement local context node", "plan")
    assert not any(f.startswith("docs/") for f in selected)
    
    # Docs task should allow/include docs
    selected_docs, _, reason_docs, meta_docs = select_context("read docs architecture guidelines", "plan")
    docs_files = [f for f in selected_docs if f.startswith("docs/")]
    assert len(docs_files) > 0
    assert any("docs/" in line for line in reason_docs.splitlines())


def test_ask_mode_no_context_by_default():
    # Verify that select_context in ask mode with a simple query doesn't trigger agent/docs rules unless forced
    selected, context, _, _ = select_context("how do I generate drums?", "ask")
    
    # Default selection should just be README.md (if small), no music engine/docs files unless matching keywords
    # Ensure it doesn't automatically pull tools/sensei.py or core/agent/* since it is not an agent task
    assert "tools/sensei.py" not in selected
    assert "core/agent/context_node.py" not in selected
    assert not any(f.startswith("docs/") for f in selected)


def test_context_max_chars_limit():
    build_index()
    
    # Test strict max_chars bounding
    selected, context, reason, meta = select_context("implement local context node", "plan", max_chars=1000)
    
    # Length of context must be <= 1000 characters
    assert len(context) <= 1000
    assert "[Content truncated due to max_chars limit...]" in context or len(context) == 0


def test_python_import_dependency_indexing():
    build_index()
    index = build_index()
    assert "core/midi_runtime.py" in index
    meta = index["core/midi_runtime.py"]
    deps = meta.get("dependencies", [])
    assert "core/midi_variation_engine.py" in deps
    assert "core/target_resolver.py" in deps


def test_one_hop_dependency_expansion():
    build_index()
    selected, context, reason, meta = select_context("change midi runtime settings", "plan", max_chars=60000)
    assert "core/midi_runtime.py" in selected
    assert "core/midi_variation_engine.py" in selected
    assert meta["core/midi_variation_engine.py"]["reason"] == "one-hop dependency"
    assert "imported by core/midi_runtime.py" in meta["core/midi_variation_engine.py"]["dependency_reason"]


def test_taxonomy_stricter_filtering():
    build_index()
    
    # 1. Taxonomy matches exist but task is agent task and does NOT mention dataset/taxonomy/capabilities
    # It must not select DatasetRoot/taxonomy/ files
    selected_normal, _, _, _ = select_context("taxonomy mapping in context node", "plan")
    # Wait, if task mentions "taxonomy", then taxonomy keywords are matched, so it is allowed!
    # Let's test a task that does NOT mention taxonomy:
    selected_agent, _, _, _ = select_context("implement agent runtime prompt", "plan")
    assert not any("DatasetRoot/taxonomy" in f for f in selected_agent)
    
    # 2. If task explicitly mentions dataset/taxonomy/capabilities:
    selected_tax, _, _, _ = select_context("read dataset taxonomy capabilities", "plan")
    # It should allow files under DatasetRoot/taxonomy/ if they matched any keywords
    # (let's assume we have files under DatasetRoot/taxonomy/ indexed)
    tax_files = [f for f in selected_tax if "DatasetRoot/taxonomy" in f]
    # If there are any taxonomy files matching the keywords, they should be included now
    # We can check they are not strictly blocked here.


@patch("tools.sensei.ask_vertex")
def test_cli_diagnostics_and_modes(mock_ask_vertex):
    mock_ask_vertex.return_value = "Mocked LLM Response"
    build_index()
    
    # Mock CLI arguments for plan mode
    test_args = ["tools/sensei.py", "plan", "implement context node"]
    
    with patch.object(sys, "argv", test_args), \
         patch("sys.stderr", new_callable=StringIO) as mock_stderr:
        
        cli_main()
        
        # Verify diagnostics printed to stderr
        stderr_output = mock_stderr.getvalue()
        assert "[sensei diagnostic]" in stderr_output
        assert "mode=plan" in stderr_output
        assert "provider=vertex" in stderr_output
        assert "selected_files=" in stderr_output
        assert "context_chars=" in stderr_output
        assert "index_used=true" in stderr_output


@patch("tools.sensei.ask_vertex")
def test_cli_ask_mode_default_and_forced(mock_ask_vertex):
    mock_ask_vertex.return_value = "Mocked LLM Response"
    build_index()
    
    # 1. Ask mode WITHOUT --with-context
    test_args_default = ["tools/sensei.py", "ask", "how to write tests"]
    with patch.object(sys, "argv", test_args_default), \
         patch("sys.stderr", new_callable=StringIO) as mock_stderr:
        
        cli_main()
        
        stderr_output = mock_stderr.getvalue()
        assert "index_used=false" in stderr_output
        assert "selected_files=0" in stderr_output

    # 2. Ask mode WITH --with-context
    test_args_forced = ["tools/sensei.py", "ask", "how to write tests", "--with-context"]
    with patch.object(sys, "argv", test_args_forced), \
         patch("sys.stderr", new_callable=StringIO) as mock_stderr:
         
        cli_main()
        
        stderr_output = mock_stderr.getvalue()
        assert "index_used=true" in stderr_output
        assert "selected_files=" in stderr_output


def test_cli_explain_command():
    build_index()
    
    test_args = ["tools/sensei.py", "context", "explain", "implement context node"]
    
    with patch.object(sys, "argv", test_args), \
         patch("sys.stdout", new_callable=StringIO) as mock_stdout:
         
        with pytest.raises(SystemExit) as exc_info:
            cli_main()
            
        assert exc_info.value.code == 0
        
        stdout_output = mock_stdout.getvalue()
        assert "=== Context Explanation ===" in stdout_output
        assert "Task: 'implement context node'" in stdout_output
        assert "File: core/agent/context_node.py" in stdout_output
        assert "Match Score:" in stdout_output
        assert "Selection Reason:" in stdout_output


def test_v1_2_specifications():
    build_index()
    
    # 1. plan mode default context <= 20000
    selected, context, reason, meta = select_context("change midi runtime settings", "plan")
    assert len(context) <= 20000
    assert meta["_budget"]["profile"] == "plan"
    assert meta["_budget"]["chars"] == 20000
    
    # 2. --deep allows up to 60000
    selected_deep, context_deep, reason_deep, meta_deep = select_context("change builder settings", "plan", deep=True)
    assert len(context_deep) <= 60000
    assert meta_deep["_budget"]["profile"] == "deep"
    assert meta_deep["_budget"]["chars"] == 60000
    
    # 3. README not included for a narrow runtime task
    assert "README.md" not in selected
    
    # 4. README included for architecture/overview task
    selected_arch, _, _, _ = select_context("read architecture overview", "plan")
    assert "README.md" in selected_arch

    # 5. ask mode forced uses default 8000
    selected_ask, context_ask, reason_ask, meta_ask = select_context("change midi runtime settings", "ask")
    assert len(context_ask) <= 8000
    assert meta_ask["_budget"]["profile"] == "default"
    assert meta_ask["_budget"]["chars"] == 8000

    # 6. context select prints budget info
    test_args = ["tools/sensei.py", "context", "select", "change builder settings"]
    with patch.object(sys, "argv", test_args), \
         patch("sys.stdout", new_callable=StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc_info:
            cli_main()
        assert exc_info.value.code == 0
        stdout_output = mock_stdout.getvalue()
        assert "budget profile: plan" in stdout_output
        assert "budget chars: 20000" in stdout_output
        assert "used chars:" in stdout_output
        assert "selected files count:" in stdout_output


def test_v1_3_specifications(mock_index_path):
    # 1. Turkish stop words are removed
    tokens = tokenize_task("ve veya ama ise için gibi ile en daha ki da de mu mı mi mü bu şu o ne nasıl neden niçin çünkü change builder")
    assert "ve" not in tokens
    assert "veya" not in tokens
    assert "ama" not in tokens
    assert "change" in tokens
    assert "builder" in tokens

    # 2. Incremental indexing reuses unchanged file records
    build_index()
    index_path = mock_index_path
    with open(index_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    # Inject dummy symbol into cached entry
    test_file = "core/agent/__init__.py"
    assert test_file in data
    data[test_file]["symbols"] = ["class DummyReuseTest"]
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        
    # Rebuild index
    new_index = build_index()
    # Verify it reused cached entry and kept dummy symbol
    assert new_index[test_file]["symbols"] == ["class DummyReuseTest"]
    assert new_index["_diagnostics"]["reused_count"] > 0

    # 3. Changed file is re-indexed
    # Change cached record's mtime so it forces a re-index
    new_index[test_file]["mtime"] = 0.0
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(new_index, f, indent=2)
        
    new_index_2 = build_index()
    # Dummy symbol must be gone because the file was re-parsed!
    assert "class DummyReuseTest" not in new_index_2[test_file]["symbols"]

    # 4. AST parse error is recorded in diagnostics
    bad_file_path = ROOT / "invalid_test_ast.py"
    with open(bad_file_path, "w", encoding="utf-8") as f:
        f.write("def bad_syntax(:\n    pass\n")
        
    try:
        final_index = build_index()
        assert any("invalid_test_ast.py: AST Parse Error:" in err for err in final_index["_diagnostics"]["ast_errors"])
    finally:
        if bad_file_path.exists():
            bad_file_path.unlink()
            
    # 5. Ableton-only research policy is present
    from tools.sensei import SYSTEM_INSTRUCTION
    assert "ableton.com" in SYSTEM_INSTRUCTION
    assert "help.ableton.com" in SYSTEM_INSTRUCTION
    assert "www.ableton.com" in SYSTEM_INSTRUCTION
    assert "Web research tool unavailable." in SYSTEM_INSTRUCTION

