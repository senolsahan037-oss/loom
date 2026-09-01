"""Bir projedeki track'ler icin kanita dayali cihaz zinciri plani.

Plan uc parcadan olusur:
  1. Track adindan rol (extract_device_chains.py ile ayni kurallar)
  2. O rol icin olculmus zincir (chain_evidence)
  3. Bu projede o zinciri tasiyan bir donor track (chain_builder)

Donor bulunamazsa plan "onerilebilir ama yerlestirilemez" olarak isaretlenir.
Cihaz XML'i uydurulmaz -- kopyalanacak gercek bir cihaz yoksa yazma adimi yok.
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
                "reason": f"'{entry['role']}' rolu icin yeterli olculmus veri yok",
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
