"""Tests for turning an attributed band finding into one EQ move.

The arithmetic tests pin the thing a naive version gets wrong: a mixdown that is
8 dB hot does not mean the owning track is cut by 8 dB, and a track holding part
of a band cannot move the band further than its own share allows.

The write tests run against .als files the test builds, never a real project.
"""

from __future__ import annotations

import gzip
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subverse_mix.mix_intervention import (  # noqa: E402
    GAIN_BEARING_MODES,
    MAX_STEP_DB,
    apply_band_move,
    live_command_payload,
    plan_band_move,
    predicted_shift_db,
    reachable_ceiling_db,
)

BAND = [2227.0, 7072.0]


# ------------------------------------------------------------------ arithmetic


def test_a_track_holding_all_of_a_band_has_no_ceiling():
    assert reachable_ceiling_db(1.0) == math.inf


def test_half_a_band_can_move_it_three_decibels():
    assert abs(reachable_ceiling_db(0.5) - 3.0103) < 1e-3


def test_the_ceiling_falls_as_the_owner_holds_less():
    assert reachable_ceiling_db(0.9) > reachable_ceiling_db(0.5) > reachable_ceiling_db(0.1)


def test_a_cut_does_not_transfer_one_for_one():
    """The whole reason the naive version is wrong."""
    shift = predicted_shift_db(0.742, -1.5)
    assert -1.2 < shift < -0.9, f"a 1.5 dB cut on 74% of a band moved it {shift:.2f} dB"
    assert abs(shift) < 1.5


def test_a_track_that_owns_everything_does_transfer_one_for_one():
    assert abs(predicted_shift_db(1.0, -3.0) - (-3.0)) < 1e-6


def test_no_step_means_no_shift():
    assert abs(predicted_shift_db(0.6, 0.0)) < 1e-9


# ------------------------------------------------------------------- fixtures


def _als(path: Path, track: str, bands, devices=("Eq8",)) -> Path:
    band_xml = "".join(
        f"""<Bands.{i}><ParameterA>
              <IsOn><Manual Value="{'true' if on else 'false'}" /></IsOn>
              <Mode><Manual Value="{mode}" /></Mode>
              <Freq><Manual Value="{freq}" /></Freq>
              <Gain><Manual Value="{gain}" /></Gain>
            </ParameterA></Bands.{i}>"""
        for i, (on, mode, freq, gain) in enumerate(bands)
    )
    device_xml = f"<Eq8>{band_xml}</Eq8>" if "Eq8" in devices else ""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton><LiveSet><Tracks>
  <AudioTrack>
    <Name><EffectiveName Value="{track}" /></Name>
    <DeviceChain>
      <Mixer><Volume><Manual Value="1" /></Volume></Mixer>
      <DeviceChain><Devices>{device_xml}</Devices></DeviceChain>
    </DeviceChain>
  </AudioTrack>
</Tracks>
<MasterTrack><DeviceChain><Mixer><Tempo><Manual Value="120" /></Tempo></Mixer></DeviceChain></MasterTrack>
</LiveSet></Ableton>"""
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(xml)
    return path


def _attribution(track="Snare", band_share=0.742, emphasis=0.655, devices=("Eq8",)):
    return {
        "band_hz": BAND,
        "ranking": [{
            "track": track,
            "band_share": band_share,
            "broadband_share": round(band_share - emphasis, 4),
            "emphasis": emphasis,
            "devices": list(devices),
            "chain_alters_band": "Eq8" in devices,
        }],
    }


# -------------------------------------------------------------------- refusals


def test_a_band_spread_across_the_mix_is_refused(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(True, 3, 4000, 0.0)])
    out = plan_band_move(als, attribution=_attribution(emphasis=0.05), excess_db=8.0)
    assert out["proposal"] is None
    assert "wrong instrument" in out["refused"]


def test_a_track_without_an_eq_is_refused_not_given_one(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [], devices=())
    out = plan_band_move(als, attribution=_attribution(), excess_db=8.0)
    assert out["proposal"] is None
    assert "no EQ Eight" in out["refused"]


def test_a_high_cut_band_is_not_used_as_a_gain_control(tmp_path):
    # Mode 6 measured at 1.4% gain usage across 120 sets: gain does nothing.
    als = _als(tmp_path / "s.als", "Snare", [(True, 6, 4000, 0.0)])
    out = plan_band_move(als, attribution=_attribution(), excess_db=8.0)
    assert out["proposal"] is None
    assert 6 not in GAIN_BEARING_MODES


def test_a_band_outside_the_finding_range_is_not_used(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(True, 3, 80, 0.0)])
    out = plan_band_move(als, attribution=_attribution(), excess_db=8.0)
    assert out["proposal"] is None
    assert "no enabled, gain-bearing EQ band" in out["refused"]


def test_a_disabled_band_is_not_switched_on(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(False, 3, 4000, 0.0)])
    assert plan_band_move(als, attribution=_attribution(), excess_db=8.0)["proposal"] is None


# -------------------------------------------------------------------- planning


def test_a_bell_is_preferred_over_a_shelf(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [
        (True, 5, 3520, -2.0),   # shelf, spills
        (True, 3, 4000, 0.0),    # bell, precise
    ])
    p = plan_band_move(als, attribution=_attribution(), excess_db=8.0)["proposal"]
    assert p["band_mode"] == 3
    assert p["spills_outside_band"] is False


def test_a_shelf_is_used_when_it_is_all_there_is_and_the_spill_is_declared(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(True, 5, 3520, -2.0)])
    p = plan_band_move(als, attribution=_attribution(), excess_db=8.0)["proposal"]
    assert p["band_mode"] == 5
    assert p["spills_outside_band"] is True
    assert any("shelf" in w for w in p["warnings"])


def test_the_step_is_capped_and_cuts_when_the_band_is_hot(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(True, 3, 4000, -2.0)])
    p = plan_band_move(als, attribution=_attribution(), excess_db=8.344)["proposal"]
    assert p["step_db"] == -MAX_STEP_DB
    assert p["proposed_gain_db"] == -3.5


def test_a_recessed_band_is_boosted_not_cut(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(True, 3, 4000, 0.0)])
    p = plan_band_move(als, attribution=_attribution(), excess_db=-6.3)["proposal"]
    assert p["step_db"] > 0


def test_a_small_excess_is_not_rounded_up_to_the_cap(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(True, 3, 4000, 0.0)])
    p = plan_band_move(als, attribution=_attribution(), excess_db=0.4)["proposal"]
    assert abs(p["step_db"] + 0.4) < 1e-9


def test_an_unreachable_finding_says_so(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(True, 3, 4000, 0.0)])
    p = plan_band_move(als, attribution=_attribution(band_share=0.742), excess_db=8.344)["proposal"]
    assert p["ceiling_db"] < 8.344
    assert any("even muted" in w for w in p["warnings"])


def test_a_reachable_finding_raises_no_ceiling_warning(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(True, 3, 4000, 0.0)])
    p = plan_band_move(als, attribution=_attribution(band_share=0.95), excess_db=2.0)["proposal"]
    assert not any("even muted" in w for w in p["warnings"])


# ---------------------------------------------------------------------- writes


def test_a_dry_run_changes_nothing(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(True, 3, 4000, -2.0)])
    before = als.read_bytes()
    p = plan_band_move(als, attribution=_attribution(), excess_db=8.0)["proposal"]
    result = apply_band_move(als, p, dry_run=True)
    assert result["applied"] is False
    assert als.read_bytes() == before


def test_applying_writes_backs_up_and_reads_back(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(True, 3, 4000, -2.0)])
    p = plan_band_move(als, attribution=_attribution(), excess_db=8.0)["proposal"]
    result = apply_band_move(als, p, dry_run=False)

    assert result["applied"] is True
    assert result["verified"] is True
    assert abs(result["read_back_db"] - (-3.5)) < 1e-6
    assert Path(result["backup_path"]).exists()

    # The change is really in the file, not just in the return value.
    again = plan_band_move(als, attribution=_attribution(), excess_db=8.0)["proposal"]
    assert abs(again["current_gain_db"] - (-3.5)) < 1e-6


def test_a_band_that_moved_since_the_proposal_is_not_overwritten(tmp_path):
    als = _als(tmp_path / "s.als", "Snare", [(True, 3, 4000, -2.0)])
    p = plan_band_move(als, attribution=_attribution(), excess_db=8.0)["proposal"]
    # Someone edits the same band in Live before the proposal is applied.
    _als(als, "Snare", [(True, 3, 4000, -5.0)])
    result = apply_band_move(als, p, dry_run=False)
    assert result["applied"] is False
    assert "moved since the proposal" in result["error"]


def test_the_live_payload_names_the_same_move(tmp_path):
    # Four bands, only the fourth eligible — so an off-by-one in the parameter
    # name would move a band the proposal never looked at.
    als = _als(tmp_path / "s.als", "Snare", [
        (True, 6, 3000, 0.0),    # high cut: no gain
        (False, 3, 4000, 0.0),   # disabled
        (True, 3, 80, 0.0),      # outside the finding's range
        (True, 3, 4000, -2.0),   # the one
    ])
    p = plan_band_move(als, attribution=_attribution(), excess_db=8.0)["proposal"]
    assert p["band_index"] == 3

    payload = live_command_payload(p)
    assert payload["track_name"] == "Snare"
    assert payload["value"] == p["proposed_gain_db"]
    # Eq8 bands are numbered from 1 in Live's own parameter names.
    assert payload["parameter"] == "4 Gain A"
    assert "live_state" in payload["precondition"]
