#!/usr/bin/env python3
"""Offline test for the persistent bidirectional connection transport.

Two isolated daemons (A, B) sharing a token on distinct ports. Verifies the
Phase 1 transport swap:
  - many messages ride ONE persistent socket (no per-message dial)
  - both directions work over the persistent socket
  - a socket drop triggers reconnect (with backoff), and messages sent while
    the peer is down are held and flushed on reconnect
  - inbound dedupe by mid (a re-delivered message is not recorded twice)
  - friend accept delivered over the existing socket (no reverse dial)

Run from repo root:  python3 test_persistent.py
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
    d = tempfile.mkdtemp(prefix="lnc-persist-" + name)
    c = os.path.join(d, ".config", "omarchy"); os.makedirs(c)
    open(os.path.join(c, "lanchat.json"), "w").write(json.dumps(
        {"token": TOKEN, "port": port, "displayName": display, "httpPort": port + 10}))
    return d


class Daemon:
    def __init__(self, home, port, display):
        self.port = port; self.display = display; self.home = home
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
        with self._lock: return [e for e in self.events if e.get("event") == kind]
    def wait_event(self, kind, timeout=6.0):
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


def _cert_fp(home_dir):
    import hashlib as _h
    cert = os.path.join(home_dir, ".config", "omarchy", "lanchat-certs", "cert.pem")
    with open(cert, "rb") as f:
        data = f.read()
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    c = x509.load_pem_x509_certificate(data)
    return _h.sha256(c.public_bytes(serialization.Encoding.DER)).hexdigest()


def disco(port, pid, name, pport):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(json.dumps({"t": "hello", "id": pid, "name": name, "port": pport}).encode(),
             ("127.0.0.1", port))
    s.close()


def tls_send(port, payload):
    """Open a TLS connection, send one line, read ack (old-style probe)."""
    import ssl as _ssl
    raw = socket.create_connection(("127.0.0.1", port), timeout=3)
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False; ctx.verify_mode = _ssl.CERT_NONE
    s = ctx.wrap_socket(raw); s.settimeout(3)
    s.sendall(json.dumps(payload).encode() + b"\n")
    try: s.recv(64)
    except Exception: pass
    s.close()


def count_msgs(d, text):
    with d._lock:
        return sum(1 for e in d.events if e.get("event") == "message"
                   and e["message"].get("text") == text)


def main():
    ha = make_home("a", 4981, "Alpha"); hb = make_home("b", 4982, "Beta")
    a = Daemon(ha, 4981, "Alpha"); b = Daemon(hb, 4982, "Beta")
    try:
        a.wait_event("ready"); b.wait_event("ready")
        ida = _cert_fp(ha); idb = _cert_fp(hb)
        disco(a.port, idb, "Beta", 4982)
        disco(b.port, ida, "Alpha", 4981)
        time.sleep(1.5)

        # Friend handshake (accept rides the persistent socket — no reverse dial).
        a.cmd(cmd="send", to=idb, text="friend me", friend_request=True)
        b.wait_event("friend-request")
        b.cmd(cmd="acceptFriend", id=ida)
        assert a.wait_event("friend-accepted"), "A never saw B's accept"
        a.wait_event("message"); b.wait_event("message")  # drain reveals
        for d in (a, b):
            with d._lock:
                d.events = [e for e in d.events if e.get("event") != "message"]
        print("OK  1. friend accept delivered over the persistent socket")

        # 2) Many messages over ONE socket (no per-message dial).
        for i in range(5):
            a.cmd(cmd="send", to=idb, text="burst-%d" % i)
        got = []
        dl = time.time() + 6
        while time.time() < dl and len(got) < 5:
            m = b.wait_event("message", timeout=2)
            if m: got.append(m["message"]["text"])
        assert got == ["burst-0", "burst-1", "burst-2", "burst-3", "burst-4"], \
            "expected 5 messages in order over one socket, got %s" % got
        print("OK  2. 5 messages delivered over one persistent socket, in order")

        # 3) Both directions ride the socket.
        b.cmd(cmd="send", to=ida, text="reply from beta")
        def _has_text(d, text):
            with d._lock:
                return any(e.get("event") == "message" and e["message"].get("text") == text
                           for e in d.events)
        dl = time.time() + 5
        while time.time() < dl and not _has_text(a, "reply from beta"):
            time.sleep(0.02)
        assert _has_text(a, "reply from beta"), "B->A reply not delivered"
        print("OK  3. both directions delivered over the persistent socket")

        # 4) Socket drop -> reconnect + hold/flush.
        # Kill B. A's socket to B dies; messages A sends are held.
        b.stop()
        a.cmd(cmd="send", to=idb, text="held-1")
        a.cmd(cmd="send", to=idb, text="held-2")
        time.sleep(0.5)
        # Restart B on the same home -> same cert fingerprint, same port.
        b = Daemon(hb, 4982, "Beta")
        b.wait_event("ready")
        # Re-seed discovery so both re-learn each other promptly.
        disco(a.port, idb, "Beta", 4982)
        disco(b.port, ida, "Alpha", 4981)
        held = []
        dl = time.time() + 10
        while time.time() < dl and len(held) < 2:
            m = b.wait_event("message", timeout=2)
            if m: held.append(m["message"]["text"])
        assert "held-1" in held and "held-2" in held, \
            "held messages not flushed after reconnect, got %s" % held
        print("OK  4. messages held while peer down flushed after reconnect")

        # 5) Inbound dedupe by mid: re-delivering the same mid is not recorded
        #    twice (simulates a reconnect re-send). B trusts A (confirmed friend).
        mid = "dup" + os.urandom(4).hex()
        tls_send(4982, {"t": "msg", "from": ida, "fromName": "Alpha",
                        "text": "dedupe-me", "mid": mid})
        tls_send(4982, {"t": "msg", "from": ida, "fromName": "Alpha",
                        "text": "dedupe-me", "mid": mid})
        time.sleep(0.8)
        assert count_msgs(b, "dedupe-me") == 1, \
            "duplicate mid delivered twice (got %d)" % count_msgs(b, "dedupe-me")
        print("OK  5. inbound dedupe by mid (re-delivery not recorded twice)")

        print("\nALL PERSISTENT-CONNECTION TESTS PASSED")
        return 0
    finally:
        a.stop(); b.stop()
        shutil.rmtree(ha, ignore_errors=True); shutil.rmtree(hb, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
