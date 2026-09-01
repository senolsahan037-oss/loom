"""Pure checks for semantic role-to-pad mapping integrity."""

from collections import defaultdict
from typing import Any, Dict, Iterable, List


ROLE_FAMILIES = {
    "kick": "kick", "sub_kick": "kick",
    "snare": "snare", "snare_alt": "snare", "snare_roll": "snare", "clap": "snare", "rim": "snare",
    "closed_hat": "hat", "open_hat": "hat", "pedal_hat": "hat", "hat": "hat",
    "tom": "tom", "low_tom": "tom", "mid_tom": "tom", "high_tom": "tom",
    "cymbal": "cymbal", "crash": "cymbal", "ride": "cymbal",
    "perc": "perc", "perc_low": "perc", "perc_mid": "perc", "perc_high": "perc",
}


def role_family(role: str) -> str:
    return ROLE_FAMILIES.get(str(role), str(role))


def find_mapping_collisions(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Report one physical note assigned to incompatible semantic families."""
    roles_by_note: dict[int, set[str]] = defaultdict(set)
    for event in events:
        if event.get("note") is not None and event.get("role"):
            roles_by_note[int(event["note"])].add(str(event["role"]))

    collisions = []
    for note, roles in sorted(roles_by_note.items()):
        families = sorted({role_family(role) for role in roles})
        if len(families) > 1:
            collisions.append({
                "note": note,
                "roles": sorted(roles),
                "role_families": families,
            })
    return collisions
