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


def _ensure_firewall() -> None:
    """Open lanchat's port (4812) at install time via pkexec (polkit).

    A fresh install should start reachable, so we open the port once here.
    pkexec pops a password prompt; NO permanent sudoers rule is created —
    every later open/close from the panel also prompts via pkexec. If no
    firewall is active there is nothing to do; if pkexec isn't available or
    the user cancels, we warn and leave it to the panel's toggle.

    Best-effort: never fail the whole install over a firewall hiccup.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    firewall = os.path.join(here, "scripts", "lanchat-firewall.sh")

    # Only bother if a firewall is actually active.
    for unit in ("ufw", "firewalld"):
        r = _run(["systemctl", "is-active", unit])
        if r.returncode == 0 and r.stdout.strip() == "active":
            break
    else:
        print("lanchat: no active firewall — nothing to open.", file=sys.stderr)
        return

    r = _run(["bash", firewall, "open"])
    if r.returncode == 0:
        print("lanchat: firewall port 4812 opened for the LAN.", file=sys.stderr)
    else:
        print("lanchat: firewall open not applied (pkexec cancelled/unavailable):\n%s"
              % (r.stdout + r.stderr).strip(), file=sys.stderr)


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
    # listener thread silently (the .51 bug). So we ensure it here as part of
    # the install. Order of attempts:
    #   1. pacman via `sudo -n` (fail fast, no TTY prompt) then `pkexec`
    #      (GUI polkit prompt — the panel runs in a graphical session).
    #   2. pip via `python3 -m pip` (gate on the MODULE, not a binary), with
    #      --break-system-packages for Arch's PEP 668 externally-managed env.
    try:
        import cryptography  # noqa: F401
    except Exception:
        print("lanchat: installing required 'cryptography' dependency...", file=sys.stderr)
        installed = False
        if shutil.which("pacman"):
            # Explain WHY a password is needed before prompting. The lanchat
            # daemon requires the `cryptography` library (cert fingerprinting,
            # message-history encryption), and Omarchy doesn't ship it, so we
            # install it with your system package manager. That needs admin
            # rights — the prompt you're about to see is for this one install.
            print(
                "lanchat: 'cryptography' is required by the lanchat daemon "
                "(identity fingerprints + encrypted history) but isn't installed "
                "on your system. Installing it now via pacman needs your "
                "administrator password — this is a one-time install.",
                file=sys.stderr)
            # Non-interactive first: sudo -n fails fast instead of hanging on a
            # prompt it can't render (no controlling TTY from the panel).
            r = _run(["sudo", "-n", "pacman", "-S", "--noconfirm", "python-cryptography"])
            if r.returncode != 0 and shutil.which("pkexec"):
                # GUI polkit prompt (an agent is running in the session).
                r = _run(["pkexec", "pacman", "-S", "--noconfirm", "python-cryptography"])
            installed = r.returncode == 0
        if not installed:
            print(
                "lanchat: could not install via pacman; trying pip instead "
                "(no admin needed, installs into your user profile)...",
                file=sys.stderr)
            # Gate on the pip MODULE being importable, not a `pip` binary on
            # PATH (system Python ships no pip binary). --break-system-packages
            # is required on Arch's PEP 668 externally-managed interpreter;
            # --user keeps it in ~/.local so pacman ownership is untouched.
            probe = _run([sys.executable, "-m", "pip", "--version"])
            if probe.returncode == 0:
                r = _run([sys.executable, "-m", "pip", "install", "--user",
                          "--break-system-packages", "cryptography"])
                installed = r.returncode == 0
        if not installed:
            print(
                "lanchat: could not auto-install 'cryptography'. Install it "
                "manually, e.g.:\n"
                "  sudo pacman -S python-cryptography\n"
                "or:  python3 -m pip install --user --break-system-packages cryptography\n"
                "then re-run this installer.",
                file=sys.stderr)
            return 1
        try:
            import cryptography  # noqa: F401
        except Exception:
            print("lanchat: 'cryptography' still not importable after install", file=sys.stderr)
            return 1
        print("lanchat: 'cryptography' installed.", file=sys.stderr)


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
    # Fresh install: open the firewall port at the one point a password may
    # already have been typed (the cryptography step above). Best-effort —
    # a firewall failure doesn't fail the install; the panel's Open button
    # can retry later.
    _ensure_firewall()
    return 0


if __name__ == "__main__":
    sys.exit(main())
