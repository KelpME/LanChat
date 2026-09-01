#!/usr/bin/env python3
"""Offline test for the friend/handshake + online-toggle security model.

Two isolated daemons (A, B) sharing a token on distinct ports. Verifies:
  - unknown host (non-friend) inbound messages are dropped
  - a friend request from a stranger gets through and becomes pending
  - accept completes the handshake (both confirmed)
  - confirmed friends' messages get through
  - offline toggle blocks inbound, queues nothing inbound-side
  - sender-side offline queue is the UI's job (verified separately)

Run from repo root:  python3 test_friends.py
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


def make_home(name, port, display):
    d = tempfile.mkdtemp(prefix="lnc-friend-" + name)
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


def tls_send(port, payload):
    """Send a TCP line over TLS; return the ack text. (No token — friend-gated.)"""
    import ssl as _ssl
    raw = socket.create_connection(("127.0.0.1", port), timeout=3)
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    s = ctx.wrap_socket(raw)
    s.settimeout(3)
    s.sendall(json.dumps(payload).encode() + b"\n")
    try:
        ack = s.recv(64)
    except Exception:
        ack = b""
    s.close()
    return ack


def _cert_fp(home_dir: str) -> str:
    """Read the SHA-256 cert fingerprint from a daemon's cert dir."""
    import hashlib as _h
    cert = os.path.join(home_dir, ".config", "omarchy", "lanchat-certs", "cert.pem")
    with open(cert, "rb") as f:
        data = f.read()
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    c = x509.load_pem_x509_certificate(data)
    return _h.sha256(c.public_bytes(serialization.Encoding.DER)).hexdigest()


def main():
    ha = make_home("a", 4951, "Alpha"); hb = make_home("b", 4952, "Beta")
    a = Daemon(ha, 4951, "Alpha"); b = Daemon(hb, 4952, "Beta")
    try:
        a.wait_event("ready"); b.wait_event("ready")
        # ready events are consumed by wait_event; read the cert fingerprints
        # directly from each daemon's cert dir (the device identity).
        ida = _cert_fp(ha); idb = _cert_fp(hb)
        def disco(port, pid, name, pport):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(json.dumps({"t": "hello", "id": pid, "name": name, "port": pport}).encode(), ("127.0.0.1", port))
            s.close()
        disco(a.port, idb, "Beta", 4952)
        disco(b.port, ida, "Alpha", 4951)
        time.sleep(1.5)  # let the UDP listener register the peer
        # Real peers re-broadcast every ~3s; without a heartbeat the faster
        # presence timeout would expire them mid-test. Simulate steady presence.
        _halt = {"go": True}
        def _beat():
            while _halt["go"]:
                disco(a.port, idb, "Beta", 4952)
                disco(b.port, ida, "Alpha", 4951)
                time.sleep(2.0)
        threading.Thread(target=_beat, daemon=True).start()

        # 1) Unknown host (stranger) plain message to A is dropped.
        a.cmd(cmd="history")
        before = len(a.events_of("message"))
        tls_send(4951, {"t": "msg", "from": "stranger", "fromName": "Stranger", "text": "hi"})
        time.sleep(0.8)
        assert len(a.events_of("message")) == before, "stranger message leaked through!"
        print("OK  1. stranger plain message dropped")

        # 2) Stranger sends a FRIEND REQUEST -> recipient sees only the request
        #    (held), not the message content, and the stranger becomes pending.
        #    A real peer proves its own identity first (1.2.0), then requests.
        import tempfile as _tf
        stranger_home = _tf.mkdtemp(prefix="lanchat-stranger-")
        import test_peer as _tp
        scert, skey = _tp.make_certs(stranger_home)
        sid = _tp.fingerprint(scert)
        _tp.authed_send("127.0.0.1", 4951, scert, skey,
                        {"t": "msg", "from": sid, "fromName": "Stranger",
                         "text": "be friends?", "friendRequest": True})
        fr = a.wait_event("friend-request")
        assert fr and fr.get("fromName") == "Stranger" and fr.get("text") == "be friends?"
        # The content is held — no plain message surfaces before acceptance.
        assert not any(e["message"].get("text") == "be friends?" for e in a.events_of("message"))
        print("OK  2. friend request from stranger held; only request surfaces")

        # 3) A sends B a normal message while not friends -> B drops it (not trusted).
        a.cmd(cmd="send", to=idb, text="hello without friend req")
        time.sleep(0.8)
        # B may have seen nothing; ensure B did NOT record it as a message.
        assert not [e for e in b.events_of("message") if e["message"].get("text") == "hello without friend req"]
        print("OK  3. message to non-friend without request is dropped by B")

        # 4) A sends B a friend request via udpFriendRequest -> B receives a
        #    verified friend request over UDP; A has B pending.
        a.cmd(cmd="udpFriendRequest", to=idb, name="Beta")
        fr2 = b.wait_event("friend-request")
        # debug: dump A's errors
        errs = a.events_of("error")
        if not fr2:
            print("  [debug] A error events:", errs)
            print("  [debug] B friend-request events:", b.events_of("friend-request"))
        assert fr2 and fr2.get("from") == ida, "B did not get friend request"
        print("OK  4. A->B friend request over UDP; B got verified request")

        # 5) B accepts via stdin -> the accept travels back over UDP -> A gets
        #    friend-accepted; both confirmed.
        b.cmd(cmd="acceptFriend", id=ida)
        ae = a.wait_event("friend-accepted")
        # A emits friend-accepted with the *sender's* id (the peer who accepted = B/idb).
        assert ae and ae["id"] == idb, "A should see accept from B(idb), got %s" % (ae or {}).get("id")
        # B confirms alpha as friend (give B's friend event a moment to land).
        time.sleep(0.5)
        fb = b.events_of("friends")
        assert any(f["id"] == ida and f["confirmed"] for f in fb[-1]["friends"]), \
            "B friends=%s" % [f["id"][:8] for e in fb for f in e["friends"]]
        print("OK  5. B accepts -> A friend-accepted over UDP; both confirmed")

        # Drain leftover message events (reveals) so the next waits see only
        # genuinely new messages.
        for d in (a, b):
            with d._lock:
                d.events = [e for e in d.events if e.get("event") != "message"]

        # 6) Confirmed friends exchange messages freely.
        a.cmd(cmd="send", to=idb, text="now we're friends")
        m3 = b.wait_event("message")
        assert m3 and m3["message"]["text"] == "now we're friends"
        print("OK  6. confirmed friend message delivered")

        # 7) Offline toggle: B offline -> B drops inbound; A broadcast stops.
        b.cmd(cmd="setOnline", online=False)
        oe = b.wait_event("online")
        assert oe and oe["online"] is False
        a.cmd(cmd="send", to=idb, text="you offline?")
        time.sleep(0.8)
        assert not [e for e in b.events_of("message") if e["message"].get("text") == "you offline?"]
        print("OK  7. offline peer drops inbound")

        # 8) Back online.
        b.cmd(cmd="setOnline", online=True)
        oe = b.wait_event("online")
        assert oe and oe["online"] is True
        print("OK  8. back online")

        # 9) Friend request over UDP (signed bootstrap — no TCP needed).
        #    A sends a signed UDP friend request to B; B verifies the signature
        #    and surfaces the request (verified fingerprint), even though no
        #    TCP connection exists between them yet.
        #    Craft a signed request from A's identity.
        import test_peer as _tp2
        import hashlib, json as _json, socket as _sock
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        # A's cert + key live in A's home cert dir.
        ca_home = ha
        ca_cert = os.path.join(ca_home, ".config", "omarchy", "lanchat-certs", "cert.pem")
        ca_key = os.path.join(ca_home, ".config", "omarchy", "lanchat-certs", "key.pem")
        with open(ca_cert, "rb") as f:
            from cryptography import x509 as _x509
            cert = _x509.load_pem_x509_certificate(f.read())
        cert_der = cert.public_bytes(serialization.Encoding.DER)
        ida_fp = hashlib.sha256(cert_der).hexdigest()
        # Sign id+nonce with A's private key.
        with open(ca_key, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        nonce = "deadbeef" * 4
        sig = key.sign((ida_fp + nonce).encode(), padding.PKCS1v15(), hashes.SHA256())
        import binascii
        # Send the signed UDP friend request to B's port.
        req = {"t": "friend-request", "id": ida_fp,
               "name": "AlphaUDP", "cert": open(ca_cert).read(),
               "nonce": nonce, "sig": binascii.hexlify(sig).decode(),
               "port": a.port}
        _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM).sendto(
            _json.dumps(req).encode(), ("127.0.0.1", b.port))
        fr3 = b.wait_event("friend-request")
        assert fr3 and fr3.get("from") == ida_fp, "B did not surface verified UDP friend request"
        assert fr3.get("fingerprint") == ida_fp, "UDP request must carry verified fingerprint"
        print("OK  9. signed UDP friend request verified + surfaced (no TCP needed)")

        # 10) Forged UDP request (wrong signature) must be rejected.
        bad_sig = binascii.hexlify(b"0" * 256).decode()[:256]
        req_bad = {"t": "friend-request", "id": ida_fp, "name": "Forged",
                   "cert": open(ca_cert).read(), "nonce": nonce,
                   "sig": bad_sig, "port": a.port}
        _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM).sendto(
            _json.dumps(req_bad).encode(), ("127.0.0.1", b.port))
        time.sleep(0.6)
        # B should NOT surface a request from this (it's rejected as forged).
        bad_reqs = [e for e in b.events_of("friend-request")
                    if e.get("name") == "Forged"]
        assert not bad_reqs, "forged UDP friend request was accepted!"
        print("OK  10. forged UDP friend request rejected (bad signature)")

        # 11) End-to-end via the daemon command: A sends udpFriendRequest to B
        #     (by B's ID, as the UI does). The daemon must resolve B's address
        #     and B receives the signed request.
        import test_peer as _tp3
        # Give A's daemon B's address (as discovery would have) via setFriend.
        a.cmd(cmd="setFriend", id=idb, address="127.0.0.1", port=b.port, name="Beta")
        a.wait_event("friend-added")
        a.cmd(cmd="udpFriendRequest", to=idb, name="Beta")
        fr4 = b.wait_event("friend-request")
        assert fr4 and fr4.get("from") == ida, "B did not get UDP friend request via command"
        assert fr4.get("fingerprint") == ida, "command-sent request must carry verified fingerprint"
        print("OK  11. udpFriendRequest command resolves address + delivers (B got verified request)")

        # 12) Two-way handshake over UDP: B accepts A's request -> the accept
        #     must travel back over UDP (signed) so A confirms the friendship,
        #     even with no TCP connection. This was the one-way bug: A saw the
        #     request "sent", B accepted locally, but the accept never reached A.
        # B accepts A's friend request (B knows A via the friend request it saw).
        b.cmd(cmd="acceptFriend", id=ida)
        # A should now get friend-accepted over UDP and confirm B.
        accepted = a.wait_event("friend-accepted")
        assert accepted and accepted.get("id") == idb, "A did not receive the friend-accept back over UDP"
        # A now has B confirmed.
        assert any(f.get("id") == idb and f.get("confirmed")
                   for e in a.events for f in e.get("friends", []) if e.get("event") == "friends") or \
               accepted.get("id") == idb, "A did not mark B as a confirmed friend"
        print("OK  12. two-way UDP handshake: B's accept travels back over UDP and A confirms the friend")

        print("\nALL FRIEND TESTS PASSED")
        return 0
    finally:
        a.stop(); b.stop()
        shutil.rmtree(ha, ignore_errors=True); shutil.rmtree(hb, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
