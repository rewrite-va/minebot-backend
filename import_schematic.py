#!/usr/bin/env python3
"""Copies a `.litematic` schematic (built in-game with Litematica) into
`tests/fixtures/schematics/`, as a checked-in test fixture -- see
`minebot/testing/litematic.py`'s own docstring for why tests read fixture
copies rather than the live Windows Prism schematics folder directly
(reproducible on any checkout, doesn't depend on this machine's live
folder still having the file).

Usage:
    ./import_schematic.py "C:\\Users\\colaila\\AppData\\Roaming\\PrismLauncher\\instances\\26.1.2 - v1 riterite\\minecraft\\schematics\\simple goto.litematic"
    ./import_schematic.py "/mnt/c/Users/.../simple goto.litematic" --name simple_goto

Accepts either a Windows-style path (`C:\\...`) or an already-WSL2-mounted
path (`/mnt/c/...`) -- Windows paths are translated via the same `/mnt/c`
convention this repo's own CLAUDE.md documents. Validates the file
actually decodes as a schematic (via litematic.py's own reader) before
copying, so a bad path/corrupt file fails loudly here rather than showing
up later as a confusing test failure.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "tests" / "fixtures" / "schematics"

_WINDOWS_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def _to_wsl_path(raw: str) -> Path:
    """Translates a `C:\\...`-style Windows path to its WSL2 `/mnt/c/...`
    mount point (this repo's own CLAUDE.md convention); passes anything
    else through unchanged (already a WSL/Linux path).
    """
    match = _WINDOWS_DRIVE_RE.match(raw)
    if match is None:
        return Path(raw)
    drive, rest = match.groups()
    rest = rest.replace("\\", "/")
    return Path(f"/mnt/{drive.lower()}/{rest}")


def _default_fixture_name(source: Path) -> str:
    # "simple goto.litematic" -> "simple_goto.litematic" -- matches the
    # existing fixture's own naming (spaces -> underscores, lowercase kept
    # as-is from the source name rather than forced, since schematic names
    # aren't guaranteed to be lowercase to begin with).
    stem = source.stem.replace(" ", "_")
    return f"{stem}.litematic"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="Path to a .litematic file (Windows or WSL2 path)")
    parser.add_argument("--name", help="Fixture filename to save as (default: derived from the source filename)")
    args = parser.parse_args()

    source = _to_wsl_path(args.source)
    if not source.is_file():
        print(f"error: no such file: {source}", file=sys.stderr)
        return 1

    # Import lazily, after argument parsing, so `--help` doesn't need a
    # working `minebot` package import to succeed.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from minebot.testing.litematic import Schematic

    try:
        schematic = Schematic.from_file(source)
    except Exception as exc:
        print(f"error: {source} did not decode as a valid schematic: {exc}", file=sys.stderr)
        return 1

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    dest_name = args.name or _default_fixture_name(source)
    if not dest_name.endswith(".litematic"):
        dest_name += ".litematic"
    dest = FIXTURES_DIR / dest_name

    shutil.copyfile(source, dest)
    print(f"imported {source} -> {dest}")
    print(f"  size: {schematic.size_x}x{schematic.size_y}x{schematic.size_z}, {len(schematic.blocks)} non-air blocks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
