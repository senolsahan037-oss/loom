"""kit_resolver: a .adg kit becomes {note, sample} pads the extension can rebuild.

Hermetic: the fixture preset's samples point at the pack author's machine;
the test lays out a fake pack folder around a copy of the preset so the
RelativePath walk-up is what resolves them, and checks that unresolved pads
are reported missing rather than guessed.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ableton.kit_resolver import KitResolveError, find_kit, resolve_kit

FIXTURE = Path(__file__).resolve().parents[1] / "ableton" / "fixtures" / "swang_bap_kit.adg"


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    root = tmp_path / "Golden Era Hip-Hop Drums by Sound Oracle"
    (root / "Drums").mkdir(parents=True)
    shutil.copy2(FIXTURE, root / "Drums" / "Swang Bap Kit.adg")
    for rel in ("Samples/One Shots/Kick/Kick Golden Era 34.aif", "Samples/One Shots/Snare/Snare Golden Era 38.aif",
                "Samples/One Shots/Hihat/Hihat Closed Golden Era 24.aif"):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"FORM")
    return root


def test_relative_paths_resolve_through_the_pack_root(pack: Path) -> None:
    kit = resolve_kit(str(pack / "Drums" / "Swang Bap Kit.adg"))
    by_note = {p["note"]: p for p in kit["pads"]}
    assert kit["kit"] == "Swang Bap Kit"
    # The fixture writes ReceivingNote 80/79/82; Live stores that value
    # inverted, so the pads are 48/49/46 (measured on Live 12.4.15b1).
    assert 48 in by_note and by_note[48]["sample"].endswith("Kick/Kick Golden Era 34.aif")
    # how the file was found is a named state, not a free-text note
    assert by_note[48]["reference_state"] == "pack_relative" and by_note[48]["found_in"]
    assert 49 in by_note and by_note[49]["role"] == "snare"
    assert 46 in by_note and by_note[46]["role"] == "closed_hat"
    assert all(Path(p["sample"]).is_file() for p in kit["pads"])


def test_unresolved_pads_are_reported_missing_not_guessed(pack: Path) -> None:
    kit = resolve_kit(str(pack / "Drums" / "Swang Bap Kit.adg"))
    assert kit["pad_count"] == 16
    # three files exist; the preset uses some of them on more than one pad
    assert {Path(p["sample"]).name for p in kit["pads"]} == {"Kick Golden Era 34.aif", "Snare Golden Era 38.aif", "Hihat Closed Golden Era 24.aif"}
    assert len(kit["pads"]) + len(kit["missing"]) == 16
    assert len(kit["missing"]) >= 10
    assert all(m["reason"] and m.get("relative") for m in kit["missing"] if "declared" in m)


def test_fidelity_names_what_the_sdk_path_drops(pack: Path) -> None:
    kit = resolve_kit(str(pack / "Drums" / "Swang Bap Kit.adg"))
    dropped = kit["fidelity"]["dropped"]
    assert "K & S Drive" in dropped["macros"]
    assert "choke groups" in dropped["always"]
    assert "Perc Palmstrike" in dropped["per_pad_effects"]  # AutoPan + Utility on that pad in the preset


def test_name_lookup_uses_the_catalogue_and_refuses_unknown_names(pack: Path) -> None:
    adg = pack / "Drums" / "Swang Bap Kit.adg"
    catalog = [{"name": "Swang Bap Kit.adg", "normalized_name": "swang bap kit", "role": "drum", "path": str(adg)},
               {"name": "Swang Bap Kit.adg", "normalized_name": "swang bap kit", "role": "bass", "path": str(adg)}]
    assert find_kit("Swang Bap Kit", catalog) == adg
    with pytest.raises(KitResolveError):
        find_kit("No Such Kit", catalog)
    with pytest.raises(KitResolveError):
        find_kit(str(pack / "Drums" / "missing.adg"))
