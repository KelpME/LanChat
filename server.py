#!/usr/bin/env python3
"""
KelpME.lanchat — LAN messaging daemon for Omarchy Quattro.

A single long-lived Python process that handles the actual networking a
Quickshell plugin cannot: a TCP server (incoming messages), UDP broadcast
(automatic peer discovery on the local network), heartbeat/expiry for online
status, and JSON persistence of message history.

It is deliberately stdlib-only (Arch/Omarchy guarantee python3; no pip
dependencies) and communicates with the shell plugin over newline-delimited
JSON: events on stdout, commands on stdin. All of the QML lives in the shell
plugin; this process is the transport.

Security model:
  - Every TCP connection and every discovery packet is authenticated with a
    shared token all machines must agree on (see config below).
  - The token travels in plaintext on the local network. This is NOT encrypted
    and is intended for a trusted home/office LAN only.
"""

import json
import os
import secrets
import socket
import sys
import threading
import time

# --------------------------------------------------------------------------
# Paths & config
# --------------------------------------------------------------------------

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "omarchy")
CONFIG_PATH = os.path.join(CONFIG_DIR, "lanchat.json")
STATE_DIR = os.path.join(os.path.expanduser("~"), ".local", "state", "lanchat")
HISTORY_PATH = os.path.join(STATE_DIR, "history.json")

DEFAULT_PORT = 4812
PEER_TIMEOUT_S = 15.0      # drop a peer after this long without a hello
BROADCAST_INTERVAL_S = 5.0
HISTORY_LIMIT = 500

CONFIG = {}
_out_lock = threading.Lock()
_stdout = sys.stdout


def _emit(event: dict) -> None:
    """Write one newline-delimited JSON event to stdout (thread-safe)."""
    line = json.dumps(event, separators=(",", ":"))
    with _out_lock:
        try:
            _stdout.write(line + "\n")
            _stdout.flush()
        except (BrokenPipeError, OSError):
            # The shell went away; nothing more we can do.
            os._exit(0)


def load_config() -> None:
    global CONFIG
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            CONFIG = json.load(f)
    else:
        CONFIG = {
            "token": secrets.token_hex(16),
            "port": DEFAULT_PORT,
            "displayName": socket.gethostname(),
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=2)
            f.write("\n")
        _emit({
            "event": "notice",
            "message": (
                "Generated a new lanchat config at ~/.config/omarchy/lanchat.json. "
                "Copy the token to every other machine you want to reach."
            ),
        })

    token = str(CONFIG.get("token", "")).strip()
    if len(token) < 8:
        _emit({"event": "error", "message": "lanchat token must be at least 8 chars. Edit ~/.config/omarchy/lanchat.json"})
    CONFIG["token"] = token


def host_id() -> str:
    return socket.gethostname()


def display_name() -> str:
    return str(CONFIG.get("displayName") or socket.gethostname())


def port() -> int:
    try:
        return int(CONFIG.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        return DEFAULT_PORT


# --------------------------------------------------------------------------
# Message history (per-machine persistence)
# --------------------------------------------------------------------------

_history = []
_hist_lock = threading.Lock()


def load_history() -> None:
    global _history
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            _history = data[-HISTORY_LIMIT:]
    except (OSError, ValueError):
        _history = []


def append_history(message: dict) -> None:
    global _history
    with _hist_lock:
        _history.append(message)
        if len(_history) > HISTORY_LIMIT:
            _history = _history[-HISTORY_LIMIT:]
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(_history, f, separators=(",", ":"))
        except OSError:
            pass


def history_snapshot() -> list:
    with _hist_lock:
        return list(_history)


# --------------------------------------------------------------------------
# Peers (discovered via UDP)
# --------------------------------------------------------------------------

_peers = {}          # id -> {id, name, address, port, lastSeen}
_peers_lock = threading.Lock()


def peer_snapshot() -> list:
    with _peers_lock:
        return sorted(
            _peers.values(),
            key=lambda p: p["name"].lower(),
        )


def upsert_peer(pid: str, name: str, address: str, pport: int) -> None:
    now = time.time()
    with _peers_lock:
        existed = pid in _peers
        _peers[pid] = {
            "id": pid,
            "name": name,
            "address": address,
            "port": pport,
            "lastSeen": int(now * 1000),
        }
    if not existed:
        _emit({"event": "peer", "peer": _peers[pid]})
    else:
        _emit({"event": "peer", "peer": _peers[pid]})


def expire_peers() -> None:
    now = time.time()
    gone = []
    with _peers_lock:
        for pid in list(_peers.keys()):
            if now - _peers[pid]["lastSeen"] / 1000.0 > PEER_TIMEOUT_S:
                gone.append(pid)
                del _peers[pid]
    for pid in gone:
        _emit({"event": "peer-gone", "id": pid})


def find_peer(pid: str):
    with _peers_lock:
        return _peers.get(pid)


# --------------------------------------------------------------------------
# UDP discovery
# --------------------------------------------------------------------------

def _udp_listener(sock: socket.socket) -> None:
    sock.settimeout(0.5)
    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            return
        try:
            pkt = json.loads(data.decode("utf-8"))
        except ValueError:
            continue
        if pkt.get("token") != CONFIG.get("token"):
            continue  # wrong network / wrong key: ignore silently
        t = pkt.get("t")
        if t == "hello":
            upsert_peer(
                str(pkt.get("id", addr[0])),
                str(pkt.get("name", pkt.get("id", addr[0]))),
                addr[0],
                int(pkt.get("port", DEFAULT_PORT)),
            )
            # Reply so the caller learns about us immediately.
            _udp_send(sock, {"t": "pong"})


def _udp_send(sock: socket.socket, pkt: dict) -> None:
    pkt["id"] = host_id()
    pkt["name"] = display_name()
    pkt["port"] = port()
    pkt["token"] = CONFIG.get("token")
    try:
        sock.sendto(json.dumps(pkt).encode("utf-8"), ("255.255.255.255", port()))
    except OSError:
        pass


def udp_loop() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind(("", port()))
    except OSError as e:
        _emit({"event": "error", "message": "lanchat UDP bind failed on port %d: %s" % (port(), e)})
        return
    threading.Thread(target=_udp_listener, args=(sock,), daemon=True).start()

    last_broadcast = 0.0
    while True:
        now = time.time()
        if now - last_broadcast >= BROADCAST_INTERVAL_S:
            _udp_send(sock, {"t": "hello"})
            last_broadcast = now
        expire_peers()
        time.sleep(0.5)


# --------------------------------------------------------------------------
# TCP server (incoming messages)
# --------------------------------------------------------------------------

def tcp_loop() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("", port()))
        srv.listen(16)
    except OSError as e:
        _emit({"event": "error", "message": "lanchat TCP bind failed on port %d: %s" % (port(), e)})
        return
    while True:
        try:
            conn, addr = srv.accept()
        except OSError:
            return
        threading.Thread(target=_handle_client, args=(conn, addr), daemon=True).start()


def _handle_client(conn: socket.socket, addr) -> None:
    conn.settimeout(30)
    try:
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                conn.close()
                return
            buf += chunk
        line, rest = buf.split(b"\n", 1)
        try:
            auth = json.loads(line.decode("utf-8"))
        except ValueError:
            conn.close()
            return
        if auth.get("token") != CONFIG.get("token"):
            conn.close()
            return

        # Remaining buffered bytes may already hold the first message.
        conn.sendall(b"ok\n")
        lines = rest.split(b"\n") if rest else []
        for line in lines:
            if line.strip():
                _handle_incoming(line, addr)
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            for line in chunk.split(b"\n"):
                if line.strip():
                    _handle_incoming(line, addr)
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _handle_incoming(line: bytes, addr) -> None:
    try:
        msg = json.loads(line.decode("utf-8"))
    except ValueError:
        return
    if msg.get("t") != "msg":
        return
    text = str(msg.get("text", ""))
    if not text.strip():
        return
    pid = str(msg.get("from", ""))
    from_name = str(msg.get("fromName", pid))
    ts = int(time.time() * 1000)
    message = {
        "from": pid,
        "fromName": from_name,
        "text": text,
        "ts": ts,
        "outgoing": False,
        "peerAddress": addr[0],
    }
    append_history(message)
    _emit({"event": "message", "message": message})


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

def send_message(peer_id: str, text: str) -> None:
    peer = find_peer(peer_id)
    if peer is None:
        _emit({"event": "error", "message": "peer '%s' is not online yet" % peer_id})
        return
    try:
        s = socket.create_connection((peer["address"], peer["port"]), timeout=5)
        s.sendall(json.dumps({"token": CONFIG.get("token")}).encode("utf-8") + b"\n")
        # Wait for auth ok (drops the client if wrong key).
        s.settimeout(5)
        ack = s.recv(64)
        msg = {
            "from": host_id(),
            "fromName": display_name(),
            "text": text,
            "ts": int(time.time() * 1000),
        }
        s.sendall(json.dumps({"t": "msg", **msg}).encode("utf-8") + b"\n")
        s.close()
        msg["to"] = peer_id
        msg["outgoing"] = True
        append_history(msg)
        _emit({"event": "message", "message": msg})
    except OSError as e:
        _emit({"event": "error", "message": "could not reach %s: %s" % (peer["name"], e)})


# --------------------------------------------------------------------------
# stdin command loop
# --------------------------------------------------------------------------

def stdin_loop() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            cmd = json.loads(raw)
        except ValueError:
            continue
        kind = cmd.get("cmd")
        if kind == "send":
            send_message(str(cmd.get("to", "")), str(cmd.get("text", "")))
        elif kind == "history":
            _emit({"event": "history", "messages": history_snapshot()})
        elif kind == "list":
            _emit({"event": "peers", "peers": peer_snapshot()})


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------

def main() -> None:
    load_config()
    load_history()
    _emit({
        "event": "ready",
        "id": host_id(),
        "name": display_name(),
        "port": port(),
    })
    threading.Thread(target=tcp_loop, daemon=True).start()
    threading.Thread(target=udp_loop, daemon=True).start()
    stdin_loop()  # blocks until the shell closes stdin; also supervises the process lifetime


if __name__ == "__main__":
    main()
