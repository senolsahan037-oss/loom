from pathlib import Path

import pytest

from ableton.inspector.alc_inspector import inspect_alc
from ableton.inspector.profile_exporter import extract_clip_boundaries
import xml.etree.ElementTree as ET


FIXTURES_DIR = Path(__file__).parent.parent / "ableton" / "fixtures"


def test_clip_boundaries_are_scoped_to_midi_clip():
    root = ET.fromstring(
        """
        <Ableton>
          <Unrelated><LoopStart Value="99"/><LoopEnd Value="100"/></Unrelated>
          <MidiClip>
            <Loop><LoopStart Value="2"/><LoopEnd Value="6"/></Loop>
            <CurrentStart Value="1"/><CurrentEnd Value="7"/>
          </MidiClip>
        </Ableton>
        """
    )

    boundaries = extract_clip_boundaries(root)

    assert boundaries == {
        "loop_start": 2.0,
        "loop_end": 6.0,
        "loop_length": 4.0,
        "start_marker": 1.0,
        "end_marker": 7.0,
        "source": "scoped_midi_clip",
    }


def test_inspect_alc_requires_existing_fixture():
    fixture = FIXTURES_DIR / "sample.alc"
    if not fixture.exists():
        pytest.skip("sample.alc fixture not added yet")

    result = inspect_alc(fixture)

    assert result["root_tag"]
    assert isinstance(result["tag_counts"], dict)


def test_inspect_kit_device_chains_integration():
    from ableton.inspector.profile_exporter import inspect_alc_embedded_kit, build_kit_profile

    # 1. Test preset profile exporter on swang_bap_kit.adg
    adg_fixture = FIXTURES_DIR / "swang_bap_kit.adg"
    if adg_fixture.exists():
        kit_profile = build_kit_profile(adg_fixture)
        assert "has_simpler" in kit_profile
        assert "has_sampler" in kit_profile
        assert "kit_write_safety" in kit_profile
        assert isinstance(kit_profile["device_chain_summary"], dict)

    # 2. Test embedded kit on two_worlds_clip.alc
    alc_fixture = FIXTURES_DIR / "two_worlds_clip.alc"
    if alc_fixture.exists():
        embedded_kit = inspect_alc_embedded_kit(alc_fixture)
        assert "has_simpler" in embedded_kit
        assert "has_sampler" in embedded_kit
        assert "kit_write_safety" in embedded_kit
        assert embedded_kit["chain_complexity"] in {"low", "medium", "high"}
        assert isinstance(embedded_kit["device_chain_summary"], dict)

