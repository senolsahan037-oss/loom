#!/usr/bin/env python3
"""Live terminal ASCII mixer: tails a MixConsoleLive2 session JSONL as it is written.

Usage:
    python3 gain_stage.py "<project>.als" --prepare-live-measurement
    python3 live_ascii_mixer.py reports/live_measurement_<stamp>/live_measurement_manifest.json

Then reload MixConsoleLive2 in Live and press play. Readings are raw, uncalibrated
meter values (see aimixmaster/live_meter_calibration.py) shown as 20*log10(raw) for
a readable live display, not verified dBFS.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterator

RAW_SILENCE_THRESHOLD = 0.000001
DISPLAY_RANGE_DB = (-60.0, 0.0)
POLL_SECONDS = 0.05
REDRAW_SECONDS = 0.1
GREEN_MAX_DB = -12.0
YELLOW_MAX_DB = -3.0

RESET = "\x1b[0m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
CLEAR_HOME = "\x1b[2J\x1b[H"


def raw_to_db(value: float) -> float:
    if value <= RAW_SILENCE_THRESHOLD:
        return float("-inf")
    return 20.0 * math.log10(value)


def follow(path: Path) -> Iterator[str]:
    while not path.exists():
        time.sleep(POLL_SECONDS)
    with path.open("r", encoding="utf-8") as stream:
        while True:
            position = stream.tell()
            line = stream.readline()
            if not line or not line.endswith("\n"):
                stream.seek(position)
                time.sleep(POLL_SECONDS)
                continue
            yield line


def bar(db: float, peak_db: float, width: int) -> str:
    low, high = DISPLAY_RANGE_DB

    def position(value: float) -> int:
        if value == float("-inf"):
            return 0
        return round(width * max(0.0, min(1.0, (value - low) / (high - low))))

    filled = position(db)
    chars = ["█" if i < filled else " " for i in range(width)]
    peak_index = position(peak_db) - 1
    if 0 <= peak_index < width and chars[peak_index] == " ":
        chars[peak_index] = "│"
    color = GREEN if db <= GREEN_MAX_DB else YELLOW if db <= YELLOW_MAX_DB else RED
    return color + "".join(chars) + RESET


def db_text(db: float) -> str:
    return "  -inf" if db == float("-inf") else f"{db:6.1f}"


def render(tracks: list[dict[str, Any]], state: dict[int, dict[str, float]], status: str, session_id: str) -> str:
    columns = shutil.get_terminal_size((100, 24)).columns
    name_width = max((len(t["live_track_name"]) for t in tracks), default=10)
    name_width = min(name_width, 28)
    bar_width = max(10, columns - name_width - 28)
    lines = [
        f"{BOLD}LIVE ASCII MIXER{RESET}  session {session_id[:8]}  {status}",
        f"{DIM}raw uncalibrated meter, 20*log10(raw) — not verified dBFS{RESET}",
        "",
    ]
    for track in tracks:
        index = track["track_index"]
        name = track["live_track_name"][:name_width].ljust(name_width)
        current = state.get(index, {}).get("db", float("-inf"))
        peak = state.get(index, {}).get("peak", float("-inf"))
        if track["track_type"] == "MidiTrack":
            lines.append(f"{DIM}{name} [no meter: MIDI track]{RESET}")
            continue
        lines.append(f"{name} [{bar(current, peak, bar_width)}] {db_text(current)} dB  peak {db_text(peak)}")
    lines.append("")
    lines.append(f"{DIM}Ctrl+C to stop.{RESET}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path, help="live_measurement_manifest.json from --prepare-live-measurement")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    session_id = manifest["session_id"]
    log_path = Path(manifest["log_path"])
    tracks = manifest["included_tracks"]

    state: dict[int, dict[str, float]] = {}
    status = "waiting for session_started"
    last_draw = 0.0

    print(f"Waiting for {log_path} ...", file=sys.stderr)

    try:
        for line in follow(log_path):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("session_id") != session_id:
                continue
            kind = event.get("event")
            if kind == "session_started":
                status = f"{GREEN}● RECORDING{RESET}"
                state.clear()
            elif kind == "session_completed":
                status = f"{DIM}■ STOPPED (completed){RESET}"
            elif kind == "session_aborted":
                status = f"{RED}■ STOPPED (aborted){RESET}"
            elif kind == "measurement_sample":
                index = event.get("track_index")
                left = event.get("meter_left")
                right = event.get("meter_right")
                if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                    continue
                raw = max(float(left), float(right))
                db = raw_to_db(raw)
                entry = state.setdefault(index, {"db": float("-inf"), "peak": float("-inf")})
                entry["db"] = db
                entry["peak"] = max(entry["peak"], db)

            now = time.monotonic()
            if now - last_draw >= REDRAW_SECONDS:
                sys.stdout.write(CLEAR_HOME + render(tracks, state, status, session_id))
                sys.stdout.flush()
                last_draw = now
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
