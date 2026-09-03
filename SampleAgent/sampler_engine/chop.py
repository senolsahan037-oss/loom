"""Turn an analysis into slice boundaries. Four modes, all returning
a list of {'start', 'end'} in seconds, clamped to the audio duration."""

import numpy as np

MODES = ("transient", "bars", "fixed", "silence", "gaps", "leftover")


def _clamp(regions, duration, min_len):
    out = []
    for start, end in regions:
        start = max(0.0, float(start))
        end = min(float(duration), float(end))
        if end - start >= min_len:
            out.append({"start": round(start, 6), "end": round(end, 6)})
    return out


def transient(analysis, min_len=0.08, max_len=None, tail=0.0):
    """One slice per detected attack, running until the next kept attack.

    onset_detect with backtrack=True often fires twice on one hit; min_len
    doubles as the de-duplication distance.
    """
    onsets = list(analysis["onset_times"])
    duration = analysis["duration_s"]
    if not onsets:
        return []

    kept = [onsets[0]]
    for time in onsets[1:]:
        if time - kept[-1] >= min_len:
            kept.append(time)

    regions = []
    for index, start in enumerate(kept):
        end = kept[index + 1] if index + 1 < len(kept) else duration
        if max_len:
            end = min(end, start + max_len)
        regions.append((start, end + tail))
    return _clamp(regions, duration, min_len)


def bars(analysis, bars_per_slice=2, beats_per_bar=4, min_len=0.2):
    """Equal loops of N bars, cut on detected beats.

    Grouping starts at the first detected beat: librosa gives no downbeat, so
    a slice boundary is a beat boundary, not necessarily bar 1.
    """
    beat_times = list(analysis["beat_times"])
    duration = analysis["duration_s"]
    step = int(bars_per_slice * beats_per_bar)
    if len(beat_times) < step + 1:
        return []

    interval = analysis.get("median_beat_interval_s") or (
        (beat_times[-1] - beat_times[0]) / max(1, len(beat_times) - 1)
    )
    regions = []
    for index in range(0, len(beat_times) - 1, step):
        start = beat_times[index]
        end_index = index + step
        end = beat_times[end_index] if end_index < len(beat_times) else start + step * interval
        regions.append((start, end))
    return _clamp(regions, duration, min_len)


def fixed(analysis, seconds=2.0, min_len=None):
    duration = analysis["duration_s"]
    min_len = seconds * 0.5 if min_len is None else min_len
    starts = np.arange(0.0, duration, seconds)
    return _clamp([(s, s + seconds) for s in starts], duration, min_len)


def silence(analysis, min_len=0.3, pad=0.02):
    """Non-silent regions, as detected at analysis time (top_db=30)."""
    duration = analysis["duration_s"]
    regions = [(start - pad, end + pad) for start, end in analysis["loud_regions"]]
    return _clamp(regions, duration, min_len)


def leftover(analysis, exclude, min_len=0.08, max_len=None, tail=0.0):
    """Loop'a ayrilan yerlerin DISINDA kalan malzeme, transientlerden kesilir.

    Olculdu 2026-09-02 (Felekten Beter, 21 bolge): tasiyici 8 barlik loop'lar
    disinda kalan kisa bolgelerin hepsi loop alaninin disindan alinmis —
    bar 21, 22, 31, 60, 64, 256'ya karsi loop 109.8-120.5 arasinda.
    """
    duration = analysis["duration_s"]
    blocked = sorted((max(0.0, float(a)), min(duration, float(b))) for a, b in exclude)
    merged = []
    for a, b in blocked:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    spans, cursor = [], 0.0
    for a, b in merged:
        if a - cursor > min_len:
            spans.append((cursor, a))
        cursor = max(cursor, b)
    if duration - cursor > min_len:
        spans.append((cursor, duration))

    onsets = list(analysis["onset_times"])
    regions = []
    for lo, hi in spans:
        inside = [o for o in onsets if lo <= o < hi]
        if not inside:
            regions.append((lo, hi))
            continue
        kept = [inside[0]]
        for t in inside[1:]:
            if t - kept[-1] >= min_len:
                kept.append(t)
        for i, start in enumerate(kept):
            end = kept[i + 1] if i + 1 < len(kept) else hi
            if max_len:
                end = min(end, start + max_len)
            regions.append((start, min(end + tail, hi)))
    return _clamp(regions, duration, min_len)


def run(mode, analysis, params):
    if mode == "transient":
        return transient(
            analysis,
            min_len=params["min_len"],
            max_len=params["max_len"],
            tail=params["tail"],
        )
    if mode == "bars":
        return bars(
            analysis,
            bars_per_slice=params["bars"],
            beats_per_bar=params["beats_per_bar"],
        )
    if mode == "fixed":
        return fixed(analysis, seconds=params["seconds"])
    if mode == "leftover":
        return leftover(analysis, params.get("exclude") or [],
                        min_len=params["min_len"], max_len=params["max_len"],
                        tail=params["tail"])
    if mode == "gaps":
        # Bolgeler analiz asamasinda hesaplanip analysis'e konur.
        return [r for r in analysis.get("vocal_free_regions", [])
                if r["end"] - r["start"] >= params["min_len"]]
    if mode == "silence":
        return silence(analysis, min_len=params["min_len"], pad=params["tail"] or 0.02)
    raise ValueError(f"unknown mode: {mode}")
