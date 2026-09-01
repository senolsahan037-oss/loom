# core/dataset_variation.py
import copy
import random
from typing import List, Dict, Any, Optional

def preserve_backbeat(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Identifies snare and clap events on beats 2 and 4 (integer beats 1.0 and 3.0 of a bar)
    and marks them as is_locked = True.
    """
    new_events = copy.deepcopy(events)
    for ev in new_events:
        if ev.get("role") in {"snare", "clap"}:
            beat_in_bar = ev["beat"] % 4.0
            if abs(beat_in_bar - 1.0) < 1e-4 or abs(beat_in_bar - 3.0) < 1e-4:
                ev["is_locked"] = True
    return new_events

def preserve_bar_length(events: List[Dict[str, Any]], bars: int) -> List[Dict[str, Any]]:
    """
    Removes any events starting at or after the bar length limit (bars * 4.0 beats).
    """
    max_beat = bars * 4.0
    return [ev for ev in events if ev["beat"] < max_beat]

def vary_density(events: List[Dict[str, Any]], role: str, amount: float, seed: int) -> List[Dict[str, Any]]:
    """
    Mutates density of the specified role.
    - amount < 0: drops fraction of unlocked notes of that role.
    - amount > 0: inserts notes of that role on empty 16th subdivisions.
    """
    new_events = copy.deepcopy(events)
    rnd = random.Random(seed)

    if amount < 0:
        abs_amount = abs(amount)
        # Separate matching role notes (locked and unlocked)
        role_notes_locked = []
        role_notes_unlocked = []
        other_notes = []

        for ev in new_events:
            if ev.get("role") == role:
                if ev.get("is_locked", False):
                    role_notes_locked.append(ev)
                else:
                    role_notes_unlocked.append(ev)
            else:
                other_notes.append(ev)

        # Shuffle and sample notes to remove
        rnd.shuffle(role_notes_unlocked)
        num_to_remove = int(len(role_notes_unlocked) * abs_amount)
        retained_unlocked = role_notes_unlocked[num_to_remove:]

        return other_notes + role_notes_locked + retained_unlocked

    elif amount > 0:
        # Determine total bars to know the grid boundary
        max_beat = max([ev["beat"] for ev in new_events]) if new_events else 4.0
        total_bars = int(max_beat / 4.0) + 1
        total_steps = total_bars * 16

        # Find steps where this role is already present
        existing_steps = set()
        for ev in new_events:
            if ev.get("role") == role:
                step_idx = int(round(ev["beat"] * 4.0))
                existing_steps.add(step_idx)

        # Identify candidate steps for insertion (16th note grid)
        candidate_steps = [
            step for step in range(total_steps)
            if step not in existing_steps
        ]

        rnd.shuffle(candidate_steps)
        num_to_add = int(len(candidate_steps) * amount * 0.5)  # Scale back slightly for musicality
        steps_to_add = candidate_steps[:num_to_add]

        for step in steps_to_add:
            # Added snare notes are low-velocity ghost notes (<= 45)
            vel = rnd.randint(25, 45) if role == "snare" else rnd.randint(75, 105)
            new_events.append({
                "role": role,
                "beat": step * 0.25,
                "velocity": vel,
                "duration": 0.2,
                "is_locked": False
            })

    return new_events

def vary_velocity_profile(events: List[Dict[str, Any]], amount: int, seed: int) -> List[Dict[str, Any]]:
    """
    Perturbs velocity of all unlocked notes by a random value in range [-amount, amount].
    """
    new_events = copy.deepcopy(events)
    rnd = random.Random(seed)

    for ev in new_events:
        if not ev.get("is_locked", False):
            offset = rnd.randint(-amount, amount)
            ev["velocity"] = min(127, max(1, ev["velocity"] + offset))
    return new_events

def adjust_velocity_to_profile(events: List[Dict[str, Any]], target_mean: int, target_std: int, seed: int) -> List[Dict[str, Any]]:
    """
    Adjusts the velocity of unlocked notes to follow the style profile's mean and standard deviation.
    Preserves low-velocity snare ghost notes from being scaled up.
    """
    new_events = copy.deepcopy(events)
    rnd = random.Random(seed)
    for ev in new_events:
        if not ev.get("is_locked", False):
            # Preserve snare ghost notes
            if ev.get("role") == "snare" and ev.get("velocity", 100) <= 45:
                # Apply minor humanization within the low range
                ev["velocity"] = min(45, max(15, ev["velocity"] + rnd.randint(-5, 5)))
            else:
                # Generate value following normal distribution
                offset = int(rnd.gauss(0, target_std))
                ev["velocity"] = min(127, max(1, target_mean + offset))
    return new_events

def vary_timing_offsets(events: List[Dict[str, Any]], swing_amount: float, seed: int) -> List[Dict[str, Any]]:
    """
    Applies timing swing shifts to off-beat notes (where beat is not an integer).
    """
    new_events = copy.deepcopy(events)
    rnd = random.Random(seed)

    for ev in new_events:
        if not ev.get("is_locked", False):
            # Check if off-beat: beat is not an integer
            if abs(ev["beat"] - round(ev["beat"])) > 1e-4:
                shift = rnd.choice([-swing_amount, swing_amount])
                ev["beat"] = max(0.0, ev["beat"] + shift)
    return new_events

def vary_hat_subdivision(events: List[Dict[str, Any]], amount: float, seed: int) -> List[Dict[str, Any]]:
    """
    Subdivides hi-hat notes into 32nd note rolls for a fraction of the hat events.
    """
    new_events = []
    rnd = random.Random(seed)

    for ev in events:
        new_events.append(copy.deepcopy(ev))
        # Subdivide closed_hat / hat notes
        if ev.get("role") in {"closed_hat", "hat"} and not ev.get("is_locked", False):
            if rnd.random() < amount:
                # Add a subdivided hit 32nd note later (0.125 beats)
                new_events.append({
                    "role": ev["role"],
                    "beat": ev["beat"] + 0.125,
                    "velocity": min(127, max(1, int(ev["velocity"] * 0.85))),
                    "duration": ev.get("duration", 0.15) * 0.5,
                    "is_locked": False
                })
    return new_events

def vary_fill_bar(events: List[Dict[str, Any]], bar_index: int, seed: int) -> List[Dict[str, Any]]:
    """
    Mutates events in the specified bar to generate a snare roll or fill.
    """
    new_events = []
    bar_start = bar_index * 4.0
    bar_end = (bar_index + 1) * 4.0

    # Preserve other bars, remove non-locked snare/perc in the target bar
    for ev in events:
        if bar_start <= ev["beat"] < bar_end:
            if ev.get("is_locked", False):
                new_events.append(copy.deepcopy(ev))
        else:
            new_events.append(copy.deepcopy(ev))

    # Add fill roll notes (e.g. 16th snare roll in the second half of the bar)
    rnd = random.Random(seed)
    # Target steps in the second half of the bar: 8th, 9th, ... 15th step of the bar
    fill_steps = [8, 9, 10, 11, 12, 13, 14, 15]
    
    for step in fill_steps:
        beat = bar_start + (step * 0.25)
        # Crescendo velocity profile
        vel = int(60 + (step - 8) * 8 + rnd.randint(-5, 5))
        new_events.append({
            "role": "snare",
            "beat": beat,
            "velocity": min(127, max(30, vel)),
            "duration": 0.15,
            "is_locked": False
        })

    return new_events

def vary_abstract_events(
    events: List[Dict[str, Any]],
    params: Dict[str, Any],
    seed: Optional[int] = None,
    style_profile: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Applies variations to a copy of abstract events based on parameters, seed, and optional style profile.
    """
    if seed is None:
        seed = random.randint(1, 1000000)

    # 1. Start with a deep copy and mark backbeats
    mutated = preserve_backbeat(events)

    # 2. Enforce bar limit
    bars = params.get("bars", 8)
    mutated = preserve_bar_length(mutated, bars)

    if style_profile:
        # --- Dataset Style Profile Driven Variation ---
        # A. Density
        kick_density = style_profile.get("kick_density", 0.5)
        hat_density = style_profile.get("hat_density", 0.5)
        ghost_snare_ratio = style_profile.get("ghost_snare_ratio", 0.0)

        mutated = vary_density(mutated, "kick", kick_density - 0.5, seed)
        mutated = vary_density(mutated, "closed_hat", hat_density - 0.5, seed + 1)
        
        if ghost_snare_ratio > 0.0:
            mutated = vary_density(mutated, "snare", ghost_snare_ratio, seed + 2)

        # B. Velocity Profile
        vel_mean = style_profile.get("velocity_mean")
        vel_std = style_profile.get("velocity_std", 10)
        if vel_mean is not None:
            mutated = adjust_velocity_to_profile(mutated, vel_mean, vel_std, seed + 3)

        # C. Timing Swing (swing_ticks converted to beat offset: 1 beat = 480 ticks)
        swing_ticks = style_profile.get("swing_ticks")
        if swing_ticks:
            swing_amount = swing_ticks / 480.0
            mutated = vary_timing_offsets(mutated, swing_amount, seed + 4)

        # D. Hi-hat subdivisions (proportional to hat density)
        if hat_density > 0.6:
            mutated = vary_hat_subdivision(mutated, (hat_density - 0.5) * 0.5, seed + 5)
    else:
        # --- Parameter Driven Variation ---
        # 3. Apply density variations
        density_shift = params.get("density_shift")
        if density_shift and density_shift != 0.0:
            mutated = vary_density(mutated, "kick", density_shift, seed)
            mutated = vary_density(mutated, "snare", density_shift, seed + 1)
            mutated = vary_density(mutated, "closed_hat", density_shift, seed + 2)

        # 4. Apply velocity humanization
        vel_human = params.get("velocity_humanization")
        if vel_human:
            mutated = vary_velocity_profile(mutated, vel_human, seed + 3)

        # 5. Apply timing swing
        swing = params.get("swing_amount")
        if swing:
            mutated = vary_timing_offsets(mutated, swing, seed + 4)

        # 6. Apply hat subdivision
        hat_sub = params.get("hat_subdivision_amount")
        if hat_sub:
            mutated = vary_hat_subdivision(mutated, hat_sub, seed + 5)

        # 7. Apply bar fill
        fill_bar = params.get("fill_bar_index")
        if fill_bar is not None:
            mutated = vary_fill_bar(mutated, fill_bar, seed + 6)

    # Maintain chronological order for clean output
    mutated.sort(key=lambda ev: ev["beat"])
    return mutated
