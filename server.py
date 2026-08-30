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

import http.server
import hashlib
import json
import os
import random
import secrets
import socket
import sys
import threading
import time
import urllib.parse

# --------------------------------------------------------------------------
# Paths & config
# --------------------------------------------------------------------------

# --- deterministic friendly-name generator -------------------------------
# Every peer gets a stable, human-readable name of the form {modifier}{trick},
# derived from hashing its peer id. Same peer id -> same name on every machine,
# no state. (Convention shared with the CpyPst project's get_friendly_name.)
_SKATE_TRICKS = [
    # Street flips & shuvits
    "Kickflip", "Heelflip", "360Flip", "Hardflip", "VarialKickflip",
    "PopShuvit", "Bigspin", "ShoveIt", "DoubleKickflip", "DoubleHeelflip",
    "DoubleShuvit", "TreFlip", "DoubleTreFlip", "Frontside180Shuvit",
    "Backside180Shuvit", "PopFrontsideShuvit", "PopBacksideShuvit",
    # Street slides & grinds
    "Boardslide", "Lipslide", "SmithGrind", "Nosegrind", "Bluntslide",
    "CrookedGrind", "FeebleGrind", "IcePickGrind", "Fifty50Grind", "FiveZeroGrind",
    "Tailslide", "Noseslide", "PidginGrind", "RockToFake", "Manual",
    "Nosemanual", "Nosepress", "Tailpress", "BluntToFeeble",
    "Frontside505", "Backside505", "SmithSlide", "CrookedSlide",
    # Vert & park
    "McTwist", "AirToFakie", "Stalefish", "MelonGrab", "MethodAir",
    "IndyGrab", "MuteGrab", "Tantrum", "Rodeo", "CabinAir", "Invert",
    "Tailgrab", "Handplant", "RockSolid", "BottleFlip", "Hangten",
    "Caballerial", "AirToReverse", "BluntAir", "StarPlant",
    # Technical & switch/fakie variations
    "PhantomFlip", "SwitchKickflip", "SwitchHeelflip", "NollieKickflip",
    "FakieFlip", "BlindFlip", "Impossible", "DoubleImpossible", "ShoveIt180",
    "PopFrontside180", "PopBackside180", "TreFlipHardflip", "KickflipBigspin",
    "HeelflipBigspin", "VarialShuvit", "Caveman", "CatLeap", "Coneflip",
    "SwitchPhantom", "FakiePhantom", "NollieHeelflip", "BlindHeelflip",
    # Big air & combos
    "DoubleMcTwist", "QuadFlip", "TripleShuvit", "Kickflip540",
    "Heelflip360", "LaserHack", "MegaFlip", "SwitchBigspin",
    "FakieBigspin", "BlindBigspin", "SwitchHardflip", "NollieHeelflip",
    "DoubleTreFlip", "TripleKickflip", "QuadrupleShuvit",
    # Aerials & grabs
    "FrontsideAir", "BacksideAir", "InlineAir",
    "Rosalin", "Rosallind", "BakerAir", "Caballerial",
    # Classics & style
    "Ollie", "PopShoveIt", "Frontside180Air", "Backside180Air",
    "Frontside540", "Backside540", "Frontside720", "Backside720",
    "KickflipNosegrind", "HeelflipBluntslide", "VarialLipslide",
    "PopFrontside", "PopBackside", "FrontsidePopShuvit",
]

_TRICK_MODIFIERS = [
    # Flip variations
    "Flip", "DoubleFlip", "TreFlip", "Hardflip", "Phantom",
    "Varial", "Nosevarial", "Toevarial", "Kickflip", "Heelflip",
    # Spin counts & rotations
    "180", "360", "540", "720", "900", "DoubleSpin", "TripleSpin",
    "HalfCab", "FullCab", "QuarterFlip", "HalfFlip",
    # Grab names
    "Indy", "Mute", "Stalefish", "Melon", "Method", "Tailgrab",
    "Handplant", "Rodeo", "Cabin", "Slot", "Roastbeef", "Nosgrass",
    "Tantrum", "Air", "Grab", "Pinch", "Huck", "Lazer",
    # Slide/grind styles
    "Boardslide", "Lipslide", "Smith", "Blunt", "Feeble",
    "Crooked", "Nosegrind", "Tailslide", "Pidgin", "Rockslide",
    # Direction & stance
    "Frontside", "Backside", "Switch", "Fakie", "Blind", "Nollie",
    "Regular", "Goofy", "PopShuvit", "Bigspin", "ShoveIt",
    # Size / power adjectives
    "Mega", "Giga", "Ultra", "Hyper", "Super", "Micro", "Mini",
    "Max", "Turbo", "Mach", "Atomic", "Quantum", "Cosmic", "Solar",
    # Rare / creative modifiers
    "Coneflip", "Caveman", "CatLeap", "Barkley", "Ollie",
    "Impossible", "DoubleImp", "TreFlip", "KickflipBigspin",
    "HeelflipBigspin", "VarialShuvit", "SwitchHardflip",
    "QuadFlip", "TripleShuvit", "QuadrupleShuvit",
    "McTwist", "Caballerial", "StarPlant", "RockSolid",
]


def friendly_name(peer_id: str) -> str:
    """Deterministic {modifier}{trick} name from a peer id (e.g. 'MegaBoardslide')."""
    digest = hashlib.sha256(peer_id.encode()).digest()
    trick = _SKATE_TRICKS[digest[0] % len(_SKATE_TRICKS)]
    modifier = _TRICK_MODIFIERS[digest[1] % len(_TRICK_MODIFIERS)]
    return f"{modifier}{trick}"

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "omarchy")
CONFIG_PATH = os.path.join(CONFIG_DIR, "lanchat.json")
STATE_DIR = os.path.join(os.path.expanduser("~"), ".local", "state", "lanchat")
HISTORY_PATH = os.path.join(STATE_DIR, "history.json")

DEFAULT_PORT = 4812
DEFAULT_HTTP_PORT = 4814
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


def _save_config() -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=2)
            f.write("\n")
    except OSError:
        pass


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
            # default self-name: a deterministic friendly {modifier}{trick} name
            "displayName": friendly_name(socket.gethostname()),
        }
        _emit({
            "event": "notice",
            "message": (
                "Generated a new lanchat config at ~/.config/omarchy/lanchat.json. "
                "Copy the token to every other machine you want to reach."
            ),
        })

    # Defaults for the optional HTTP API (always present so toggling is simple).
    CONFIG.setdefault("httpEnabled", False)
    CONFIG.setdefault("httpPort", DEFAULT_HTTP_PORT)
    _save_config()

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


def http_enabled() -> bool:
    return bool(CONFIG.get("httpEnabled", False))


def http_port() -> int:
    try:
        return int(CONFIG.get("httpPort", DEFAULT_HTTP_PORT))
    except (TypeError, ValueError):
        return DEFAULT_HTTP_PORT


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
            pid = str(pkt.get("id", addr[0]))
            # Prefer the peer's broadcast display name; fall back to a
            # deterministic friendly name derived from its id.
            name = str(pkt.get("name") or friendly_name(pid))
            upsert_peer(
                pid,
                name,
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
    # Use the sender's broadcast name if present; otherwise fall back to a
    # deterministic friendly name for their id.
    from_name = str(msg.get("fromName") or friendly_name(pid))
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

def send_message(peer_id: str, text: str) -> bool:
    peer = find_peer(peer_id)
    if peer is None:
        _emit({"event": "error", "message": "peer '%s' is not online yet" % peer_id})
        return False
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
        return True
    except OSError as e:
        _emit({"event": "error", "message": "could not reach %s: %s" % (peer["name"], e)})
        return False


# --------------------------------------------------------------------------
# Optional HTTP API
# --------------------------------------------------------------------------
# A small stdlib HTTP server for sending messages / reading state from other
# tools (curl, scripts, an agent). Disabled by default; toggled on/off from the
# panel UI. Authenticated with the same shared token as the TCP/UDP layer.

_http_server = None
_http_server_thread = None


class _ApiHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request logging
        pass

    def _auth_ok(self, token):
        return bool(token) and token == CONFIG.get("token")

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
        if not self._auth_ok(token):
            return self._send_json(401, {"ok": False, "error": "unauthorized"})
        if parsed.path == "/peers":
            return self._send_json(200, {"ok": True, "peers": peer_snapshot()})
        if parsed.path == "/messages":
            return self._send_json(200, {"ok": True, "messages": history_snapshot()})
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        body = self._body()
        if parsed.path == "/send":
            token = body.get("token")
            if not self._auth_ok(token):
                return self._send_json(401, {"ok": False, "error": "unauthorized"})
            to = str(body.get("to", ""))
            text = str(body.get("text", ""))
            if not to or not text.strip():
                return self._send_json(400, {"ok": False, "error": "to and text required"})
            if find_peer(to) is None:
                return self._send_json(404, {"ok": False, "error": "peer offline"})
            if not send_message(to, text):
                return self._send_json(500, {"ok": False, "error": "delivery failed"})
            return self._send_json(200, {"ok": True})
        self._send_json(404, {"ok": False, "error": "not found"})


def _start_http() -> bool:
    global _http_server, _http_server_thread
    if _http_server is not None:
        return True
    # Retry briefly: after a daemon restart the previous HTTP socket may still
    # be settling, and a single failed bind would otherwise leave the API off
    # until the toggle is flipped. Each attempt is cheap (immediate on success).
    last_err = None
    for attempt in range(5):
        try:
            srv = http.server.ThreadingHTTPServer(("", http_port()), _ApiHandler)
            _http_server = srv
            _http_server_thread = threading.Thread(target=srv.serve_forever, daemon=True)
            _http_server_thread.start()
            _emit({"event": "http", "enabled": True, "port": http_port()})
            return True
        except OSError as e:
            last_err = e
            time.sleep(0.4)
    _emit({"event": "http", "enabled": False, "port": http_port(), "error": str(last_err)})
    return False


def _stop_http() -> None:
    global _http_server, _http_server_thread
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
    _emit({"event": "http", "enabled": False, "port": http_port()})


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
        elif kind == "setHttp":
            enabled = bool(cmd.get("enabled"))
            if enabled:
                _start_http()
            else:
                _stop_http()
            CONFIG["httpEnabled"] = enabled
            _save_config()
        elif kind == "setName":
            name = str(cmd.get("name", "")).strip()[:12]
            if name:
                CONFIG["displayName"] = name
                _save_config()
                _emit({
                    "event": "ready",
                    "id": host_id(),
                    "name": display_name(),
                    "port": port(),
                    "httpEnabled": http_enabled(),
                    "httpPort": http_port(),
                })
        elif kind == "regenerateName":
            # Pick a fresh random friendly {modifier}{trick} name. Seed the
            # RNG from the current time so a re-roll usually differs from the
            # previous one.
            rng = random.Random()
            rng.seed(time.time_ns())
            trick = rng.choice(_SKATE_TRICKS)
            modifier = rng.choice(_TRICK_MODIFIERS)
            name = f"{modifier}{trick}"[:12]
            CONFIG["displayName"] = name
            _save_config()
            _emit({
                "event": "ready",
                "id": host_id(),
                "name": display_name(),
                "port": port(),
                "httpEnabled": http_enabled(),
                "httpPort": http_port(),
            })


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
        "httpEnabled": http_enabled(),
        "httpPort": http_port(),
    })
    if http_enabled():
        _start_http()
    threading.Thread(target=tcp_loop, daemon=True).start()
    threading.Thread(target=udp_loop, daemon=True).start()
    stdin_loop()  # blocks until the shell closes stdin; also supervises the process lifetime


if __name__ == "__main__":
    main()
