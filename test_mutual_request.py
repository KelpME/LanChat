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


def _unsolicited_accept(victim_port, attacker_home, attacker_id, victim_id, name, victim_pport):
    """Fire a VALID signed UDP friend-accept at the victim using the
    attacker's own cert+key while the victim has NO outstanding request to
    the attacker. This is the marketplace review #9076 attack: the signature
    is genuine (it proves identity) but it asserts consent the victim never
    requested."""
    import secrets as _sec
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    cert_dir = os.path.join(attacker_home, ".config", "omarchy", "lanchat-certs")
    with open(os.path.join(cert_dir, "key.pem"), "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), None)
    with open(os.path.join(cert_dir, "cert.pem"), "rb") as fh:
        cert_pem = fh.read().decode()
    nonce = _sec.token_hex(16)
    sig = key.sign((attacker_id + nonce).encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    pkt = {"t": "friend-accept", "id": attacker_id, "name": name, "cert": cert_pem,
           "nonce": nonce, "sig": sig.hex(), "port": victim_pport, "to": victim_id}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(json.dumps(pkt).encode(), ("127.0.0.1", victim_port))
    s.close()


def _pair(name_a, name_b, pa, pb):
    """Two isolated daemons that see each other via mutual discovery beats."""
    a, b, ida, idb, _ha, _hb = _pair6(name_a, name_b, pa, pb)
    return a, b, ida, idb


def _pair6(name_a, name_b, pa, pb):
    """Like _pair but also returns the two HOME dirs (for cert access)."""
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
    return a, b, ida, idb, ha, hb


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

    # ---- M5: unsolicited signed friend-accept is rejected ----------------
    # Regression (marketplace review #9076): a valid signature proves WHO
    # sent the accept, not that we ever asked. A LAN peer that never had an
    # outstanding request from us must NOT be able to confirm itself as a
    # friend by firing a signed UDP friend-accept out of the blue.
    i, j, idi, idj, i_home, j_home = _pair6("forgeA", "forgeB", 4979, 4980)
    try:
        # j has NO idea i exists; i has no outbound intent for j.
        _unsolicited_accept(i.port, j_home, idj, idi, "forgeB", i.port)
        time.sleep(1.5)
        check("M5a unsolicited friend-accept does NOT confirm the peer",
              not _confirmed(i, idj))
        check("M5b no friend-accepted event on the victim",
              not i.events_of("friend-accepted"))
        check("M5c rejected with reason=not-requested",
              any("not-requested" in str(e) and idj[:12] in str(e)
                  for e in i.events_of("diagnostic")))
        # The legit handshake still works after the attack is dropped:
        # mutual requests must still auto-accept both sides.
        i.cmd(cmd="udpFriendRequest", to=idj, name="forgeB")
        j.cmd(cmd="udpFriendRequest", to=idi, name="forgeA")
        time.sleep(2.5)
        check("M5d legit mutual handshake still confirms both sides",
              _confirmed(i, idj) and _confirmed(j, idi))
    finally:
        i.stop(); j.stop()

    # ---- M6: stranger-created pending entry is NOT consent ----------------
    # Regression (marketplace review #9076 round 2): with requests enabled a
    # stranger can authenticate over TCP, send a friendRequest to create its
    # own unconfirmed pending entry, then follow with a valid signed UDP
    # accept. The pending entry was remotely created, so it must NOT satisfy
    # the consent gate — only our own outbound intent does.
    k, m, idk, idm, k_home, m_home = _pair6("chainA", "chainB", 4983, 4984)
    try:
        # Stranger m opens an authenticated TCP connection to victim k and
        # files a friendRequest (banner surfaces on k; no confirmation).
        import test_peer as _tp
        with open(os.path.join(m_home, ".config", "omarchy", "lanchat-certs", "cert.pem")) as fh:
            m_cert = fh.read()
        with open(os.path.join(m_home, ".config", "omarchy", "lanchat-certs", "key.pem")) as fh:
            m_key = fh.read()
        s = _tp.authed_connect("127.0.0.1", k.port, m_cert, m_key)
        s.sendall((json.dumps({"t": "msg", "from": idm, "fromName": "chainB",
                               "text": "be my friend", "friendRequest": True}) + "\n").encode())
        s.close()
        got_k = _wait_inbound(k, idm, 8.0)
        check("M6a stranger TCP friendRequest surfaces as banner", bool(got_k))
        # Same trick as M5: genuine signed UDP accept, no outbound intent.
        _unsolicited_accept(k.port, m_home, idm, idk, "chainB", k.port)
        time.sleep(1.5)
        check("M6b self-created pending + UDP accept does NOT confirm",
              not _confirmed(k, idm))
        check("M6c no friend-accepted event on the victim",
              not k.events_of("friend-accepted"))
        # The legit path through the same pending state still works: the
        # USER accepts the banner -> confirm happens locally on k.
        k.cmd(cmd="acceptFriend", id=idm)
        time.sleep(1.0)
        check("M6d explicit user acceptance still confirms", _confirmed(k, idm))
    finally:
        k.stop(); m.stop()

    print()
    if all(checks):
        print("ALL MUTUAL-REQUEST TESTS PASSED (%d checks)" % len(checks))
        return 0
    print("MUTUAL-REQUEST TESTS FAILED (%d/%d passed)" % (sum(checks), len(checks)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
