"""An evidence-backed device chain plan for the tracks in a project.

A plan has three parts:
  1. The role, from the track name (the same rules as extract_device_chains.py)
  2. The measured chain for that role (chain_evidence)
  3. A donor track in this project that already carries it (chain_builder)

With no donor the plan is marked recommendable but not placeable. Device XML is
never invented -- with no real device to copy there is no write step.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from extract_device_chains import display_name, role_for  # noqa: E402

from . import chain_evidence  # noqa: E402
from .chain_builder import chain_of  # noqa: E402
from aimixmaster.project_analyzer import iter_tracks  # noqa: E402


def _coverage(candidate: tuple[str, ...], wanted: tuple[str, ...]) -> float:
    if not wanted:
        return 0.0
    return sum(1 for device in wanted if device in candidate) / len(wanted)


def plan_project(root: ET.Element, tracks_data: list[dict] | None = None) -> dict:
    evidence_rows = tracks_data if tracks_data is not None else chain_evidence.load_tracks()

    existing = []
    for track in iter_tracks(root):
        name = display_name(track)
        if not name:
            continue
        existing.append({"name": name, "role": role_for(name), "chain": chain_of(track)})

    plans = []
    for entry in existing:
        recommendation = chain_evidence.recommend(entry["role"], evidence_rows)
        if recommendation is None:
            plans.append({
                "track": entry["name"],
                "role": entry["role"],
                "status": "no_evidence",
                "current_chain": list(entry["chain"]),
                "reason": f"not enough measured data for the '{entry['role']}' role",
            })
            continue

        if entry["chain"]:
            plans.append({
                "track": entry["name"],
                "role": entry["role"],
                "status": "already_has_chain",
                "current_chain": list(entry["chain"]),
                "recommended_chain": list(recommendation.chain),
                "coverage": round(_coverage(entry["chain"], recommendation.chain), 3),
            })
            continue

        wanted = recommendation.chain
        donors = sorted(
            (item for item in existing if item["chain"]),
            key=lambda item: (-_coverage(item["chain"], wanted), len(item["chain"])),
        )
        best = donors[0] if donors else None
        best_coverage = _coverage(best["chain"], wanted) if best else 0.0

        plans.append({
            "track": entry["name"],
            "role": entry["role"],
            "status": "can_transplant" if best and best_coverage >= 0.5 else "no_donor",
            "current_chain": [],
            "recommended_chain": list(wanted),
            "evidence": [
                {"device": item.device, "presence": item.presence, "occurrences": item.occurrences}
                for item in recommendation.devices
            ],
            "role_sample": recommendation.role_sample,
            "donor": best["name"] if best and best_coverage >= 0.5 else None,
            "donor_chain": list(best["chain"]) if best and best_coverage >= 0.5 else None,
            "donor_coverage": round(best_coverage, 3) if best else 0.0,
        })

    counts = {}
    for plan in plans:
        counts[plan["status"]] = counts.get(plan["status"], 0) + 1
    return {"track_count": len(plans), "status_counts": counts, "plans": plans}
