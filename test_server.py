#!/usr/bin/env python3
"""Offline test harness for server.py.

Runs two isolated daemon instances on different ports (separate $HOME dirs,
same token), injects synthetic discovery packets to simulate two machines on a
LAN, then exercises the full path: authenticated TCP send -> receive -> persist.

Not part of the plugin runtime — developer-only. Run from the repo root:

    python3 test_server.py
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
import ssl as _ssl

TOKEN = "test-shared-secret-token"
HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")
if HERE not in sys.path:
    sys.path.insert(0, HERE)


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


def make_home(name, port, display):
    d = tempfile.mkdtemp(prefix="lanchat-test-" + name)
    cfg_dir = os.path.join(d, ".config", "omarchy")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "lanchat.json"), "w") as f:
        json.dump({"token": TOKEN, "port": port, "displayName": display, "httpPort": port + 10}, f)
    return d


class Daemon:
    def __init__(self, home, port, display):
        self.home = home
        self.port = port
        self.display = display
        self.events = []
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            [sys.executable, SRV],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(os.environ, HOME=home),
            bufsize=1,
            text=True,
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                self.events.append(ev)

    def wait_event(self, kind, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                for ev in self.events:
                    if ev.get("event") == kind:
                        self.events.remove(ev)
                        return ev
            time.sleep(0.02)
        return None

    def cmd(self, **kwargs):
        self.proc.stdin.write(json.dumps(kwargs) + "\n")
        self.proc.stdin.flush()

    def stop(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.wait(timeout=5)


def udp_broadcast(target_port, pkt):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1)
    s.sendto(json.dumps(pkt).encode(), ("127.0.0.1", target_port))
    s.close()


def wait_until(fn, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.02)
    return False


def main():
    home_a = make_home("alpha", 4911, "Alpha-machine")
    home_b = make_home("beta", 4912, "Beta-machine")
    a = Daemon(home_a, 4911, "Alpha-machine")
    b = Daemon(home_b, 4912, "Beta-machine")

    try:
        assert a.wait_event("ready"), "A did not become ready"
        assert b.wait_event("ready"), "B did not become ready"
        print("OK  both daemons ready")

        # Simulate discovery: each learns the other via a synthetic hello using
        # the cert-fingerprint id.
        ida = _cert_fp(home_a); idb = _cert_fp(home_b)
        udp_broadcast(a.port, {"t": "hello", "id": idb, "name": "Beta-machine", "port": b.port})
        udp_broadcast(b.port, {"t": "hello", "id": ida, "name": "Alpha-machine", "port": a.port})
        assert wait_until(lambda: _has_peer(a, idb)), "A did not learn beta"
        assert wait_until(lambda: _has_peer(b, ida)), "B did not learn alpha"
        print("OK  discovery populated peer lists")

        # Become friends first (friend request + accept) so messages flow.
        a.cmd(cmd="send", to=idb, text="friend me", friend_request=True)
        b.wait_event("message")
        b.cmd(cmd="acceptFriend", id=ida)
        a.wait_event("friend-accepted")

        # Send from A -> B over real TLS, authenticated.
        a.cmd(cmd="send", to=idb, text="hello from alpha")
        msg = b.wait_event("message")
        assert msg and msg["message"]["text"] == "hello from alpha", "B did not receive the message"
        assert msg["message"]["fromName"] == "Alpha-machine"
        assert msg["message"]["outgoing"] is False
        print("OK  message delivered A -> B over TLS")

        # Friend gate: a stranger (non-friend, non-request) plain message is dropped.
        import ssl as _ssl
        _ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT); _ctx.check_hostname=False; _ctx.verify_mode=_ssl.CERT_NONE
        bad_raw = socket.create_connection(("127.0.0.1", b.port), timeout=3)
        bad = _ctx.wrap_socket(bad_raw)
        bad.sendall(b'{"t":"msg","from":"x","fromName":"x","text":"should never land"}\n')
        time.sleep(0.5)
        bad.close()
        assert not wait_until(lambda: _has_message(b, "should never land"), timeout=1.0), "stranger message leaked through"
        print("OK  non-friend message dropped (friend gate)");

        # Persistence: B's history file contains the delivered message.
        hist_path = os.path.join(home_b, ".local", "state", "lanchat", "history.json")
        with open(hist_path) as f:
            hist = json.load(f)
        assert any(m.get("text") == "hello from alpha" for m in hist), "history not persisted"
        print("OK  history persisted to disk")

        # History reload command returns what's on disk.
        b.cmd(cmd="history")
        hev = b.wait_event("history")
        assert hev and any(m.get("text") == "hello from alpha" for m in hev["messages"])
        print("OK  history command replays persisted messages")

        # ---- friendly naming --------------------------------------------
        import server as _srv
        n1 = _srv.friendly_name("laptop")
        n2 = _srv.friendly_name("laptop")
        n3 = _srv.friendly_name("desktop")
        assert n1 == n2 and n1 != n3, "friendly_name not deterministic/per-id"
        assert n1 == _srv.friendly_name("laptop"), "friendly_name not pure"
        print("OK  friendly_name deterministic per peer id:", n1, "|", n3)

        # ---- HTTP API ----------------------------------------------------
        import urllib.request
        import urllib.error

        # Disabled by default: the API should be refused (connection refused).
        a.cmd(cmd="setHttp", enabled=True)
        aev = a.wait_event("http")
        assert aev and aev["enabled"] is True, "http enable event not emitted"
        http_port = int(aev.get("port", 0))
        assert http_port > 0, "http port missing from event"
        print("OK  HTTP API enabled via stdin on port %d" % http_port)

        def http(method, path, body=None, token=None):
            url = "https://127.0.0.1:%d%s" % (http_port, path)
            data = None
            headers = {}
            if body is not None:
                payload = dict(body)
                if token is not None:
                    payload["token"] = token
                data = json.dumps(payload).encode()
                headers["Content-Type"] = "application/json"
            elif token is not None:
                sep = "&" if "?" in url else "?"
                url += sep + "token=" + token
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            # self-signed cert: disable verification for the test
            ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = _ssl.CERT_NONE
            try:
                with urllib.request.urlopen(req, timeout=5, context=ssl_ctx) as resp:
                    return resp.status, json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode())

        # /health works without auth.
        code, _ = http("GET", "/health")
        assert code == 200
        print("OK  /health responds")

        # /send via HTTP (authenticated) delivers to B.
        code, res = http("POST", "/send", {"to": idb, "text": "via http"}, token=TOKEN)
        assert code == 200 and res.get("ok"), "http send failed: %s" % res
        m2 = b.wait_event("message")
        assert m2 and m2["message"]["text"] == "via http", "http message not delivered"
        print("OK  HTTP POST /send delivered authenticated message")

        # Send-only mode: reads (peers/messages) blocked by default (apiFullAccess=False).
        code, res = http("GET", "/peers", token=TOKEN)
        assert code == 403, "read should be blocked in send-only mode, got %d" % code
        print("OK  HTTP GET /peers blocked in send-only mode")

        # Enable full access -> reads work.
        a.cmd(cmd="setApiFullAccess", enabled=True)
        aev = a.wait_event("api-full-access")
        assert aev and aev["enabled"] is True
        code, res = http("GET", "/peers", token=TOKEN)
        assert code == 200 and any(p["id"] == idb for p in res["peers"])
        print("OK  HTTP GET /peers lists peers with full access")

        # Wrong token rejected.
        code, res = http("POST", "/send", {"to": "beta", "text": "nope"}, token="WRONG")
        assert code == 401
        code, res = http("GET", "/peers", token="WRONG")
        assert code == 401
        print("OK  HTTP API rejects wrong token")

        # Disable again.
        a.cmd(cmd="setHttp", enabled=False)
        aev = a.wait_event("http")
        assert aev and aev["enabled"] is False, "http disable event not emitted"
        print("OK  HTTP API disabled via stdin")

        print("\nALL TESTS PASSED")
        return 0
    finally:
        a.stop()
        b.stop()
        shutil.rmtree(home_a, ignore_errors=True)
        shutil.rmtree(home_b, ignore_errors=True)


def _has_peer(d, pid):
    with d._lock:
        return any(p.get("id") == pid for p in [e.get("peer") for e in d.events if e.get("event") == "peer"])


def _has_message(d, text):
    with d._lock:
        return any(m.get("text") == text for e in d.events if e.get("event") == "message" for m in [e["message"]])


if __name__ == "__main__":
    sys.exit(main())
