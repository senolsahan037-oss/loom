"""Dataset-free mutation of canonical abstract drum-role events."""

import copy
import random
from typing import Any, Dict, List, Optional


def mutate_abstract_events(
    abstract_events: List[Dict[str, Any]],
    prompt: str,
    seed: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Preserve the legacy musical mutation behavior without physical MIDI parsing."""
    prompt = prompt.lower()
    rng = random.Random(seed) if seed is not None else random.Random()
    mutated = []
    energy_up = any(word in prompt for word in ["artir", "artır", "energy", "increase", "more", "hareketli"])
    energy_down = any(word in prompt for word in ["azalt", "sade", "decrease", "simplify", "less", "sadelestir", "sadeleştir"])
    humanize = "humanize" in prompt or "insan" in prompt
    snare_roll = any(word in prompt for word in ["roll", "fill", "ekle"])

    for event in abstract_events:
        changed = copy.deepcopy(event)
        if energy_up:
            changed["velocity"] = min(127, int(changed["velocity"] * 1.15))
        elif energy_down:
            changed["velocity"] = int(changed["velocity"] * 0.80)
            if changed["role"] == "closed_hat" and changed["beat"] % 0.5 != 0.0 and rng.random() > 0.3:
                continue
        if humanize and changed["role"] == "closed_hat":
            changed["velocity"] = max(1, min(127, changed["velocity"] + rng.randint(-12, 12)))
        mutated.append(changed)

    if snare_roll and mutated:
        bar_start = int(max(event["beat"] for event in mutated) / 4.0) * 4.0
        for offset in [3.5, 3.625, 3.75, 3.875]:
            mutated.append({
                "role": "snare",
                "beat": bar_start + offset,
                "velocity": rng.randint(90, 115),
                "duration": 0.125,
            })
    return mutated
