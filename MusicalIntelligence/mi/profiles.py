"""Per-layer access to the measured evidence.

One rule: a layer only ever sees its own evidence. Scoring a bass line against a
kick pattern because both are "rhythm" mixes two things that were measured
separately and answers neither question. So the drum writer gets drum patterns,
the bass writer gets bass behaviour, the chord writer gets harmony, and
ArrangementGPS -- which builds the project rather than writing notes -- gets the
song-level maps.

Where a layer has no measurable fit, this says so instead of inventing a score.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "data" / "corpus"
FILES = {
    "drum": CORPUS / "drum_patterns.json",
    "bass": CORPUS / "pop_bass.json",
    "chord": CORPUS / "pop_harmony.json",
    "arrangement": CORPUS / "pop_songmaps.json",
}

# Genre names Sensei may be handed, mapped onto styles the drum corpus measured.
# A style that was never measured maps to None and is refused, not approximated.
STYLE_ALIASES = {
    "trap": "hiphop", "hip hop": "hiphop", "hip-hop": "hiphop", "rap": "hiphop",
    "boom bap": "hiphop", "r&b": "soul", "rnb": "soul",
    # House and techno share the measured dance pattern: kick on every beat,
    # open hat on every offbeat. Drum and bass and dubstep do NOT -- one is a
    # breakbeat at double tempo, the other is halftime -- so they stay refused
    # rather than handed a four-on-the-floor that would be wrong.
    "house": "dance", "techno": "dance", "electro": "dance", "disco": "dance",
    "edm": "dance", "club": "dance",
    "drum and bass": None, "dnb": None, "jungle": None,
    "ambient": None, "dubstep": None, "trance": None,
}


@lru_cache(maxsize=8)
def _load(layer: str) -> dict:
    path = FILES.get(layer)
    return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}


def available() -> dict[str, bool]:
    return {layer: path.exists() for layer, path in FILES.items()}


# ---------------------------------------------------------------- drum layer
def normalise_style(name: str) -> str | None:
    key = (name or "").strip().lower()
    if key in STYLE_ALIASES:
        return STYLE_ALIASES[key]
    return key if key in (_load("drum").get("styles") or {}) else None


def drum_evidence(style: str) -> dict | None:
    resolved = normalise_style(style)
    if resolved is None:
        return None
    profile = (_load("drum").get("styles") or {}).get(resolved)
    if not profile:
        return None
    return {**profile, "style": resolved, "source": _load("drum").get("source")}


def known_styles() -> list[str]:
    return sorted((_load("drum").get("styles") or {}).keys())


def drum_fit(positions: list[float], style: str, part: str = "kick") -> float | None:
    """How well a drum candidate's onsets sit where this style actually hits.

    1.0 means every hit is on the style's strongest position; near zero means it
    plays where the style never does.
    """
    profile = drum_evidence(style)
    if not profile or part not in profile.get("parts", {}):
        return None
    weights = profile["parts"][part]["positions"]
    peak = max(weights)
    if peak <= 0 or not positions:
        return None
    grid = profile.get("grid", 16)
    total = sum(weights[int(round(beat * (grid / 4))) % grid] for beat in positions)
    return round(total / len(positions) / peak, 4)


# ---------------------------------------------------------------- bass layer
def bass_evidence() -> dict | None:
    data = _load("bass")
    if not data:
        return None
    return {
        "source": data.get("source"),
        "against_chord": data.get("bass_against_chord"),
        "motion_between_chords": data.get("motion_between_chords"),
        "moves_within_one_chord": data.get("moves_within_one_chord"),
        "role_by_degree": data.get("bass_role_by_degree"),
    }


def bass_fit(pitches: list[int], bars: float) -> float | None:
    """How much a bass candidate behaves like the measured bass.

    Measured: the bass holds one note under a chord 89% of the time, and moves
    between chords by a step or a fourth or fifth far more often than anything
    else. So a candidate is judged on restraint and on the size of its moves --
    not on where its onsets fall, which is the drum question.
    """
    data = _load("bass")
    if not data or not pitches or bars <= 0:
        return None
    motion = data.get("motion_between_chords") or {}
    if not motion:
        return None

    distinct_per_bar = len(set(pitches)) / bars
    # Two distinct pitches a bar is the measured norm; more is busier than the
    # corpus, and the score falls away smoothly rather than at a cliff.
    restraint = min(1.0, 2.0 / distinct_per_bar) if distinct_per_bar > 0 else 0.0

    steps = [b - a for a, b in zip(pitches, pitches[1:]) if a != b]
    if not steps:
        return round(restraint, 4)
    weight = sum(float(motion.get(str(step), 0.0)) for step in steps) / len(steps)
    best = max(float(value) for value in motion.values())
    return round((restraint + (weight / best if best else 0.0)) / 2, 4)


# --------------------------------------------------------------- chord layer
def chord_evidence() -> dict | None:
    data = _load("chord")
    if not data:
        return None
    return {
        "source": data.get("source"),
        "songs": data.get("songs_used"),
        "degree_share": data.get("degree_share"),
        "top_transitions": data.get("top_transitions"),
    }


def melody_evidence() -> dict | None:
    data = _load("chord")
    return {"source": data.get("source"),
            "interval_share": data.get("melody_interval_share")} if data else None


def progression_fit(degrees: list[str]) -> float | None:
    """How ordinary a chord sequence is, against measured transitions."""
    data = chord_evidence()
    if not data or len(degrees) < 2:
        return None
    table = {(row["from"], row["to"]): row["share"] for row in data["top_transitions"]}
    best = max(table.values()) if table else 0.0
    if best <= 0:
        return None
    moves = [table.get((a, b), 0.0) for a, b in zip(degrees, degrees[1:])]
    return round(sum(moves) / len(moves) / best, 4)


# --------------------------------------------------------- arrangement layer
def arrangement_evidence(limit: int = 0) -> dict | None:
    """Song-level shape for ArrangementGPS, which builds projects, not notes."""
    data = _load("arrangement")
    if not data:
        return None
    maps = data.get("maps") or []
    return {
        "source": data.get("source"),
        "songs": data.get("songs"),
        "mode_share": data.get("mode_share"),
        "loop_length_share": data.get("loop_length_share"),
        "median_chords_per_song": data.get("median_chords_per_song"),
        "maps": maps[:limit] if limit else [],
    }
