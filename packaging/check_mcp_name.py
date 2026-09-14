#!/usr/bin/env python3
"""Fails unless the built wheel carries the MCP Registry name in its metadata.

The registry proves that a pypi package and a registry entry belong to the same
person by looking for a `mcp-name:` line in the package's long description. It
comes from an HTML comment near the top of README.md, which means an innocent
README edit can silently remove it — and the failure would then land at
`mcp-publisher publish`, after the version number has already been spent on
PyPI and cannot be re-uploaded.

So it is checked here instead, on the artifact that is about to be published,
before anything leaves the runner.
"""

from __future__ import annotations

import glob
import sys
import zipfile
from pathlib import Path

MCP_NAME = "mcp-name: io.github.senolsahan037-oss/loom"
DIST = Path(__file__).resolve().parent / "build" / "dist"


def main() -> int:
    wheels = sorted(glob.glob(str(DIST / "*.whl")))
    if not wheels:
        print(f"error: no wheel found in {DIST}", file=sys.stderr)
        return 1

    failed = False
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
            if not names:
                print(f"error: {wheel} has no METADATA", file=sys.stderr)
                failed = True
                continue
            metadata = archive.read(names[0]).decode("utf-8")

        if MCP_NAME in metadata:
            print(f"ok: {Path(wheel).name} carries {MCP_NAME!r}")
        else:
            print(
                f"error: {Path(wheel).name} is missing {MCP_NAME!r}.\n"
                "       It is written as an HTML comment near the top of README.md;\n"
                "       restore it there, since pyproject.toml reads that file as the\n"
                "       long description.",
                file=sys.stderr,
            )
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
