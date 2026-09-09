#!/usr/bin/env python3
"""Offline test for ADD-BY-FINGERPRINT notify (cmd setFriend).

Two isolated daemons (A, B) sharing a token on distinct ports. Verifies:
  F1  A adds B by fingerprint (setFriend id=fpB) while both are up and
      discovered -> B receives an INBOUND friend-request event from A
      (the _wait_inbound filter skips A's own outgoing echo).
  F2  mutual fingerprint-add: A setFriend fpB, then B setFriend fpA ->
      BOTH sides end confirmed via the mutual auto-accept, no clicks.
  F3  non-mutual control: A setFriend fpB only -> B gets the banner and
      confirms only after explicitly accepting (cmd acceptFriend).
  F4  offline/unseen friend: A setFriend with an id nobody advertised ->
      A gets an error event, no crash, friend still recorded locally.

Run from repo root:  python3 test_fingerprint_request.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

TOKEN = "test-shared-secret-token"
HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")


def make_home(name, port, display):
    d = tempfile.mkdtemp(prefix="lnc-fpr-" + name)
    c = os.path.join(d, ".config", "omarchy"); os.makedirs(c)
    open(os.path.join(c, "lanchat.json"), "w").write(json.dumps(
        {"token": TOKEN, "port": port, "displayName": display, "httpPort": port + 10, "visibility": "open"}))
    return d


class Daemon:
    def __init__(self, home, port, display):
        self.port = port; self.display = display
        self.events = []; self._lock = threading.Lock()
        self.proc = subprocess.Popen([sys.executable, SRV], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            env=dict(os.environ, HOME=home), bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()
    def _read(self):
        for line in self.proc.stdout:
            try: e = json.loads(line)
            except: continue
            with self._lock: self.events.append(e)
    def events_of(self, kind):
        with self._lock:
            return [e for e in self.events if e.get("event") == kind]
    def wait_event(self, kind, timeout=4.0):
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
    def stop(self):
        try: self.proc.stdin.close()
        except OSError: pass
        try: self.proc.wait(timeout=5)
        except: self.proc.kill()


def _cert_fp(home_dir: str) -> str:
    import hashlib as _h
    cert = os.path.join(home_dir, ".config", "omarchy", "lanchat-certs", "cert.pem")
    with open(cert, "rb") as f:
        data = f.read()
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    c = x509.load_pem_x509_certificate(data)
    return _h.sha256(c.public_bytes(serialization.Encoding.DER)).hexdigest()


def _disco(port, pid, name, pport):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(json.dumps({"t": "hello", "id": pid, "name": name, "port": pport}).encode(), ("127.0.0.1", port))
    s.close()


def _pair(name_a, name_b, pa, pb):
    ha = make_home(name_a, pa, name_a); hb = make_home(name_b, pb, name_b)
    a = Daemon(ha, pa, name_a); b = Daemon(hb, pb, name_b)
    a.wait_event("ready"); b.wait_event("ready")
    ida = _cert_fp(ha); idb = _cert_fp(hb)
    def _beat():
        while True:
            try:
                _disco(a.port, idb, name_b, pb)
                _disco(b.port, ida, name_a, pa)
            except OSError:
                return
            time.sleep(2.0)
    threading.Thread(target=_beat, daemon=True).start()
    time.sleep(1.5)  # let the UDP listener register the peers
    return a, b, ida, idb


def _confirmed(daemon, pid):
    fs = daemon.events_of("friends")
    if not fs:
        return False
    return any(f["id"] == pid and f.get("confirmed") for f in fs[-1]["friends"])


def _wait_inbound(daemon, pid, timeout=8.0):
    """Wait for an INBOUND friend-request event from pid, skipping this
    daemon's own outgoing echo (outgoing:true carries to/toName, no from)."""
    dl = time.time() + timeout
    while time.time() < dl:
        got = [x for x in daemon.events_of("friend-request")
               if not x.get("outgoing") and x.get("from") == pid]
        if got:
            return got[0]
        time.sleep(0.05)
    return None


def main():
    checks = []
    def check(name, ok):
        checks.append(ok)
        print(("OK   " if ok else "FAIL ") + name)

    # ---- F1: fingerprint-add pushes a real request to the friend ---------
    a, b, ida, idb = _pair("fprA", "fprB", 4981, 4982)
    try:
        a.cmd(cmd="setFriend", id=idb, name="fprB")
        got_b = _wait_inbound(b, ida, 8.0)
        check("F1a B receives A's friend-request (inbound, from==fpA)", bool(got_b))
        # A recorded the outbound intent: the outgoing echo confirms it.
        out_a = [x for x in a.events_of("friend-request") if x.get("outgoing") and x.get("to") == idb]
        check("F1b A emitted outgoing friend-request feedback for B", bool(out_a))
        time.sleep(1.0)
        check("F1c A recorded B as a confirmed friend locally", _confirmed(a, idb))
    finally:
        a.stop(); b.stop()

    # ---- F2: mutual fingerprint-add -> instant handshake on both sides ---
    c, d, idc, idd = _pair("mutFprA", "mutFprB", 4983, 4984)
    try:
        c.cmd(cmd="setFriend", id=idd, name="mutFprB")
        time.sleep(0.5)
        d.cmd(cmd="setFriend", id=idc, name="mutFprA")
        acc_c = c.wait_event("friend-accepted", timeout=8)
        acc_d = d.wait_event("friend-accepted", timeout=8)
        check("F2a C auto-accepted D (friend-accepted on C)", bool(acc_c and acc_c.get("id") == idd))
        check("F2b D auto-accepted C (friend-accepted on D)", bool(acc_d and acc_d.get("id") == idc))
        time.sleep(1.0)
        check("F2c C's friends list shows D confirmed", _confirmed(c, idd))
        check("F2d D's friends list shows C confirmed", _confirmed(d, idc))
    finally:
        c.stop(); d.stop()

    # ---- F3: non-mutual control — banner, explicit accept only -----------
    e, f, ide, idf = _pair("ctlFprA", "ctlFprB", 4985, 4986)
    try:
        e.cmd(cmd="setFriend", id=idf, name="ctlFprB")
        got_f = _wait_inbound(f, ide, 8.0)
        check("F3a one-way fingerprint-add surfaces the banner at F", bool(got_f))
        time.sleep(1.0)
        check("F3b no friend-accepted on F (no ghost accept)",
              not any(ev.get("id") == ide for ev in f.events_of("friend-accepted")))
        check("F3c F's friends list does NOT show E confirmed", not _confirmed(f, ide))
        # F explicitly accepts -> handshake completes on both sides.
        f.cmd(cmd="acceptFriend", id=ide)
        acc_f = f.wait_event("friend-accepted", timeout=6)
        acc_e = e.wait_event("friend-accepted", timeout=6)
        check("F3d F's explicit accept confirmed F side", bool(acc_f and acc_f.get("id") == ide))
        check("F3e E learned of the accept (friend-accepted on E)", bool(acc_e and acc_e.get("id") == idf))
        time.sleep(1.0)
        check("F3f both lists confirmed after explicit accept",
              _confirmed(e, idf) and _confirmed(f, ide))
    finally:
        e.stop(); f.stop()

    # ---- F4: offline/unseen friend -> error event, local trust kept ------
    g, _h, idg, _idh = _pair("offA", "offB", 4987, 4988)
    ghost = "0" * 64
    try:
        g.cmd(cmd="setFriend", id=ghost, name="ghost-friend")
        err = g.wait_event("error", timeout=4)
        check("F4a A got an error event for the unseen friend",
              bool(err and "ghost-friend" in (err.get("message") or "")))
        time.sleep(0.8)
        check("F4b no crash (daemon still alive)", g.proc.poll() is None)
        check("F4c ghost friend still recorded locally as confirmed", _confirmed(g, ghost))
    finally:
        g.stop(); _h.stop()

    print()
    if all(checks):
        print("ALL FINGERPRINT-REQUEST TESTS PASSED (%d checks)" % len(checks))
        return 0
    print("FINGERPRINT-REQUEST TESTS FAILED (%d/%d passed)" % (sum(checks), len(checks)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
