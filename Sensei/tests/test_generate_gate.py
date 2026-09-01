from core.generate_gate import evaluate_generate

import json
import subprocess
import sys
from pathlib import Path


def test_generate_reports_missing_target_without_payload():
    result = evaluate_generate({"genre": "Hip Hop", "bars": 4, "seed": 1})
    assert result["status"] == "blocked"
    assert result["write_authorized"] is False
    assert result["payload"] is None
    assert result["report"]["code"] == "project_target_context_missing"


def test_generate_reports_unresolved_target_without_writing():
    result = evaluate_generate({"target_context": {}, "genre": "Hip Hop", "bars": 4, "seed": 1})
    assert result["status"] == "blocked"
    assert result["report"]["code"] == "target_profile_unresolved"


def test_generate_cli_runs_from_outside_project_root(tmp_path):
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "tools" / "generate_cli.py"), "--context", str(root / "state" / "current_project_context.example.json")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert report["schema_version"] == "sensei.generate-report.v1"
    assert report["status"] == "ready_to_write"
    assert report["write_authorized"] is True
    assert report["payload"]["notes"]


def test_generate_cli_fails_closed_when_context_is_missing(tmp_path):
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "tools" / "generate_cli.py"), "--context", str(tmp_path / "missing.json")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert report["status"] == "blocked"
    assert report["write_authorized"] is False
    assert report["report"]["code"] == "project_context_unreadable"
