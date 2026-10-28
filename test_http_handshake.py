#!/usr/bin/env python3
"""Regression test: the HTTP API's TLS handshake must not block the accept loop.

The old implementation wrapped the LISTENING socket, so accept() performed a
synchronous, deadline-less TLS handshake on the serve_forever thread: another
local UID could hold a raw TCP connection open (sending nothing) and stall
every new API connection — and even srv.shutdown() — before any handler ran
or the token was checked.

Now _TlsHTTPServer wraps each accepted connection with HANDSHAKE_TIMEOUT_S.
Verified here:
  - while a stalled (silent) raw connection is open, a real TLS client can
    still complete an authenticated request (bounded by handshake timeout +
    slack),
  - a connection sending garbage TLS bytes is dropped and the server keeps
    serving,
  - a client that completes the handshake then sends nothing is dropped and
    its handler thread freed within REQUEST_TIMEOUT_S,
  - connections beyond MAX_WORKERS are refused without spawning another
    handler thread,
  - _stop_http()/srv.shutdown() completes within HANDSHAKE_TIMEOUT_S + slack
    while the stalled connection is still open.

Run: python3 test_http_handshake.py
"""
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

_TOKEN = "http-handshake-test-token"


def _client_ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _authed_post(host, port, ok_expected_code=404):
    """Authenticated POST /send. The token is valid, but the peer does not
    exist, so the handler answers 404 ("peer offline") — any code at all
    proves the request got dispatched past the TLS layer and token check."""
    # Client-side timeouts must exceed the server's handshake deadline: with
    # one stalled handshake queued, a request waits up to HANDSHAKE_TIMEOUT_S
    # (5s, see http_api) before the accept loop reaches it.
    tmo = 10.0
    tls = _client_ctx().wrap_socket(socket.create_connection((host, port), timeout=tmo))
    try:
        body = json.dumps({"to": "nobody-here", "text": "ping"}).encode()
        tls.sendall(
            b"POST /send HTTP/1.1\r\n"
            + b"Host: localhost\r\n"
            + ("Authorization: Bearer %s\r\n" % _TOKEN).encode()
            + ("Content-Length: %d\r\n" % len(body)).encode()
            + b"Content-Type: application/json\r\n"
            + b"Connection: close\r\n\r\n"
            + body
        )
        data = b""
        while b"\r\n" not in data or len(data) < 12:
            chunk = tls.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data:
                break
        return int(data.split(b" ")[1])
    finally:
        tls.close()


def main():
    real_home = os.path.expanduser("~")
    tmp_home = tempfile.mkdtemp(prefix="lanchat-http-hs-")
    os.environ["HOME"] = tmp_home
    cfg_dir = os.path.join(tmp_home, ".config", "omarchy")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "lanchat.json"), "w") as f:
        json.dump({"token": _TOKEN, "port": 4917, "displayName": "HsTest"}, f)

    import http_api  # noqa: E402  (after HOME sandboxing, like test_units.py)
    import server  # noqa: E402

    # load_config() normally runs in daemon main(); call it here so the token
    # in the sandboxed lanchat.json lands in STATE.config.
    server.load_config()

    srv = None
    try:
        tls_ctx = server.ensure_tls()  # generate cert in the sandboxed HOME
        srv = http_api._TlsHTTPServer(("127.0.0.1", 0), http_api._ApiHandler, tls_ctx)
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        # 1. Stalled handshake must not block new API connections.
        stalled = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            deadline = http_api.HANDSHAKE_TIMEOUT_S + 3.0
            t0 = time.time()
            code = _authed_post("127.0.0.1", port)
            dt = time.time() - t0
            assert code == 404, "expected 404 (peer offline), got %s" % code
            assert dt < deadline, "authed request took %.2fs (deadline %.2fs)" % (dt, deadline)
            print("OK  authed request dispatched while a stalled handshake held the port (%.2fs)" % dt)

            # 2. Garbage TLS bytes are dropped; the server keeps serving.
            garbage = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                garbage.sendall(b"\x16\x03\x01\x00\xffthis-is-not-tls")
                time.sleep(0.3)
            finally:
                garbage.close()
            t0 = time.time()
            code = _authed_post("127.0.0.1", port)
            assert code == 404, "expected 404 after garbage client, got %s" % code
            assert time.time() - t0 < deadline, "server stuck after garbage client"
            print("OK  garbage TLS client dropped, server still serving")
        finally:
            stalled.close()

        # 3. Post-handshake stalled client releases its thread within
        # REQUEST_TIMEOUT_S. Complete the TLS handshake, then send NOTHING: the
        # handler blocks on the request read, and the socket timeout must drop
        # it and free the worker. REQUEST_TIMEOUT_S is shrunk here so the suite
        # stays fast — the mechanism (a bounded read deadline) is what's tested.
        orig_rto = http_api.REQUEST_TIMEOUT_S
        http_api.REQUEST_TIMEOUT_S = 2.0
        http_api._active_workers = 0
        try:
            stall = socket.create_connection(("127.0.0.1", port), timeout=5)
            stalled_tls = _client_ctx().wrap_socket(stall)  # handshake done
            try:
                stalled_tls.settimeout(5)
                t0 = time.time()
                deadline = http_api.REQUEST_TIMEOUT_S + 5.0
                while http_api._active_workers > 0 and time.time() - t0 < deadline:
                    time.sleep(0.1)
                dt = time.time() - t0
                assert http_api._active_workers == 0, (
                    "worker not released after post-handshake stall: %d after %.2fs"
                    % (http_api._active_workers, dt))
                assert dt < http_api.REQUEST_TIMEOUT_S + 3.0, (
                    "post-handshake thread released too late (%.2fs)" % dt)
                print("OK  post-handshake stalled client released its thread (%.2fs)" % dt)
            finally:
                try:
                    stalled_tls.close()
                except OSError:
                    pass
                try:
                    stall.close()
                except OSError:
                    pass
        finally:
            http_api.REQUEST_TIMEOUT_S = orig_rto
            http_api._active_workers = 0

        # 4. Worker cap refuses connections beyond MAX_WORKERS. Open MAX_WORKERS
        # completed-but-idle TLS connections (each holds a handler thread), then
        # assert the next connection is refused without spawning another thread.
        orig_max = http_api.MAX_WORKERS
        http_api.MAX_WORKERS = 4
        http_api._active_workers = 0
        held = []
        try:
            for _ in range(http_api.MAX_WORKERS):
                s = socket.create_connection(("127.0.0.1", port), timeout=5)
                t = _client_ctx().wrap_socket(s)
                t.settimeout(5)
                held.append(t)
            time.sleep(0.3)  # let the handler threads register
            assert http_api._active_workers == http_api.MAX_WORKERS, (
                "expected %d active workers, got %d"
                % (http_api.MAX_WORKERS, http_api._active_workers))
            extra = None
            refused = False
            try:
                extra = socket.create_connection(("127.0.0.1", port), timeout=5)
                extra.settimeout(3)
                _client_ctx().wrap_socket(extra)  # refused: server closes it
                # If the handshake unexpectedly succeeded, the server kept it —
                # send and expect the connection to be dropped.
                extra.sendall(b"GET /health HTTP/1.0\r\n\r\n")
                if not extra.recv(16):
                    refused = True
            except (OSError, ssl.SSLError, socket.timeout):
                refused = True
            finally:
                if extra is not None:
                    try:
                        extra.close()
                    except OSError:
                        pass
            assert refused, "connection beyond MAX_WORKERS was not refused"
            assert http_api._active_workers <= http_api.MAX_WORKERS, (
                "worker cap exceeded: %d active" % http_api._active_workers)
            print("OK  worker cap refused connection beyond MAX_WORKERS")
        finally:
            for t in held:
                try:
                    t.close()
                except OSError:
                    pass
            http_api.MAX_WORKERS = orig_max
            http_api._active_workers = 0

        # 5. shutdown() completes while the stalled connection is open — the
        # handshake deadline bounds the accept loop, and serve_forever's 0.5s
        # poll interval observes the shutdown flag right after.
        http_api._http_server = srv
        http_api._http_server_thread = thread
        stalled2 = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            t0 = time.time()
            http_api._stop_http()
            dt = time.time() - t0
            assert dt < http_api.HANDSHAKE_TIMEOUT_S + 3.0, (
                "shutdown took %.2fs while a stalled conn was open" % dt
            )
            assert not thread.is_alive() or dt < http_api.HANDSHAKE_TIMEOUT_S + 3.0
            print("OK  shutdown completed with stalled conn open (%.2fs)" % dt)
            srv = None  # _stop_http closed it
        finally:
            stalled2.close()

        print("\nALL HTTP HANDSHAKE TESTS PASSED")
    finally:
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
        import shutil
        shutil.rmtree(tmp_home, ignore_errors=True)
        os.environ["HOME"] = real_home


if __name__ == "__main__":
    main()
