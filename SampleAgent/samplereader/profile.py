"""Turn many readings into one reference profile -- "what our own material is".

A profile is a median and an inter-quartile range per dimension. Median and IQR
rather than mean and stdev because one six-minute master among forty chops
would drag a mean and leave the profile describing nothing.

The profile refuses to exist on thin evidence. Everything downstream of it --
the whole idea of "in the vein of what we already do" -- is only as honest as
the number of files behind it.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from statistics import median

from .read import Reading, CHOP_BPM_MIN, CHOP_BPM_MAX

# Under this many readable files, no profile is produced.
MIN_PROFILE_FILES = 8
# A dimension needs its own coverage; tempo is often missing on one-shots.
MIN_DIMENSION_FILES = 5

# The dimensions that carry the sound's identity. Level is deliberately absent:
# gain is a mixing decision, not a characteristic of the source record.
TIMBRE_DIMENSIONS = (
    "centroid_hz",
    "rolloff85_hz",
    "bandwidth_hz",
    "low_ratio",
    "air_ratio",
    "harmonic_ratio",
    "noise_floor_dbfs",
    "stereo_width",
    "onset_rate_hz",
)


@dataclass(frozen=True)
class Band:
    dimension: str
    n: int
    median: float
    q1: float
    q3: float

    @property
    def iqr(self) -> float:
        return self.q3 - self.q1

    def as_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "n": self.n,
            "median": round(self.median, 4),
            "q1": round(self.q1, 4),
            "q3": round(self.q3, 4),
        }


def _quartiles(values: list[float]) -> tuple[float, float, float]:
    ordered = sorted(values)
    n = len(ordered)
    mid = median(ordered)
    lower = ordered[: n // 2]
    upper = ordered[(n + 1) // 2 :]
    return (median(lower) if lower else mid, mid, median(upper) if upper else mid)


def build_profile(readings: list[Reading], label: str) -> dict:
    usable = [r for r in readings if r.ok]
    if len(usable) < MIN_PROFILE_FILES:
        return {
            "label": label,
            "ok": False,
            "reason": f"thin_evidence({len(usable)}<{MIN_PROFILE_FILES})",
            "files_read": len(readings),
            "files_usable": len(usable),
        }

    bands: dict[str, dict] = {}
    for dim in TIMBRE_DIMENSIONS:
        values = [getattr(r, dim) for r in usable if getattr(r, dim) is not None]
        if len(values) < MIN_DIMENSION_FILES:
            continue
        q1, med, q3 = _quartiles([float(v) for v in values])
        bands[dim] = Band(dim, len(values), med, q1, q3).as_dict()

    chops = [r.chop_bpm for r in usable if r.chop_bpm is not None]
    tempo_block: dict = {"n": len(chops)}
    if len(chops) >= MIN_DIMENSION_FILES:
        q1, med, q3 = _quartiles([float(c) for c in chops])
        tempo_block.update(
            {
                "chop_bpm_median": round(med, 2),
                "chop_bpm_q1": round(q1, 2),
                "chop_bpm_q3": round(q3, 2),
                "in_window_share": round(
                    sum(1 for c in chops if CHOP_BPM_MIN <= c <= CHOP_BPM_MAX) / len(chops), 3
                ),
            }
        )
    else:
        tempo_block["reason"] = f"thin_evidence({len(chops)}<{MIN_DIMENSION_FILES})"

    keys: dict[str, int] = {}
    for r in usable:
        if r.key:
            keys[r.key] = keys.get(r.key, 0) + 1

    return {
        "label": label,
        "ok": True,
        "files_read": len(readings),
        "files_usable": len(usable),
        "chop_window": [CHOP_BPM_MIN, CHOP_BPM_MAX],
        "tempo": tempo_block,
        "keys": dict(sorted(keys.items(), key=lambda kv: -kv[1])),
        "bands": bands,
    }


def save_profile(profile: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_profile(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
