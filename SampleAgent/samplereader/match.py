"""Score one reading against a profile: is this record the continuation of ours?

Distance is measured in IQRs, not in raw units, so a dimension the producer is
loose about (say bandwidth spread over 2 kHz) does not outvote one they are
tight about. A dimension the candidate cannot report is skipped and counted --
a score built on three of nine dimensions is reported as such, never dressed up
as a full match.
"""
from __future__ import annotations

from dataclasses import dataclass

from .read import Reading, CHOP_BPM_MIN, CHOP_BPM_MAX

# Fewer dimensions than this in common and there is nothing to compare.
MIN_SHARED_DIMENSIONS = 5
# An IQR this small means the profile is nearly a point; widen it so a
# candidate is not punished for a rounding difference.
MIN_IQR_FRACTION = 0.05


@dataclass(frozen=True)
class Match:
    name: str
    path: str
    scored: bool
    reason: str | None
    distance: float | None
    dimensions_used: int
    dimensions_total: int
    tempo_gate: str
    chop_bpm: float | None
    worst: tuple[str, float] | None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "scored": self.scored,
            "reason": self.reason,
            "distance": None if self.distance is None else round(self.distance, 3),
            "dimensions_used": self.dimensions_used,
            "dimensions_total": self.dimensions_total,
            "tempo_gate": self.tempo_gate,
            "chop_bpm": self.chop_bpm,
            "worst_dimension": None if self.worst is None else self.worst[0],
            "worst_distance": None if self.worst is None else round(self.worst[1], 3),
        }


def _tempo_gate(reading: Reading) -> str:
    if reading.chop_bpm is None:
        return "unknown"
    return "in_window" if CHOP_BPM_MIN <= reading.chop_bpm <= CHOP_BPM_MAX else "out_of_window"


def score(reading: Reading, profile: dict) -> Match:
    bands = profile.get("bands", {})
    gate = _tempo_gate(reading)

    if not reading.ok:
        return Match(reading.name, reading.path, False, reading.error, None, 0,
                     len(bands), gate, reading.chop_bpm, None)
    if not profile.get("ok"):
        return Match(reading.name, reading.path, False, "profile_not_usable", None, 0,
                     len(bands), gate, reading.chop_bpm, None)

    per_dimension: list[tuple[str, float]] = []
    for dim, band in bands.items():
        value = getattr(reading, dim, None)
        if value is None:
            continue
        spread = float(band["q3"]) - float(band["q1"])
        floor = abs(float(band["median"])) * MIN_IQR_FRACTION
        spread = max(spread, floor, 1e-6)
        per_dimension.append((dim, abs(float(value) - float(band["median"])) / spread))

    if len(per_dimension) < MIN_SHARED_DIMENSIONS:
        return Match(reading.name, reading.path, False,
                     f"too_few_dimensions({len(per_dimension)})", None,
                     len(per_dimension), len(bands), gate, reading.chop_bpm, None)

    distance = sum(d for _, d in per_dimension) / len(per_dimension)
    worst = max(per_dimension, key=lambda kv: kv[1])
    return Match(reading.name, reading.path, True, None, distance, len(per_dimension),
                 len(bands), gate, reading.chop_bpm, worst)


def rank(readings: list[Reading], profile: dict, require_tempo_window: bool = True) -> list[Match]:
    matches = [score(r, profile) for r in readings]
    keep = [m for m in matches if m.scored]
    if require_tempo_window:
        keep = [m for m in keep if m.tempo_gate == "in_window"]
    keep.sort(key=lambda m: m.distance if m.distance is not None else float("inf"))
    return keep
