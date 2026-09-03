"""The crate agent: SubverseLab's sample-reader and Sampler under one roof.

Ported into Loom on 2026-09-03 so the MCP can run the whole chain that used
to be three CLIs: fetch a source (YouTube or file), read the audio itself
(tempo, key, level, quality -- the reader never guesses and says why when it
cannot answer), rank the chop spots, choose a chop mode from that evidence,
slice, and write a pack whose manifest carries every measurement and reason.

`samplereader/` and `sampler_engine/` are the Launchpad tools' own code,
unchanged; this module only orchestrates. Heavy dependencies (librosa,
soundfile, yt-dlp, ffmpeg) are imported when a function runs, not at import.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

AGENT_VERSION = "crate-agent/0.1.0"
LOOM_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PACK_ROOT = LOOM_DIR / "Sessions" / "SamplePacks"
DEFAULT_WORK_ROOT = LOOM_DIR / "Sessions" / "SamplePacks" / "_work"
MODES = ("transient", "bars", "fixed", "silence", "leftover")
STRIPPED_ANALYSIS_KEYS = ("beat_times", "onset_times", "loud_regions")


def _engine():
    from sampler_engine import analyze as analyze_mod  # noqa: WPS433
    from sampler_engine import chop as chop_mod
    from sampler_engine import compat, fetch, write as write_mod
    return fetch, analyze_mod, chop_mod, write_mod, compat


def _reader():
    from samplereader import read_file  # noqa: WPS433
    from samplereader import spots as spots_mod
    from samplereader import loops
    return read_file, spots_mod, loops


def _resolve_modes(modes) -> list[str]:
    if not modes:
        return ["transient"]
    if isinstance(modes, str):
        modes = [m.strip() for m in modes.split(",") if m.strip()]
    if "all" in modes:
        return list(MODES)
    unknown = [m for m in modes if m not in MODES]
    if unknown:
        raise ValueError(f"unknown chop mode(s) {unknown}; known: {list(MODES) + ['all']}")
    return list(dict.fromkeys(modes))


def _timestamp(value):
    fetch, *_ = _engine()
    return None if value in (None, "") else fetch.parse_timestamp(str(value))


# --- stages -----------------------------------------------------------------

def fetch(source: str, start=None, end=None, workdir: str | None = None) -> dict[str, Any]:
    """URL or file -> decoded 44.1 kHz stereo WAV kept in `workdir`."""
    fetch_mod, *_ = _engine()
    start_s, end_s = _timestamp(start), _timestamp(end)
    if start_s is not None and end_s is not None and end_s <= start_s:
        raise ValueError("end must be later than start")
    root = Path(workdir).expanduser() if workdir else DEFAULT_WORK_ROOT
    root.mkdir(parents=True, exist_ok=True)
    job = Path(tempfile.mkdtemp(prefix="crate_", dir=str(root)))
    resolved = fetch_mod.resolve(source, str(job), start=start_s, end=end_s, quiet=True)
    return {
        "wav": resolved["wav"],
        "workdir": str(job),
        "origin": resolved["origin"],
        "meta": resolved["meta"],
        "data_source": "measured:this_run",
    }


def read(path: str) -> dict[str, Any]:
    """The reader's measurement of one file, as a dict, refusals included."""
    read_file, _spots, _loops = _reader()
    target = Path(path).expanduser()
    if not target.is_file():
        raise FileNotFoundError(f"no audio file at {target}")
    reading = read_file(target)
    result = reading.as_dict() if hasattr(reading, "as_dict") else dict(reading.__dict__)
    result["engine"] = "subverselab-sample-reader"
    return result


def spots(path: str, top: int = 6, video_id: str | None = None) -> dict[str, Any]:
    """Chop candidates inside a longer recording, with the grid they rest on."""
    _read, spots_mod, loops = _reader()
    target = Path(path).expanduser()
    if not target.is_file():
        raise FileNotFoundError(f"no audio file at {target}")
    found, grid = spots_mod.find_and_grid(str(target))
    found = found[: max(1, int(top))]
    result: dict[str, Any] = {
        "path": str(target),
        "spots": [s.as_dict() for s in found],
        "grid": grid,
        "count": len(found),
        "engine": "subverselab-sample-reader",
    }
    if video_id:
        result["watch_urls"] = loops.watch_urls(video_id, found)
    if not found:
        result["reason"] = "no candidate: the file is too short or has no windows above the silence floor"
    return result


def chop(path: str, modes=None, out_dir: str | None = None, name: str | None = None,
         bpm: float | None = None, grid_offset=None, bars: int = 2, beats_per_bar: int = 4,
         seconds: float = 2.0, min_len: float = 0.08, max_len: float | None = None, tail: float = 0.0,
         top_db: float = 30.0, fade_ms: float = 5.0, normalize_dbfs: float | None = None,
         bit_depth: int = 24, max_slices: int = 200, keep_source: bool = True,
         source_meta: dict[str, Any] | None = None, exclude=None, extra_manifest: dict[str, Any] | None = None,
         **_ignored) -> dict[str, Any]:
    """Slice one decoded file into a pack, exactly as the Sampler CLI does."""
    fetch_mod, analyze_mod, chop_mod, write_mod, compat = _engine()
    src = Path(path).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"no audio file at {src}")
    mode_list = _resolve_modes(modes)
    if bit_depth not in (16, 24, 32):
        raise ValueError("bit_depth must be 16, 24 or 32")

    analysis = analyze_mod.analyze(str(src), top_db=float(top_db))
    if bpm:
        analysis = analyze_mod.apply_bpm_override(analysis, float(bpm), _timestamp(grid_offset))

    meta = dict(source_meta or {})
    title = meta.get("title") or src.stem
    slug = name or fetch_mod.slugify(title)
    if meta.get("video_id") and not name:
        slug = f"{slug}_{meta['video_id']}"
    pack_dir = Path(out_dir).expanduser() if out_dir else DEFAULT_PACK_ROOT
    pack_dir = pack_dir / slug
    pack_dir.mkdir(parents=True, exist_ok=True)

    audio, sr = analyze_mod.load_stereo(str(src))
    if keep_source:
        shutil.copy2(src, pack_dir / "_source.wav")

    params = {"min_len": min_len, "max_len": max_len, "tail": tail, "bars": bars,
              "beats_per_bar": beats_per_bar, "seconds": seconds, "top_db": top_db,
              "bpm_override": bpm, "exclude": list(exclude or [])}
    results, total = [], 0
    for mode in mode_list:
        regions = chop_mod.run(mode, analysis, params)
        written = write_mod.write_slices(audio, sr, regions, str(pack_dir / mode), mode,
                                         fade_ms=fade_ms, normalize_db=normalize_dbfs,
                                         bit_depth=bit_depth, max_slices=max_slices)
        results.append(written)
        total += written["slices_written"]

    manifest = {
        "tool": "subverselab-sampler",
        "agent": AGENT_VERSION,
        "version": "0.1.0",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": {**meta, "origin": meta.get("origin", "local_file"), "path": str(src)},
        "audio": {"sample_rate": sr, "channels": int(audio.shape[1]), "bit_depth_written": bit_depth},
        "analysis": {k: v for k, v in analysis.items() if k not in STRIPPED_ANALYSIS_KEYS},
        "chop_params": params,
        "render": {"fade_ms": fade_ms, "normalize_dbfs": normalize_dbfs, "max_slices": max_slices},
        "modes": results,
        "env": {"shims": compat.SHIMS_APPLIED},
        "data_source": "measured:this_run",
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    write_mod.write_manifest(str(pack_dir), manifest)
    return {
        "pack_dir": str(pack_dir),
        "manifest": str(pack_dir / "manifest.json"),
        "modes": [{"mode": r.get("mode", m), "slices_written": r["slices_written"],
                   "regions_found": r["regions_found"], "truncated": r.get("truncated_at_max_slices")}
                  for m, r in zip(mode_list, results)],
        "slices_total": total,
        "analysis": manifest["analysis"],
        "engine": "subverselab-sampler",
    }


# --- the trigger ------------------------------------------------------------

def _choose_modes(reading: dict[str, Any], bpm_override: float | None) -> tuple[list[str], float | None, str]:
    """Which chop mode the evidence supports, and why."""
    if bpm_override:
        return ["bars", "transient"], float(bpm_override), f"bars on the given {bpm_override:g} BPM grid, transients alongside"
    tempo = reading.get("tempo_bpm")
    if tempo and reading.get("in_chop_range"):
        return ["bars", "transient"], float(tempo), (
            f"bars: the reader measured {tempo:g} BPM ({reading.get('tempo_source')}), "
            f"folded to {reading.get('chop_bpm')} inside the chop range")
    if tempo:
        return ["bars", "transient"], float(tempo), (
            f"bars: the reader measured {tempo:g} BPM ({reading.get('tempo_source')}) but its fold "
            f"{reading.get('chop_bpm')} is outside the chop range; transients alongside")
    return ["transient"], None, (
        f"transients only: the reader gave no tempo ({reading.get('tempo_reason') or 'no reason recorded'})")


def _grid_offset_from(grid: dict[str, Any] | None):
    if not isinstance(grid, dict):
        return None
    for key in ("first_beat", "offset_s", "first_downbeat_s", "downbeat_s", "grid_start_s", "start_s"):
        value = grid.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def run(source: str, start=None, end=None, out_dir: str | None = None, name: str | None = None,
        modes=None, bpm: float | None = None, top_spots: int = 6, dry_run: bool = True,
        **_ignored) -> dict[str, Any]:
    """Fetch -> read -> spots -> choose -> chop, with every reason on record."""
    fetched = fetch(source, start=start, end=end)
    try:
        reading = read(fetched["wav"])
        found = spots(fetched["wav"], top=top_spots, video_id=(fetched["meta"] or {}).get("video_id"))
        if modes:
            mode_list, grid_bpm, reason = _resolve_modes(modes), (float(bpm) if bpm else None), "modes given by the caller"
        else:
            mode_list, grid_bpm, reason = _choose_modes(reading, bpm)
        grid_offset = _grid_offset_from(found.get("grid")) if grid_bpm else None
        plan = {
            "modes": mode_list,
            "bpm": grid_bpm,
            "grid_offset_s": grid_offset,
            "reason": reason,
            "reading": {k: reading.get(k) for k in ("ok", "error", "duration_s", "tempo_bpm", "tempo_source",
                                                     "tempo_reason", "chop_bpm", "in_chop_range", "key",
                                                     "key_confidence", "key_reason", "peak_dbfs", "rms_dbfs",
                                                     "noise_floor_dbfs", "harmonic_ratio", "onset_rate_hz")},
            "spots": found.get("spots", []),
            "watch_urls": found.get("watch_urls"),
        }
        result: dict[str, Any] = {
            "agent": AGENT_VERSION,
            "dry_run": dry_run,
            "source": {**(fetched["meta"] or {}), "origin": fetched["origin"]},
            "wav": fetched["wav"],
            "plan": plan,
        }
        if dry_run:
            if fetched["origin"] == "local_file":
                # A local file costs nothing to decode again; keep no copies.
                result.pop("wav", None)
                result["note"] = "Nothing was sliced. Call again with dry_run=false to write the pack."
            else:
                result["note"] = ("Nothing was sliced. The decoded download is kept at 'wav' so the real run "
                                  "can pass it as a local source without downloading again.")
            return result
        pack = chop(fetched["wav"], modes=mode_list, out_dir=out_dir, name=name, bpm=grid_bpm,
                    grid_offset=grid_offset, source_meta={**(fetched["meta"] or {}), "origin": fetched["origin"]},
                    extra_manifest={"reader": reading, "spots": found.get("spots", []), "grid": found.get("grid"),
                                    "choice": {"modes": mode_list, "bpm": grid_bpm, "reason": reason}})
        result["pack"] = pack
        return result
    finally:
        if not dry_run or fetched["origin"] == "local_file":
            shutil.rmtree(fetched["workdir"], ignore_errors=True)
