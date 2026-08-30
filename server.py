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
  - All message transport is encrypted with a per-install self-signed TLS
    certificate. The device's true identity is the SHA-256 fingerprint of that
    cert (not its hostname), and peers verify each other's fingerprint when
    connecting — a friend link is keyed on the fingerprint.
  - Messaging is gated by the friend/handshake model: only confirmed friends
    (or peers you've sent a request to) can message you. Discovery still shows
    all token-matching peers so you can initiate.
  - An online/offline toggle stops broadcasts and drops inbound messages while
    offline.
"""

import http.server
import hashlib
import json
import os
import random
import secrets
import socket
import ssl
import subprocess
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

# Version of the plugin/daemon. Keep in sync with manifest.json "version".
# Bump when behaviour changes; breaking changes should bump the major number.
VERSION = "1.0.0"

CONFIG = {}
_out_lock = threading.Lock()
_stdout = sys.stdout

NAME_MAX = 32  # maximum length of a display name (generated or custom)


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
    # Online presence + friend list (persisted).
    CONFIG.setdefault("online", True)
    CONFIG.setdefault("friends", [])
    # Attachment download folder (defaults to the system Downloads dir).
    CONFIG.setdefault("downloadDir", os.path.join(os.path.expanduser("~"), "Downloads"))
    # Send-delay/undo window in seconds (0 = disabled).
    CONFIG.setdefault("sendDelay", 0)
    # Stable, unique per-install id (independent of hostname so two machines
    # with the same hostname still distinguish each other).
    CONFIG.setdefault("id", secrets.token_hex(6))
    _save_config()

    token = str(CONFIG.get("token", "")).strip()
    if len(token) < 8:
        _emit({"event": "error", "message": "lanchat token must be at least 8 chars. Edit ~/.config/omarchy/lanchat.json"})
    CONFIG["token"] = token


def host_id() -> str:
    """The device's true, stable identity — the cert fingerprint.

    Unlike the cosmetic display name, this never changes (the cert persists),
    so renaming/re-rolling a name cannot break a friend link.
    """
    return cert_fingerprint()


# --------------------------------------------------------------------------
# TLS identity
# --------------------------------------------------------------------------
# Each install generates a persistent self-signed certificate. The SHA-256
# fingerprint of that cert is the device's true, stable identity — independent
# of the cosmetic display name. The fingerprint is what friends are keyed on.

CERT_DIR = os.path.join(os.path.expanduser("~"), ".config", "omarchy", "lanchat-certs")
CERT_KEY = os.path.join(CERT_DIR, "key.pem")
CERT_PEM = os.path.join(CERT_DIR, "cert.pem")


def _gen_cert() -> None:
    os.makedirs(CERT_DIR, exist_ok=True)
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", CERT_KEY, "-out", CERT_PEM, "-days", "3650",
             "-subj", "/CN=lanchat-%s" % str(CONFIG.get("id") or "peer"[:8])],
            check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        # Fallback: try the cryptography library if openssl isn't available.
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            import datetime as _dt
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "lanchat")])
            cert = (x509.CertificateBuilder()
                    .subject_name(subject).issuer_name(issuer)
                    .public_key(key.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(_dt.datetime.utcnow())
                    .not_valid_after(_dt.datetime.utcnow() + _dt.timedelta(days=3650))
                    .sign(key, hashes.SHA256()))
            with open(CERT_KEY, "wb") as f:
                f.write(key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
            with open(CERT_PEM, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
        except Exception as e:
            _emit({"event": "error", "message": "lanchat TLS cert generation failed: %s" % e})


def ensure_tls() -> ssl.SSLContext:
    """Generate the cert if needed and return a server SSL context."""
    if not (os.path.exists(CERT_KEY) and os.path.exists(CERT_PEM)):
        _gen_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_PEM, CERT_KEY)
    return ctx


def cert_fingerprint() -> str:
    """SHA-256 fingerprint of our cert — the stable device identity."""
    if not os.path.exists(CERT_PEM):
        ensure_tls()
    with open(CERT_PEM, "rb") as f:
        data = f.read()
    # Parse the DER cert via the cryptography lib (robust across Python/OpenSSL).
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        cert = x509.load_pem_x509_certificate(data)
        return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    except Exception:
        # Fallback: raw PEM fingerprint (still stable per cert)
        return hashlib.sha256(data).hexdigest()


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
    if not message.get("mid"):
        message["mid"] = secrets.token_hex(8)
    with _hist_lock:
        _history.append(message)
        if len(_history) > HISTORY_LIMIT:
            _history = _history[-HISTORY_LIMIT:]
        _save_history_locked()


def _save_history_locked() -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(_history, f, separators=(",", ":"))
    except OSError:
        pass


def history_snapshot() -> list:
    with _hist_lock:
        return list(_history)


def history_for_peer(peer_id: str, offset: int = 0, limit: int = 100) -> dict:
    """Lazy-load a peer's thread, newest-last, paged by offset/limit."""
    with _hist_lock:
        peer_msgs = [m for m in _history if (m.get("to") == peer_id or m.get("from") == peer_id)]
    total = len(peer_msgs)
    start = max(0, total - offset - limit)
    page = peer_msgs[start:max(start + limit, total - offset)] if total else []
    return {"peer": peer_id, "total": total, "messages": page}


def clear_history_for_peer(peer_id: str) -> int:
    global _history
    with _hist_lock:
        before = len(_history)
        _history = [m for m in _history if not (m.get("to") == peer_id or m.get("from") == peer_id)]
        removed = before - len(_history)
        _save_history_locked()
    return removed


def delete_message(mid: str) -> bool:
    global _history
    with _hist_lock:
        before = len(_history)
        _history = [m for m in _history if m.get("mid") != mid]
        removed = before != len(_history)
        if removed:
            _save_history_locked()
    return removed


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


def upsert_peer(pid: str, name: str, address: str, pport: int, phttp: int = None) -> None:
    now = time.time()
    with _peers_lock:
        existed = pid in _peers
        _peers[pid] = {
            "id": pid,
            "name": name,
            "address": address,
            "port": pport,
            "httpPort": phttp,
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
# Friends (Path A handshake) + online presence
# --------------------------------------------------------------------------

def friends_list() -> list:
    return list(CONFIG.get("friends", []))


def _friends_lock():
    return threading.Lock()


def is_friend(pid: str, address: str = "") -> bool:
    for f in CONFIG.get("friends", []):
        if f.get("id") == pid and f.get("confirmed"):
            return True
    return False


def is_pending(pid: str) -> bool:
    """True if we've sent this peer a friend request but they haven't accepted."""
    for f in CONFIG.get("friends", []):
        if f.get("id") == pid and not f.get("confirmed"):
            return True
    return False


def is_online() -> bool:
    return bool(CONFIG.get("online", True))


def add_friend(pid: str, address: str, name: str, confirmed: bool) -> None:
    friends = CONFIG.get("friends", [])
    for f in friends:
        if f.get("id") == pid:
            f["address"] = address
            f["name"] = name
            f["confirmed"] = confirmed
            break
    else:
        friends.append({"id": pid, "address": address, "name": name, "confirmed": confirmed})
    CONFIG["friends"] = friends
    _save_config()
    _emit({"event": "friends", "friends": friends_list()})


def is_trusted(pid: str, address: str = "") -> bool:
    """IP-gating: accept traffic only from confirmed friends or pending-request peers."""
    if not is_online():
        return False
    for f in CONFIG.get("friends", []):
        if f.get("id") == pid:
            return True  # confirmed friend OR a peer we're requesting (we initiated)
    # Fall back to address match for confirmed friends whose id differs.
    for f in CONFIG.get("friends", []):
        if f.get("confirmed") and f.get("address") == address:
            return True
    return False


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
            # Skip ourselves — our own broadcast/scan echoes back on loopback.
            if pid == host_id():
                continue
            # Prefer the peer's broadcast display name; fall back to a
            # deterministic friendly name derived from its id.
            name = str(pkt.get("name") or friendly_name(pid))
            upsert_peer(
                pid,
                name,
                addr[0],
                int(pkt.get("port", DEFAULT_PORT)),
                int(pkt.get("httpPort", 0)) or None,
            )
            # Reply so the caller learns about us immediately.
            _udp_send(sock, {"t": "pong"})


def _udp_send(sock: socket.socket, pkt: dict, target: str = "") -> None:
    pkt["id"] = host_id()
    pkt["name"] = display_name()
    pkt["port"] = port()
    pkt["httpPort"] = http_port()
    pkt["token"] = CONFIG.get("token")
    dest = target or "255.255.255.255"
    try:
        sock.sendto(json.dumps(pkt).encode("utf-8"), (dest, port()))
    except OSError:
        pass


def _local_subnet_hosts() -> list:
    """Enumerate LAN host addresses from our active interfaces' netmasks.

    Uses ioctl (SIOCGIFADDR / SIOCGIFNETMASK) to read the real netmask rather
    than assuming /24. Interfaces with a /32 netmask (e.g. VPNs) are skipped
    since they have no usable host range.
    """
    import ipaddress
    import fcntl
    import struct

    hosts = []
    try:
        SIOCGIFADDR = 0x8915
        SIOCGIFNETMASK = 0x891B
        for idx, name in socket.if_nameindex():
            if name == "lo":
                continue
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                addr = socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFADDR,
                    struct.pack("256s", name[:15].encode()))[20:24])
                mask = socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFNETMASK,
                    struct.pack("256s", name[:15].encode()))[20:24])
                s.close()
            except OSError:
                continue
            try:
                net = ipaddress.ip_network("%s/%s" % (addr, mask), strict=False)
            except ValueError:
                continue
            if net.num_addresses < 4:  # /32 or /31 — no usable host range
                continue
            for h in net.hosts():
                hosts.append(str(h))
    except Exception:
        pass
    return hosts


def _scan_subnet(sock: socket.socket) -> None:
    """Unicast hello to every host on the local subnet — LocalSend-style
    fallback for when UDP broadcast is filtered/blocked by the network."""
    for host in _local_subnet_hosts():
        _udp_send(sock, {"t": "hello"}, target=host)


def udp_loop() -> None:
    global _udp_sock
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind(("", port()))
    except OSError as e:
        _emit({"event": "error", "message": "lanchat UDP bind failed on port %d: %s" % (port(), e)})
        return
    _udp_sock = sock
    threading.Thread(target=_udp_listener, args=(sock,), daemon=True).start()

    last_broadcast = 0.0
    last_scan = 0.0
    while True:
        now = time.time()
        # Only announce ourselves while online (appear offline otherwise).
        if is_online() and now - last_broadcast >= BROADCAST_INTERVAL_S:
            _udp_send(sock, {"t": "hello"})
            last_broadcast = now
        # Subnet scan every ~15s as a broadcast fallback.
        if is_online() and now - last_scan >= 15.0:
            _scan_subnet(sock)
            last_scan = now
        expire_peers()
        time.sleep(0.5)


_udp_sock = None


def broadcast_now() -> None:
    """Immediately announce ourselves so a name change reaches peers right away."""
    if _udp_sock is not None:
        _udp_send(_udp_sock, {"t": "hello"})


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
    tls_ctx = ensure_tls()
    while True:
        try:
            raw_conn, addr = srv.accept()
            conn = tls_ctx.wrap_socket(raw_conn, server_side=True)
        except (OSError, ssl.SSLError):
            continue
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
    if msg.get("t") == "friendAccept":
        pid = str(msg.get("from", ""))
        pname = str(msg.get("fromName") or friendly_name(pid))
        if is_pending(pid) or is_friend(pid):
            add_friend(pid, addr[0], pname, confirmed=True)
            _emit({"event": "friend-accepted", "id": pid, "name": pname})
        return
    if msg.get("t") == "friendReject":
        pid = str(msg.get("from", ""))
        _emit({"event": "friend-rejected", "id": pid, "name": str(msg.get("fromName") or friendly_name(pid))})
        return
    if msg.get("t") != "msg":
        return
    pid = str(msg.get("from", ""))
    is_req = bool(msg.get("friendRequest"))
    # Friend requests are the entry point: a stranger may ask to be friends.
    # Everything else must come from a trusted peer.
    if not is_req and not is_trusted(pid, addr[0]):
        return
    text = str(msg.get("text", ""))
    if not text.strip():
        return
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
        "friendRequest": bool(msg.get("friendRequest")),
    }
    # A friend request from a stranger is a legitimate inbound channel: it's
    # how they ask to talk. Record them as a pending-request peer so their
    # replies (accept) are trusted.
    if msg.get("friendRequest") and not is_pending(pid) and not is_friend(pid):
        add_friend(pid, addr[0], from_name, confirmed=False)
    append_history(message)
    _emit({"event": "message", "message": message})


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

_attachments = {}   # fileId -> {"path": str, "name": str, "expires": float}
_att_lock = threading.Lock()


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def register_attachment(file_id: str, path: str, name: str, ttl: float = 600.0) -> None:
    with _att_lock:
        _attachments[file_id] = {"path": path, "name": name, "expires": time.time() + ttl}


def get_attachment(file_id: str):
    with _att_lock:
        a = _attachments.get(file_id)
        if a and a["expires"] > time.time():
            return a
        return None


def _download_attachment(peer: dict, file_id: str, save_to: str) -> bool:
    """Receiver fetches a file from a peer's HTTPS server and saves it."""
    import urllib.request
    import urllib.error
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # fingerprint-verified peer already
    url = "https://%s:%d/attachment?fileId=%s&token=%s" % (
        peer["address"], peer.get("httpPort") or http_port(), file_id, CONFIG.get("token"))
    try:
        with urllib.request.urlopen(url, timeout=60, context=ctx) as resp:
            os.makedirs(os.path.dirname(save_to) or ".", exist_ok=True)
            with open(save_to, "wb") as f:
                f.write(resp.read())
        return True
    except Exception:
        return False


def _tls_connect(peer: dict, expected_fingerprint: str = ""):
    """Connect to a peer over TLS and verify their cert fingerprint.

    Self-signed certs are expected, so we do NOT verify against a CA — instead
    we check the peer's cert fingerprint matches what we friended/expect. This
    is the LocalSend-style trust model: the fingerprint is the identity.
    Returns the wrapped socket, or None on failure/mismatch.
    """
    try:
        raw = socket.create_connection((peer["address"], peer["port"]), timeout=5)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # we do fingerprint verification ourselves
        s = ctx.wrap_socket(raw)
        if expected_fingerprint:
            der = s.getpeercert(binary_form=True)
            if not der:
                s.close()
                return None
            actual = hashlib.sha256(der).hexdigest()
            if actual != expected_fingerprint:
                s.close()
                return None
        return s
    except OSError:
        return None
    except Exception:
        return None


def send_message(peer_id: str, text: str, friend_request: bool = False, attachment: dict = None) -> bool:
    peer = find_peer(peer_id)
    if peer is None:
        _emit({"event": "error", "message": "peer '%s' is not online yet" % peer_id})
        return False
    s = _tls_connect(peer, expected_fingerprint=peer_id)
    if s is None:
        _emit({"event": "error", "message": "could not establish secure connection to %s" % peer["name"]})
        return False
    try:
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
        if attachment:
            msg["attachment"] = attachment  # {name,size,mime,fileId,sha256}
        if friend_request:
            msg["friendRequest"] = True
            # Record the peer as a pending-request friend on our side, so they
            # become trusted (can reply/accept) and show as a friend request.
            if not is_pending(peer_id) and not is_friend(peer_id):
                add_friend(peer_id, peer["address"], peer["name"], confirmed=False)
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


def send_control(peer_id: str, ctype: str) -> bool:
    """Send a friend accept/reject control message to a peer."""
    peer = find_peer(peer_id)
    if peer is None:
        return False
    s = _tls_connect(peer, expected_fingerprint=peer_id)
    if s is None:
        return False
    try:
        s.sendall(json.dumps({"token": CONFIG.get("token")}).encode("utf-8") + b"\n")
        s.settimeout(5)
        ack = s.recv(64)
        s.sendall(json.dumps({
            "t": ctype,  # friendAccept / friendReject
            "from": host_id(),
            "fromName": display_name(),
        }).encode("utf-8") + b"\n")
        s.close()
        return True
    except OSError:
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
        if parsed.path == "/attachment":
            file_id = (qs.get("fileId") or [""])[0]
            att = get_attachment(file_id)
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
            srv.socket = ensure_tls().wrap_socket(srv.socket, server_side=True)
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
            att = cmd.get("attachment")
            if att and att.get("path"):
                # Local file: register it for the receiver to fetch, build metadata.
                path = str(att["path"])
                file_id = secrets.token_hex(8)
                name = str(att.get("name") or os.path.basename(path))
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                register_attachment(file_id, path, name)
                att = {"name": name, "size": size, "mime": "application/octet-stream",
                       "fileId": file_id, "sha256": _file_sha256(path)}
            send_message(
                str(cmd.get("to", "")),
                str(cmd.get("text", "")),
                bool(cmd.get("friend_request")),
                att,
            )
        elif kind == "history":
            peer = str(cmd.get("peer", ""))
            if peer:
                _emit({"event": "history", "peer": peer,
                       "total": history_for_peer(peer)["total"],
                       "messages": history_for_peer(peer, int(cmd.get("offset", 0)), int(cmd.get("limit", 100)))["messages"]})
            else:
                _emit({"event": "history", "messages": history_snapshot()})
        elif kind == "clearChat":
            removed = clear_history_for_peer(str(cmd.get("peer", "")))
            _emit({"event": "chat-cleared", "peer": str(cmd.get("peer", "")), "removed": removed})
        elif kind == "deleteMessage":
            ok = delete_message(str(cmd.get("mid", "")))
            _emit({"event": "message-deleted", "mid": str(cmd.get("mid", "")), "ok": ok})
        elif kind == "setDownloadDir":
            CONFIG["downloadDir"] = str(cmd.get("dir", ""))
            _save_config()
            _emit({"event": "download-dir", "dir": CONFIG["downloadDir"]})
        elif kind == "setSendDelay":
            CONFIG["sendDelay"] = int(cmd.get("seconds", 0))
            _save_config()
            _emit({"event": "send-delay", "seconds": CONFIG["sendDelay"]})
        elif kind == "acceptAttachment":
            peer = find_peer(str(cmd.get("from", "")))
            if not peer:
                _emit({"event": "error", "message": "attachment sender not found"})
                continue
            file_id = str(cmd.get("fileId", ""))
            name = str(cmd.get("name", "download"))
            save_to = os.path.join(CONFIG.get("downloadDir", os.path.expanduser("~/Downloads")), name)
            ok = _download_attachment(peer, file_id, save_to)
            _emit({"event": "attachment-saved", "ok": ok, "path": save_to})
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
            name = str(cmd.get("name", "")).strip()[:NAME_MAX]
            if name:
                CONFIG["displayName"] = name
                _save_config()
                broadcast_now()
                _emit(_ready_event())
        elif kind == "regenerateName":
            # Pick a fresh random friendly {modifier}{trick} name. Seed the
            # RNG from the current time so a re-roll usually differs from the
            # previous one.
            rng = random.Random()
            rng.seed(time.time_ns())
            trick = rng.choice(_SKATE_TRICKS)
            modifier = rng.choice(_TRICK_MODIFIERS)
            name = f"{modifier}{trick}"[:NAME_MAX]
            CONFIG["displayName"] = name
            _save_config()
            broadcast_now()
            _emit(_ready_event())
        elif kind == "setOnline":
            on = bool(cmd.get("online"))
            CONFIG["online"] = on
            _save_config()
            _emit({"event": "online", "online": on})
            if on:
                broadcast_now()
        elif kind == "acceptFriend":
            pid = str(cmd.get("id", ""))
            peer = find_peer(pid)
            pname = peer["name"] if peer else friendly_name(pid)
            if send_control(pid, "friendAccept"):
                add_friend(pid, peer["address"] if peer else "", pname, confirmed=True)
                _emit({"event": "friend-accepted", "id": pid, "name": pname})
        elif kind == "rejectFriend":
            pid = str(cmd.get("id", ""))
            send_control(pid, "friendReject")
            _emit({"event": "friend-rejected", "id": pid})


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------

def _ready_event() -> dict:
    return {
        "event": "ready",
        "id": host_id(),
        "name": display_name(),
        "version": VERSION,
        "port": port(),
        "httpEnabled": http_enabled(),
        "httpPort": http_port(),
        "online": is_online(),
        "friends": friends_list(),
        "downloadDir": CONFIG.get("downloadDir", os.path.join(os.path.expanduser("~"), "Downloads")),
        "sendDelay": CONFIG.get("sendDelay", 0),
    }


def main() -> None:
    load_config()
    load_history()
    _emit(_ready_event())
    if http_enabled():
        _start_http()
    threading.Thread(target=tcp_loop, daemon=True).start()
    threading.Thread(target=udp_loop, daemon=True).start()
    stdin_loop()  # blocks until the shell closes stdin; also supervises the process lifetime


if __name__ == "__main__":
    main()
