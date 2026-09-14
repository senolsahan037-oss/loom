"""Tests for band attribution.

Every signal here is synthesised and every .als is written by the test, so none
of this needs Ableton, the user's library, or any particular project on disk.

What these prove: the band measurement finds the band, emphasis separates a
narrow-band track from a merely loud one, gain and arrangement length weight the
result, and the .als reader resolves samples, tempo and faders. What they do not
prove: that the ranking names the instrument a listener would blame — the source
audio is measured without its device chain, which is why the result is labelled
a suspect list and carries `chain_alters_band`.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subverse_mix.band_attribution import (  # noqa: E402
    BAND_ALTERING_DEVICES,
    _band_power,
    attribute_band,
)

SR = 44100
BAND = (2227.0, 7072.0)


def _tone(freq: float, seconds: float = 2.0, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ---------------------------------------------------------------- band power


def test_band_power_finds_a_tone_inside_the_band():
    band, total = _band_power(_tone(4000.0), SR, *BAND)
    assert total > 0
    assert band / total > 0.95, "a 4 kHz tone should sit almost entirely in 2.2-7 kHz"


def test_band_power_rejects_a_tone_outside_the_band():
    band, total = _band_power(_tone(100.0), SR, *BAND)
    assert band / total < 0.05, "a 100 Hz tone should not register in 2.2-7 kHz"


def test_band_power_returns_zero_for_silence():
    band, total = _band_power(np.zeros(SR, dtype=np.float32), SR, *BAND)
    assert (band, total) == (0.0, 0.0)


def test_band_power_ignores_a_fragment_too_short_to_transform():
    band, total = _band_power(_tone(4000.0, seconds=0.01), SR, *BAND)
    assert (band, total) == (0.0, 0.0)


# ------------------------------------------------------------- .als fixtures


def _write_als(path: Path, tracks, tempo: float = 120.0) -> Path:
    """Writes a minimal but real .als: gzipped XML in Live's own shape."""
    blocks = []
    for name, sample, fader, clip_gain, beats, devices in tracks:
        device_xml = "".join(f"<{d} />" for d in devices)
        blocks.append(f"""
      <AudioTrack>
        <Name><EffectiveName Value="{name}" /></Name>
        <DeviceChain>
          <Mixer><Volume><Manual Value="{fader}" /></Volume></Mixer>
          <DeviceChain><Devices>{device_xml}</Devices></DeviceChain>
          <MainSequencer><ClipTimeable><ArrangerAutomation><Events>
            <AudioClip>
              <CurrentStart Value="0" />
              <CurrentEnd Value="{beats}" />
              <SampleVolume Value="{clip_gain}" />
              <IsWarped Value="true" />
              <Loop>
                <LoopStart Value="0" />
                <OutMarker Value="{beats}" />
              </Loop>
              <SampleRef><FileRef>
                <RelativePath Value="{sample}" />
                <Path Value="{sample}" />
              </FileRef></SampleRef>
            </AudioClip>
          </Events></ArrangerAutomation></ClipTimeable></MainSequencer>
        </DeviceChain>
      </AudioTrack>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton>
  <LiveSet>
    <Tracks>{''.join(blocks)}</Tracks>
    <MasterTrack><DeviceChain><Mixer>
      <Tempo><Manual Value="{tempo}" /></Tempo>
    </Mixer></DeviceChain></MasterTrack>
  </LiveSet>
</Ableton>"""
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(xml)
    return path


def _project(tmp_path: Path, tracks, tempo: float = 120.0) -> Path:
    for _, sample, *_ in tracks:
        if isinstance(sample, str) and not (tmp_path / sample).exists():
            raise AssertionError(f"test forgot to write {sample}")
    return _write_als(tmp_path / "set.als", tracks, tempo)


# --------------------------------------------------------------- attribution


def test_emphasis_separates_a_narrow_band_track_from_a_loud_one(tmp_path):
    """The point of the whole module.

    `loud_wide` is far louder and dominates the band in absolute terms, but its
    energy is spread across the spectrum. `quiet_bright` is small yet entirely
    inside the band. Share alone would name the loud track; emphasis must name
    the bright one.
    """
    rng = np.random.default_rng(7)
    wide = (rng.standard_normal(SR * 2) * 0.5).astype(np.float32)
    sf.write(tmp_path / "wide.wav", wide, SR)
    sf.write(tmp_path / "bright.wav", _tone(4000.0, amp=0.05), SR)

    als = _project(tmp_path, [
        ("loud_wide", "wide.wav", 1.0, 1.0, 8, []),
        ("quiet_bright", "bright.wav", 1.0, 1.0, 8, []),
    ])

    ranking = attribute_band(als, *BAND)["ranking"]
    by_name = {r["track"]: r for r in ranking}

    assert ranking[0]["track"] == "quiet_bright", (
        "emphasis must rank the narrow-band track first, not the loud one"
    )
    assert by_name["quiet_bright"]["broadband_share"] < by_name["loud_wide"]["broadband_share"]
    assert by_name["quiet_bright"]["emphasis"] > 0
    assert by_name["loud_wide"]["emphasis"] < 0


def test_fader_and_clip_gain_change_the_share(tmp_path):
    sf.write(tmp_path / "a.wav", _tone(4000.0), SR)
    sf.write(tmp_path / "b.wav", _tone(4000.0), SR)

    loud = _project(tmp_path, [
        ("a", "a.wav", 1.0, 1.0, 8, []),
        ("b", "b.wav", 0.25, 1.0, 8, []),
    ])
    shares = {r["track"]: r["band_share"] for r in attribute_band(loud, *BAND)["ranking"]}
    assert shares["a"] > shares["b"] * 3, "a 4x fader difference must move the share"


def test_a_clip_on_the_timeline_longer_counts_for_more(tmp_path):
    sf.write(tmp_path / "s.wav", _tone(4000.0), SR)
    sf.write(tmp_path / "l.wav", _tone(4000.0), SR)

    als = _project(tmp_path, [
        ("short", "s.wav", 1.0, 1.0, 4, []),
        ("long", "l.wav", 1.0, 1.0, 16, []),
    ])
    shares = {r["track"]: r["band_share"] for r in attribute_band(als, *BAND)["ranking"]}
    assert shares["long"] > shares["short"]


def test_a_band_altering_device_is_flagged(tmp_path):
    sf.write(tmp_path / "x.wav", _tone(4000.0), SR)
    sf.write(tmp_path / "y.wav", _tone(4000.0), SR)

    als = _project(tmp_path, [
        ("clean", "x.wav", 1.0, 1.0, 8, []),
        ("saturated", "y.wav", 1.0, 1.0, 8, ["Saturator"]),
    ])
    rows = {r["track"]: r for r in attribute_band(als, *BAND)["ranking"]}
    assert rows["saturated"]["chain_alters_band"] is True
    assert rows["clean"]["chain_alters_band"] is False
    assert "Saturator" in BAND_ALTERING_DEVICES


def test_a_missing_sample_is_reported_not_guessed(tmp_path):
    sf.write(tmp_path / "there.wav", _tone(4000.0), SR)
    als = _write_als(tmp_path / "set.als", [
        ("present", "there.wav", 1.0, 1.0, 8, []),
        ("absent", "gone.wav", 1.0, 1.0, 8, []),
    ])
    result = attribute_band(als, *BAND)
    absent = [r for r in result["ranking"] if r["track"] == "absent"]
    # A track whose audio cannot be read contributes no energy, so it does not
    # appear in the ranking at all — it is never given a guessed share.
    assert absent == []
    assert [r["track"] for r in result["ranking"]] == ["present"]


def test_tempo_is_read_from_the_set(tmp_path):
    sf.write(tmp_path / "t.wav", _tone(4000.0), SR)
    als = _project(tmp_path, [("t", "t.wav", 1.0, 1.0, 8, [])], tempo=174.0)
    assert attribute_band(als, *BAND)["tempo"] == 174.0


def test_an_inverted_band_is_refused(tmp_path):
    sf.write(tmp_path / "t.wav", _tone(4000.0), SR)
    als = _project(tmp_path, [("t", "t.wav", 1.0, 1.0, 8, [])])
    try:
        attribute_band(als, 7000.0, 2000.0)
    except ValueError:
        return
    raise AssertionError("a band whose high edge is below its low edge must raise")


def test_the_result_states_its_limitations(tmp_path):
    sf.write(tmp_path / "t.wav", _tone(4000.0), SR)
    als = _project(tmp_path, [("t", "t.wav", 1.0, 1.0, 8, [])])
    limitations = attribute_band(als, *BAND)["limitations"]
    joined = " ".join(limitations).lower()
    assert "device chain" in joined
    assert "does not prove" in joined
