#!/usr/bin/env python3
"""Offline test for MUTUAL friend-request auto-accept.

Two isolated daemons (A, B) sharing a token on distinct ports. Verifies:
  M1  mutual requests auto-accept: A requests B, then B requests A back ->
      BOTH daemons emit friend-accepted and both friends lists show the
      other as confirmed. Neither user clicked Accept.
  M2  non-mutual control: fresh daemons, A requests B only -> B surfaces a
      friend-request banner event and does NOT confirm (no ghost accept).
  M3  stale-intent guard: after M2's pair is unfriended, a NEW request from
      the same peer does not auto-accept (intent was discarded).
  M4  crossed simultaneous requests: both sides end confirmed (idempotent).

Run from repo root:  python3 test_mutual_request.py
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

USED_PORTS = set()


def make_home(name, port, display):
    d = tempfile.mkdtemp(prefix="lnc-mutual-" + name)
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

    # ---- M1: mutual requests auto-accept --------------------------------
    a, b, ida, idb = _pair("mutA", "mutB", 4971, 4972)
    try:
        a.cmd(cmd="udpFriendRequest", to=idb, name="mutB")
        got_b = b.wait_event("friend-request", timeout=6)
        check("M1a A->B request arrives at B (banner event)", bool(got_b and got_b.get("from") == ida))
        # B sends a request BACK instead of clicking Accept.
        b.cmd(cmd="udpFriendRequest", to=ida, name="mutA")
        acc_b = b.wait_event("friend-accepted", timeout=6)
        acc_a = a.wait_event("friend-accepted", timeout=6)
        check("M1b B auto-accepted A (friend-accepted on B)", bool(acc_b and acc_b.get("id") == ida))
        check("M1c A confirmed via the accept notify (friend-accepted on A)", bool(acc_a and acc_a.get("id") == idb))
        time.sleep(1.0)
        check("M1d A's friends list shows B confirmed", _confirmed(a, idb))
        check("M1e B's friends list shows A confirmed", _confirmed(b, ida))
    finally:
        a.stop(); b.stop()

    # ---- M2: non-mutual control — banner still shows, no auto-accept ----
    c, d, idc, idd = _pair("ctlA", "ctlB", 4973, 4974)
    try:
        c.cmd(cmd="udpFriendRequest", to=idd, name="ctlB")
        got_d = d.wait_event("friend-request", timeout=6)
        check("M2a one-way request still surfaces the banner event", bool(got_d and got_d.get("from") == idc))
        time.sleep(1.5)
        check("M2b no friend-accepted on D (no ghost accept)", not d.events_of("friend-accepted"))
        check("M2c D's friends list does NOT show C confirmed", not _confirmed(d, idc))
    finally:
        c.stop(); d.stop()

    # ---- M3: stale intent discarded — unfriend, re-request, no accept ---
    e, f, ide, idf = _pair("staleA", "staleB", 4975, 4976)
    try:
        e.cmd(cmd="udpFriendRequest", to=idf, name="staleB")
        got_f = f.wait_event("friend-request", timeout=6)
        check("M3a first request surfaces at F", bool(got_f))
        # F now sends a request BACK -> mutual accept (baseline for the guard).
        f.cmd(cmd="udpFriendRequest", to=ide, name="staleA")
        acc = e.wait_event("friend-accepted", timeout=6)
        check("M3b mutual accept fired", bool(acc))
        time.sleep(1.0)
        check("M3b2 F actually confirmed as E's friend", _confirmed(e, idf))
        # Unfriend: E drops F (notifies F over signed UDP; both sides drop).
        n_rej_f = len(f.events_of("friend-rejected"))
        e.cmd(cmd="unfriend", id=idf)
        time.sleep(1.5)
        removed = any(ev.get("id") == idf for ev in e.events_of("friend-removed"))
        check("M3c E actually removed F (friend-removed)", removed)
        # E's outbound intent for F must now be gone. F re-requests E:
        # E must NOT auto-accept — the request must surface as a banner.
        # wait_inbound skips E's own outgoing echo (outgoing:true has no
        # "from", and a naive wait_event pops it and fails the check).
        f.cmd(cmd="udpFriendRequest", to=ide, name="staleA")
        got_e = _wait_inbound(e, idf, 8.0)
        check("M3d after unfriend, re-request surfaces as a banner (no ghost accept)",
              bool(got_e))
        check("M3e no friend-accepted on E after re-request",
              not any(ev.get("id") == idf for ev in e.events_of("friend-accepted")))
    finally:
        e.stop(); f.stop()

    # ---- M4: crossed simultaneous requests --------------------------------
    g, h, idg, idh = _pair("crossA", "crossB", 4977, 4978)
    try:
        g.cmd(cmd="udpFriendRequest", to=idh, name="crossB")
        time.sleep(0.3)  # both in flight; order irrelevant
        h.cmd(cmd="udpFriendRequest", to=idg, name="crossA")
        time.sleep(2.5)
        check("M4a crossed requests: G confirmed with H", _confirmed(g, idh))
        check("M4b crossed requests: H confirmed with G", _confirmed(h, idg))
    finally:
        g.stop(); h.stop()

    print()
    if all(checks):
        print("ALL MUTUAL-REQUEST TESTS PASSED (%d checks)" % len(checks))
        return 0
    print("MUTUAL-REQUEST TESTS FAILED (%d/%d passed)" % (sum(checks), len(checks)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
