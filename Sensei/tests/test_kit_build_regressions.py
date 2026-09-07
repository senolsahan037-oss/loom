"""Kit regressions: synthetic profiles and isolated MCP calls, no Live access."""
import importlib.util
import json
from pathlib import Path

import pytest

from ableton import kit_resolver as kr


def test_unnamed_pad_uses_sample_basename():
    assert kr.role_from_label("Note 87", "unknown_pad", "Kick BNYX 2.wav") == "kick"
    assert kr.role_from_label("Note 86", None, "Hihat Closed BNYX.wav") == "closed_hat"
    assert kr.role_from_label("Note 85", None, "Hihat Open BNYX.wav") == "open_hat"
    assert kr.role_from_label("Rim", None, "Kick.wav") == "rim"
    assert kr.role_from_label("Custom texture") == "unknown_pad"


def test_gm_numbers_do_not_override_roles():
    mapping = kr.pad_mapping([{"note": 36, "role": "snare"},
                              {"note": 38, "role": "kick"},
                              {"note": 42, "role": "closed_hat"}])
    assert mapping["mode"] == "by_role"
    assert mapping["map"][36] == 38
    assert mapping["map"][38] == 36


def test_live_numbers_do_not_prove_preset_identity():
    kit = {"pads": [{"note": 80, "role": "kick"}]}
    result = kr.resolve_pad_notes([80], kit)
    assert result["mapping"]["generate_pads"] == []


def test_device_replacement_and_profile_safety_are_reported(tmp_path, monkeypatch):
    sample = tmp_path / "Kick BNYX.wav"
    sample.write_bytes(b"RIFF")
    monkeypatch.setattr(kr, "find_kit", lambda *a: tmp_path / "kit.adg")
    monkeypatch.setattr(kr, "_relative_paths", lambda *a: {})
    monkeypatch.setattr(kr, "build_kit_profile", lambda *a: {
        "kit_name": "BNYX", "pad_count": 1, "kit_write_safety": "unsafe",
        "device_chain_summary": {"87": {"devices": ["DrumCell"]}},
        "pads": {"87": {"label": "Note 87", "primary_sample": {"path": str(sample)},
                          "device_profile": {"decay": 123}, "normalized_role": "unknown_pad"}}})
    kit = kr.resolve_kit("kit")
    assert kit["pads"][0]["role_source"] == "sample_filename"
    assert kit["profile_write_safety"] == "unsafe"
    assert kit["fidelity"]["preset_preserved"] is False
    assert kit["fidelity"]["dropped"]["device_replacements"][0]["source"] == ["DrumCell"]
    assert kit["fidelity"]["dropped"]["device_parameters"]["87"] == {"decay": 123}


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOM_BRIDGE_ROOT", str(tmp_path / "bridge"))
    path = Path(__file__).resolve().parents[2] / "mcp_server" / "server.py"
    spec = importlib.util.spec_from_file_location("loom_kit_regressions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "handle_live_state", lambda *a: {})
    monkeypatch.setattr(module, "_beats_per_bar", lambda *a: (4, "test"))
    return module


def test_plan_kit_selection_is_per_track_and_override_is_visible(server, tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"project": {"bpm": 90}, "locators": [{"name": "Intro", "start_bar": 1, "end_bar": 4}],
                               "tracks": [{"name": "A", "sensei_role": "drum", "instrument_family": "Kit A"},
                                          {"name": "B", "sensei_role": "drum", "instrument_family": "Kit B"}]}))
    def resolve(ref):
        return {"kit": ref, "path": ref, "pad_count": 1, "missing": [],
                "pads": [{"note": 80, "name": "Kick", "sample": "kick.wav", "role": "kick"}],
                "fidelity": {"preset_preserved": False, "dropped": {}}, "profile_write_safety": "unsafe"}
    monkeypatch.setattr(server, "resolve_kit_reference", resolve)
    dry = server.handle_project_build({"plan_path": str(plan)})
    assert dry["kits"]["A"]["reference"] == "Kit A"
    assert dry["kits"]["B"]["reference"] == "Kit B"
    assert dry["kits"]["A"]["selection_source"] == "plan"
    override = server.handle_project_build({"plan_path": str(plan), "kit": "User Kit"})
    assert override["kits"]["A"]["selection_source"] == "explicit_kit"
    assert override["kits"]["A"]["plan_reference"] == "Kit A"
    assert override["kits"]["B"]["reference"] == "User Kit"


def test_lossy_kit_is_refused_before_bridge_submission(server, monkeypatch):
    monkeypatch.setattr(server, "resolve_kit_reference", lambda *a: {
        "kit": "BNYX", "pads": [{"note": 87}], "missing": [], "fidelity": {"preset_preserved": False}})
    monkeypatch.setattr(server.bridge_client, "submit_request", lambda *a, **k: pytest.fail("unexpected Live request"))
    result = server.handle_live_command({"op": "build_drum_kit", "kit": "BNYX", "track": "Kit"})
    assert result["outcome"]["code"] == "kit_rebuild_requires_consent"
