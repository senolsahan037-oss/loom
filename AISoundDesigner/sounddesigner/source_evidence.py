"""The producer's real sound palette -- measured, not suggested.

Two things are kept apart:
  * Samples that carry IDENTITY: library files reusable in another project
    (e.g. "Kick Golden Era 46.aif").
  * Samples that do not: bounce and freeze output. They are a fifth of the
    measurement and mean nothing in another project, so they stay out.

Where a role has too few observations, nothing is returned. This layer does not
fill gaps.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "measured_sound_sources.json"
# See Presetor/presetor/chain_evidence.py -- the same distinction.
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "fixture_sound_sources.json"


def active_data_path() -> Path:
    return DATA_PATH if DATA_PATH.exists() else FIXTURE_PATH


def data_source() -> str:
    return "measured" if DATA_PATH.exists() else "synthetic_fixture"

# Live's own render output: "Bounce ...", "... [2026-05-11 221049]",
# "Freeze ...". None of it travels outside its project.
_BOUNCE_PATTERNS = (
    re.compile(r"^bounce\b", re.I),
    # Live's own marker sits mid-name: "Love Train C1 2 (Bounce) [2025-09-17].wav".
    # Anchoring at the start missed every bounce the program itself named.
    re.compile(r"\(bounce\)", re.I),
    re.compile(r"\bfreeze\b", re.I),
    re.compile(r"^\d+-audio \d+", re.I),
)
# Reverb and convolution impulse responses appear as a SampleRef on the track
# but are not a sound SOURCE -- the first measurement had reverb IRs mixed into
# the snare palette.
_NON_SOURCE_PATTERNS = (
    re.compile(r"early[_ ]reflections", re.I),
    re.compile(r"^hybrid_", re.I),
    re.compile(r"\bimpulse\b", re.I),
    re.compile(r"\bIR[_ ]", re.I),
)
MIN_ROLE_SAMPLE = 8
# A sample seen six times inside one project is that project's decision, not a
# habit. Entering the palette requires being seen in MORE THAN ONE PROJECT.
MIN_PROJECTS = 2

# A multisample instrument ships one file per pitch -- "Zero Hour Bass A0.aif"
# through "Zero Hour Bass D3.aif" is one bass, not thirty-six sounds. Counting
# them separately buries every other source in a role's palette. Only a musical
# note name collapses a family; a numeric tail does not, because "Kick Golden
# Era 46" and "Kick Golden Era 48" really are two different kicks.
MIN_MULTISAMPLE_NOTES = 4
_NOTE_SUFFIX = re.compile(r"^(?P<base>.+?)[ _-]*(?P<note>[A-G](?:#|b)?-?[0-8])$")


@dataclass(frozen=True)
class SourceEvidence:
    sample: str
    occurrences: int
    projects: int


@dataclass(frozen=True)
class RolePalette:
    role: str
    role_sample: int
    instruments: tuple[tuple[str, int], ...]
    samples: tuple[SourceEvidence, ...]


def is_bounce(name: str) -> bool:
    return any(pattern.search(name) for pattern in _BOUNCE_PATTERNS)


def is_non_source(name: str) -> bool:
    return any(pattern.search(name) for pattern in _NON_SOURCE_PATTERNS)


def load_tracks(data_path: Path | None = None) -> list[dict]:
    path = data_path or active_data_path()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} yok. Olculmus veri: python3 scripts/extract_sound_sources.py --out {DATA_PATH}"
            f"  |  Fixture: python3 scripts/build_fixtures.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))["tracks"]


def _note_family(name: str) -> tuple[str, str] | None:
    """("Zero Hour Bass", "A0") for a pitched member of a multisample family."""
    match = _NOTE_SUFFIX.match(name.rsplit(".", 1)[0])
    return (match.group("base").strip(), match.group("note")) if match else None


def multisample_families(names: Iterable[str]) -> dict[str, int]:
    """Base name -> how many distinct pitches of it appear across `names`."""
    pitches: dict[str, set] = defaultdict(set)
    for name in names:
        family = _note_family(name)
        if family:
            pitches[family[0]].add(family[1])
    return {base: len(notes) for base, notes in pitches.items()
            if len(notes) >= MIN_MULTISAMPLE_NOTES}


def identity_samples(row: dict) -> list[str]:
    return [
        name
        for name in (row.get("all_samples") or [])
        if not is_bounce(name) and not is_non_source(name)
    ]


def palette(role: str, tracks: list[dict] | None = None) -> RolePalette | None:
    rows = tracks if tracks is not None else load_tracks()
    role_rows = [row for row in rows if row["role"] == role]
    if len(role_rows) < MIN_ROLE_SAMPLE:
        return None

    families = multisample_families(
        name for row in role_rows for name in identity_samples(row))

    def entry(name: str) -> str:
        family = _note_family(name)
        if family and family[0] in families:
            return f"{family[0]} (multisample, {families[family[0]]} notes)"
        return name

    occurrences: Counter = Counter()
    projects: dict[str, set] = defaultdict(set)
    for row in role_rows:
        # A family counts once per track, however many of its pitches are loaded.
        seen = {entry(name) for name in identity_samples(row)}
        for name in seen:
            occurrences[name] += 1
            projects[name].add(row.get("project"))

    chosen = tuple(
        sorted(
            (
                SourceEvidence(sample=name, occurrences=count, projects=len(projects[name]))
                for name, count in occurrences.items()
                if len(projects[name]) >= MIN_PROJECTS
            ),
            key=lambda item: (-item.projects, -item.occurrences, item.sample),
        )
    )
    instruments = tuple(Counter(d for row in role_rows for d in (row.get("instruments") or [])).most_common(5))
    if not chosen and not instruments:
        return None
    return RolePalette(role=role, role_sample=len(role_rows), instruments=instruments, samples=chosen[:20])


def known_roles(tracks: list[dict] | None = None) -> list[str]:
    rows = tracks if tracks is not None else load_tracks()
    return sorted({row["role"] for row in rows})


def summary(tracks: list[dict] | None = None) -> dict:
    rows = tracks if tracks is not None else load_tracks()
    all_samples = [name for row in rows for name in (row.get("all_samples") or [])]
    identity = [name for name in all_samples if not is_bounce(name) and not is_non_source(name)]

    palettes = {}
    for role in known_roles(rows):
        result = palette(role, rows)
        if result and result.samples:
            palettes[role] = {
                "role_sample": result.role_sample,
                "instruments": [{"device": device, "count": count} for device, count in result.instruments],
                "samples": [
                    {"sample": item.sample, "occurrences": item.occurrences, "projects": item.projects}
                    for item in result.samples[:8]
                ],
            }
    return {
        "data_source": data_source(),
        "tracks_scanned": len(rows),
        "tracks_with_instruments": sum(1 for row in rows if row.get("instruments")),
        "sample_uses_total": len(all_samples),
        "sample_uses_identity": len(identity),
        "bounce_share": round(1 - len(identity) / len(all_samples), 3) if all_samples else 0.0,
        "top_instruments": Counter(d for row in rows for d in (row.get("instruments") or [])).most_common(10),
        "top_identity_samples": Counter(identity).most_common(20),
        "role_palettes": palettes,
        "roles_without_palette": [role for role in known_roles(rows) if role not in palettes],
    }
