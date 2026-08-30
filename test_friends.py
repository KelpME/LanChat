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
import json, os, shutil, socket, subprocess, sys, tempfile, threading, time

TOKEN = "test-shared-secret-token"
HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")


def make_home(name, port, display):
    d = tempfile.mkdtemp(prefix="lnc-friend-" + name)
    c = os.path.join(d, ".config", "omarchy"); os.makedirs(c)
    open(os.path.join(c, "lanchat.json"), "w").write(json.dumps(
        {"token": TOKEN, "port": port, "displayName": display, "httpPort": port + 10}))
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

        # 1) Unknown host (stranger) plain message to A is dropped.
        a.cmd(cmd="history")
        before = len(a.events_of("message"))
        tls_send(4951, {"t": "msg", "from": "stranger", "fromName": "Stranger", "text": "hi"})
        time.sleep(0.8)
        assert len(a.events_of("message")) == before, "stranger message leaked through!"
        print("OK  1. stranger plain message dropped")

        # 2) Stranger sends a FRIEND REQUEST -> it gets through (pending on A).
        tls_send(4951, {"t": "msg", "from": "stranger", "fromName": "Stranger", "text": "be friends?", "friendRequest": True})
        m = a.wait_event("message")
        assert m and m["message"].get("friendRequest") and m["message"]["text"] == "be friends?"
        print("OK  2. friend request from stranger gets through")

        # 3) A sends B a normal message while not friends -> B drops it (not trusted).
        a.cmd(cmd="send", to=idb, text="hello without friend req")
        time.sleep(0.8)
        # B may have seen nothing; ensure B did NOT record it as a message.
        assert not [e for e in b.events_of("message") if e["message"].get("text") == "hello without friend req"]
        print("OK  3. message to non-friend without request is dropped by B")

        # 4) A sends B a friend request via send (friend_request=True) -> B receives as pending.
        a.cmd(cmd="send", to=idb, text="want to be friends?", friend_request=True)
        m2 = b.wait_event("message")
        # debug: dump A's errors
        errs = a.events_of("error")
        if not m2:
            print("  [debug] A error events:", errs)
            print("  [debug] B message events:", [e["message"] for e in b.events_of("message")])
        assert m2 and m2["message"].get("friendRequest"), "B did not get friend request"
        # A now has beta as pending (confirmed=False).
        fa = a.events_of("friends")
        assert fa and any(f["id"] == idb and not f["confirmed"] for f in fa[-1]["friends"])
        print("OK  4. A->B friend request; A has beta pending")

        # 5) B accepts via stdin -> A gets friend-accepted, both confirmed.
        b.cmd(cmd="acceptFriend", id=ida)
        ae = a.wait_event("friend-accepted")
        # A emits friend-accepted with the *sender's* id (the peer who accepted = B/idb).
        assert ae and ae["id"] == idb, "A should see accept from B(idb), got %s" % (ae or {}).get("id")
        # B confirms alpha as friend (give B's friend event a moment to land).
        time.sleep(0.5)
        fb = b.events_of("friends")
        assert any(f["id"] == ida and f["confirmed"] for f in fb[-1]["friends"]), \
            "B friends=%s" % [f["id"][:8] for e in fb for f in e["friends"]]
        print("OK  5. B accepts -> A friend-accepted; B confirmed alpha")

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

        print("\nALL FRIEND TESTS PASSED")
        return 0
    finally:
        a.stop(); b.stop()
        shutil.rmtree(ha, ignore_errors=True); shutil.rmtree(hb, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
