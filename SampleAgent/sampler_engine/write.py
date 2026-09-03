"""Write slices to disk as WAV, plus the pack manifest."""

import json
import os

import numpy as np
import soundfile as sf

SUBTYPES = {16: "PCM_16", 24: "PCM_24", 32: "FLOAT"}
SILENT_FLOOR_DBFS = -60.0


def _fade(block, sr, fade_ms):
    n = int(sr * fade_ms / 1000.0)
    n = min(n, len(block) // 2)
    if n <= 1:
        return block
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    block[:n] *= ramp
    block[-n:] *= ramp[::-1]
    return block


def _timecode(seconds):
    minutes = int(seconds // 60)
    return f"{minutes:d}m{seconds - minutes * 60:05.2f}s"


def write_slices(audio, sr, regions, out_dir, mode, fade_ms=5.0,
                 normalize_db=None, bit_depth=24, max_slices=200):
    os.makedirs(out_dir, exist_ok=True)
    subtype = SUBTYPES[bit_depth]
    written, skipped_silent = [], 0

    for index, region in enumerate(regions[:max_slices], start=1):
        start = int(round(region["start"] * sr))
        end = min(len(audio), int(round(region["end"] * sr)))
        if end - start < 2:
            continue

        block = np.array(audio[start:end], dtype=np.float32, copy=True)
        peak = float(np.max(np.abs(block))) if block.size else 0.0
        peak_db = 20 * np.log10(peak) if peak > 0 else float("-inf")
        if peak_db < SILENT_FLOOR_DBFS:
            skipped_silent += 1
            continue

        block = _fade(block, sr, fade_ms)
        gain_db = 0.0
        if normalize_db is not None and peak > 0:
            target = 10 ** (normalize_db / 20.0)
            gain = target / peak
            block = np.clip(block * gain, -1.0, 1.0)
            gain_db = 20 * np.log10(gain)

        name = f"{index:03d}_{mode}_{_timecode(region['start'])}.wav"
        path = os.path.join(out_dir, name)
        sf.write(path, block, sr, subtype=subtype)

        written.append({
            "index": index,
            "file": os.path.join(mode, name),
            "start_s": round(region["start"], 6),
            "end_s": round(region["end"], 6),
            "length_s": round((end - start) / sr, 6),
            "peak_dbfs": round(peak_db, 2),
            "gain_applied_db": round(gain_db, 2),
        })

    return {
        "mode": mode,
        "regions_found": len(regions),
        "slices_written": len(written),
        "skipped_silent": skipped_silent,
        "truncated_at_max_slices": len(regions) > max_slices,
        "slices": written,
    }


def write_manifest(pack_dir, manifest):
    path = os.path.join(pack_dir, "manifest.json")
    with open(path, "w") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
    return path
