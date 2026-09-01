#!/usr/bin/env python3
"""Lanchat control-channel bridge.

The lanchat daemon runs under systemd (no stdin/stdout pipe to the shell). The
QML shell still uses a Quickshell `Process` that talks JSON-lines over
stdin/stdout. This bridge is that Process: it connects to the daemon's
unix-socket control channel and proxies both directions —

    QML stdin  -> daemon socket  (commands)
    daemon     -> QML stdout     (events)

Keeping the bridge as the Process means ALL of the QML's command/event wiring
is unchanged; only the thing the Process runs changed from `server.py` to this
bridge. It is stdlib-only (like server.py).

Behaviour on the daemon being down:
  - If the daemon isn't reachable yet (systemd still starting it / it crashed),
    retry a few times over a short window, then exit non-zero so the QML's
    existing onDaemonExit -> restartTimer -> startDaemon loop reports the
    daemon as down and respawns this bridge.
  - A dropped connection mid-session (daemon restarted under systemd) exits so
    the QML restarts the bridge, which reconnects.

Usage: python3 lanchat-bridge.py [--socket PATH]
"""
import os
import select
import socket
import sys
import time


def socket_path() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(
        os.path.expanduser("~"), ".config", "omarchy")
    return os.path.join(base, "lanchat.sock")


def main() -> int:
    path = socket_path()
    if "--socket" in sys.argv:
        i = sys.argv.index("--socket")
        if i + 1 < len(sys.argv):
            path = sys.argv[i + 1]

    # Try to connect for a short window, then give up so the QML can report the
    # daemon down and respawn us. A daemon that's simply mid-restart comes up
    # within this window under systemd Restart=always.
    sock: socket.socket | None = None
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(path)
            sock = s
            break
        except OSError:
            s.close()
            time.sleep(0.2)
    if sock is None:
        return 1

    # Proxy stdin (QML commands) -> socket, and socket (daemon events) -> stdout.
    # Single-threaded select so EOF/error on either side tears the bridge down
    # cleanly (which the QML treats as daemon-exit and restarts us).
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    fds = [stdin, sock]
    while True:
        try:
            r, _, _ = select.select(fds, [], [])
        except (OSError, ValueError):
            break
        for fd in r:
            if fd is stdin:
                try:
                    data = os.read(stdin.fileno(), 65536)
                except OSError:
                    sock.close()
                    return 0
                if not data:  # QML closed stdin (shell exiting)
                    sock.close()
                    return 0
                try:
                    sock.sendall(data)
                except OSError:
                    sock.close()
                    return 0
            else:  # daemon socket
                try:
                    data = sock.recv(65536)
                except OSError:
                    return 0
                if not data:  # daemon closed (restarting)
                    sock.close()
                    return 0
                try:
                    stdout.write(data)
                    stdout.flush()
                except OSError:
                    sock.close()
                    return 0


if __name__ == "__main__":
    sys.exit(main())
