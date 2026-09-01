#!/usr/bin/env python3
"""Ensure the lanchat daemon's systemd user unit is installed, enabled, and
running. Idempotent — safe to run on every shell start.

The plugin ships the unit at <plugin>/systemd/lanchat.service, but `omarchy
plugin add` only clones the repo; it does not install systemd units. This
helper closes that gap so a fresh install is fully automatic: it copies the
unit into ~/.config/systemd/user/, daemon-reloads, and enables+starts it.

All operations are user-level (systemd --user), so no sudo is required.

Exit codes:
  0  unit is enabled and the daemon is active (or was just made so)
  1  something failed (unit missing, systemctl unavailable, start failed)
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
UNIT_SRC = os.path.join(HERE, "systemd", "lanchat.service")
UNIT_NAME = "lanchat.service"


def _user_unit_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(base, "systemd", "user")


def _run(args, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, **kw)


def _is_enabled() -> bool:
    r = _run(["systemctl", "--user", "is-enabled", UNIT_NAME])
    return r.returncode == 0 and r.stdout.strip() == "enabled"


def _is_active() -> bool:
    r = _run(["systemctl", "--user", "is-active", UNIT_NAME])
    return r.returncode == 0 and r.stdout.strip() == "active"


def main() -> int:
    if not os.path.exists(UNIT_SRC):
        print("lanchat: unit source missing: %s" % UNIT_SRC, file=sys.stderr)
        return 1
    if shutil.which("systemctl") is None:
        print("lanchat: systemctl not found (no systemd user session?)", file=sys.stderr)
        return 1

    # Verify the daemon's Python dependency is present BEFORE starting it.
    # `cryptography` is NOT guaranteed by Omarchy (it was a manual install on
    # some machines) — if it's missing, every UDP friend request crashes the
    # listener thread silently (the .51 bug). Fail here with a clear message
    # instead of letting the daemon start into a half-broken state.
    try:
        import cryptography  # noqa: F401
    except Exception as _dep_err:
        print(
            "lanchat: the 'cryptography' Python library is not installed for "
            "%s, which the lanchat daemon requires. Install it, e.g.:\n"
            "  sudo pacman -S python-cryptography\n"
            "or:  python3 -m pip install --user cryptography\n"
            "then re-run this installer." % sys.executable,
            file=sys.stderr)
        return 1

    # Already enabled and running — nothing to do.
    if _is_enabled() and _is_active():
        return 0

    # Install the unit file if it isn't there.
    unit_dir = _user_unit_dir()
    os.makedirs(unit_dir, exist_ok=True)
    unit_dst = os.path.join(unit_dir, UNIT_NAME)
    try:
        shutil.copyfile(UNIT_SRC, unit_dst)
    except OSError as e:
        print("lanchat: failed to copy unit: %s" % e, file=sys.stderr)
        return 1

    r = _run(["systemctl", "--user", "daemon-reload"])
    if r.returncode != 0:
        print("lanchat: daemon-reload failed: %s" % r.stderr.strip(), file=sys.stderr)
        return 1

    r = _run(["systemctl", "--user", "enable", "--now", UNIT_NAME])
    if r.returncode != 0:
        print("lanchat: enable --now failed: %s" % r.stderr.strip(), file=sys.stderr)
        return 1

    if not _is_active():
        print("lanchat: unit enabled but daemon not active", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
