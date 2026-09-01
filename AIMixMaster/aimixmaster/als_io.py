"""Lossless-at-the-container-level ALS gzip/XML loading and atomic saving."""

from __future__ import annotations

import gzip
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


class AlsFormatError(ValueError):
    """Raised when a file cannot be parsed as an Ableton XML set."""


def load_als(path: Path) -> ET.ElementTree:
    if not path.is_file():
        raise AlsFormatError(f"ALS file does not exist: {path}")
    try:
        with gzip.open(path, "rb") as stream:
            return ET.parse(stream)
    except gzip.BadGzipFile as exc:
        raise AlsFormatError(f"Invalid ALS gzip container: {path}") from exc
    except ET.ParseError as exc:
        raise AlsFormatError(f"Invalid ALS XML: {path}") from exc
    except (EOFError, OSError) as exc:
        raise AlsFormatError(f"Cannot read ALS gzip container: {path}") from exc


def save_als_atomic(tree: ET.ElementTree, path: Path) -> None:
    """Write a gzip ALS, fsync it, then atomically replace the target."""
    directory = path.parent
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as raw_stream:
            with gzip.GzipFile(fileobj=raw_stream, mode="wb", mtime=0) as stream:
                tree.write(stream, encoding="utf-8", xml_declaration=True)
            raw_stream.flush()
            os.fsync(raw_stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
