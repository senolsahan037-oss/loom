#!/usr/bin/env python3
# © 2026 Şenol Şahan / SubverseLab · Loom · https://subverselab.com/loom
"""Install Loom in one command.

Registers the MCP server with every client found on this machine, prepares
the Loom extension package (.ablx) for Live 12.4 beta, and builds the
catalogues from this machine's own Ableton install.

Live integration is the extension and nothing else: the MCP talks to the
extension's own file bridge. Adding the .ablx to Live is the one step Live
does not let a script do; this script builds the package, checks whether the
extension is installed and running, checks that the running one speaks the
protocol this checkout's MCP speaks, and says exactly what is left.

Nothing here needs a virtual environment or a package install -- it runs on the
Python that ships with macOS.

  python3 install.py                    install
  python3 install.py --check            report what would happen, change nothing
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "mcp_server" / "server.py"
sys.path.insert(0, str(ROOT / "mcp_server"))
import bridge_client  # noqa: E402  (the one place the protocol version is defined on the MCP side)

# Every MCP client that keeps its servers in a JSON file, and the key the
# server list lives under.
CLIENTS = [
    ("Claude Desktop", Path.home() / "Library/Application Support/Claude/claude_desktop_config.json", "mcpServers"),
    ("Antigravity", Path.home() / ".gemini/config/mcp_config.json", "mcpServers"),
    ("Claude Code", Path.home() / ".claude.json", "mcpServers"),
]


def entry() -> dict:
    # Plain python3 on purpose: the server has no third-party dependency, so
    # there is no environment to point at.
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


EXTENSION_DIR = ROOT / "extension"
EXTENSION_PACKAGE = EXTENSION_DIR / "dist" / "loom.ablx"
EXTENSION_MANIFEST = EXTENSION_DIR / "manifest.json"
EXTENSION_ID = bridge_client.LOOM_EXTENSION_IDS[0]
EXTENSION_DATA = bridge_client.EXTENSIONS_DATA_DIR / EXTENSION_ID


def source_versions() -> dict:
    """The versions this checkout would install: one package version
    (manifest.json, checked against package.json by the build) and the
    bridge protocol the MCP speaks."""
    manifest = json.loads(EXTENSION_MANIFEST.read_text(encoding="utf-8"))
    package = json.loads((EXTENSION_DIR / "package.json").read_text(encoding="utf-8"))
    return {"manifest_version": manifest.get("version"), "package_version": package.get("version"),
            "sdk_api_version": manifest.get("minimumApiVersion"),
            "bridge_protocol": bridge_client.SUPPORTED_BRIDGE_PROTOCOLS[0]}


def package_extension(check: bool) -> list[str]:
    """Build the .ablx from source when the toolchain is here (node + the
    Ableton SDK, which is not redistributable and must be vendored locally)."""
    notes = []
    versions = source_versions()
    if versions["manifest_version"] != versions["package_version"]:
        notes.append(f"  FAILED   versions               manifest.json {versions['manifest_version']} != package.json {versions['package_version']}; fix before packaging")
        return notes
    notes.append(f"  ok       versions               extension {versions['manifest_version']}, SDK API {versions['sdk_api_version']}, bridge protocol {versions['bridge_protocol']}")
    sdk = EXTENSION_DIR / "node_modules" / "@ableton-extensions" / "sdk"
    stale = EXTENSION_PACKAGE.exists() and EXTENSION_PACKAGE.stat().st_mtime < max(
        p.stat().st_mtime for p in [EXTENSION_MANIFEST, *(EXTENSION_DIR / "src").glob("*.ts")])
    if EXTENSION_PACKAGE.exists() and not stale:
        notes.append(f"  ok       package                {EXTENSION_PACKAGE}")
        return notes
    if not shutil.which("npm") or not sdk.exists():
        notes.append("  TODO     package                " + ("the .ablx is older than the source" if stale else "no .ablx built yet")
                     + ", and the toolchain to build it is missing (needs npm and the Ableton Extensions SDK vendored under "
                     "extension/vendor)")
        return notes
    if check:
        notes.append("  would    package                run 'npm run package' in extension/" + (" (the .ablx is older than the source)" if stale else ""))
        return notes
    sys.stdout.flush()
    result = subprocess.run(["npm", "run", "package"], cwd=str(EXTENSION_DIR), capture_output=True, text=True)
    if result.returncode == 0 and EXTENSION_PACKAGE.exists():
        notes.append(f"  ok       package                built {EXTENSION_PACKAGE}")
    else:
        notes.append(f"  FAILED   package                npm run package: {(result.stderr or result.stdout).strip()[-300:]}")
    return notes


def extension_status() -> tuple[bool, str]:
    """Is the Loom extension installed in Live, is its bridge alive, and does
    the running one speak this checkout's protocol? Installation is Live's
    own step (the .ablx is added inside Live); what can be checked is the
    storage directory Live creates for it and the state its bridge publishes."""
    if not EXTENSION_DATA.exists():
        legacy = bridge_client.legacy_bridge_roots()
        if legacy:
            return False, ("not installed yet; an earlier package is (" + ", ".join(i for i, _ in legacy) + ") -- add loom.ablx, remove the "
                           "old extension from Live's Extensions, restart Live, then carry the old journal over with live_command op=journal_import")
        return False, "not installed in Live yet (no Extensions Data directory for it)"
    target = bridge_client.target_from(EXTENSION_DATA / "bridge", "loom_extension")
    if target.state is None:
        if target.state_file.exists():
            return False, "installed, but its state file is unreadable"
        return False, "installed, but its bridge has never published state -- restart Live with the extension enabled"
    versions = source_versions()
    running = f"{target.surface_version} / {target.bridge_protocol or 'no protocol'}"
    wanted = f"loom-extension/{versions['manifest_version']} / {versions['bridge_protocol']}"
    report = target.protocol_report()
    if target.age is not None and target.age >= bridge_client.STATE_FRESH_SECONDS:
        where = f"installed; bridge last seen {target.age / 60:.0f} min ago ({running}) -- Live is probably closed"
    else:
        where = f"running ({running}, state {target.age:.0f}s old)"
    if report["status"] in ("UPGRADE_REQUIRED", "PROTOCOL_MISMATCH"):
        return False, f"{where}; the MCP will NOT send it mutations: {report['status']} -- this checkout ships {wanted}; add the new .ablx and restart Live"
    if target.surface_version != f"loom-extension/{versions['manifest_version']}":
        return True, f"{where}; protocol compatible, but the package differs from this checkout ({wanted}) -- add the new .ablx when convenient"
    return True, where


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

    print("\n2. Ableton Live integration: the Loom extension (Live 12.4 beta)")
    for line in package_extension(args.check):
        print(line)
    loaded, detail = extension_status()
    print("  %s  extension              %s" % ("ok      " if loaded else "TODO    ", detail))
    if not loaded:
        print("     Adding an extension is Live's own step. Once, in Live 12.4 beta:")
        print(f"     add {EXTENSION_PACKAGE.name} from the Extensions settings, then restart Live.")
        print("     Then 'python3 install.py --check' or the MCP tool live_bridge_status confirms the bridge.")

    print("\n3. Catalogues from this machine's Ableton library")
    code = scan(args.check)

    print("\n" + "=" * 62)
    if args.check:
        print("Nothing was changed. Run without --check to install.")
        return code
    print("Installed. Restart your MCP client so it picks up the new server.")
    if not loaded:
        print("Then add the Loom extension to Live 12.4 beta (step 2) and restart Live.")
    return code


if __name__ == "__main__":
    sys.exit(main())
