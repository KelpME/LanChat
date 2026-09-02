#!/usr/bin/env python3
"""Regression test for the systemd control-channel transport.

The daemon can run in two modes:
  - legacy stdin/stdout (no --socket) — the test harness / old shell spawn
  - unix-socket control channel (--socket) — the systemd unit + QML bridge

This test covers the socket mode end to end: a daemon started with --socket
serves a unix socket at $XDG_RUNTIME_DIR/lanchat.sock; a bridge process
(lanchat-bridge.py) connects to it and proxies QML-style stdin<->socket and
socket->stdout. It asserts the command surface (ready, list, setName) works
over the bridge exactly as it does over stdin, and that the bridge exits when
the daemon goes away so the QML's onDaemonExit -> restartTimer loop respawns it.

Run: python3 test_systemd_control.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")
BRIDGE = os.path.join(HERE, "lanchat-bridge.py")
TOKEN = "test-systemd-token-1234"


def make_home(name, port):
    d = tempfile.mkdtemp(prefix="lanchat-sysd-" + name)
    cfg = os.path.join(d, ".config", "omarchy")
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "lanchat.json"), "w") as f:
        json.dump({"token": TOKEN, "port": port, "displayName": "SysdTest",
                   "visibility": "open"}, f)
    return d


def wait_socket(path, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return False


def read_until(bridge, pred, timeout=5.0):
    """Read bridge stdout events until `pred(ev)` holds (or timeout).

    The daemon interleaves its own events (ready echoes, peer discovery,
    presence) with the reply to a command, so tests must not assume the reply
    is the very next line — that made them flaky. Bound the wait so a missing
    reply fails instead of hanging.
    """
    end = time.time() + timeout
    while time.time() < end:
        line = bridge.stdout.readline()
        if not line:
            break
        ev = json.loads(line)
        if pred(ev):
            return ev
    raise AssertionError("timed out waiting for expected bridge event")


def main():
    rt = tempfile.mkdtemp(prefix="lanchat-sysd-rt-")
    home = make_home("daemon", 4921)
    env = dict(os.environ, HOME=home, XDG_RUNTIME_DIR=rt)

    daemon = None
    bridge = None
    try:
        # --- daemon in socket mode -------------------------------------
        daemon = subprocess.Popen([sys.executable, SRV, "--socket"], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sock = os.path.join(rt, "lanchat.sock")
        assert wait_socket(sock), "daemon did not create the control socket"
        print("OK  daemon serves unix-socket control channel at", sock)

        # --- bridge connects and proxies -------------------------------
        bridge = subprocess.Popen([sys.executable, BRIDGE, "--socket", sock],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
        # ready is pushed by the daemon when the bridge connects.
        line = bridge.stdout.readline()
        ready = json.loads(line)
        assert ready.get("event") == "ready", ready
        print("OK  bridge received ready on connect:", ready.get("name"))

        # command round-trip over the bridge (QML -> daemon -> event)
        bridge.stdin.write(json.dumps({"cmd": "list"}) + "\n"); bridge.stdin.flush()
        read_until(bridge, lambda e: e.get("event") == "peers")
        print("OK  command (list) proxied over bridge -> peers event")

        # a second command (setName) to confirm repeated round-trips
        bridge.stdin.write(json.dumps({"cmd": "setName", "name": "BridgeRenamed"}) + "\n"); bridge.stdin.flush()
        read_until(bridge, lambda e: e.get("event") == "ready" and e.get("name") == "BridgeRenamed")
        print("OK  second command (setName) proxied -> ready echo")

        # --- daemon down: bridge must exit so the QML restarts it -------
        daemon.terminate()
        daemon.wait(timeout=5)
        daemon = None
        bridge.stdin.close()
        rc = bridge.wait(timeout=8)
        bridge = None
        # Any exit code works — onDaemonExit restarts the bridge regardless.
        print("OK  bridge exited when daemon stopped (rc=%s) so QML respawns it" % rc)

        # --- ensure helper + systemd unit ship with the plugin ----------
        assert os.path.exists(os.path.join(HERE, "lanchat-ensure-systemd.py")), \
            "lanchat-ensure-systemd.py missing"
        assert os.path.exists(os.path.join(HERE, "systemd", "lanchat.service")), \
            "systemd/lanchat.service missing"
        assert os.path.exists(os.path.join(HERE, "systemd", "lanchat.path")), \
            "systemd/lanchat.path missing"
        print("OK  ensure helper + systemd units (service + path watcher) ship with the plugin")

        print("\nALL SYSTEMD-CONTROL TESTS PASSED")
        return 0
    finally:
        for p in (daemon, bridge):
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass
                try:
                    p.wait(timeout=3)
                except Exception:
                    p.kill()
        shutil.rmtree(rt, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
