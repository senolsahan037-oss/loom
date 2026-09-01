import os
import sys
from pathlib import Path
from unittest.mock import patch

# Add Sensei path to sys.path
sys.path.append(str(Path(__file__).parent.parent / "Sensei"))

from generators.drum_generator import generate_drum_pattern
from ableton.inspector.profile_exporter import build_kit_profile, inspect_alc_embedded_kit


def run_validation():
    workspace_root = Path(__file__).parent.parent
    fixtures_dir = workspace_root / "Sensei" / "ableton" / "fixtures"

    # Define candidates with varying layouts
    candidates = [
        # Fixtures
        {"name": "swang_bap_kit", "path": fixtures_dir / "swang_bap_kit.adg", "type": "preset"},
        {"name": "two_worlds_kit", "path": fixtures_dir / "two_worlds_kit.adg", "type": "preset"},
        # Real Sound Oracle presets
        {
            "name": "Beacon Kit",
            "path": Path("~/Music/Ableton/Factory Packs/Trap Drums by Sound Oracle/Drums/Beacon Kit.adg").expanduser(),
            "type": "preset",
        },
        {
            "name": "Diverge Kit",
            "path": Path("~/Music/Ableton/Factory Packs/Trap Drums by Sound Oracle/Drums/Diverge Kit.adg").expanduser(),
            "type": "preset",
        },
        # Real Core Library Presets
        {
            "name": "Akustichord Kit",
            "path": Path(
                "/Applications/Ableton Live 12 Standard.app/Contents/App-Resources/Core Library/Racks/Drum Racks/Sampled/Akustichord Kit.adg"
            ),
            "type": "preset",
        },
        {
            "name": "Alert Kit",
            "path": Path(
                "/Applications/Ableton Live 12 Standard.app/Contents/App-Resources/Core Library/Racks/Drum Racks/Sampled/Alert Kit.adg"
            ),
            "type": "preset",
        },
        {
            "name": "Atom Kit",
            "path": Path(
                "/Applications/Ableton Live 12 Standard.app/Contents/App-Resources/Core Library/Racks/Drum Racks/Sampled/Atom Kit.adg"
            ),
            "type": "preset",
        },
        {
            "name": "Battu Kit",
            "path": Path(
                "/Applications/Ableton Live 12 Standard.app/Contents/App-Resources/Core Library/Racks/Drum Racks/Sampled/Battu Kit.adg"
            ),
            "type": "preset",
        },
    ]

    # Add GM 36/38/42 mock kit
    gm_mock_path = fixtures_dir / "gm_mock_kit.adg"
    gm_mock_path.write_text("gm_mock")
    candidates.append({"name": "GM Mock Kit", "path": gm_mock_path, "type": "gm_mock"})

    print(
        f"{'KIT NAME':<20} | {'PADS':<4} | {'LAYOUT ID':<15} | {'SOURCE':<10} | {'KICK':<4} | {'SNARE':<5} | {'HAT':<4} | {'SAFE':<5} | {'EVENTS':<6} | {'INVALID':<7}"
    )
    print("-" * 100)

    import generators.drum_generator as dg

    orig_build = dg.build_kit_profile

    def mock_build(path):
        p = Path(path)
        if p.name == "gm_mock_kit.adg":
            return {
                "type": "kit_profile",
                "kit_id": "gm_mock_kit",
                "kit_name": "GM Mock Kit",
                "pads": {
                    "36": {
                        "note": 36,
                        "label": "GM Kick",
                        "pad_semantic_group": "drum_core",
                        "normalized_role": "kick",
                        "confidence": 0.9,
                    },
                    "38": {
                        "note": 38,
                        "label": "GM Snare",
                        "pad_semantic_group": "drum_core",
                        "normalized_role": "snare",
                        "confidence": 0.9,
                    },
                    "42": {
                        "note": 42,
                        "label": "GM Hat",
                        "pad_semantic_group": "drum_core",
                        "normalized_role": "closed_hat",
                        "confidence": 0.9,
                    },
                },
            }
        return orig_build(path)

    with patch("generators.drum_generator.build_kit_profile", side_effect=mock_build):
        for cand in candidates:
            f = cand["path"]
            if not f.exists() and cand["type"] != "gm_mock":
                continue

            try:
                res = generate_drum_pattern({"kit_path": str(f), "bars": 4, "seed": 42})
                diag = res["diagnostics"]
                chosen = res["chosen_notes"]

                invalid_count = 0
                if res["generation_safe"]:
                    if cand["type"] == "gm_mock":
                        pads = {"36": {}, "38": {}, "42": {}}
                    elif f.suffix.lower() == ".adg":
                        prof = build_kit_profile(f)
                        pads = prof.get("pads", {})
                    else:
                        prof = inspect_alc_embedded_kit(f)
                        pads = prof.get("pads", {})
                    invalid_count = sum(1 for ev in res["events"] if str(ev["note"]) not in pads)

                hat_val = chosen.get("closed_hat") or chosen.get("hat") or "-"
                print(
                    f"{cand['name'][:20]:<20} | "
                    f"{diag.get('pad_map_size', 0):<4} | "
                    f"{diag.get('rack_layout_id', 'unknown')[:15]:<15} | "
                    f"{diag.get('rack_layout_source', 'unknown'):<10} | "
                    f"{chosen.get('kick', '-'):<4} | "
                    f"{chosen.get('snare', '-'):<5} | "
                    f"{hat_val:<4} | "
                    f"{str(res['generation_safe']):<5} | "
                    f"{len(res['events']):<6} | "
                    f"{invalid_count:<7}"
                )
            except Exception as e:
                print(
                    f"{cand['name'][:20]:<20} | ERR  | "
                    f"{'N/A':<15} | {'N/A':<10} | "
                    f"{'-':<4} | {'-':<5} | {'-':<4} | False | 0      | 0"
                )

    if gm_mock_path.exists():
        gm_mock_path.unlink()


if __name__ == "__main__":
    run_validation()
