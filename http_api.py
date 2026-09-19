#!/usr/bin/env python3
"""Optional HTTP API for kelpme.lanchat — extracted verbatim from server.py (Commit 2).

A small stdlib HTTP server for sending messages / reading state from other
tools (curl, scripts, an agent). Disabled by default; toggled on/off from the
panel UI. Authenticated with the same shared token as the TCP/UDP layer.

server.py keeps owning the collaborator functions (_emit, ensure_tls,
http_port, http_bind, api_full_access, peer_snapshot, history_snapshot,
find_peer, send_message, get_attachment); every call site here goes through a
deferred `import server` inside the function/method body — late-bound, no
import cycle, monkeypatch-safe.

Ownership: the module-private _http_server/_http_server_thread globals.
"""

import http.server
import json
import os
import threading
import time
import urllib.parse

# init(state) wiring: STATE is bound once by server.py at import time
# (http_api.init(STATE)).

STATE = None

# /attachment streams in this chunk size instead of buffering whole files.
ATT_HTTP_CHUNK = 64 * 1024


def init(state):
    """Bind this module's STATE to the daemon's shared State instance."""
    global STATE
    STATE = state


_http_server = None
_http_server_thread = None


class _ApiHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request logging
        pass

    # --- rate limiting ------------------------------------------------------
    # Class-level shared state: a simple sliding-window throttle on outbound
    # sends and on failed auth (brute-force guard). Rates are modest; a legit
    # local agent won't hit them, but a scanner/attacker quickly does.
    _MAX_BODY = 256 * 1024            # reject bodies larger than this
    _SEND_WINDOW_S = 10.0             # allow up to _SEND_MAX per window
    _SEND_MAX = 40
    _AUTH_WINDOW_S = 10.0             # allow up to _AUTH_MAX failed auths
    _AUTH_MAX = 10
    _send_ts = []                     # timestamps of recent /send calls
    _auth_fail_ts = []                # timestamps of recent failed auths
    _rl_lock = threading.Lock()

    @classmethod
    def _throttle(cls, bucket, limit, window, now):
        """Return True if the request is allowed; else False (over the limit)."""
        with cls._rl_lock:
            bucket[:] = [t for t in bucket if now - t < window]
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def _auth_ok(self, token):
        ok = bool(token) and token == STATE.config.get("token")
        if not ok:
            # Count the failure for the brute-force throttle.
            self._throttle(self._auth_fail_ts, self._AUTH_MAX, self._AUTH_WINDOW_S, time.time())
        return ok

    def _auth_blocked(self):
        """True if the recent failed-auth window is full (too many bad tokens)."""
        now = time.time()
        with self._rl_lock:
            self._auth_fail_ts[:] = [t for t in self._auth_fail_ts if now - t < self._AUTH_WINDOW_S]
            return len(self._auth_fail_ts) >= self._AUTH_MAX

    def _send_json(self, code, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > self._MAX_BODY:
            self._send_json(413, {"ok": False, "error": "request body too large"})
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def _request_token(self):
        """The request's auth token. Accepted ONLY from the Authorization
        header (Bearer scheme) — never from the URL query, which leaks into
        server logs, history, diagnostics, and process listings. An empty
        string means 'no token' and fails _auth_ok downstream."""
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        token = self._request_token()
        if parsed.path == "/health":
            return self._send_json(200, {"ok": True})
        if self._auth_blocked():
            return self._send_json(429, {"ok": False, "error": "rate limited"})
        if not self._auth_ok(token):
            return self._send_json(401, {"ok": False, "error": "unauthorized"})
        if parsed.path == "/peers":
            import server
            if not server.api_full_access():
                return self._send_json(403, {"ok": False, "error": "read access disabled"})
            return self._send_json(200, {"ok": True, "peers": server.peer_snapshot()})
        if parsed.path == "/messages":
            import server
            if not server.api_full_access():
                return self._send_json(403, {"ok": False, "error": "read access disabled"})
            return self._send_json(200, {"ok": True, "messages": server.history_snapshot()})
        if parsed.path == "/attachment":
            import server
            # Serving a registered file to a confirmed friend (token in the
            # Authorization header) is peer-to-peer file transfer, not script
            # read-access — so it must NOT be gated behind apiFullAccess. Auth
            # is enforced above.
            file_id = (qs.get("fileId") or [""])[0]
            att = server.get_attachment(file_id)
            if not att:
                return self._send_json(404, {"ok": False, "error": "not found"})
            try:
                size = os.stat(att["path"]).st_size
            except OSError:
                return self._send_json(404, {"ok": False, "error": "file missing"})
            # Stream in fixed chunks: never buffer the whole file in daemon
            # memory. A file that shrinks/vanishes mid-stream just truncates
            # the body — the short Content-Length signals it to the client.
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", "attachment; filename=%s" % att["name"])
            self.end_headers()
            try:
                with open(att["path"], "rb") as f:
                    sent = 0
                    while sent < size:
                        chunk = f.read(ATT_HTTP_CHUNK)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        sent += len(chunk)
            except OSError:
                # Header already sent — can't turn this into a 404; drop the
                # connection rather than continue with a corrupt body.
                self.close_connection = True
                return
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        import server
        parsed = urllib.parse.urlparse(self.path)
        body = self._body()
        if body is None:  # oversized body already answered with 413
            return
        if parsed.path == "/send":
            token = self._request_token()
            if self._auth_blocked():
                return self._send_json(429, {"ok": False, "error": "rate limited"})
            if not self._auth_ok(token):
                return self._send_json(401, {"ok": False, "error": "unauthorized"})
            if not self._throttle(self._send_ts, self._SEND_MAX, self._SEND_WINDOW_S, time.time()):
                return self._send_json(429, {"ok": False, "error": "rate limited"})
            to = str(body.get("to", ""))
            text = str(body.get("text", ""))
            if not to or not text.strip():
                return self._send_json(400, {"ok": False, "error": "to and text required"})
            if server.find_peer(to) is None:
                return self._send_json(404, {"ok": False, "error": "peer offline"})
            if not server.send_message(to, text):
                return self._send_json(500, {"ok": False, "error": "delivery failed"})
            return self._send_json(200, {"ok": True})
        self._send_json(404, {"ok": False, "error": "not found"})


def _start_http() -> bool:
    global _http_server, _http_server_thread
    import server
    if _http_server is not None:
        return True
    # Retry briefly: after a daemon restart the previous HTTP socket may still
    # be settling, and a single failed bind would otherwise leave the API off
    # until the toggle is flipped. Each attempt is cheap (immediate on success).
    last_err = None
    for attempt in range(5):
        try:
            srv = http.server.ThreadingHTTPServer((server.http_bind(), server.http_port()), _ApiHandler)
            srv.socket = server.ensure_tls().wrap_socket(srv.socket, server_side=True)
            _http_server = srv
            _http_server_thread = threading.Thread(target=srv.serve_forever, daemon=True)
            _http_server_thread.start()
            server._emit({"event": "http", "enabled": True, "port": server.http_port(), "bind": server.http_bind()})
            return True
        except OSError as e:
            last_err = e
            time.sleep(0.4)
    server._emit({"event": "http", "enabled": False, "port": server.http_port(), "error": str(last_err)})
    return False


def _stop_http() -> None:
    global _http_server, _http_server_thread
    import server
    srv = _http_server
    _http_server = None
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
    server._emit({"event": "http", "enabled": False, "port": server.http_port()})
