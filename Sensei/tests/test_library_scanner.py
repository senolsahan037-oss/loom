from pathlib import Path

from ableton.library_scanner import _classify_item, scan_ableton_library


def test_classifies_suite_asset_types():
    assert _classify_item(Path("/library/Factory Packs/Synth Essentials/Sounds/Bass/Deep.adv"))["content_type"] == "instrument_preset"
    assert _classify_item(Path("/library/Factory Packs/Beat Tools/Grooves/Swing.agr"))["content_type"] == "groove"
    assert _classify_item(Path("/library/Factory Packs/Beat Tools/MIDI Files/Pattern.mid"))["content_type"] == "midi_reference"
    assert _classify_item(Path("/library/Factory Packs/Creative Extensions/Devices/Tool.amxd"))["content_type"] == "max_device"
    assert _classify_item(Path("/library/Factory Packs/Beat Tools/Samples/Loop.ogg"))["content_type"] == "sample"


def test_ignores_live_analysis_and_peak_cache_files(tmp_path):
    (tmp_path / "clip.asd").write_text("cache")
    (tmp_path / "clip.pkf").write_text("cache")
    (tmp_path / "pattern.mid").write_text("midi reference")

    library = scan_ableton_library([tmp_path])

    assert [item["name"] for item in library["items"]] == ["pattern"]
    assert len(library["midi_references"]) == 1
