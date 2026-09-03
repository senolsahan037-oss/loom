from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from .mix_analyzer import MIX_CONTRACT_VERSION, extract_mix_features

PROFILE_DOCUMENT_VERSION = 1
MIN_PROFILE_SOURCE_COUNT = 3


class GenreProfileError(ValueError):
    pass


def _distribution_optional(
    values: Iterable[Optional[float]],
) -> Dict[str, Optional[float]]:
    finite = [
        float(value)
        for value in values
        if value is not None and np.isfinite(value)
    ]
    if not finite:
        return {"median": None, "p25": None, "p75": None}
    return {
        "median": round(float(np.median(finite)), 3),
        "p25": round(float(np.percentile(finite, 25)), 3),
        "p75": round(float(np.percentile(finite, 75)), 3),
    }


def _validate_profile(profile: Dict[str, Any]) -> None:
    if not isinstance(profile, dict):
        raise GenreProfileError("Genre profile must be a JSON object.")
    required = {
        "id",
        "name",
        "source_count",
        "source_stage",
        "measurement_contract",
        "spectral_relative_db",
        "spectral_p25_db",
        "spectral_p75_db",
        "master_metric_distributions",
    }
    missing = required.difference(profile)
    if missing:
        raise GenreProfileError(
            f"Genre profile is missing fields: {', '.join(sorted(missing))}."
        )
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(profile["id"])):
        raise GenreProfileError("Genre profile id must be a lowercase slug.")
    if not isinstance(profile["name"], str) or not profile["name"].strip():
        raise GenreProfileError("Genre profile name must be a non-empty string.")
    if profile["measurement_contract"] != MIX_CONTRACT_VERSION:
        raise GenreProfileError("Genre profile measurement contract is incompatible.")
    source_count = profile["source_count"]
    if isinstance(source_count, bool) or not isinstance(source_count, int):
        raise GenreProfileError("Genre profile source count must be an integer.")
    if source_count < MIN_PROFILE_SOURCE_COUNT:
        raise GenreProfileError(
            f"A genre profile requires at least {MIN_PROFILE_SOURCE_COUNT} source tracks."
        )
    spectral_fields = (
        "spectral_relative_db",
        "spectral_p25_db",
        "spectral_p75_db",
    )
    for field in spectral_fields:
        bands = profile[field]
        if not isinstance(bands, dict) or not bands:
            raise GenreProfileError(
                f"Genre profile {field} has no spectral measurements."
            )
        for center, value in bands.items():
            try:
                center_hz = float(center)
            except (TypeError, ValueError) as exc:
                raise GenreProfileError(
                    f"Genre profile {field} contains an invalid band."
                ) from exc
            if (
                center_hz <= 0
                or not np.isfinite(center_hz)
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not np.isfinite(value)
            ):
                raise GenreProfileError(
                    f"Genre profile {field} contains an invalid band."
                )
    spectral_centers = set(profile["spectral_relative_db"])
    if any(
        set(profile[field]) != spectral_centers
        for field in ("spectral_p25_db", "spectral_p75_db")
    ):
        raise GenreProfileError("Genre profile spectral distributions are incomplete.")
    for center in spectral_centers:
        if not (
            profile["spectral_p25_db"][center]
            <= profile["spectral_relative_db"][center]
            <= profile["spectral_p75_db"][center]
        ):
            raise GenreProfileError(
                "Genre profile spectral distributions are not ordered."
            )
    if profile["source_stage"] != "released_master":
        raise GenreProfileError(
            "Genre profiles must be measured from declared released masters."
        )
    distributions = profile["master_metric_distributions"]
    required_metrics = {
        "integrated_lufs",
        "sample_peak_dbfs",
        "crest_factor_db",
        "channel_balance_db",
    }
    if not isinstance(distributions, dict) or not required_metrics.issubset(
        distributions
    ):
        raise GenreProfileError(
            "Genre profile master metric distributions are incomplete."
        )
    for metric in required_metrics:
        distribution = distributions[metric]
        if not isinstance(distribution, dict) or not {
            "median",
            "p25",
            "p75",
        }.issubset(distribution):
            raise GenreProfileError(
                f"Genre profile distribution for {metric} is malformed."
            )
        values = (
            distribution["p25"],
            distribution["median"],
            distribution["p75"],
        )
        if all(value is None for value in values):
            continue
        if any(
            value is None
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not np.isfinite(value)
            for value in values
        ):
            raise GenreProfileError(
                f"Genre profile distribution for {metric} is malformed."
            )
        if not values[0] <= values[1] <= values[2]:
            raise GenreProfileError(
                f"Genre profile distribution for {metric} is not ordered."
            )


class GenreProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": PROFILE_DOCUMENT_VERSION, "profiles": []}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GenreProfileError("Genre profile catalog could not be read.") from exc
        if not isinstance(document, dict):
            raise GenreProfileError("Genre profile catalog is malformed.")
        if document.get("version") != PROFILE_DOCUMENT_VERSION:
            raise GenreProfileError("Genre profile catalog version is incompatible.")
        profiles = document.get("profiles")
        if not isinstance(profiles, list):
            raise GenreProfileError("Genre profile catalog is malformed.")
        for profile in profiles:
            _validate_profile(profile)
        return document

    def list(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": profile["id"],
                "name": profile["name"],
                "source_count": profile["source_count"],
                "measurement_contract": profile["measurement_contract"],
            }
            for profile in self._read()["profiles"]
        ]

    def all(self) -> List[Dict[str, Any]]:
        return list(self._read()["profiles"])

    def get(self, profile_id: str) -> Optional[Dict[str, Any]]:
        normalized = profile_id.strip().lower()
        return next(
            (
                profile
                for profile in self._read()["profiles"]
                if profile["id"] == normalized
            ),
            None,
        )

    def upsert(self, profile: Dict[str, Any]) -> None:
        _validate_profile(profile)
        document = self._read()
        profiles = [
            existing
            for existing in document["profiles"]
            if existing["id"] != profile["id"]
        ]
        profiles.append(profile)
        profiles.sort(key=lambda item: item["name"].casefold())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(
                {"version": PROFILE_DOCUMENT_VERSION, "profiles": profiles},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def build_genre_profile(
    profile_id: str,
    name: str,
    source_paths: Iterable[Path],
) -> Dict[str, Any]:
    paths = list(source_paths)
    if len(paths) < MIN_PROFILE_SOURCE_COUNT:
        raise GenreProfileError(
            f"A genre profile requires at least {MIN_PROFILE_SOURCE_COUNT} source tracks."
        )

    feature_sets = [
        extract_mix_features(path, path.name)
        for path in paths
    ]
    band_maps = [
        {
            str(band["center_hz"]): band["loudness_relative_db"]
            for band in features["spectral_bands"]
            if band["loudness_relative_db"] is not None
        }
        for features in feature_sets
    ]
    common_centers = set(band_maps[0])
    for band_map in band_maps[1:]:
        common_centers.intersection_update(band_map)
    spectral_relative_db = {
        center: round(
            float(np.median([band_map[center] for band_map in band_maps])),
            3,
        )
        for center in sorted(common_centers, key=float)
    }
    spectral_p25_db = {
        center: round(
            float(np.percentile([band_map[center] for band_map in band_maps], 25)),
            3,
        )
        for center in sorted(common_centers, key=float)
    }
    spectral_p75_db = {
        center: round(
            float(np.percentile([band_map[center] for band_map in band_maps], 75)),
            3,
        )
        for center in sorted(common_centers, key=float)
    }
    tonal_coverage: Dict[str, int] = {}
    for features in feature_sets:
        candidate = features.get("tonal_map", {}).get("key_candidate") or {}
        bucket = f"{candidate.get('root', 'unknown')}:{candidate.get('mode', 'unknown')}"
        tonal_coverage[bucket] = tonal_coverage.get(bucket, 0) + 1
    profile = {
        "id": profile_id.strip().lower(),
        "name": name.strip(),
        "source_count": len(paths),
        "source_stage": "released_master",
        "measurement_contract": MIX_CONTRACT_VERSION,
        "spectral_aggregation": "median",
        "tonal_coverage": {
            "observed_key_mode_buckets": tonal_coverage,
            "source_count": len(paths),
            "coverage_note": "Coverage is measured from source audio; missing buckets require additional source tracks.",
        },
        "spectral_relative_db": spectral_relative_db,
        "spectral_p25_db": spectral_p25_db,
        "spectral_p75_db": spectral_p75_db,
        "master_metric_distributions": {
            "integrated_lufs": _distribution_optional(
                features["analysis"]["integrated_lufs"]
                for features in feature_sets
            ),
            "sample_peak_dbfs": _distribution_optional(
                features["analysis"]["sample_peak_dbfs"]
                for features in feature_sets
            ),
            "crest_factor_db": _distribution_optional(
                features["analysis"]["crest_factor_db"]
                for features in feature_sets
            ),
            "channel_balance_db": _distribution_optional(
                features["channel_balance_db"]
                for features in feature_sets
            ),
        },
    }
    _validate_profile(profile)
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build or update a Subverse genre profile from measured tracks."
    )
    parser.add_argument("--id", required=True, dest="profile_id")
    parser.add_argument("--name", required=True)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("tracks", nargs="+", type=Path)
    args = parser.parse_args()

    missing = [path for path in args.tracks if not path.is_file()]
    if missing:
        parser.error(f"Track was not found: {missing[0]}")
    try:
        profile = build_genre_profile(args.profile_id, args.name, args.tracks)
        GenreProfileStore(args.catalog).upsert(profile)
    except GenreProfileError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
