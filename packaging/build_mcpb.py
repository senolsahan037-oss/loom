#!/usr/bin/env python3
"""Builds the .mcpb bundle that the MCP Registry lists from a GitHub release.

Why a bundle and not PyPI: the registry lists metadata only and the artifact has
to exist somewhere first. PyPI needs an account this project does not have;
MCPB artifacts are hosted on GitHub releases, which it already uses.

Why the uv runtime rather than vendored dependencies: Loom needs numpy, scipy,
librosa, soundfile and mido, and MCPB cannot portably bundle compiled wheels —
a bundle built on this Mac would not run on anyone else's machine. Declaring
them in pyproject.toml and letting the host resolve them is the supported path
for exactly this case.

The staging is the same one the wheel uses, so there is one answer to "what
ships" rather than two that drift.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_wheel import ROOT, PKG, stage  # noqa: E402

BUNDLE = ROOT / "packaging" / "mcpb"


def manifest(version: str) -> dict:
    return {
        "manifest_version": "0.4",
        "name": "loom",
        "display_name": "Loom by SubverseLab",
        "version": version,
        "description": "Measurement-based production for Ableton Live.",
        "long_description": (
            "A local MCP server that reads your own .als projects and Ableton library, "
            "answers with counts instead of guesses, and writes MIDI, device chains, "
            "automation and arrangement markers into a running Live session, verifying "
            "every write by reading it back."
        ),
        "author": {"name": "Şenol Şahan", "url": "https://subverselab.com/loom"},
        "homepage": "https://subverselab.com/loom",
        "documentation": "https://subverselab.com/loom",
        "repository": {"type": "git", "url": "https://github.com/senolsahan037-oss/loom"},
        "license": "SEE LICENSE IN LICENSE",
        "keywords": ["ableton", "ableton-live", "music-production", "midi", "audio", "mcp"],
        "server": {
            "type": "uv",
            "entry_point": "subverselab_loom/mcp_server/server.py",
            # Required even for the uv runtime. `--directory` points uv at the
            # bundle root so it reads the pyproject.toml shipped beside this
            # manifest and resolves numpy, scipy, librosa, soundfile and mido on
            # the host rather than from wheels compiled on the build machine.
            "mcp_config": {
                "command": "uv",
                "args": [
                    "run",
                    "--directory", "${__dirname}",
                    "subverselab_loom/mcp_server/server.py",
                ],
            },
        },
        "compatibility": {
            # macOS only, and stated rather than implied: the writer path is an
            # Ableton Live extension, the audio tap is Core Audio, and opening a
            # set shells out to `open -a Live`. Claiming win32 or linux here
            # would be claiming something that has never run.
            "platforms": ["darwin"],
            "runtimes": {"python": ">=3.11"},
        },
    }


def build() -> Path:
    version = json.loads((ROOT / "extension" / "manifest.json").read_text())["version"]

    stage()                                   # same staging as the wheel
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    BUNDLE.mkdir(parents=True)

    shutil.copytree(PKG, BUNDLE / "subverselab_loom")
    shutil.copy2(ROOT / "packaging" / "pyproject.toml", BUNDLE / "pyproject.toml")
    for name in ("README.md", "LICENSE", "NOTICE", "CITATION.cff"):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, BUNDLE / name)

    (BUNDLE / "manifest.json").write_text(
        json.dumps(manifest(version), indent=2, ensure_ascii=False) + "\n"
    )

    subprocess.run(["npx", "--yes", "@anthropic-ai/mcpb", "pack", str(BUNDLE),
                    str(ROOT / "packaging" / f"loom-{version}.mcpb")], check=True)

    artifact = ROOT / "packaging" / f"loom-{version}.mcpb"
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    print(f"\n  {artifact.name}  {artifact.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"  sha256: {digest}")
    (ROOT / "packaging" / "mcpb.sha256").write_text(f"{digest}  {artifact.name}\n")
    return artifact


if __name__ == "__main__":
    build()
