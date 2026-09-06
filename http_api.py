#!/usr/bin/env python3
"""Optional HTTP API for KelpME.lanchat — extracted verbatim from server.py (Commit 2).

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
import threading
import time
import urllib.parse

# init(state) wiring: STATE is bound once by server.py at import time
# (http_api.init(STATE)).

STATE = None


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

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]
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
            # query) is peer-to-peer file transfer, not script read-access — so
            # it must NOT be gated behind apiFullAccess. Auth is enforced above.
            file_id = (qs.get("fileId") or [""])[0]
            att = server.get_attachment(file_id)
            if not att:
                return self._send_json(404, {"ok": False, "error": "not found"})
            try:
                with open(att["path"], "rb") as f:
                    data = f.read()
            except OSError:
                return self._send_json(404, {"ok": False, "error": "file missing"})
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", "attachment; filename=%s" % att["name"])
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/game/feed":
            # Loopback game feed the game window's browser polls. Carries
            # roomId+gameId (session scope); returns buffered events since
            # `after` plus the latest authoritative snapshot. Not gated behind
            # apiFullAccess (a live game window is not script read-access).
            import game_kit
            room_id = (qs.get("roomId") or [""])[0]
            game_id = (qs.get("gameId") or [""])[0]
            after = int((qs.get("after") or ["0"])[0] or 0)
            if not room_id or not game_id:
                return self._send_json(400, {"ok": False, "error": "roomId and gameId required"})
            data = game_kit.feed_drain(room_id, game_id, after)
            if data is None:
                return self._send_json(404, {"ok": False, "error": "session not found"})
            return self._send_json(200, {"ok": True, **data})
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        import server
        parsed = urllib.parse.urlparse(self.path)
        body = self._body()
        if body is None:  # oversized body already answered with 413
            return
        if parsed.path == "/send":
            token = body.get("token")
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
        # ---- loopback game control (the game window's transport) ----
        if parsed.path.startswith("/game/"):
            # Auth is enforced the same way (token in the JSON body).
            token = body.get("token")
            if self._auth_blocked():
                return self._send_json(429, {"ok": False, "error": "rate limited"})
            if not self._auth_ok(token):
                return self._send_json(401, {"ok": False, "error": "unauthorized"})
            import game_kit
            room_id = str(body.get("roomId", ""))
            game_id = str(body.get("gameId", ""))
            if parsed.path == "/game/join":
                return self._send_json(200, {"ok": True, **game_kit.route_join(
                    room_id, game_id, str(body.get("seat", "")), body.get("theme") or {})})
            if parsed.path == "/game/input":
                if not room_id or not game_id:
                    return self._send_json(400, {"ok": False, "error": "roomId and gameId required"})
                return self._send_json(200, {"ok": True, **game_kit.route_input(
                    room_id, game_id, str(body.get("action", "")), body.get("value"))})
            if parsed.path == "/game/drop":
                if not room_id or not game_id:
                    return self._send_json(400, {"ok": False, "error": "roomId and gameId required"})
                return self._send_json(200, {"ok": True, **game_kit.route_drop(
                    room_id, game_id, str(body.get("seat", "")))})
            if parsed.path == "/game/leave":
                if not room_id or not game_id:
                    return self._send_json(400, {"ok": False, "error": "roomId and gameId required"})
                return self._send_json(200, {"ok": True, **game_kit.route_leave(room_id, game_id)})
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
    _stop_loopback()


# --------------------------------------------------------------------------
# Plain-HTTP loopback game feed (the browser game window hits THIS, not the
# TLS API). Bound to 127.0.0.1 on httpPort+1, serves ONLY /game/*, token-auth.
# No TLS -> no self-signed-cert warning in the game window's browser.
# --------------------------------------------------------------------------

_loopback = None          # the ThreadingHTTPServer
_loopback_thread = None   # its serving thread


class _GameLoopbackHandler(http.server.BaseHTTPRequestHandler):
    """Plain-HTTP, loopback-only handler for the game window's feed/control.
    Reuses _ApiHandler's auth + rate-limiting helpers via class inheritance of
    the mixins (token + throttle). Serves ONLY the /game/* surface."""

    # Reuse the shared rate-limiting state from _ApiHandler (class-level).
    _MAX_BODY = 256 * 1024
    _SEND_WINDOW_S = _ApiHandler._SEND_WINDOW_S
    _SEND_MAX = _ApiHandler._SEND_MAX
    _AUTH_WINDOW_S = _ApiHandler._AUTH_WINDOW_S
    _AUTH_MAX = _ApiHandler._AUTH_MAX
    _send_ts = _ApiHandler._send_ts
    _auth_fail_ts = _ApiHandler._auth_fail_ts
    _rl_lock = _ApiHandler._rl_lock

    def log_message(self, *args):
        pass

    def _send_json(self, code, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")  # game window is a separate origin
        self.end_headers()
        self.wfile.write(data)

    def _auth_ok(self, token):
        import server
        return bool(token) and token == server.STATE.config.get("token")

    def _auth_blocked(self):
        import time as _t
        now = _t.time()
        with self._rl_lock:
            self._auth_fail_ts[:] = [t for t in self._auth_fail_ts if now - t < self._AUTH_WINDOW_S]
            return len(self._auth_fail_ts) >= self._AUTH_MAX

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _gate(self, token):
        if self._auth_blocked():
            self._send_json(429, {"ok": False, "error": "rate limited"})
            return False
        if not self._auth_ok(token):
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        # Static game-bundle files (the game window's own JS/CSS/html) are NOT
        # token-gated: the browser loads them via relative imports with no
        # token in the URL. The loopback bind (127.0.0.1) is the boundary for
        # these; they contain no secrets, only the game's client code. Only the
        # /game/* feed/control endpoints touch real state and need the token.
        if parsed.path.startswith("/www/"):
            return self._serve_www(parsed.path[len("/www/"):])
        if parsed.path in ("/favicon.ico", "/favicon.png"):
            self.send_response(204)
            self.end_headers()
            return

        # Everything else is the token-gated game feed/control surface.
        token = (qs.get("token") or [""])[0]
        if not self._gate(token):
            return
        if parsed.path == "/game/feed":
            import game_kit
            room_id = (qs.get("roomId") or [""])[0]
            game_id = (qs.get("gameId") or [""])[0]
            after = int((qs.get("after") or ["0"])[0] or 0)
            if not room_id or not game_id:
                return self._send_json(400, {"ok": False, "error": "roomId and gameId required"})
            data = game_kit.feed_drain(room_id, game_id, after)
            if data is None:
                return self._send_json(404, {"ok": False, "error": "session not found"})
            return self._send_json(200, {"ok": True, **data})
        if parsed.path == "/game/list":
            # Available games (name/title/version) — the UI picker reads this.
            import game_kit
            return self._send_json(200, {"ok": True, "games": [
                {"name": g["name"], "title": g["title"], "version": g["version"],
                 "modes": g["modes"], "maxPlayers": g["maxPlayers"]}
                for g in game_kit.discover_games()]})
        self._send_json(404, {"ok": False, "error": "not found"})

    def _serve_www(self, rel: str):
        """Serve a static file from a game bundle's `www/` folder. The first
        path segment is the game name; the rest is the file under its www root.
        index.html is served for a bare game-name path. Only bundled/user games
        are reachable — never arbitrary paths (path-traversal guarded)."""
        import os as _os

        import game_kit
        parts = rel.split("/", 1)
        game_name = parts[0]
        sub = parts[1] if len(parts) > 1 else ""
        web = game_kit.game_web_dir(game_name)
        if not web:
            return self._send_json(404, {"ok": False, "error": "game not found"})
        # Resolve + guard against path traversal.
        safe = _os.path.normpath(sub or "index.html")
        if safe.startswith("..") or _os.path.isabs(safe):
            return self._send_json(403, {"ok": False, "error": "forbidden"})
        full = _os.path.join(web, safe)
        if not _os.path.isfile(full):
            return self._send_json(404, {"ok": False, "error": "not found"})
        import mimetypes
        mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            return self._send_json(404, {"ok": False, "error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._send_json(400, {"ok": False, "error": "empty body"})
        if length > self._MAX_BODY:
            return self._send_json(413, {"ok": False, "error": "request body too large"})
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send_json(400, {"ok": False, "error": "bad json"})
        if not isinstance(body, dict):
            return self._send_json(400, {"ok": False, "error": "bad body"})
        if not self._gate(body.get("token", "")):
            return
        import game_kit
        parsed = urllib.parse.urlparse(self.path)
        room_id = str(body.get("roomId", ""))
        game_id = str(body.get("gameId", ""))
        if parsed.path == "/game/join":
            return self._send_json(200, {"ok": True, **game_kit.route_join(
                room_id, game_id, str(body.get("seat", "")), body.get("theme") or {})})
        if parsed.path == "/game/input":
            if not room_id or not game_id:
                return self._send_json(400, {"ok": False, "error": "roomId and gameId required"})
            return self._send_json(200, {"ok": True, **game_kit.route_input(
                room_id, game_id, str(body.get("action", "")), body.get("value"))})
        if parsed.path == "/game/drop":
            if not room_id or not game_id:
                return self._send_json(400, {"ok": False, "error": "roomId and gameId required"})
            return self._send_json(200, {"ok": True, **game_kit.route_drop(
                room_id, game_id, str(body.get("seat", "")))})
        if parsed.path == "/game/leave":
            if not room_id or not game_id:
                return self._send_json(400, {"ok": False, "error": "roomId and gameId required"})
            return self._send_json(200, {"ok": True, **game_kit.route_leave(room_id, game_id)})
        self._send_json(404, {"ok": False, "error": "not found"})


def _loopback_port() -> int:
    import server
    return server.http_port() + 1


def _start_loopback() -> bool:
    """Start the plain-HTTP loopback game feed on httpPort+1 (127.0.0.1 only).
    Independent of the TLS API (the browser must not hit a self-signed cert).
    Runs the bind in a background thread so a slow/contended bind NEVER delays
    the daemon's TCP listener startup (a fixed test sleep must not miss it)."""
    global _loopback, _loopback_thread
    import server
    if _loopback is not None:
        return True

    def _bind():
        global _loopback, _loopback_thread
        last_err = None
        for attempt in range(5):
            try:
                srv = http.server.ThreadingHTTPServer(("127.0.0.1", _loopback_port()), _GameLoopbackHandler)
                _loopback = srv
                _loopback_thread = threading.Thread(target=srv.serve_forever, daemon=True)
                _loopback_thread.start()
                server._emit({"event": "game-feed", "enabled": True, "port": _loopback_port(), "bind": "127.0.0.1"})
                return True
            except OSError as e:
                last_err = e
                time.sleep(0.4)
        server._emit({"event": "game-feed", "enabled": False, "port": _loopback_port(), "error": str(last_err)})
        return False

    threading.Thread(target=_bind, daemon=True).start()
    return True


def _stop_loopback() -> None:
    global _loopback, _loopback_thread
    import server
    srv = _loopback
    _loopback = None
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
    server._emit({"event": "game-feed", "enabled": False, "port": _loopback_port()})
