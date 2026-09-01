"""Build and resolve Ableton preset identities with dataset-derived genre neighbors."""

from __future__ import annotations

import math
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "sensei.preset-genre-identity.v1"
GRAPH_SCHEMA_VERSION = "sensei.genre-neighbor-graph.v1"
_ROLE_TAGS = {
    "bass": {"Clips|Music Clip|Bassline"},
    "chord": {"Clips|Music Clip|Chord Progression", "Clips|Music Clip|Single Chord"},
    "drum": {"Clips|Drum Clip|Full Drum Clip", "Clips|Drum Clip|Top Drum Clip", "Clips|Drum Clip|Percussion Clip"},
}


def _normalized_name(value: Any) -> str:
    name = Path(str(value or "")).name
    if Path(name).suffix.lower() in {".adg", ".adv"}:
        name = Path(name).stem
    return " ".join(name.casefold().split())


def _pack(entry: dict[str, Any]) -> str | None:
    if entry.get("pack"):
        return str(entry["pack"])
    path = str(entry.get("path") or "")
    marker = "/Factory Packs/"
    return path.split(marker, 1)[1].split("/", 1)[0] if marker in path else None


def _role(entry: dict[str, Any]) -> str | None:
    tags = set((entry.get("source_native") or {}).get("ableton_tags") or [])
    for role, role_tags in _ROLE_TAGS.items():
        if tags & role_tags:
            return role
    return None


def build_genre_neighbor_graph(corpus: Iterable[dict[str, Any]]) -> dict[str, Any]:
    genre_features: dict[str, Counter[str]] = defaultdict(Counter)
    pack_genres: dict[str, Counter[str]] = defaultdict(Counter)
    role_genre_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for entry in corpus:
        native = entry.get("source_native") or {}
        genres = {str(value) for value in native.get("ableton_genres") or [] if value}
        if not genres:
            continue
        pack = _pack(entry)
        role = _role(entry)
        tags = {str(value) for value in native.get("ableton_tags") or [] if value and not str(value).startswith("Genres|")}
        for genre in genres:
            if pack:
                pack_genres[pack][genre] += 1
                genre_features[genre][f"pack:{pack}"] += 1
            for tag in tags:
                genre_features[genre][f"tag:{tag}"] += 1
            if role:
                role_genre_counts[role][genre] += 1

    def cosine(left: Counter[str], right: Counter[str]) -> float:
        shared = set(left) & set(right)
        dot = sum(left[key] * right[key] for key in shared)
        denominator = math.sqrt(sum(value * value for value in left.values())) * math.sqrt(sum(value * value for value in right.values()))
        return dot / denominator if denominator else 0.0

    neighbors: dict[str, list[dict[str, Any]]] = {}
    for genre in sorted(genre_features):
        ranked = [
            {"genre": other, "similarity": round(cosine(genre_features[genre], genre_features[other]), 6)}
            for other in genre_features if other != genre
        ]
        neighbors[genre] = sorted((item for item in ranked if item["similarity"] > 0), key=lambda item: (-item["similarity"], item["genre"]))[:12]
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "pack_genre_counts": {pack: dict(sorted(counts.items())) for pack, counts in sorted(pack_genres.items())},
        "role_genre_counts": {role: dict(sorted(counts.items())) for role, counts in sorted(role_genre_counts.items())},
        "neighbors": neighbors,
    }


def build_preset_identities(
    index_items: Iterable[dict[str, Any]],
    bass_catalog: Iterable[dict[str, Any]],
    chord_catalog: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    catalog_by_path = {str(entry.get("path")): entry for entry in [*bass_catalog, *chord_catalog]}
    identities: list[dict[str, Any]] = []
    for item in index_items:
        path = str(item.get("path") or "")
        if Path(path).suffix.lower() not in {".adg", ".adv"}:
            continue
        is_ableton_library = "/Music/Ableton/" in path or "/Ableton Live " in path and "/Core Library/" in path
        if not is_ableton_library:
            continue
        native = item.get("source_native") or {}
        tags = {str(value) for value in native.get("ableton_tags") or []}
        catalog_entry = catalog_by_path.get(path)
        if catalog_entry:
            profile_id = str(catalog_entry["profile_id"])
            role = "bass" if ".bass." in profile_id else "chord"
        elif "device:ableton:instr:DrumGroupDevice" in tags:
            profile_id, role = "ableton.drum-rack.v1", "drum"
        else:
            continue
        identities.append({
            "schema_version": SCHEMA_VERSION,
            "reference_id": item.get("reference_id") or (catalog_entry or {}).get("reference_id"),
            "name": item.get("name") or Path(path).name,
            "normalized_name": _normalized_name(item.get("name") or path),
            "path": path,
            "pack": item.get("pack") or (catalog_entry or {}).get("pack"),
            "role": role,
            "profile_id": profile_id,
            "native_genres": sorted({str(value) for value in native.get("ableton_genres") or [] if value}),
            "native_sounds": sorted({str(value) for value in native.get("ableton_sounds") or [] if value}),
            "native_drums": sorted({str(value) for value in native.get("ableton_drums") or [] if value}),
        })
    return sorted(identities, key=lambda item: (item["normalized_name"], item["role"], item["path"]))


def resolve_preset_genre(
    *,
    device_name: str,
    role: str,
    identities: Iterable[dict[str, Any]],
    graph: dict[str, Any],
    minimum_confidence: float = 0.0,
    default_genre: str | None = None,
) -> dict[str, Any]:
    matches = [item for item in identities if item.get("role") == role and item.get("normalized_name") == _normalized_name(device_name)]
    if not matches:
        return {"resolved": False, "reason": "preset_identity_unresolved", "candidates": []}
    identity_keys = {(item.get("profile_id"), tuple(item.get("native_genres") or [])) for item in matches}
    if len(identity_keys) > 1:
        return {"resolved": False, "reason": "preset_identity_ambiguous", "candidates": matches}
    identity = max(matches, key=lambda item: (bool(item.get("native_genres")), bool(item.get("pack"))))
    seed_counts = Counter({str(genre): int(count) for genre, count in (identity.get("related_genre_counts") or {}).items()})
    if not seed_counts:
        seed_counts.update({genre: 1 for genre in identity.get("native_genres") or []})
    if not seed_counts and identity.get("pack"):
        seed_counts.update((graph.get("pack_genre_counts") or {}).get(identity["pack"]) or {})
    if not seed_counts:
        # No direct or pack-level genre evidence for this preset at all. Only
        # fall back to a caller-supplied default (e.g. the project's other
        # already-resolved tracks) when that genre actually has corpus
        # coverage for this role -- this still never invents a genre that
        # has zero real examples to generate from.
        role_counts_for_default = (graph.get("role_genre_counts") or {}).get(role) or {}
        if default_genre and int(role_counts_for_default.get(default_genre, 0)) > 0:
            return {
                "resolved": True,
                "reason": "",
                "identity": identity,
                "genre_mode": "single",
                "genre": default_genre,
                "genres": [default_genre],
                "weights": [1.0],
                "confidence": 0.0,
                "margin": 0.0,
                "candidates": [],
                "genre_source": "project_default_genre",
            }
        return {"resolved": False, "reason": "genre_evidence_missing", "identity": identity, "candidates": []}
    role_counts = (graph.get("role_genre_counts") or {}).get(role) or {}
    total = sum(seed_counts.values()) or 1
    scores: Counter[str] = Counter()
    for genre, count in seed_counts.items():
        weight = count / total
        scores[genre] += 0.7 * weight
        for neighbor in (graph.get("neighbors") or {}).get(genre) or []:
            scores[str(neighbor["genre"])] += 0.3 * weight * float(neighbor["similarity"])
    ranked = [
        {"genre": genre, "score": round(score, 6), "role_corpus_count": int(role_counts.get(genre, 0))}
        for genre, score in scores.items() if int(role_counts.get(genre, 0)) > 0
    ]
    ranked.sort(key=lambda item: (-item["score"], item["genre"]))
    if not ranked:
        return {"resolved": False, "reason": "no_role_genre_corpus", "identity": identity, "candidates": []}
    confidence = ranked[0]["score"]
    margin = confidence - (ranked[1]["score"] if len(ranked) > 1 else 0.0)
    if minimum_confidence > 0 and confidence < minimum_confidence:
        return {"resolved": False, "reason": "genre_neighbor_confidence_low", "identity": identity, "candidates": ranked, "confidence": confidence, "margin": round(margin, 6)}
    tied = [item for item in ranked if abs(item["score"] - confidence) <= 0.000001]
    if len(tied) > 1:
        return {
            "resolved": True, "reason": "", "identity": identity, "genre_mode": "synthesis",
            "genres": [item["genre"] for item in tied], "weights": [round(1 / len(tied), 6)] * len(tied),
            "confidence": confidence, "margin": 0.0, "candidates": ranked,
        }
    return {"resolved": True, "reason": "", "identity": identity, "genre_mode": "single", "genre": ranked[0]["genre"], "genres": [ranked[0]["genre"]], "weights": [1.0], "confidence": confidence, "margin": round(margin, 6), "candidates": ranked}


def write_genre_identity_artifacts(
    *,
    index_items: Iterable[dict[str, Any]],
    bass_catalog: Iterable[dict[str, Any]],
    chord_catalog: Iterable[dict[str, Any]],
    corpus: Iterable[dict[str, Any]],
    output_directory: str | Path,
) -> dict[str, Any]:
    output = Path(output_directory).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    index_items = list(index_items)
    identities = build_preset_identities(index_items, bass_catalog, chord_catalog)
    corpus = list(corpus)
    graph = build_genre_neighbor_graph(corpus)
    related_genres: dict[str, Counter[str]] = defaultdict(Counter)
    drum_identities = {item["normalized_name"] for item in identities if item["role"] == "drum"}
    seen_related_clips: set[tuple[str, str]] = set()
    for entry in [*corpus, *index_items]:
        if _role(entry) != "drum":
            continue
        clip_name = _normalized_name(entry.get("name") or entry.get("path"))
        genres = (entry.get("source_native") or {}).get("ableton_genres") or []
        for preset_name in drum_identities:
            if clip_name == preset_name or clip_name.startswith(preset_name + " "):
                for genre in genres:
                    evidence_key = (clip_name, str(genre))
                    if genre and evidence_key not in seen_related_clips:
                        related_genres[preset_name][str(genre)] += 1
                        seen_related_clips.add(evidence_key)
    for identity in identities:
        counts = related_genres.get(identity["normalized_name"])
        if counts:
            identity["related_genre_counts"] = dict(sorted(counts.items()))
    identity_path = output / "ableton_preset_genre_identities.jsonl"
    graph_path = output / "ableton_genre_neighbor_graph.json"
    identity_temp = identity_path.with_suffix(".jsonl.tmp")
    graph_temp = graph_path.with_suffix(".json.tmp")
    with identity_temp.open("w", encoding="utf-8") as handle:
        for identity in identities:
            handle.write(json.dumps(identity, ensure_ascii=False, sort_keys=True) + "\n")
    graph["generated_at"] = datetime.now(timezone.utc).isoformat()
    graph["identity_count"] = len(identities)
    with graph_temp.open("w", encoding="utf-8") as handle:
        json.dump(graph, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(identity_temp, identity_path)
    os.replace(graph_temp, graph_path)
    return {"identity_path": str(identity_path), "graph_path": str(graph_path), "identity_count": len(identities), "graph": graph}
