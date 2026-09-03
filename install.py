#!/usr/bin/env python3
"""Install Loom in one command.

Registers the MCP server with every client found on this machine, copies the
Live Remote Scripts into Ableton's User Library, and builds the catalogues from
this machine's own Ableton install.

Nothing here needs a virtual environment or a package install -- it runs on the
Python that ships with macOS.

  python3 install.py            install
  python3 install.py --check    report what would happen, change nothing
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "mcp_server" / "server.py"

# Every MCP client that keeps its servers in a JSON file, and the key the
# server list lives under.
CLIENTS = [
    ("Claude Desktop", Path.home() / "Library/Application Support/Claude/claude_desktop_config.json", "mcpServers"),
    ("Antigravity", Path.home() / ".gemini/config/mcp_config.json", "mcpServers"),
    ("Claude Code", Path.home() / ".claude.json", "mcpServers"),
]

REMOTE_SCRIPTS = ["Loom", "ArrangementGPSBuilder"]
ABLETON_REMOTE_DIR = Path.home() / "Music/Ableton/User Library/Remote Scripts"


def entry() -> dict:
    # Plain python3 on purpose: 27 of the 31 tools have no third-party
    # dependency, so there is no environment to point at.
    return {"command": "python3", "args": [str(SERVER)]}


def register(check: bool) -> list[str]:
    notes = []
    for name, config_path, key in CLIENTS:
        if not config_path.exists():
            notes.append(f"  skipped  {name:<16} not installed ({config_path.name} absent)")
            continue
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            notes.append(f"  FAILED   {name:<16} config is not valid JSON: {error}")
            continue

        servers = data.setdefault(key, {})
        if servers.get("loom") == entry():
            notes.append(f"  already  {name:<16} registered and up to date")
            continue

        if check:
            notes.append(f"  would    {name:<16} add or update the 'loom' entry")
            continue

        backup = config_path.with_suffix(config_path.suffix + f".loom-backup-{int(time.time())}")
        shutil.copy2(config_path, backup)
        servers["loom"] = entry()
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        notes.append(f"  ok       {name:<16} registered (backup: {backup.name})")
    return notes


def install_remote_scripts(check: bool) -> list[str]:
    notes = []
    if not ABLETON_REMOTE_DIR.parent.exists():
        return ["  skipped  Ableton User Library not found; Live integration not installed"]
    for script in REMOTE_SCRIPTS:
        source = ROOT / "AbletonScripts" / script
        if not source.is_dir():
            notes.append(f"  FAILED   {script:<22} missing from this copy of Loom")
            continue
        target = ABLETON_REMOTE_DIR / script
        if check:
            notes.append(f"  would    {script:<22} copy into {ABLETON_REMOTE_DIR}")
            continue
        target.mkdir(parents=True, exist_ok=True)
        copied = 0
        for item in source.glob("*.py"):
            shutil.copy2(item, target / item.name)
            copied += 1
        shutil.rmtree(target / "__pycache__", ignore_errors=True)
        notes.append(f"  ok       {script:<22} {copied} files -> {target}")
    return notes


LIVE_PREFS = Path.home() / "Library/Preferences/Ableton"


def control_surface_status() -> tuple[bool, str]:
    """Has Live actually loaded the Loom control surface?

    Selecting a Control Surface cannot be automated. Live stores that choice in
    Preferences.cfg, an undocumented binary format that differs between
    versions; writing it would risk the user's whole preference file for the
    sake of one dropdown. What can be automated is the check: Live logs every
    remote script it loads, and the surface announces itself on load.
    """
    logs = sorted(LIVE_PREFS.glob("Live */Log.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return False, "no Live log found -- has Ableton Live been run on this machine?"
    newest = logs[0]
    try:
        text = newest.read_text(encoding="utf-8", errors="ignore")
    except OSError as error:
        return False, f"could not read {newest}: {error}"
    version = newest.parent.name
    if "Loom control surface loaded" in text:
        return True, f"loaded, according to {version}'s log"
    if "SenseiRemote" in text:
        return False, f"{version} last loaded the old SenseiRemote surface -- re-select Loom"
    return False, f"not loaded yet, according to {version}'s log"


def scan(check: bool) -> int:
    # Flush first: the child writes straight to the terminal, so without this
    # its output lands above the parent's buffered header.
    sys.stdout.flush()
    argv = [sys.executable, str(ROOT / "scripts" / "setup_scan.py")]
    if check:
        argv.append("--check")
    return subprocess.call(argv)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="report only, change nothing")
    args = parser.parse_args()

    print("Loom" + (" -- check only, nothing will be changed" if args.check else ""))
    print("=" * 62)
    print("\n1. MCP clients")
    for line in register(args.check):
        print(line)

    print("\n2. Ableton Live integration")
    for line in install_remote_scripts(args.check):
        print(line)

    print("\n3. Catalogues from this machine's Ableton library")
    code = scan(args.check)

    print("\n4. Live Control Surface")
    loaded, detail = control_surface_status()
    print("  %s  %s" % ("ok      " if loaded else "TODO    ", detail))
    if not loaded:
        print("     Live cannot be told to select a Control Surface from outside -- that")
        print("     choice lives in an undocumented binary preferences file. Do it once:")
        print("     Live -> Settings -> Link/MIDI -> Control Surface -> Loom")
        print("     Then run 'python3 install.py --check' to confirm it took.")

    print("\n" + "=" * 62)
    if args.check:
        print("Nothing was changed. Run without --check to install.")
        return code
    print("Installed. Restart your MCP client so it picks up the new server.")
    if not loaded:
        print("Then restart Ableton Live and select Loom as a Control Surface (step 4).")
    return code


if __name__ == "__main__":
    sys.exit(main())
