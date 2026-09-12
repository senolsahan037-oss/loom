#!/usr/bin/env python3
"""Builds the subverselab-loom wheel from a staging copy of this checkout.

Loom is not laid out as a Python distribution and deliberately stays that way:
`mcp_server` is not a package, its siblings are imported by name after the
server inserts their directories into `sys.path`, and `LOOM_DIR` is derived from
`__file__` so every engine finds its own data. That arrangement is what makes a
clone runnable with one command, and rewriting it into packages to satisfy a
packaging tool would be the tail wagging the dog.

So nothing here moves. The wheel is built from a staged copy that preserves the
same relative layout under one root, `subverselab_loom/`. Inside the installed
package, `Path(__file__).parents[1]` lands on that root exactly as it lands on
the checkout root today, so `LOOM_DIR / "Sensei"` keeps resolving and no import
in the tree has to change.

What is left out is as deliberate as what goes in. The working tree is 1.5 GB
and almost none of it is the program: measured corpora, node_modules, reports,
recorded sessions and test fixtures. Those are evidence and output, not the
tool, and several are regenerated on the user's own machine from their own
Ableton library — shipping them would mean shipping someone else's library.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "packaging" / "build"
PKG = STAGE / "subverselab_loom"

# Directories the server imports from, by the names it imports them under.
CODE_DIRS = [
    "mcp_server",
    "Sensei/core",
    "Sensei/ableton",
    "Sensei/engine",
    "AIMixMaster/aimixmaster",
    "MusicalIntelligence/mi",
    "Presetor",
    "AISoundDesigner",
    "MixAnalyzer/subverse_mix",
    "SampleAgent",
]

# Small data the engines read at runtime. Anything large enough to notice is
# either regenerated locally or is a corpus that is not ours to redistribute.
DATA_GLOBS = [
    "Sensei/data/genre_identity/*.jsonl",
    "MusicalIntelligence/data/*.json",
    "Presetor/data/*.json",
    "AISoundDesigner/data/*.json",
]

EXCLUDE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "tests", "test_*", ".pytest_cache",
    "node_modules", "*.wav", "*.aif", "*.aiff", "*.als", "*.onnx",
)


def stage() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    PKG.mkdir(parents=True)

    for relative in CODE_DIRS:
        source = ROOT / relative
        if not source.is_dir():
            print(f"  skip (absent): {relative}")
            continue
        destination = PKG / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, ignore=EXCLUDE)
        print(f"  code: {relative}")

    for pattern in DATA_GLOBS:
        for source in ROOT.glob(pattern):
            destination = PKG / source.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            print(f"  data: {source.relative_to(ROOT)}  ({source.stat().st_size // 1024} KB)")

    # setuptools will not treat a directory without __init__.py as a package, and
    # the tree has very few. They are added to the staged copy only — the server
    # still imports these modules by name off sys.path, not through the package.
    for directory in [PKG, *(p for p in PKG.rglob("*") if p.is_dir())]:
        init = directory / "__init__.py"
        if not init.exists():
            init.write_text("")

    for name in ("README.md", "LICENSE", "NOTICE", "CITATION.cff"):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, STAGE / name)

    shutil.copy2(ROOT / "packaging" / "pyproject.toml", STAGE / "pyproject.toml")


def size_report() -> None:
    total = sum(f.stat().st_size for f in PKG.rglob("*") if f.is_file())
    print(f"\n  staged: {total / 1024 / 1024:.1f} MB, "
          f"{sum(1 for f in PKG.rglob('*') if f.is_file())} files")


def build() -> None:
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--sdist"],
                   cwd=STAGE, check=True)
    for artifact in sorted((STAGE / "dist").glob("*")):
        print(f"  {artifact.name}  {artifact.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    stage()
    size_report()
    build()
