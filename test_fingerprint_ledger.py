#!/usr/bin/env python3
"""Fingerprint-ledger friend recovery.

The daemon keeps an independent ledger (~/.local/state/lanchat/fingerprints.json)
of every fingerprint it has CONFIRMED as a friend, separate from the config. On
boot it reconciles: any fingerprint the ledger holds but the config lacks is
re-added as a confirmed friend. This is the durability layer that guarantees a
friend is never permanently lost by a config-save slippage or a lost
lanchat.json — the exact scenario of MB Pro (confirmed in memory, gone from
disk after the update restart).

This test:
  1. Friends A <-> B over the normal UDP accept (confirmed).
  2. Confirms B's fingerprint is written to A's ledger file.
  3. SIMULATES the losing-bug: erases B from A's lanchat.json friends (a
     plausible "stale config write" outcome) while leaving the ledger intact.
  4. Restarts A and asserts B is RECOVERED into the config friends list.

Run from repo root: python3 test_fingerprint_ledger.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

TOKEN = "test-shared-secret-token"
HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")


def make_home(name, port):
    d = tempfile.mkdtemp(prefix="lnc-ledger-" + name)
    c = os.path.join(d, ".config", "omarchy"); os.makedirs(c)
    open(os.path.join(c, "lanchat.json"), "w").write(json.dumps(
        {"token": TOKEN, "port": port, "displayName": name, "httpPort": port + 10,
         "visibility": "open", "friends": []}))
    return d


class Daemon:
    def __init__(self, home, port, name):
        self.port = port; self.name = name; self.home = home
        self.events = []; self._lock = threading.Lock()
        self.proc = subprocess.Popen([sys.executable, SRV], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            env=dict(os.environ, HOME=home), bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()
    def _read(self):
        for line in self.proc.stdout:
            try: e = json.loads(line)
            except Exception: continue
            with self._lock: self.events.append(e)
    def events_of(self, kind):
        with self._lock: return [e for e in self.events if e.get("event") == kind]
    def wait_event(self, kind, timeout=8.0):
        dl = time.time() + timeout
        while time.time() < dl:
            with self._lock:
                for e in self.events:
                    if e.get("event") == kind:
                        self.events.remove(e); return e
            time.sleep(0.02)
        return None
    def cmd(self, **kw):
        self.proc.stdin.write(json.dumps(kw) + "\n"); self.proc.stdin.flush()
    def config_friends(self):
        p = os.path.join(self.home, ".config", "omarchy", "lanchat.json")
        return json.load(open(p)).get("friends", [])
    def stop(self):
        try: self.proc.stdin.close()
        except OSError: pass
        try: self.proc.wait(timeout=5)
        except Exception: self.proc.kill()


def _cert_fp(home):
    import hashlib as _h
    cert = os.path.join(home, ".config", "omarchy", "lanchat-certs", "cert.pem")
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    c = x509.load_pem_x509_certificate(open(cert, "rb").read())
    return _h.sha256(c.public_bytes(serialization.Encoding.DER)).hexdigest()


def disco(port, pid, name, pport):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(json.dumps({"t": "hello", "id": pid, "name": name, "port": pport}).encode(),
             ("127.0.0.1", port))
    s.close()


def ledger_path(home):
    return os.path.join(home, ".local", "state", "lanchat", "fingerprints.json")


def main():
    ha = make_home("A", 5201); hb = make_home("B", 5202)
    a = Daemon(ha, 5201, "Alpha"); b = Daemon(hb, 5202, "Beta")
    beat = {"go": True}
    def _beat():
        while beat["go"]:
            try:
                disco(a.port, _cert_fp(hb), "Beta", 5202)
                disco(b.port, _cert_fp(ha), "Alpha", 5201)
            except Exception:
                pass
            time.sleep(1.5)
    threading.Thread(target=_beat, daemon=True).start()

    try:
        if not a.wait_event("ready") or not b.wait_event("ready"):
            print("FAIL: daemons not ready"); return 1
        ida, idb = _cert_fp(ha), _cert_fp(hb)

        # 1. befriend A <-> B (UDP accept, confirmed)
        a.cmd(cmd="send", to=idb, text="hi", friend_request=True)
        b.wait_event("friend-request")
        b.cmd(cmd="acceptFriend", id=ida)
        if not (a.wait_event("friend-accepted") and b.wait_event("friend-accepted")):
            print("FAIL: handshake not completed"); return 1
        time.sleep(0.6)

        # 2. A's ledger must hold B's fingerprint
        ok_ledger = False
        for _ in range(20):
            try:
                led = json.load(open(ledger_path(ha)))
                if idb in led:
                    ok_ledger = True; break
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        print(("PASS  " if ok_ledger else "FAIL  ") +
              "A's fingerprint ledger records B after confirm")
        if not ok_ledger:
            return 1

        # 3. Simulate the losing bug: B is confirmed in config now ...
        if not any(f.get("id") == idb for f in a.config_friends()):
            print("FAIL: pre-requisite — B not a confirmed friend in A's config"); return 1
        # ... force a "stale config write" by erasing B from A's config friends
        cfg_path = os.path.join(ha, ".config", "omarchy", "lanchat.json")
        cfg = json.load(open(cfg_path))
        cfg["friends"] = [f for f in cfg["friends"] if f.get("id") != idb]
        json.dump(cfg, open(cfg_path, "w"), indent=2)
        print("PASS  simulated config loss: erased B from A's lanchat.json "
              "(ledger still holds it)")

        # 4. restart A; boot reconcile must recover B from the ledger
        a.stop(); time.sleep(0.4)
        a = Daemon(ha, 5201, "Alpha")
        ready = a.wait_event("ready")
        if not ready:
            print("FAIL: A did not restart"); return 1
        time.sleep(0.5)
        recovered = any(f.get("id") == idb and f.get("confirmed")
                        for f in a.config_friends())
        # and the ready event must carry B as a confirmed friend
        ready_has_b = any(f.get("id") == idb and f.get("confirmed")
                          for f in (ready or {}).get("friends", []))
        print(("PASS  " if recovered else "FAIL  ") +
              "B recovered into A's config after restart (from ledger)")
        print(("PASS  " if ready_has_b else "FAIL  ") +
              "A's ready event re-lists B as a confirmed friend")
        if not (recovered and ready_has_b):
            return 1

        print("\nALL FINGERPRINT-LEDGER TESTS PASSED")
        return 0
    finally:
        beat["go"] = False
        a.stop(); b.stop()
        shutil.rmtree(ha, ignore_errors=True); shutil.rmtree(hb, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
