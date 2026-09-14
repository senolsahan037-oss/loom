"""Attributes a mixdown band finding to the tracks that actually feed that band.

Mix Check measures a stereo mixdown and says, correctly, that it cannot tell
which instrument causes a spectral difference. That sentence is where the tool
has always stopped: a measurement with no owner is not yet a decision, and
"2227-7072 Hz is 8.3 dB hot" cannot be acted on until someone knows which fader
to reach for.

This module closes that step without opening Live. Every audio track in an .als
points at real sample files on disk, and the set records which region of each
file plays, where it plays, its clip gain and its fader. That is enough to
measure each track's own contribution to a frequency band directly from the
source audio.

The number that matters is not a track's share of the band. A busy track is a
large share of everything, so share alone re-discovers the arrangement. What
identifies a culprit is EMPHASIS: the track's share of the band minus its share
of broadband energy. A track that is 5% of the mix and 30% of 2-7 kHz is the
one pushing that band; a track that is 30% of both is simply loud.

The same correction was needed in the sampler's drum classifier, where raw band
share made everything a snare because the snare band is the widest.

WHAT THIS DOES NOT ACCOUNT FOR, and it is not a small caveat:

  - Device chains. The measurement is of the SOURCE audio. A track whose chain
    carries a Saturator or an EQ will reach the master sounding different from
    what is measured here. Such tracks are flagged `chain_alters_band` and their
    numbers are a floor, not a verdict.
  - Sends and returns. Reverb and delay returns add band energy that belongs to
    no single track.
  - Warp. Beats are converted to seconds at the project tempo, which is exact
    only for a constant-tempo warp. Clips are flagged when the mapped region
    runs past the end of the file.
  - Automation. A fader that moves is read at its stored value.

So this ranks suspects and says why. It does not prove a cause, and nothing
here should be applied to a mix without listening.
"""

from __future__ import annotations

import gzip
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import soundfile as sf
except ImportError as exc:  # pragma: no cover - environment contract
    raise ImportError(
        "band_attribution needs soundfile to read the project's own samples"
    ) from exc


# Devices that demonstrably move energy inside an audio band. A track carrying
# one of these is measured at its source, so its post-chain contribution to the
# band is not what this module reports.
BAND_ALTERING_DEVICES = frozenset({
    "Saturator", "Eq8", "FilterEQ3", "AutoFilter", "Overdrive", "Amp",
    "Cabinet", "DrumBuss", "Vinyl", "Erosion", "Redux", "Dynamic Tube",
    "MultibandDynamics", "Corpus", "Resonator", "FrequencyShifter",
    "MxDeviceAudioEffect",
})

# A clip quieter than this contributes nothing worth ranking.
SILENCE_FLOOR_DB = -60.0


@dataclass
class ClipPlacement:
    """One clip's use of one sample file."""

    path: Path
    arrangement_beats: float
    region_start_s: float
    region_end_s: float
    clip_gain: float
    warped: bool
    region_clamped: bool = False


@dataclass
class TrackContribution:
    name: str
    fader: float
    devices: List[str] = field(default_factory=list)
    clips: List[ClipPlacement] = field(default_factory=list)
    band_energy: float = 0.0
    total_energy: float = 0.0
    missing_files: List[str] = field(default_factory=list)
    unreadable_files: List[str] = field(default_factory=list)

    @property
    def chain_alters_band(self) -> bool:
        return any(d in BAND_ALTERING_DEVICES for d in self.devices)


def _attr(node: Optional[ET.Element], default: Optional[str] = None) -> Optional[str]:
    return node.get("Value") if node is not None else default


def _float(node: Optional[ET.Element], default: float) -> float:
    raw = _attr(node)
    try:
        return float(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _project_tempo(root: ET.Element) -> float:
    """The set's own tempo. Beats are the .als time unit, not bars."""
    for path in (
        ".//MasterTrack/DeviceChain/Mixer/Tempo/Manual",
        ".//MasterTrack//Tempo/Manual",
        ".//Tempo/Manual",
    ):
        node = root.find(path)
        if node is not None:
            tempo = _float(node, 0.0)
            if tempo > 0:
                return tempo
    raise ValueError("No tempo found in the set; beats cannot be mapped to seconds.")


def _sample_path(clip: ET.Element, project_dir: Path) -> Optional[Path]:
    ref = clip.find(".//SampleRef/FileRef")
    if ref is None:
        return None

    relative = _attr(ref.find(".//RelativePath"))
    if relative:
        candidate = (project_dir / relative).resolve()
        if candidate.exists():
            return candidate

    absolute = _attr(ref.find(".//Path"))
    if absolute:
        candidate = Path(absolute)
        if candidate.exists():
            return candidate
        # The set was moved: fall back to the filename inside this project.
        matches = list(project_dir.rglob(candidate.name))
        if len(matches) == 1:
            return matches[0]

    return Path(absolute) if absolute else None


def _clip_placement(
    clip: ET.Element, project_dir: Path, tempo: float
) -> Optional[ClipPlacement]:
    path = _sample_path(clip, project_dir)
    if path is None:
        return None

    start_beat = _float(clip.find("CurrentStart"), 0.0)
    end_beat = _float(clip.find("CurrentEnd"), 0.0)
    arrangement_beats = max(0.0, end_beat - start_beat)
    if arrangement_beats <= 0:
        return None

    loop = clip.find("Loop")
    region_start_beats = _float(loop.find("LoopStart") if loop is not None else None, 0.0)
    region_end_beats = _float(loop.find("OutMarker") if loop is not None else None, 0.0)
    if region_end_beats <= region_start_beats:
        region_end_beats = region_start_beats + arrangement_beats

    seconds_per_beat = 60.0 / tempo
    return ClipPlacement(
        path=path,
        arrangement_beats=arrangement_beats,
        region_start_s=region_start_beats * seconds_per_beat,
        region_end_s=region_end_beats * seconds_per_beat,
        clip_gain=_float(clip.find("SampleVolume"), 1.0),
        warped=_attr(clip.find("IsWarped")) == "true",
    )


def _band_power(
    samples: np.ndarray, sample_rate: int, low_hz: float, high_hz: float
) -> Tuple[float, float]:
    """Returns (power inside the band, total power), both linear."""
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if samples.size < 2048:
        return 0.0, 0.0

    # One FFT over the region. The region is a few seconds at most, and the
    # question is how much energy sits in the band on average, not where.
    window = np.hanning(samples.size)
    spectrum = np.abs(np.fft.rfft(samples * window)) ** 2
    freqs = np.fft.rfftfreq(samples.size, 1.0 / sample_rate)

    total = float(spectrum.sum())
    if total <= 0:
        return 0.0, 0.0

    in_band = (freqs >= low_hz) & (freqs <= high_hz)
    return float(spectrum[in_band].sum()), total


def _read_region(
    path: Path, start_s: float, end_s: float
) -> Tuple[Optional[np.ndarray], int, bool]:
    """Reads the played region. Returns (samples, sample_rate, clamped)."""
    with sf.SoundFile(str(path)) as handle:
        sample_rate = handle.samplerate
        length_s = len(handle) / sample_rate
        clamped = end_s > length_s + 1e-6
        start_s = max(0.0, min(start_s, length_s))
        end_s = max(start_s, min(end_s, length_s))
        if end_s - start_s < 0.05:
            return None, sample_rate, clamped
        handle.seek(int(start_s * sample_rate))
        frames = int((end_s - start_s) * sample_rate)
        return handle.read(frames, dtype="float32", always_2d=False), sample_rate, clamped


def _track_name(track: ET.Element) -> str:
    return _attr(track.find(".//Name/EffectiveName"), "(unnamed)") or "(unnamed)"


def _track_devices(track: ET.Element) -> List[str]:
    return [d.tag for d in track.findall(".//DeviceChain/DeviceChain/Devices/")]


def attribute_band(
    als_path: str | Path,
    low_hz: float,
    high_hz: float,
    *,
    cache: Optional[Dict[Tuple[str, float, float], Tuple[float, float]]] = None,
) -> Dict[str, Any]:
    """Ranks the .als audio tracks by how much they emphasise one band.

    `low_hz`/`high_hz` come straight from a Mix Check finding's evidence block.
    """
    if high_hz <= low_hz:
        raise ValueError("high_hz must be above low_hz")

    als_path = Path(als_path)
    project_dir = als_path.parent
    with gzip.open(als_path) as handle:
        root = ET.parse(handle).getroot()

    tempo = _project_tempo(root)
    cache = {} if cache is None else cache

    contributions: List[TrackContribution] = []
    for track in root.findall(".//LiveSet/Tracks/AudioTrack"):
        contribution = TrackContribution(
            name=_track_name(track),
            fader=_float(track.find(".//DeviceChain/Mixer/Volume/Manual"), 1.0),
            devices=_track_devices(track),
        )

        for clip in track.findall(".//AudioClip"):
            placement = _clip_placement(clip, project_dir, tempo)
            if placement is None:
                continue
            contribution.clips.append(placement)

            if not placement.path.exists():
                contribution.missing_files.append(placement.path.name)
                continue

            key = (str(placement.path), placement.region_start_s, placement.region_end_s)
            if key in cache:
                band, total = cache[key]
            else:
                try:
                    samples, sample_rate, clamped = _read_region(
                        placement.path, placement.region_start_s, placement.region_end_s
                    )
                except Exception:
                    contribution.unreadable_files.append(placement.path.name)
                    continue
                placement.region_clamped = clamped
                if samples is None:
                    continue
                band, total = _band_power(samples, sample_rate, low_hz, high_hz)
                cache[key] = (band, total)

            # Energy reaching the master: source power, scaled by clip gain and
            # fader (both linear amplitudes, so squared for power), weighted by
            # how long the clip is actually on the timeline.
            gain_power = (placement.clip_gain * contribution.fader) ** 2
            weight = gain_power * placement.arrangement_beats
            contribution.band_energy += band * weight
            contribution.total_energy += total * weight

        if contribution.clips:
            contributions.append(contribution)

    band_sum = sum(c.band_energy for c in contributions)
    total_sum = sum(c.total_energy for c in contributions)

    rows: List[Dict[str, Any]] = []
    for c in contributions:
        if c.total_energy <= 0:
            continue
        band_share = c.band_energy / band_sum if band_sum > 0 else 0.0
        total_share = c.total_energy / total_sum if total_sum > 0 else 0.0
        in_track_db = (
            10 * math.log10(c.band_energy / c.total_energy)
            if c.band_energy > 0
            else SILENCE_FLOOR_DB
        )
        rows.append({
            "track": c.name,
            "band_share": round(band_share, 4),
            "broadband_share": round(total_share, 4),
            "emphasis": round(band_share - total_share, 4),
            "band_ratio_db": round(in_track_db, 2),
            "fader": round(c.fader, 4),
            "clips": len(c.clips),
            "devices": c.devices,
            "chain_alters_band": c.chain_alters_band,
            "missing_files": sorted(set(c.missing_files)),
            "unreadable_files": sorted(set(c.unreadable_files)),
            "region_clamped": any(p.region_clamped for p in c.clips),
        })

    rows.sort(key=lambda r: r["emphasis"], reverse=True)

    return {
        "als": str(als_path),
        "tempo": tempo,
        "band_hz": [low_hz, high_hz],
        "tracks_measured": len(rows),
        "audio_tracks_in_set": len(root.findall(".//LiveSet/Tracks/AudioTrack")),
        "ranking": rows,
        "limitations": [
            "Source audio is measured; device chains are not applied. A track "
            "flagged chain_alters_band reaches the master sounding different "
            "from what is measured here.",
            "Return tracks (reverb, delay) add band energy that belongs to no "
            "single track and are not ranked.",
            "Beats map to seconds at the project tempo, exact only for a "
            "constant-tempo warp; a clip whose region ran past the end of its "
            "file is flagged region_clamped.",
            "Automated faders are read at their stored value.",
            "This ranks suspects. It does not prove a cause; verify by "
            "soloing the named track and listening.",
        ],
    }
