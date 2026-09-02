#!/usr/bin/env python3
"""Single source of truth for the plugin version.

server.py owns the version literal (VERSION = "x.y.z"); manifest.json is
stamped from it so the two can never drift. Bump through this script (or
`make bump-version`) instead of editing either file by hand.

Usage:
  python3 scripts/bump_version.py            # +0.0.1 (patch) on both files
  python3 scripts/bump_version.py 1.6.0      # explicit x.y.z on both files
  python3 scripts/bump_version.py --check    # verify server.py == manifest.json (exit 1 on drift)
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SERVER = HERE / "server.py"
MANIFEST = HERE / "manifest.json"

# Matches ONLY the standalone literal assignment, never `VERSION = _git_version()`.
# MULTILINE so `^`/`$` anchor per-line (re.subn over the whole file relies on it).
LIT_RE = re.compile(r'^VERSION = "(\d+)\.(\d+)\.(\d+)"\s*$', re.MULTILINE)
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def read_server_version() -> tuple[str, str, str]:
    for line in SERVER.read_text().splitlines():
        m = LIT_RE.match(line)
        if m:
            return m.groups()
    raise SystemExit("bump_version: no VERSION = \"x.y.z\" literal found in server.py")


def read_manifest_version() -> tuple[str, str, str]:
    data = json.loads(MANIFEST.read_text())
    v = data.get("version")
    if not v or not SEMVER_RE.match(v):
        raise SystemExit(f"bump_version: bad manifest version {v!r}")
    maj, minor, patch = v.split(".")
    return maj, minor, patch


def write_server_version(new: str) -> None:
    text = SERVER.read_text()
    new_text, n = LIT_RE.subn(f'VERSION = "{new}"', text, count=1)
    if n != 1:
        raise SystemExit("bump_version: could not rewrite the server.py VERSION literal")
    SERVER.write_text(new_text)


def write_manifest_version(new: str) -> None:
    data = json.loads(MANIFEST.read_text())
    data["version"] = new
    MANIFEST.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main() -> None:
    args = [a for a in sys.argv[1:] if a]

    if "--check" in args:
        sv = ".".join(read_server_version())
        mv = ".".join(read_manifest_version())
        if sv != mv:
            print(f"VERSION drift: server.py={sv}  manifest.json={mv}")
            sys.exit(1)
        print(f"versions in sync: {sv}")
        return

    if args and not args[0].startswith("-"):
        new = args[0]
        if not SEMVER_RE.match(new):
            raise SystemExit(f"bump_version: invalid version {new!r} (want x.y.z)")
    else:
        maj, minor, patch = read_server_version()
        new = f"{maj}.{minor}.{int(patch) + 1}"

    write_server_version(new)
    write_manifest_version(new)
    print(f"bumped to {new} (server.py + manifest.json)")


if __name__ == "__main__":
    main()