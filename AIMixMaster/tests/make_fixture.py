#!/usr/bin/env python3
"""Build the committed .als these tests measure against.

Before this, the gain staging and drum buss tests read a pointer file naming
whichever project the author happened to be working on, and a project on a path
no other machine has. They passed only while that project sat where it was left,
and all seven went red the day it moved -- silently, because the suite never ran
them.

A fixture is a file the tests own. It is small, it is committed, and it holds one
of each structure the reports have to handle: an audio track, a MIDI track, a
group with a child, a return, and the main track, with faders at unity and at a
known offset and a Utility whose gain encoding must not be assumed to be dB.
"""
from __future__ import annotations

import gzip
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OUT = FIXTURES / "gain_staging.als"
# The drum buss pair: the set before the build, and the set after it. The builder
# clones an existing native chain rather than synthesising one, so the fixture
# has to carry a real source chain (KICK BUSS) and an empty target (DRUM BUSS).
BUSS_BEFORE = FIXTURES / "drum_buss_before.als"
BUSS_AFTER = FIXTURES / "drum_buss_after.als"

TRACK = """    <{tag} Id="{tid}">
      <Name><EffectiveName Value="{name}" /><UserName Value="{name}" /></Name>
      <TrackGroupId Value="{group}" />
      <DeviceChain>
        <AudioOutputRouting><Target Value="{target}" /><UpperDisplayString Value="Main" /></AudioOutputRouting>
        <Mixer>
          <Volume><Manual Value="{volume}" /><MidiControllerRange><Min Value="0.0003162277" /><Max Value="1.99526238" /></MidiControllerRange><AutomationTarget Id="{tid}001" /></Volume>
          <Pan><Manual Value="0" /><MidiControllerRange><Min Value="-1" /><Max Value="1" /></MidiControllerRange><AutomationTarget Id="{tid}002" /></Pan>
          <Sends>{sends}</Sends>
        </Mixer>
        <DeviceChain><Devices>{devices}</Devices></DeviceChain>
      </DeviceChain>
    </{tag}>
"""

SEND = ('<TrackSendHolder Id="0"><Send><Manual Value="{value}" />'
        '<MidiControllerRange><Min Value="0.0003162277" /><Max Value="1.99526238" /></MidiControllerRange>'
        '<AutomationTarget Id="9001" /></Send></TrackSendHolder>')

# Live writes the Utility device under the tag StereoGain, not its display name.
# The reports key off the tag, so a fixture using the friendly name exercises a
# different branch and proves nothing about the device the tests are about.
UTILITY = ('<StereoGain Id="70"><On><Manual Value="true" /></On>'
           '<Gain><Manual Value="0.5011872053" />'
           '<MidiControllerRange><Min Value="0.0003162277" /><Max Value="1.99526238" /></MidiControllerRange>'
           '<AutomationTarget Id="70001" /></Gain></StereoGain>')


def _param(path: str, value: str, target_id: int) -> str:
    return (f'<{path}><Manual Value="{value}" />'
            f'<MidiControllerRange><Min Value="0" /><Max Value="1" /></MidiControllerRange>'
            f'<AutomationTarget Id="{target_id}" /></{path}>')


def _eq8() -> str:
    """Eight bands, each with the two parameter sets the report reads.

    The conservative parameter pass walks every band and both parameter sets, so
    a stub with one band would leave most of that code untested while looking
    green.
    """
    bands = "".join(
        f'<Bands.{index}>'
        + "".join(f'<{side}><IsOn><Manual Value="true" /></IsOn></{side}>'
                  for side in ("ParameterA", "ParameterB"))
        + f'</Bands.{index}>'
        for index in range(8)
    )
    return ('<Eq8 Id="200"><On><Manual Value="true" /></On>'
            + _param("GlobalGain", "0.5", 2001) + bands + '</Eq8>')


def _glue() -> str:
    starting = {"Threshold": "-4.0", "Range": "1", "Makeup": "1.0", "Attack": "3",
                "Ratio": "1", "Release": "3", "DryWet": "0.8", "PeakClipIn": "true",
                "SideChain/OnOff": "true", "SideChainEq/On": "true"}
    body = ""
    for index, (path, value) in enumerate(starting.items()):
        if "/" in path:
            outer, inner = path.split("/")
            body += f'<{outer}><{inner}><Manual Value="{value}" /></{inner}></{outer}>'
        else:
            body += f'<{path}><Manual Value="{value}" /></{path}>'
    return f'<GlueCompressor Id="201"><On><Manual Value="true" /></On>{body}</GlueCompressor>'


def _utility() -> str:
    starting = {"StereoWidth": "1.4", "Mono": "true", "BassMono": "false",
                "BassMonoFrequency": "90.0", "Balance": "0.2", "Gain": "0.5",
                "Mute": "true"}
    body = "".join(f'<{path}><Manual Value="{value}" /></{path}>'
                   for path, value in starting.items())
    return f'<StereoGain Id="202"><On><Manual Value="true" /></On>{body}</StereoGain>'


NATIVE_CHAIN = _eq8() + _glue() + _utility()


def buss_set(built: bool) -> str:
    """A set with a source chain to clone from and a target to clone into."""
    tracks = [
        TRACK.format(tag="GroupTrack", tid=20, name="KICK BUSS", group="-1",
                     target="AudioOut/Main", volume="1", sends="", devices=NATIVE_CHAIN),
        TRACK.format(tag="GroupTrack", tid=21, name="DRUM BUSS", group="-1",
                     target="AudioOut/Main", volume="1", sends="",
                     devices=NATIVE_CHAIN if built else ""),
        # The template export walks all four busses a boom bap set carries.
        TRACK.format(tag="GroupTrack", tid=24, name="SNARE BUSS", group="-1",
                     target="AudioOut/Main", volume="1", sends="", devices=NATIVE_CHAIN),
        TRACK.format(tag="GroupTrack", tid=25, name="PERC BUSS", group="-1",
                     target="AudioOut/Main", volume="1", sends="", devices=NATIVE_CHAIN),
        # template_exporter.REQUIRED_ROLES names these two as well.
        TRACK.format(tag="AudioTrack", tid=26, name="LIVE BASS", group="-1",
                     target="AudioOut/Main", volume="1", sends="", devices=NATIVE_CHAIN),
        TRACK.format(tag="AudioTrack", tid=27, name="# New Old Sub", group="-1",
                     target="AudioOut/Main", volume="1", sends="", devices=NATIVE_CHAIN),
        TRACK.format(tag="AudioTrack", tid=22, name="KICK", group="21",
                     target="AudioOut/GroupTrack", volume="1", sends="", devices=""),
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Ableton MajorVersion="5" MinorVersion="12.0_12120" Creator="Ableton Live 12.1"'
        ' Revision="fixture">\n  <LiveSet>\n    <NextPointeeId Value="30000" />\n    <Tracks>\n'
        + "".join(tracks) +
        '    </Tracks>\n'
        + TRACK.format(tag="MainTrack", tid=23, name="Main", group="-1",
                       target="AudioOut/External", volume="1", sends="", devices="")
        + '    <Transport><CurrentTime Value="0" /></Transport>\n  </LiveSet>\n</Ableton>\n'
    )


def build() -> str:
    tracks = [
        TRACK.format(tag="AudioTrack", tid=8, name="DRUM BUSS", group="10",
                     target="AudioOut/GroupTrack", volume="1", sends=SEND.format(value="0.3"),
                     devices=UTILITY),
        TRACK.format(tag="MidiTrack", tid=9, name="BASS", group="-1",
                     target="AudioOut/Main", volume="0.5011872053", sends=SEND.format(value="0"),
                     devices=""),
        TRACK.format(tag="GroupTrack", tid=10, name="DRUMS", group="-1",
                     target="AudioOut/Main", volume="1", sends="", devices=""),
        TRACK.format(tag="ReturnTrack", tid=11, name="A-Reverb", group="-1",
                     target="AudioOut/Main", volume="1", sends="", devices=""),
    ]
    main = TRACK.format(tag="MainTrack", tid=12, name="Main", group="-1",
                        target="AudioOut/External", volume="1", sends="", devices="")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Ableton MajorVersion="5" MinorVersion="12.0_12120" Creator="Ableton Live 12.1"'
        ' Revision="fixture">\n  <LiveSet>\n    <Tracks>\n'
        + "".join(tracks) +
        "    </Tracks>\n" + main +
        '    <MainTrack />\n'
        '    <Transport><CurrentTime Value="0" /></Transport>\n'
        '  </LiveSet>\n</Ableton>\n'
    )


def _write(path: Path, text: str) -> None:
    with gzip.open(path, "wb") as stream:
        stream.write(text.encode("utf-8"))
    print(f"{path.name}  ({path.stat().st_size} bytes)")


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    _write(OUT, build())
    _write(BUSS_BEFORE, buss_set(built=False))
    # The built set is produced by running the real builder, not by copying the
    # chain by hand: the builder clones with fresh ids, and a hand-copied fixture
    # would carry duplicates that the verifier is there to catch.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from aimixmaster.als_io import load_als, save_als_atomic
    from aimixmaster.buss_builder import build_drum_buss

    tree = load_als(BUSS_BEFORE)
    result = build_drum_buss(tree.getroot())
    save_als_atomic(tree, BUSS_AFTER)
    print(f"{BUSS_AFTER.name}  built={result.inserted_tags}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
