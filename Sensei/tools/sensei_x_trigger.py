#!/usr/bin/env python3
"""Send one Sensei manual-trigger MIDI pulse for each physical X key press."""

from __future__ import annotations

import argparse
import signal
import sys
import time


MIDI_CHANNEL = 15  # MIDI channel 16 in user-facing 1-based notation.
MIDI_NOTE = 119
NOTE_VELOCITY = 127
NOTE_LENGTH_SECONDS = 0.02
DEFAULT_IAC_PORT = "Sensei X Trigger Bus 1"
MACOS_X_KEYCODE = 7


class XKeyGate:
    """Suppress macOS autorepeat and repeated down events until key-up."""

    def __init__(self):
        self.pressed = False

    def key_down(self, is_repeat=False):
        if self.pressed or is_repeat:
            return False
        self.pressed = True
        return True

    def key_up(self):
        self.pressed = False


def find_iac_output(mido_module, requested_name: str):
    names = list(mido_module.get_output_names())
    exact = next((name for name in names if name == requested_name), None)
    if exact:
        return exact
    matches = [name for name in names if requested_name.lower() in name.lower()]
    if len(matches) == 1:
        return matches[0]
    available = ", ".join(names) if names else "none"
    raise RuntimeError(
        "IAC MIDI port '{}' not found. Available outputs: {}".format(
            requested_name, available
        )
    )


def send_trigger(mido_module, output_port):
    output_port.send(mido_module.Message(
        "note_on", channel=MIDI_CHANNEL, note=MIDI_NOTE, velocity=NOTE_VELOCITY
    ))
    time.sleep(NOTE_LENGTH_SECONDS)
    output_port.send(mido_module.Message(
        "note_off", channel=MIDI_CHANNEL, note=MIDI_NOTE, velocity=0
    ))


def run(port_name: str = DEFAULT_IAC_PORT):
    try:
        import mido
        import Quartz
    except ImportError as exc:
        raise RuntimeError("Required macOS MIDI dependency is missing: {}".format(exc))

    resolved_port = find_iac_output(mido, port_name)
    output = mido.open_output(resolved_port)
    gate = XKeyGate()

    def callback(_proxy, event_type, event, _refcon):
        keycode = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode
        )
        if keycode != MACOS_X_KEYCODE:
            return event
        if event_type == Quartz.kCGEventKeyDown:
            is_repeat = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventAutorepeat
            )
            if gate.key_down(bool(is_repeat)):
                send_trigger(mido, output)
                print("[X] MIDI trigger sent", flush=True)
        elif event_type == Quartz.kCGEventKeyUp:
            gate.key_up()
        return event

    mask = (
        Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
    )
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        mask,
        callback,
        None,
    )
    if tap is None:
        output.close()
        raise RuntimeError(
            "Keyboard listener could not start. Allow your terminal/Python in "
            "System Settings > Privacy & Security > Accessibility."
        )

    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    loop = Quartz.CFRunLoopGetCurrent()
    Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)

    def stop(_signum, _frame):
        Quartz.CFRunLoopStop(loop)

    signal.signal(signal.SIGINT, stop)
    print("Listening for X → {} (channel 16, note 119). Ctrl+C to exit.".format(resolved_port))
    try:
        Quartz.CFRunLoopRun()
    finally:
        output.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=DEFAULT_IAC_PORT, help="IAC MIDI output port name")
    args = parser.parse_args(argv)
    try:
        run(args.port)
    except RuntimeError as exc:
        print("Sensei X trigger error: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
