"""The crate agent on synthetic audio: no network, no YouTube, no Live.

A click track at a known tempo stands in for a record. What is proven: the
reader's dict reaches the agent unchanged, spots come back ranked with
reasons, chop writes real slices and a manifest that records how, and the
trigger's dry run plans without writing while the real run writes a pack
whose manifest carries the reading, the spots and the choice.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crate_agent  # noqa: E402

SR = 44_100


def _click_track(path: Path, bpm: float = 120.0, bars: int = 8, beats_per_bar: int = 4) -> None:
    beat = 60.0 / bpm
    total = int(SR * beat * beats_per_bar * bars)
    rng = np.random.default_rng(7)
    signal = np.zeros(total, dtype=np.float32)
    # A sustained two-note pad keeps every window above the reader's silence
    # floor and gives the spot finder harmonic content; the clicks carry the grid.
    t_axis = np.arange(total) / SR
    signal += 0.18 * np.sin(2 * np.pi * 110.0 * t_axis) + 0.12 * np.sin(2 * np.pi * 165.0 * t_axis)
    for index in range(bars * beats_per_bar):
        start = int(index * beat * SR)
        length = int(0.06 * SR)
        env = np.exp(-np.linspace(0, 6, length))
        tone = np.sin(2 * np.pi * (110.0 if index % beats_per_bar == 0 else 220.0) * np.arange(length) / SR)
        signal[start:start + length] += 0.6 * env * tone
    signal += 0.005 * rng.standard_normal(total).astype(np.float32)
    sf.write(path, np.column_stack((signal, signal * 0.9)), SR, subtype="FLOAT")


def test_read_returns_the_readers_dict_with_refusal_fields(tmp_path: Path) -> None:
    wav = tmp_path / "click.wav"
    _click_track(wav)
    reading = crate_agent.read(str(wav))
    assert reading["ok"] is True and reading["duration_s"] > 15
    assert "tempo_bpm" in reading and "tempo_reason" in reading and "key_reason" in reading
    assert reading["engine"] == "subverselab-sample-reader"


def test_spots_refuse_a_short_file_with_a_reason(tmp_path: Path) -> None:
    wav = tmp_path / "short.wav"
    _click_track(wav, bars=4)  # 8 s: under the finder's 20 s floor
    found = crate_agent.spots(str(wav), top=3)
    assert found["count"] == 0 and "too short" in found["reason"]


def test_spots_rank_windows_with_reasons(tmp_path: Path) -> None:
    wav = tmp_path / "click.wav"
    _click_track(wav, bars=16)  # 32 s: enough tracked beats for the window finder
    found = crate_agent.spots(str(wav), top=3, video_id="abc123")
    assert found["count"] >= 1 and found["count"] <= 3
    first = found["spots"][0]
    assert {"start_s", "end_s", "score", "reason"} <= set(first)
    assert found["watch_urls"] and "abc123" in found["watch_urls"][0]


def test_chop_writes_slices_and_a_manifest_that_says_how(tmp_path: Path) -> None:
    wav = tmp_path / "click.wav"
    _click_track(wav)
    pack = crate_agent.chop(str(wav), modes=["fixed", "transient"], out_dir=str(tmp_path / "packs"),
                            name="click", seconds=2.0, keep_source=False)
    pack_dir = Path(pack["pack_dir"])
    assert pack_dir.name == "click" and pack["slices_total"] > 0
    assert sorted(m["mode"] for m in pack["modes"]) == ["fixed", "transient"]
    assert len(list((pack_dir / "fixed").glob("*.wav"))) == next(m["slices_written"] for m in pack["modes"] if m["mode"] == "fixed")
    manifest = json.loads((pack_dir / "manifest.json").read_text())
    assert manifest["data_source"] == "measured:this_run" and manifest["chop_params"]["seconds"] == 2.0
    assert not (pack_dir / "_source.wav").exists()


def test_chop_with_a_given_bpm_writes_that_grid(tmp_path: Path) -> None:
    wav = tmp_path / "click.wav"
    _click_track(wav, bpm=120.0)
    pack = crate_agent.chop(str(wav), modes=["bars"], out_dir=str(tmp_path / "packs"), name="grid",
                            bpm=120.0, bars=2, keep_source=False)
    manifest = json.loads(Path(pack["manifest"]).read_text())
    assert manifest["chop_params"]["bpm_override"] == 120.0
    assert manifest["analysis"]["bpm"] == 120.0
    # 8 bars at 2 bars per slice -> 4 slices, allowing one lost to the grid edge
    bars_mode = next(m for m in pack["modes"] if m["mode"] == "bars")
    assert 3 <= bars_mode["slices_written"] <= 4


def test_unknown_mode_is_refused(tmp_path: Path) -> None:
    wav = tmp_path / "click.wav"
    _click_track(wav, bars=2)
    try:
        crate_agent.chop(str(wav), modes=["magic"], out_dir=str(tmp_path / "packs"))
        assert False, "did not refuse"
    except ValueError as error:
        assert "unknown chop mode" in str(error)


import shutil

import pytest


@pytest.mark.skipif(shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None,
                    reason="the fetch stage decodes through ffmpeg/ffprobe, which this machine lacks")
def test_run_dry_plans_without_writing_and_real_run_writes_with_reasons(tmp_path: Path) -> None:
    wav = tmp_path / "click.wav"
    _click_track(wav)
    work = tmp_path / "work"
    crate_agent.DEFAULT_WORK_ROOT = work
    plan = crate_agent.run(str(wav), out_dir=str(tmp_path / "packs"), name="agentpack", dry_run=True)
    assert plan["dry_run"] is True and "pack" not in plan
    assert plan["plan"]["modes"] and plan["plan"]["reason"]
    assert plan["source"]["origin"] == "local_file"
    assert not (tmp_path / "packs").exists()

    real = crate_agent.run(str(wav), out_dir=str(tmp_path / "packs"), name="agentpack", dry_run=False, bpm=120.0)
    pack_dir = Path(real["pack"]["pack_dir"])
    assert pack_dir.name == "agentpack" and real["pack"]["slices_total"] > 0
    manifest = json.loads((pack_dir / "manifest.json").read_text())
    assert manifest["choice"]["bpm"] == 120.0 and "bars" in manifest["choice"]["modes"]
    assert manifest["reader"]["ok"] is True and isinstance(manifest["spots"], list)
    assert "given" in manifest["choice"]["reason"]
    assert not any(p.is_dir() for p in work.glob("crate_*")), "work directory was not cleaned up"
