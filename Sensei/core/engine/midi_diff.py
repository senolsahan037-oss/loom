# core/midi_diff.py
from typing import List, Dict, Any

def diff_midi_notes(original: List[Dict[str, Any]], mutated: List[Dict[str, Any]], pad_map: Dict[int, str]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Compares two lists of MIDI note dictionaries.
    Identifies added, removed, velocity-modified, and timing-modified notes.
    """
    added_notes = []
    removed_notes = []
    modified_velocity = []
    modified_timing = []

    # Group notes by (quantized_step_index, pitch) to establish unique identities
    def group_notes(notes):
        grouped = {}
        for note in notes:
            step = int(round(note["time"] * 4.0))  # Quantize time to 16th step index
            key = (step, note["pitch"])
            grouped.setdefault(key, []).append(note)
        # Sort notes within the same step/pitch by time for deterministic indexing
        for key in grouped:
            grouped[key].sort(key=lambda n: n["time"])
        return grouped

    orig_grouped = group_notes(original)
    mut_grouped = group_notes(mutated)

    all_keys = set(orig_grouped.keys()).union(set(mut_grouped.keys()))

    for key in all_keys:
        step, pitch = key
        orig_list = orig_grouped.get(key, [])
        mut_list = mut_grouped.get(key, [])

        max_len = max(len(orig_list), len(mut_list))
        for i in range(max_len):
            if i < len(orig_list) and i < len(mut_list):
                # Matched note: check for modifications
                orig_note = orig_list[i]
                mut_note = mut_list[i]

                vel_changed = orig_note["velocity"] != mut_note["velocity"]
                time_changed = (
                    abs(orig_note["time"] - mut_note["time"]) > 1e-4 or
                    abs(orig_note["duration"] - mut_note["duration"]) > 1e-4
                )

                if vel_changed:
                    modified_velocity.append({
                        "pitch": pitch,
                        "time": mut_note["time"],
                        "original_velocity": orig_note["velocity"],
                        "new_velocity": mut_note["velocity"]
                    })
                if time_changed:
                    modified_timing.append({
                        "pitch": pitch,
                        "original_time": orig_note["time"],
                        "new_time": mut_note["time"],
                        "original_duration": orig_note["duration"],
                        "new_duration": mut_note["duration"]
                    })
            elif i < len(orig_list):
                # Present in original, missing in mutated -> Removed
                removed_notes.append(orig_list[i])
            else:
                # Present in mutated, missing in original -> Added
                added_notes.append(mut_list[i])

    return {
        "added_notes": added_notes,
        "removed_notes": removed_notes,
        "modified_velocity": modified_velocity,
        "modified_timing": modified_timing
    }
