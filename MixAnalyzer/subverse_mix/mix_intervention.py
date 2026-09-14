"""Turns an attributed band finding into one bounded, verifiable EQ move.

`band_attribution` answers "which track feeds this band". This answers "what is
the smallest change that moves it, and how far will the mixdown actually shift".

Three things it refuses to do, each of which would be the easy version:

1. **It does not apply the finding's number as the cut.** A mixdown 8.3 dB hot
   in a band does not mean the owning track is cut by 8.3 dB. The owner is one
   contributor among several, so the reachable change is bounded by everything
   else in that band. The module computes that ceiling and says so — for a track
   holding 74% of a band, muting it entirely moves the mixdown by 5.9 dB, and
   nothing the EQ can do reaches 8.3.

2. **It does not invent an EQ band.** It moves the gain of a band that is
   already enabled, already inside the finding's frequency range, and in a mode
   where gain has an effect. If no such band exists, it proposes nothing rather
   than adding a device or re-purposing a band the producer tuned by hand.

3. **It does not take one large step.** The proposal is capped, because the only
   honest way to use it is to apply, re-measure, and look again — which is what
   Mix Check's own `experiment` field asks for.

Which EQ modes actually carry gain was measured, not assumed: across 120 of the
user's own sets, mode 6 had exactly 1 non-zero gain in 70 enabled bands (a high
cut, gain meaningless), while modes 1, 2, 3, 5 and 7 used gain in 46-62% of
bands. Mode 0 sits at 13% — a low cut carrying a leftover value from before the
mode was changed — so it is treated as not gain-bearing.
"""

from __future__ import annotations

import math
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from AIMixMaster.aimixmaster.als_io import load_als, save_als_atomic  # noqa: E402


# Measured over 120 real sets: share of enabled bands carrying a non-zero gain.
#   mode 0  13.3%   low cut, leftover gain          -> not gain-bearing
#   mode 1  45.6%                                   -> gain-bearing
#   mode 2  60.7%                                   -> gain-bearing
#   mode 3  62.1%   bell, the precise tool          -> gain-bearing
#   mode 5  50.6%   shelf, spills outside the band  -> gain-bearing
#   mode 6   1.4%   high cut                        -> not gain-bearing
#   mode 7  57.1%                                   -> gain-bearing
GAIN_BEARING_MODES = frozenset({1, 2, 3, 5, 7})
BELL_MODE = 3
SHELF_MODES = frozenset({2, 5})

# One step, then re-measure. Larger moves are a mix decision, not a correction.
MAX_STEP_DB = 1.5

# Below this the owner is not distinct enough from the rest of the mix to act on.
MIN_EMPHASIS = 0.15

# Eq8 refuses gains outside this in the interface; do not write past it.
EQ8_GAIN_LIMIT_DB = 15.0


@dataclass
class BandMove:
    track: str
    device: str
    band_index: int
    band_freq_hz: float
    band_mode: int
    current_gain_db: float
    proposed_gain_db: float
    step_db: float
    reason: str
    predicted_mixdown_shift_db: float
    ceiling_db: float
    finding_excess_db: float
    owner_band_share: float
    spills_outside_band: bool
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _manual(node: Optional[ET.Element], tag: str) -> Optional[str]:
    if node is None:
        return None
    found = node.find(f"{tag}/Manual")
    return found.get("Value") if found is not None else None


def _band_params(eq: ET.Element, index: int) -> Optional[ET.Element]:
    band = eq.find(f"Bands.{index}")
    if band is None:
        return None
    inner = band.find("ParameterA")
    return inner if inner is not None else band


def reachable_ceiling_db(owner_share: float) -> float:
    """How far the mixdown band can move if the owner is removed entirely.

    The rest of the mix still sits in that band, so the change is bounded:
    10*log10(1 - share). A track holding all of a band has no ceiling; a track
    holding half of it can move the band 3 dB and no further.
    """
    remainder = max(0.0, 1.0 - owner_share)
    if remainder <= 0:
        return float("inf")
    return -10 * math.log10(remainder)


def predicted_shift_db(owner_share: float, step_db: float) -> float:
    """Mixdown band change when the owner alone moves by `step_db`.

    Only the owner's contribution scales; everything else in the band is
    untouched, which is precisely why the cut does not transfer one for one.
    """
    scaled = owner_share * (10 ** (step_db / 10.0))
    total = scaled + (1.0 - owner_share)
    if total <= 0:
        return 0.0
    return 10 * math.log10(total)


def plan_band_move(
    als_path: str | Path,
    *,
    attribution: Dict[str, Any],
    excess_db: float,
    max_step_db: float = MAX_STEP_DB,
) -> Dict[str, Any]:
    """Proposes one EQ move for the track that owns the band, or refuses."""
    low_hz, high_hz = attribution["band_hz"]
    ranking = attribution.get("ranking") or []
    if not ranking:
        return {"proposal": None, "refused": "No track was attributed to this band."}

    owner = ranking[0]
    if owner["emphasis"] < MIN_EMPHASIS:
        return {
            "proposal": None,
            "refused": (
                f"The strongest suspect ({owner['track']}) emphasises the band by "
                f"only {owner['emphasis']*100:.1f} points, below the {MIN_EMPHASIS*100:.0f} "
                "needed to act. The band is spread across the mix; an EQ move on one "
                "track is the wrong instrument."
            ),
        }

    tree = load_als(Path(als_path))
    root = tree.getroot()

    track = next(
        (
            t for t in root.findall(".//LiveSet/Tracks/AudioTrack")
            if (t.find(".//Name/EffectiveName") is not None
                and t.find(".//Name/EffectiveName").get("Value") == owner["track"])
        ),
        None,
    )
    if track is None:
        return {"proposal": None, "refused": f"Track {owner['track']!r} not found in the set."}

    eq = track.find(".//DeviceChain/DeviceChain/Devices/Eq8")
    if eq is None:
        return {
            "proposal": None,
            "refused": (
                f"{owner['track']} has no EQ Eight. Adding a device is a mix decision, "
                "not a correction; add one by hand and run this again."
            ),
        }

    candidates = []
    for index in range(8):
        params = _band_params(eq, index)
        if params is None or _manual(params, "IsOn") != "true":
            continue
        try:
            mode = int(float(_manual(params, "Mode") or -1))
            freq = float(_manual(params, "Freq") or 0.0)
            gain = float(_manual(params, "Gain") or 0.0)
        except (TypeError, ValueError):
            continue
        if mode not in GAIN_BEARING_MODES:
            continue
        if not (low_hz <= freq <= high_hz):
            continue
        candidates.append({"index": index, "mode": mode, "freq": freq, "gain": gain})

    if not candidates:
        return {
            "proposal": None,
            "refused": (
                f"{owner['track']} has no enabled, gain-bearing EQ band between "
                f"{low_hz:.0f} and {high_hz:.0f} Hz. Re-purposing a band the producer "
                "tuned for something else is not a correction."
            ),
        }

    # A bell only moves the band it is on; a shelf takes everything above or
    # below it, so it is the second choice even when it sits closer.
    candidates.sort(key=lambda c: (c["mode"] != BELL_MODE, abs(c["gain"])))
    chosen = candidates[0]

    ceiling = reachable_ceiling_db(owner["band_share"])
    direction = -1.0 if excess_db > 0 else 1.0
    step = direction * min(max_step_db, abs(excess_db))

    proposed = chosen["gain"] + step
    warnings: List[str] = []
    if abs(proposed) > EQ8_GAIN_LIMIT_DB:
        proposed = math.copysign(EQ8_GAIN_LIMIT_DB, proposed)
        step = proposed - chosen["gain"]
        warnings.append(f"Clamped to the EQ Eight limit of {EQ8_GAIN_LIMIT_DB:g} dB.")

    if abs(excess_db) > ceiling:
        warnings.append(
            f"The finding asks for {abs(excess_db):.1f} dB but this track can move the "
            f"mixdown band by at most {ceiling:.1f} dB even muted, because it holds "
            f"{owner['band_share']*100:.1f}% of the band. The rest has to come from "
            "elsewhere, or the finding is not an EQ problem."
        )
    if owner.get("chain_alters_band"):
        warnings.append(
            "This track's share was measured from its source audio; its own chain "
            f"({', '.join(owner.get('devices') or []) or 'none'}) already changes this band, "
            "so the share is a floor."
        )
    spills = chosen["mode"] in SHELF_MODES
    if spills:
        warnings.append(
            f"Band {chosen['index']} is a shelf at {chosen['freq']:.0f} Hz, so the move "
            f"also affects frequencies outside {low_hz:.0f}-{high_hz:.0f} Hz."
        )

    move = BandMove(
        track=owner["track"],
        device="Eq8",
        band_index=chosen["index"],
        band_freq_hz=chosen["freq"],
        band_mode=chosen["mode"],
        current_gain_db=chosen["gain"],
        proposed_gain_db=round(proposed, 3),
        step_db=round(step, 3),
        reason=(
            f"{owner['track']} holds {owner['band_share']*100:.1f}% of "
            f"{low_hz:.0f}-{high_hz:.0f} Hz while being {owner['broadband_share']*100:.1f}% "
            f"of broadband energy (emphasis {owner['emphasis']*100:+.1f} points)."
        ),
        predicted_mixdown_shift_db=round(predicted_shift_db(owner["band_share"], step), 3),
        ceiling_db=round(ceiling, 3),
        finding_excess_db=excess_db,
        owner_band_share=owner["band_share"],
        spills_outside_band=spills,
        warnings=warnings,
    )
    return {"proposal": move.to_dict(), "refused": None}


def apply_band_move(
    als_path: str | Path, proposal: Dict[str, Any], *, dry_run: bool = True
) -> Dict[str, Any]:
    """Writes the move into the .als. Dry run by default.

    On apply: a timestamped backup first, an atomic save, then the file is
    reloaded from disk and the written value compared. A save that cannot be
    read back as the intended value is reported as a failure, not a success.
    """
    als_path = Path(als_path)
    result: Dict[str, Any] = {
        "dry_run": dry_run,
        "applied": False,
        "backup_path": None,
        "verified": None,
        "target": {
            "track": proposal["track"],
            "band_index": proposal["band_index"],
            "from_db": proposal["current_gain_db"],
            "to_db": proposal["proposed_gain_db"],
        },
    }

    tree = load_als(als_path)
    root = tree.getroot()
    track = next(
        (
            t for t in root.findall(".//LiveSet/Tracks/AudioTrack")
            if (t.find(".//Name/EffectiveName") is not None
                and t.find(".//Name/EffectiveName").get("Value") == proposal["track"])
        ),
        None,
    )
    if track is None:
        result["error"] = f"Track {proposal['track']!r} is no longer in the set."
        return result

    eq = track.find(".//DeviceChain/DeviceChain/Devices/Eq8")
    params = _band_params(eq, proposal["band_index"]) if eq is not None else None
    node = params.find("Gain/Manual") if params is not None else None
    if node is None:
        result["error"] = "The EQ band named in the proposal is no longer there."
        return result

    current = float(node.get("Value"))
    if abs(current - proposal["current_gain_db"]) > 1e-6:
        result["error"] = (
            f"The band has moved since the proposal was made: it reads {current} dB, "
            f"the proposal was written against {proposal['current_gain_db']} dB. "
            "Re-measure rather than overwriting someone's change."
        )
        return result

    if dry_run:
        return result

    backup = als_path.with_suffix(f".loom_backup_{int(time.time())}.als")
    shutil.copy2(als_path, backup)
    result["backup_path"] = str(backup)

    node.set("Value", str(proposal["proposed_gain_db"]))
    save_als_atomic(tree, als_path)

    reloaded = load_als(als_path).getroot()
    check_track = next(
        (
            t for t in reloaded.findall(".//LiveSet/Tracks/AudioTrack")
            if (t.find(".//Name/EffectiveName") is not None
                and t.find(".//Name/EffectiveName").get("Value") == proposal["track"])
        ),
        None,
    )
    check_eq = check_track.find(".//DeviceChain/DeviceChain/Devices/Eq8") if check_track is not None else None
    check_params = _band_params(check_eq, proposal["band_index"]) if check_eq is not None else None
    written = _manual(check_params, "Gain")

    result["applied"] = True
    result["verified"] = (
        written is not None
        and abs(float(written) - proposal["proposed_gain_db"]) < 1e-6
    )
    result["read_back_db"] = float(written) if written is not None else None
    if not result["verified"]:
        result["error"] = (
            "The saved file does not read back as the intended value. "
            f"The backup at {backup} is the file before this write."
        )
    return result


def live_command_payload(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """The same move addressed at a running Live instead of the file.

    Returned rather than sent: the caller decides whether Live is the right
    target, and `live_state` is what confirms the open set is this project.
    """
    return {
        "tool": "live_command",
        "action": "set_device_parameter",
        "track_name": proposal["track"],
        "device": proposal["device"],
        "parameter": f"{proposal['band_index'] + 1} Gain A",
        "value": proposal["proposed_gain_db"],
        "unit": "dB",
        "precondition": (
            "live_state must report the open set as this project, and the named "
            "device must still be the one measured."
        ),
    }
