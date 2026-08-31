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

import hashlib
import http.server
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

from naming import _SKATE_TRICKS, _TRICK_MODIFIERS, friendly_name  # noqa: E402

# --------------------------------------------------------------------------
# Paths & config
# --------------------------------------------------------------------------


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "omarchy")
CONFIG_PATH = os.path.join(CONFIG_DIR, "lanchat.json")
STATE_DIR = os.path.join(os.path.expanduser("~"), ".local", "state", "lanchat")
HISTORY_PATH = os.path.join(STATE_DIR, "history.json")

DEFAULT_PORT = 4812
DEFAULT_HTTP_PORT = 4814
PEER_TIMEOUT_S = 6.0       # drop a peer after this long without a hello
BROADCAST_INTERVAL_S = 3.0
HISTORY_LIMIT = 500

# Version of the plugin/daemon. Keep in sync with manifest.json "version".
# Bump when behaviour changes; breaking changes should bump the major number.
# The reported version derives from the checked-out git commit (short hash) so
# both machines can verify they're running the same code; falls back to the
# manifest version when git isn't available (e.g. a bare copy).
#
# 1.2.1 — security hardening of the HTTP API: it now binds loopback-only by
#   default (httpBind; set "0.0.0.0" for LAN exposure), and adds rate limits
#   (/send, failed-auth brute-force guard -> 429) plus a 256KB body cap.
# 1.2.0 — security: inbound message identity is now proven by challenge-response.
#   Python's TLS stack can't request a client cert without CA-verifying it (which
#   rejects self-signed peers), so on every inbound connection the dialing side
#   announces its cert and must sign a random nonce with the matching private
#   key before any of its messages are trusted. An attacker who harvested a
#   friend's fingerprint cannot impersonate them without the friend's key,
#   closing the spoofing gap. Old clients that never complete the proof are
#   dropped on inbound. Both machines must run >= 1.2.0.
# 1.1.0 — persistent bidirectional connections: one long-lived outbound TLS
#   socket per peer (message-over-socket, reconnect+hold/flush+dedupe, accept
#   over the existing socket). Old and new transports do not interoperate; both
#   machines must run >= 1.1.0.
VERSION = "1.2.1"


def _git_version() -> str:
    try:
        import subprocess as _sp
        here = os.path.dirname(os.path.abspath(__file__))
        short = _sp.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=here, stderr=_sp.DEVNULL, text=True).strip()
        if short:
            return "%s-%s" % (VERSION, short)
    except Exception:
        pass
    return VERSION


VERSION = _git_version()

CONFIG = {}
_out_lock = threading.Lock()
_stdout = sys.stdout

NAME_MAX = 32  # maximum length of a display name (generated or custom)


def atomic_write(path: str, text: str) -> None:
    """Write a file atomically (write temp + rename) so a crash mid-write
    never leaves a truncated/corrupt file. Caller is responsible for making
    the parent directory exist."""
    import tempfile
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------
# A timestamped diagnostic log (in addition to the stdout event stream) so a
# failing handshake / vanishing peer / one-way delivery can be traced after
# the fact. Written best-effort; never raises.

_LOG_PATH = os.path.join(STATE_DIR, "daemon.log")
_log_lock = threading.Lock()


def _log(msg: str) -> None:
    """Append one timestamped line to the diagnostic log (thread-safe, best-effort)."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _log_lock:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write("%s  %s\n" % (ts, msg))
    except OSError:
        pass


def _diag(msg: str, **fields) -> None:
    """Log a diagnostic line AND surface it to the UI as a diagnostic event."""
    extra = ("  " + " ".join("%s=%s" % (k, v) for k, v in fields.items())) if fields else ""
    _log(msg + extra)
    ev = {"event": "diagnostic", "message": msg, "ts": int(time.time() * 1000), "version": VERSION}
    ev.update(fields)
    _emit(ev)


def _save_config() -> None:
    try:
        atomic_write(CONFIG_PATH, json.dumps(CONFIG, indent=2) + "\n")
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
    # API bind address. Loopback-only by default so the token-authenticated API
    # isn't exposed to the LAN; set to "0.0.0.0" to allow remote agents
    # (e.g. other machines holding the token) to reach it.
    CONFIG.setdefault("httpBind", "127.0.0.1")
    # Full API access to chat data (read history/peers) vs send-only.
    # When False, the agent can send messages to friends but cannot read
    # history, list peers, or download attachments.
    CONFIG.setdefault("apiFullAccess", False)
    # Panel size: "small" | "medium" | "large" | "xl" | "full".
    CONFIG.setdefault("panelSize", "medium")
    # Manual pixel override for panel size (0 = follow the preset).
    CONFIG.setdefault("customW", 0)
    CONFIG.setdefault("customH", 0)
    # User status: "available" | "dnd" | "away" | "brb". Broadcast to friends.
    CONFIG.setdefault("status", "available")
    # Play a sound on incoming messages.
    CONFIG.setdefault("soundEnabled", True)
    # Presence indicators (each direction independently toggleable).
    CONFIG.setdefault("typingEnabled", True)        # send my typing to peers
    CONFIG.setdefault("showTyping", True)           # show peers' typing in my UI
    CONFIG.setdefault("readReceiptsEnabled", True)  # send my read receipts to peers
    CONFIG.setdefault("showReadReceipts", True)     # show read receipts in my UI
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
            import datetime as _dt

            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
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


# --------------------------------------------------------------------------
# Identity proof (challenge-response)
# --------------------------------------------------------------------------
# The TCP transport has no client certs (Python can't request a cert without
# CA-verifying it, which rejects self-signed peers). To close the
# unauthenticated-inbound spoofing gap, the dialing side proves it owns the
# private key for its claimed cert fingerprint: the receiver reads the peer's
# `identity` (claimed id + cert), sends a random `challenge` nonce, and the
# peer must return an `identityProof` — a signature over that nonce using the
# claimed cert's private key. A stranger who harvested a friend's fingerprint
# but not its key cannot sign the nonce, so impersonation is impossible.

_priv_key = None
_priv_key_lock = threading.Lock()


def _our_cert_pem() -> str:
    if not os.path.exists(CERT_PEM):
        ensure_tls()
    with open(CERT_PEM, "r", encoding="utf-8") as f:
        return f.read()


def _load_priv_key():
    global _priv_key
    if _priv_key is None:
        from cryptography.hazmat.primitives import serialization
        with _priv_key_lock:
            if _priv_key is None:
                if not os.path.exists(CERT_KEY):
                    ensure_tls()
                with open(CERT_KEY, "rb") as f:
                    _priv_key = serialization.load_pem_private_key(f.read(), password=None)
    return _priv_key


def _sign(data: bytes) -> str:
    """Sign bytes with our private key (RSA PKCS1v15-SHA256), hex-encoded."""
    import binascii

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    sig = _load_priv_key().sign(data, padding.PKCS1v15(), hashes.SHA256())
    return binascii.hexlify(sig).decode("ascii")


def _verify(cert_pem: str, data: bytes, sig_hex: str) -> bool:
    """Verify a signature over `data` using the public key in `cert_pem`."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
        pub = cert.public_key()
        sig = bytes.fromhex(sig_hex)
        pub.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def _cert_fingerprint_of_pem(cert_pem: str) -> str:
    """SHA-256 fingerprint of the cert in `cert_pem` (empty on parse failure)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
        return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    except Exception:
        return ""


def port() -> int:
    try:
        return int(CONFIG.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        return DEFAULT_PORT


def http_enabled() -> bool:
    return bool(CONFIG.get("httpEnabled", False))


def api_full_access() -> bool:
    """Whether the API can read chat data (history/peers/attachments) or is send-only."""
    return bool(CONFIG.get("apiFullAccess", False))


def panel_size() -> str:
    size = str(CONFIG.get("panelSize", "medium"))
    return size if size in ("small", "medium", "large", "xl", "full") else "medium"


STATUSES = ("available", "dnd", "away", "brb")


def status() -> str:
    s = str(CONFIG.get("status", "available"))
    return s if s in STATUSES else "available"


def sound_enabled() -> bool:
    return bool(CONFIG.get("soundEnabled", True))


def typing_enabled() -> bool:
    return bool(CONFIG.get("typingEnabled", True))


def show_typing() -> bool:
    return bool(CONFIG.get("showTyping", True))


def read_receipts_enabled() -> bool:
    return bool(CONFIG.get("readReceiptsEnabled", True))


def show_read_receipts() -> bool:
    return bool(CONFIG.get("showReadReceipts", True))


def http_port() -> int:
    try:
        return int(CONFIG.get("httpPort", DEFAULT_HTTP_PORT))
    except (TypeError, ValueError):
        return DEFAULT_HTTP_PORT


def http_bind() -> str:
    """Loopback by default; "0.0.0.0" opts into LAN exposure."""
    return str(CONFIG.get("httpBind") or "127.0.0.1")


# --------------------------------------------------------------------------
# Message history (per-machine persistence)
# --------------------------------------------------------------------------

_history = []
_hist_lock = threading.Lock()
_seen_mids = set()      # mids already appended to history (inbound dedupe)


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
        _seen_mids.add(message["mid"])
        _history.append(message)
        if len(_history) > HISTORY_LIMIT:
            _history = _history[-HISTORY_LIMIT:]
        _save_history_locked()


def _save_history_locked() -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        atomic_write(HISTORY_PATH, json.dumps(_history, separators=(",", ":")))
    except OSError:
        pass


def history_snapshot() -> list:
    with _hist_lock:
        return list(_history)


def _has_mid(mid: str) -> bool:
    """True if a message with this mid is already in history (dedupe on
    reconnect / re-delivery)."""
    with _hist_lock:
        return mid in _seen_mids


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


def clear_all_history() -> int:
    """Clear every conversation (both sent and received messages)."""
    global _history
    with _hist_lock:
        removed = len(_history)
        _history = []
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


def edit_message(mid: str, new_text: str) -> bool:
    """Replace a message's text (by mid). Returns True if found and edited."""
    global _history
    new_text = new_text.strip()
    if not new_text:
        return False
    with _hist_lock:
        for m in _history:
            if m.get("mid") == mid:
                m["text"] = new_text
                m["edited"] = True
                _save_history_locked()
                return True
    return False


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


def upsert_peer(pid: str, name: str, address: str, pport: int, phttp: object = None, pstatus: str = "available", pversion: str = "") -> None:
    now = time.time()
    with _peers_lock:
        existed = pid in _peers
        prev_version = _peers[pid].get("version", "") if existed else ""
        _peers[pid] = {
            "id": pid,
            "name": name,
            "address": address,
            "port": pport,
            "httpPort": phttp,
            "status": pstatus if pstatus in STATUSES else "available",
            "lastSeen": int(now * 1000),
            "version": pversion or prev_version,
        }
    if not existed:
        _diag("peer-discovered", id=pid[:12], name=name, address=address, port=pport, version=pversion)
        _emit({"event": "peer", "peer": _peers[pid]})
    else:
        _emit({"event": "peer", "peer": _peers[pid]})


def expire_peers() -> None:
    now = time.time()
    gone = []
    with _peers_lock:
        for pid in list(_peers.keys()):
            age = now - _peers[pid]["lastSeen"] / 1000.0
            if age > PEER_TIMEOUT_S:
                gone.append((pid, age))
                del _peers[pid]
    for pid, age in gone:
        _drop_conn(pid)  # close any persistent socket to the expired peer
        _diag("peer-expired", id=pid[:12], age_s=round(age, 1), timeout_s=PEER_TIMEOUT_S)
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


def unfriend(pid: str) -> bool:
    """Remove a peer from friends (unfriend). Returns True if they were removed."""
    friends = CONFIG.get("friends", [])
    before = len(friends)
    friends = [f for f in friends if f.get("id") != pid]
    CONFIG["friends"] = friends
    _save_config()
    _emit({"event": "friends", "friends": friends_list()})
    return len(friends) != before


def is_trusted(pid: str, address: str = "") -> bool:
    """Accept traffic only from confirmed friends or pending-request peers.

    Trust is keyed on the peer's identity (cert fingerprint id), not the IP
    address — so a forged/unknown id from the same IP as a friend is NOT
    trusted.
    """
    if not is_online():
        return False
    for f in CONFIG.get("friends", []):
        if f.get("id") == pid:
            return True  # confirmed friend OR a peer we're requesting (we initiated)
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
        # No token on discovery — any machine on the LAN can be seen, and the
        # friend handshake is the gate that decides who can actually message us.
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
                str(pkt.get("status") or "available"),
                str(pkt.get("version") or ""),
            )
            # Reply so the caller learns about us immediately. Send a UNICAST
            # pong back to the sender's address — broadcast replies get lost
            # on networks where broadcasts are filtered, leaving discovery
            # one-way (they see us, we don't see them).
            _udp_send(sock, {"t": "pong"}, target=addr[0])


def _udp_send(sock: socket.socket, pkt: dict, target: str = "") -> None:
    pkt["id"] = host_id()
    pkt["name"] = display_name()
    pkt["port"] = port()
    pkt["httpPort"] = http_port()
    pkt["status"] = status()
    pkt["version"] = VERSION
    dest = target or "255.255.255.255"
    try:
        sock.sendto(json.dumps(pkt).encode("utf-8"), (dest, port()))
    except OSError as e:
        # A full send buffer (ENOBUFS / EAGAIN) means we're flooding faster than
        # the socket drains — the classic symptom is peers "vanishing" because
        # their hellos never make it out. Surface it (rate-limited) so it's
        # diagnosable without spamming the log.
        errno = getattr(e, "errno", None)
        now = time.time()
        _udp_fail_window[0] += 1
        _udp_fail_window[1] = now
        # Only log every ~5s of failures, and summarize the count.
        if now - _udp_fail_window[2] >= 5.0:
            _udp_fail_window[2] = now
            _diag("udp-send-failed", target=dest[:32], errno=errno, count=_udp_fail_window[0])
            _udp_fail_window[0] = 0


_udp_fail_window = [0, 0.0, 0.0]  # [count since last log, last_fail_time, last_log_time]


def _local_subnet_hosts(max_hosts: int = 512) -> list:
    """Enumerate LAN host addresses from our active interfaces' netmasks.

    Uses ioctl (SIOCGIFADDR / SIOCGIFNETMASK) to read the real netmask rather
    than assuming /24. Only broadcast-capable, non-point-to-point interfaces
    with a usable host range are considered — this excludes VPN tunnels
    (point-to-point, e.g. tailscale) and virtual bridges (docker0, veth, etc.)
    which are either point-to-point or huge non-LAN networks that would flood
    the UDP send queue. The total is also capped so a misread interface can
    never dump thousands of unicast hellos per scan.
    """
    import fcntl
    import ipaddress
    import struct

    hosts = []
    try:
        SIOCGIFADDR = 0x8915
        SIOCGIFNETMASK = 0x891B
        SIOCGIFFLAGS = 0x8913
        IFF_POINTOPOINT = 0x10
        IFF_BROADCAST = 0x2
        # Virtual/container interface name prefixes to skip: docker bridges and
        # veth pairs, VM bridges, tun/tap tunnels, wireguard, etc. These are not
        # the LAN we're scanning and scanning their (often /16) subnets would
        # flood the UDP send queue.
        _VIRT_PREFIXES = ("docker", "br-", "veth", "virbr", "vmnet", "vboxnet",
                          "tun", "tap", "wg", "tailscale", "zt", "docker_gwbridge")
        for idx, name in socket.if_nameindex():
            if name == "lo":
                continue
            if name.lower().startswith(_VIRT_PREFIXES):
                continue
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                flags = struct.unpack("H", fcntl.ioctl(s.fileno(), SIOCGIFFLAGS,
                    struct.pack("256s", name[:15].encode()))[16:18])[0]
                addr = socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFADDR,
                    struct.pack("256s", name[:15].encode()))[20:24])
                mask = socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFNETMASK,
                    struct.pack("256s", name[:15].encode()))[20:24])
                s.close()
            except OSError:
                continue
            # VPN/tunnel/virtual interfaces are not LAN: they're either
            # point-to-point (no real subnet) or broadcast-capable virtual
            # bridges (docker/veth) that aren't the LAN we're scanning for.
            if not (flags & IFF_BROADCAST) or (flags & IFF_POINTOPOINT):
                continue
            try:
                net = ipaddress.ip_network("%s/%s" % (addr, mask), strict=False)
            except ValueError:
                continue
            if net.num_addresses < 4:  # /32 or /31 — no usable host range
                continue
            for h in net.hosts():
                hosts.append(str(h))
                if len(hosts) >= max_hosts:
                    return hosts
    except Exception:
        pass
    return hosts


def _scan_subnet(sock: socket.socket) -> None:
    """Unicast hello to every host on the local subnet — LocalSend-style
    fallback for when UDP broadcast is filtered/blocked by the network.

    Called once at startup only. Sends are throttled so the scan never bursts
    enough datagrams to back up the socket's send queue.
    """
    for host in _local_subnet_hosts():
        _udp_send(sock, {"t": "hello"}, target=host)
        time.sleep(0.01)  # ~100/s — keeps the buffer from filling on a /24


def _announce_to_known(sock: socket.socket) -> None:
    """Unicast hello directly to every peer we already know.

    Broadcast may be filtered on some networks (so periodic broadcast hellos
    never arrive), but unicast always works. Re-announcing to known peers every
    broadcast interval keeps them from expiring — this is what makes steady-
    state discovery reliable where broadcast is unreliable.
    """
    with _peers_lock:
        known = [(p["address"], p["port"]) for p in _peers.values()]
    for addr, pport in known:
        _udp_send(sock, {"t": "hello"}, target=addr)


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
    last_unicast = 0.0
    scanned_once = False
    while True:
        now = time.time()
        # Only announce ourselves while online (appear offline otherwise).
        if is_online() and now - last_broadcast >= BROADCAST_INTERVAL_S:
            _udp_send(sock, {"t": "hello"})
            last_broadcast = now
            # Reliable steady-state discovery: on networks where UDP broadcast
            # is filtered, peers only ever appear via the one-time scan. Keep
            # them alive by re-announcing DIRECTLY to every known peer every
            # broadcast interval — unicast always works, unlike broadcast.
            _announce_to_known(sock)
            last_unicast = now
        # Subnet scan ONCE shortly after startup, as a broadcast fallback for
        # networks where broadcasts are filtered. It is NOT repeated: blind
        # unicasting to every host on the /24 every few seconds floods the UDP
        # send queue and chokes inbound traffic. Broadcast + unicast-to-known
        # handle steady-state discovery; the scan only seeds peers that
        # broadcast filtering hides.
        if is_online() and not scanned_once and now - last_broadcast >= 3.0:
            _scan_subnet(sock)
            scanned_once = True
        expire_peers()
        time.sleep(0.5)


_udp_sock = None


def broadcast_now() -> None:
    """Immediately announce ourselves so a name change reaches peers right away."""
    if _udp_sock is not None:
        _udp_send(_udp_sock, {"t": "hello"})


# --------------------------------------------------------------------------
# Persistent bidirectional connection manager
# --------------------------------------------------------------------------
# Every peer gets ONE active full-duplex TLS socket, reused for many messages
# in both directions — no per-message dial. Both sides dial out to every known
# peer AND accept the peer's dial. To avoid a racy split-brain (each side
# keeping a different socket of a two-dial pair), the socket initiated by the
# side with the LOWER cert-fingerprint id is the "keeper"; the redundant one is
# closed. A socket that drops triggers reconnect-with-backoff; messages sent
# while no socket is up are held and flushed on reconnect, deduped by mid.
#
# Identity: an outbound socket's peer id is known (we verified the cert). An
# inbound socket presents no client cert, so its peer id is learned from the
# first message's "from" field (the existing self-claimed-id trust model).

_conns = {}          # pid -> connection state dict
_conns_lock = threading.Lock()
_RECONNECT_MAX_BACKOFF = 16.0


def _conn(pid: str) -> dict:
    with _conns_lock:
        c = _conns.get(pid)
        if c is None:
            c = {
                "pid": pid,
                "sock": None,
                "initiator": None,      # host_id() if we dialed, else the pid
                "lock": threading.Lock(),   # serializes writes + close
                "backoff": 2.0,
                "next_try": 0.0,
                "dialing": False,
                "hold": [],             # outbound msgs awaiting a socket
                "sent_mids": set(),     # mids already written (dedupe on flush)
            }
            _conns[pid] = c
        return c


def _close_sock(sock) -> None:
    # shutdown() before close() reliably wakes a concurrent recv() blocked in
    # another thread's reader — a bare close() does NOT on Linux, which left
    # "loser" sockets open forever (the ESTAB socket leak that stalled delivery).
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _flush_locked(c: dict) -> None:
    """Write queued outbound messages over the active socket, in order, skipping
    any mid already written before a drop (dedupe). Called under c['lock']."""
    if c["sock"] is None:
        return
    still = []
    for p in c["hold"]:
        mid = p.get("mid")
        if mid and mid in c["sent_mids"]:
            continue  # already attempted before the socket dropped
        try:
            c["sock"].sendall(json.dumps(p, separators=(",", ":")).encode("utf-8") + b"\n")
            if mid:
                c["sent_mids"].add(mid)
        except OSError:
            still.append(p)
            break  # socket died mid-flush; keep the rest for the next reconnect
    c["hold"] = still


def _write(pid: str, payload: dict) -> bool:
    """Write one JSON line to a peer's active socket, or hold it for later if the
    socket is down. Returns True if delivered now, False if queued/dropped.
    Only messages (and the accept handshake) are held; transient control
    messages (typing / read / reject) are dropped when the socket is down."""
    c = _conn(pid)
    holdable = payload.get("t") in ("msg", "friendAccept")
    mid = payload.get("mid")
    with c["lock"]:
        sock = c["sock"]
        if sock is not None:
            try:
                sock.sendall(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
                if mid:
                    c["sent_mids"].add(mid)
                return True
            except OSError:
                _close_sock(sock)
                c["sock"] = None
        if holdable:
            c["hold"].append(payload)
        return False


def _drop_conn(pid: str) -> None:
    c = _conn(pid)
    with c["lock"]:
        sock = c["sock"]
        c["sock"] = None
        c["initiator"] = None
    if sock is not None:
        _close_sock(sock)


def _drop_all_conns() -> None:
    with _conns_lock:
        pids = list(_conns.keys())
    for pid in pids:
        _drop_conn(pid)


def _maybe_set_active(pid: str, sock, initiator: str) -> bool:
    """Try to make `sock` the active connection for `pid`. When another socket
    already holds the slot, apply the deterministic keeper tie-break (keep the
    socket initiated by the lower fingerprint id) so both machines converge on
    the same connection. Adopts `sock` unconditionally when no socket is up yet.
    Returns True if `sock` is (or becomes) the active connection."""
    keeper = min(host_id(), pid)
    c = _conn(pid)
    with c["lock"]:
        cur = c["sock"]
        if cur is sock:
            return True
        if cur is not None:
            cur_init = c.get("initiator")
            if cur_init == keeper and initiator != keeper:
                return False      # a better (keeper) socket already active
            if cur_init != keeper and initiator != keeper:
                return False      # neither is the keeper yet — keep the first
            _close_sock(cur)      # new socket is the keeper -> replace
        c["sock"] = sock
        c["initiator"] = initiator
        c["backoff"] = 2.0
        c["next_try"] = 0.0
        try:
            sock.settimeout(None)
        except OSError:
            pass
        _flush_locked(c)
        return True


def _reader_outbound(pid: str, c: dict, sock) -> None:
    """Reader for a socket WE dialed. We already verified the peer's cert
    fingerprint (conn_loop passes expected_fingerprint=pid), so messages from
    the peer are trusted. We still participate in the peer's identity proof:
    its server sends us a `challenge` and we reply with a signature over the
    nonce (proving WE own the key for our claimed id). Reads messages until the
    socket drops, then clears the slot so the supervisor reconnects."""
    src = ("", 0)
    try:
        src = sock.getpeername()[:2]
    except OSError:
        pass
    buf = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except ValueError:
                        continue
                    t = msg.get("t")
                    if t == "challenge":
                        nonce = str(msg.get("nonce") or "")
                        if nonce:
                            try:
                                sock.sendall(json.dumps(
                                    {"t": "identityProof", "sig": _sign(nonce.encode("utf-8"))},
                                    separators=(",", ":"),
                                ).encode("utf-8") + b"\n")
                            except OSError:
                                pass
                        continue
                    if t == "identity":
                        # Peer's server announcing itself; we already trust it.
                        continue
                    _handle_incoming(msg, src)
    except (OSError, ssl.SSLError):
        pass
    finally:
        with c["lock"]:
            if c["sock"] is sock:
                c["sock"] = None
                c["initiator"] = None
        _close_sock(sock)


def _reader_inbound(sock) -> None:
    """Reader for a socket the PEER dialed. Identity is NOT taken on faith: the
    peer must first prove it owns the private key for its claimed cert
    fingerprint. It sends `identity` (claimed id + cert), we reply `challenge`
    (random nonce), and it must return `identityProof` (signature over the
    nonce). Only then are its messages adopted/processed — so an attacker who
    harvested a friend's fingerprint but not its key cannot impersonate them.
    If it loses the keeper tie-break to an already-active socket, it still
    reads inbound messages but is never used for writing."""
    src = ("", 0)
    try:
        src = sock.getpeername()[:2]
    except OSError:
        pass
    addr = src[0] if src else "?"
    pid = None
    active = False
    buf = b""
    auth_state = "idle"     # idle -> awaiting identity; challenged -> awaiting proof; ok -> done
    auth_cert = ""          # peer's cert (PEM) once identity is accepted
    nonce = ""              # the challenge we sent
    deferred = []           # messages arriving before proof completes
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                t = msg.get("t")
                if auth_state != "ok":
                    if t == "identity" and auth_state == "idle":
                        claimed = str(msg.get("from") or "")
                        cert_pem = str(msg.get("cert") or "")
                        if (not claimed or claimed == host_id()
                                or not cert_pem
                                or _cert_fingerprint_of_pem(cert_pem) != claimed):
                            _log("inbound-identity-rejected from=%s addr=%s reason=bad-identity"
                                 % (claimed[:12], addr))
                            return
                        pid = claimed
                        auth_cert = cert_pem
                        nonce = secrets.token_hex(16)
                        auth_state = "challenged"
                        try:
                            sock.sendall(json.dumps(
                                {"t": "challenge", "nonce": nonce}, separators=(",", ":")
                            ).encode("utf-8") + b"\n")
                        except OSError:
                            return
                    elif t == "identityProof" and auth_state == "challenged":
                        if not _verify(auth_cert, nonce.encode("utf-8"), str(msg.get("sig") or "")):
                            _log("inbound-identity-rejected from=%s addr=%s reason=bad-signature"
                                 % (pid[:12], addr))
                            return
                        # Proven: this connection owns the key for `pid`.
                        auth_state = "ok"
                        active = _maybe_set_active(pid, sock, initiator=pid)
                        for m in deferred:
                            _handle_incoming(m, src)
                        deferred = []
                    else:
                        # While awaiting proof, hold any other messages (e.g. a
                        # friend request the dialer sent right after identity).
                        deferred.append(msg)
                    continue
                # Authenticated: normal inbound handling.
                if pid is not None:
                    _handle_incoming(msg, src)
    except (OSError, ssl.SSLError):
        pass
    finally:
        if active and pid:
            c = _conn(pid)
            with c["lock"]:
                if c["sock"] is sock:
                    c["sock"] = None
                    c["initiator"] = None
        _close_sock(sock)


def _peer_dial_targets() -> list:
    """Known peers + confirmed friends to keep a connection to. Friends we
    haven't seen recently are still dialed from their stored address."""
    targets = {}
    with _peers_lock:
        for pid, p in _peers.items():
            targets[pid] = (p["address"], p["port"])
    for f in CONFIG.get("friends", []):
        if f.get("confirmed") and f.get("address"):
            targets.setdefault(f["id"], (f["address"], f.get("port") or DEFAULT_PORT))
    return [(pid, addr, pport) for pid, (addr, pport) in targets.items()]


def conn_loop() -> None:
    """Supervisor: dial out to peers with no active socket, respecting backoff."""
    while True:
        time.sleep(1.0)
        if not is_online():
            continue
        now = time.time()
        for pid, addr, pport in _peer_dial_targets():
            if pid == host_id():
                continue
            c = _conn(pid)
            with c["lock"]:
                if c["sock"] is not None or c["dialing"]:
                    continue
                if now < c.get("next_try", 0.0):
                    continue
                c["dialing"] = True
            s = _tls_connect({"id": pid, "address": addr, "port": pport, "name": pid},
                             expected_fingerprint=pid)
            with c["lock"]:
                c["dialing"] = False
            if s is None:
                with c["lock"]:
                    c["backoff"] = min(c["backoff"] * 2.0, _RECONNECT_MAX_BACKOFF)
                    c["next_try"] = now + c["backoff"]
                continue
            # Announce our identity (claimed fingerprint + cert) so the peer's
            # inbound reader can challenge us and verify we own the key. Sent
            # before any real message; harmless if the peer is an old version.
            try:
                s.sendall(json.dumps(
                    {"t": "identity", "from": host_id(), "cert": _our_cert_pem()},
                    separators=(",", ":"),
                ).encode("utf-8") + b"\n")
            except OSError:
                _close_sock(s)
                continue
            if _maybe_set_active(pid, s, initiator=host_id()):
                threading.Thread(target=_reader_outbound, args=(pid, c, s), daemon=True).start()
            else:
                _close_sock(s)


# --------------------------------------------------------------------------
# TCP listener (accepts the peer's outbound dials)
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
        threading.Thread(target=_reader_inbound, args=(conn,), daemon=True).start()


# Handshake hold: pid -> list of held messages awaiting the recipient's accept.
# _pending_first = inbound requests we received and are holding (revealed on accept).
# _pending_sent  = outbound requests we sent and are holding (revealed on accept).
_pending_first = {}
_pending_sent = {}
_pending_lock = threading.Lock()


def _reveal(held, outgoing: bool = False) -> None:
    """Surface held handshake messages as normal messages (clears the hold)."""
    for m in held:
        m.pop("held", None)
        m.pop("friendRequest", None)
        append_history(m)
        _emit({"event": "message", "message": m})


def _handle_incoming(msg: dict, addr) -> None:
    if msg.get("t") == "friendAccept":
        pid = str(msg.get("from", ""))
        pname = str(msg.get("fromName") or friendly_name(pid))
        if is_pending(pid) or is_friend(pid):
            add_friend(pid, addr[0], pname, confirmed=True)
            _emit({"event": "friend-accepted", "id": pid, "name": pname})
            # They accepted: reveal the messages we held until then.
            with _pending_lock:
                held = _pending_sent.pop(pid, [])
            _diag("inbound-friend-accept", peer=pid[:12], name=pname, revealed=len(held))
            _reveal(held)
        return
    if msg.get("t") == "friendReject":
        pid = str(msg.get("from", ""))
        # They declined: drop our held-outgoing messages for them.
        with _pending_lock:
            _pending_sent.pop(pid, None)
        _emit({"event": "friend-rejected", "id": pid, "name": str(msg.get("fromName") or friendly_name(pid))})
        _diag("inbound-friend-reject", peer=pid[:12])
        return
    if msg.get("t") == "typing":
        _emit({"event": "typing", "from": str(msg.get("from", "")), "fromName": str(msg.get("fromName") or friendly_name(msg.get("from", "")))})
        return
    if msg.get("t") == "typingStopped":
        _emit({"event": "typing-stopped", "from": str(msg.get("from", ""))})
        return
    if msg.get("t") == "read":
        _emit({"event": "read-receipt", "from": str(msg.get("from", "")), "mid": str(msg.get("mid", ""))})
        return
    if msg.get("t") != "msg":
        return
    pid = str(msg.get("from", ""))
    is_req = bool(msg.get("friendRequest"))
    # Dedupe on re-delivery: a message whose mid is already in history (e.g.
    # the socket dropped after delivery but before ack, then re-flushed) is
    # not appended again.
    if msg.get("mid") and _has_mid(msg["mid"]):
        _log("inbound-dropped from=%s reason=duplicate-mid %s" % (pid[:12], msg["mid"][:12]))
        return
    # Friend requests are the entry point: a stranger may ask to be friends.
    # Everything else must come from a trusted peer.
    if not is_req and not is_trusted(pid, addr[0]):
        # The classic one-way-delivery failure: the sender thinks they're
        # friends, but we drop their message because we don't consider them
        # trusted. Record whether they're a friend, pending, or unknown so the
        # asymmetry is visible.
        _diag("inbound-dropped",
              from_id=pid[:12], address=addr[0],
              reason="untrusted",
              friend=is_friend(pid), pending=is_pending(pid))
        return
    text = str(msg.get("text", ""))
    att = msg.get("attachment")
    # Allow an attachment-only message with no text (the UI sends those); only
    # drop truly empty messages.
    if not text.strip() and not att:
        _log("inbound-dropped from=%s reason=empty" % pid[:12])
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
    if msg.get("mid"):
        message["mid"] = msg["mid"]
    # Carry the attachment metadata (name/size/mime/fileId/sha256) through so
    # the receiver can present the accept bar and download the file.
    if att:
        message["attachment"] = att
    # A friend request from a stranger is a legitimate inbound channel: it's
    # how they ask to talk. Record them as a pending-request peer so their
    # replies (accept) are trusted. Hold EVERY friend request from a
    # non-confirmed peer (not just the first) so content never surfaces until
    # they're accepted.
    if msg.get("friendRequest") and not is_friend(pid):
        if not is_pending(pid):
            add_friend(pid, addr[0], from_name, confirmed=False)
        # Do NOT surface the message content yet — hold it so the receiver
        # sees only a friend request until they accept. Assign a mid now so
        # the accept can reveal the same message (replace in the UI).
        if not message.get("mid"):
            message["mid"] = secrets.token_hex(8)
        message["held"] = True
        with _pending_lock:
            _pending_first.setdefault(pid, []).append(message)
        _emit({"event": "friend-request", "from": pid, "fromName": from_name, "text": text, "ts": ts, "mid": message["mid"]})
        _diag("inbound-friend-request", peer=pid[:12], name=from_name, text=text[:40])
        return
    append_history(message)
    _emit({"event": "message", "message": message})
    _diag("inbound-message", peer=pid[:12], name=from_name, text=text[:40])


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


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _safe_filename(name: str) -> str:
    """Reduce an untrusted attachment name to a safe basename.

    The name arrives inside a peer's message and is joined straight into the
    download directory, so it must never carry a path separator, dot-dot, or
    control character (blocks path-traversal writes outside downloadDir).
    """
    if not name:
        return "download"
    # Normalize both separator styles, then keep only the final component.
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    # Drop control characters and leading/trailing dots + whitespace.
    name = "".join(c for c in name if c.isprintable() and ord(c) >= 0x20)
    name = name.strip(" .")
    return name or "download"


def register_attachment(file_id: str, path: str, name: str, ttl: float = 600.0) -> None:
    with _att_lock:
        _attachments[file_id] = {"path": path, "name": name, "expires": time.time() + ttl}


def get_attachment(file_id: str):
    with _att_lock:
        a = _attachments.get(file_id)
        if a and a["expires"] > time.time():
            return a
        return None


def _http_response(sock, path: str):
    """Send a GET over a connected TLS socket and read status + headers.

    Returns (code, headers, content_length, body_prefix) where body_prefix is
    any body bytes already read with the headers. The remaining body is read
    from the socket by the caller (streamed), so we never buffer a whole file.
    """
    sock.sendall(("GET %s HTTP/1.0\r\nHost: lanchat\r\nConnection: close\r\n\r\n" % path).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = lines[0].decode("latin-1", "replace") if lines else ""
    parts = status.split(" ")
    code = int(parts[1]) if len(parts) > 1 else 0
    headers = {}
    for ln in lines[1:]:
        if b":" in ln:
            k, _, v = ln.partition(b":")
            headers[k.strip().lower()] = v.strip().decode("latin-1", "replace")
    clen = 0
    try:
        clen = int(headers.get("content-length") or 0)
    except (TypeError, ValueError):
        clen = 0
    return code, headers, clen, rest


def _download_attachment(peer: dict, file_id: str, save_to: str,
                         expected_sha256: str = "", mid: str = "",
                         expected_fingerprint: str = "") -> bool:
    """Receiver fetches a file from a peer's HTTPS server and saves it.

    The TLS connection's cert fingerprint is verified against the peer's true
    identity (expected_fingerprint = the friend's cert fingerprint), exactly as
    the message transport does — closing the previous unauthenticated gap. The
    body is streamed to a temp file that is atomically renamed on success, the
    sender's sha256 is verified when provided, and progress/saved events are
    emitted from the calling thread. The acceptAttachment handler runs this in a
    background thread so a large transfer never blocks the message/presence loop.
    """
    addr = peer.get("address", "?")
    hport = peer.get("httpPort") or http_port()
    name = str(peer.get("name") or "?")
    tmp = save_to + ".part"
    s = None
    try:
        s = _tls_connect_host(addr, hport, name, expected_fingerprint=expected_fingerprint)
        if s is None:
            _log("attachment-download-rejected peer=%s file=%s err=untrusted-cert"
                 % (name, os.path.basename(save_to)))
            _emit({"event": "attachment-saved", "ok": False, "path": save_to,
                   "mid": mid, "fileId": file_id, "error": "sender certificate not trusted"})
            return False
        s.settimeout(60)
        code, headers, clen, prefix = _http_response(
            s, "/attachment?fileId=%s&token=%s" % (file_id, CONFIG.get("token")))
        if code != 200:
            _log("attachment-download-failed peer=%s file=%s http=%s"
                 % (name, os.path.basename(save_to), code))
            _emit({"event": "attachment-saved", "ok": False, "path": save_to,
                   "mid": mid, "fileId": file_id, "error": "sender returned HTTP %s" % code})
            return False
        os.makedirs(os.path.dirname(save_to) or ".", exist_ok=True)
        h = hashlib.sha256()
        got = 0
        total = clen
        with open(tmp, "wb") as f:
            # Flush any bytes already read with the headers, then stream the rest.
            if prefix:
                f.write(prefix)
                h.update(prefix)
                got += len(prefix)
                _emit({"event": "attachment-progress", "fileId": file_id, "mid": mid,
                       "bytes": got, "total": total})
            if clen > 0:
                while got < clen:
                    chunk = s.recv(min(65536, clen - got))
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    _emit({"event": "attachment-progress", "fileId": file_id, "mid": mid,
                           "bytes": got, "total": total})
            else:
                # No Content-Length: read until the server closes the connection.
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    _emit({"event": "attachment-progress", "fileId": file_id, "mid": mid,
                           "bytes": got, "total": total})
        digest = h.hexdigest()
        if expected_sha256 and digest != expected_sha256:
            _remove_file(tmp)
            _log("attachment-checksum-mismatch file=%s expected=%s got=%s"
                 % (os.path.basename(save_to), expected_sha256, digest))
            _emit({"event": "attachment-saved", "ok": False, "path": save_to,
                   "mid": mid, "fileId": file_id, "error": "checksum mismatch"})
            return False
        os.replace(tmp, save_to)  # atomic: never a truncated final file
        _emit({"event": "attachment-saved", "ok": True, "path": save_to,
               "mid": mid, "fileId": file_id})
        return True
    except Exception as e:
        _remove_file(tmp)
        _log("attachment-download-failed peer=%s file=%s err=%s"
             % (name, os.path.basename(save_to), e))
        _emit({"event": "attachment-saved", "ok": False, "path": save_to,
               "mid": mid, "fileId": file_id, "error": str(e)})
        return False
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


def _tls_connect_host(addr: str, port: int, name: str, expected_fingerprint: str = ""):
    """Open a TLS connection to addr:port and verify the peer's cert
    fingerprint. Self-signed certs are expected, so we do NOT verify against a
    CA — instead we check the peer's cert fingerprint matches the identity we
    friended/expect (LocalSend-style trust model: fingerprint = identity).
    Returns the wrapped socket, or None on failure/mismatch."""
    try:
        raw = socket.create_connection((addr, port), timeout=5)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # we do fingerprint verification ourselves
        s = ctx.wrap_socket(raw)
        if expected_fingerprint:
            der = s.getpeercert(binary_form=True)
            if not der:
                s.close()
                _log("tls-connect-failed peer=%s addr=%s:%s err=no-cert" % (name, addr, port))
                return None
            actual = hashlib.sha256(der).hexdigest()
            if actual != expected_fingerprint:
                s.close()
                _log("tls-connect-failed peer=%s addr=%s:%s err=fingerprint-mismatch" % (name, addr, port))
                return None
        return s
    except OSError as e:
        _log("tls-connect-failed peer=%s addr=%s:%s err=%s" % (name, addr, port, e))
        return None
    except Exception as e:
        _log("tls-connect-failed peer=%s addr=%s:%s err=%s" % (name, addr, port, e))
        return None


def _tls_connect(peer: dict, expected_fingerprint: str = ""):
    """TLS-connect to a peer's message port, verifying their cert fingerprint."""
    return _tls_connect_host(
        peer.get("address", "?"), peer.get("port", "?"), str(peer.get("name") or "?"), expected_fingerprint)


def send_message(peer_id: str, text: str, friend_request: bool = False, attachment: dict = None) -> bool:
    peer = find_peer(peer_id)
    name = (peer or {}).get("name") or friendly_name(peer_id)
    msg = {
        "t": "msg",
        "from": host_id(),
        "fromName": display_name(),
        "text": text,
        "ts": int(time.time() * 1000),
        "to": peer_id,
        "outgoing": True,
        "mid": secrets.token_hex(8),
    }
    if attachment:
        msg["attachment"] = attachment  # {name,size,mime,fileId,sha256}
    # Never re-propose friendship to someone who already accepted. The UI only
    # sets friend_request for non-friends, but if its state is momentarily
    # stale this guard keeps us from re-holding a message to a confirmed friend
    # as a request (the "keeps sending friend requests to friends" bug).
    if friend_request and not is_friend(peer_id):
        msg["friendRequest"] = True
        # Record the peer as a pending-request friend on our side, so they
        # become trusted (can reply/accept) and show as a friend request.
        if not is_pending(peer_id) and not is_friend(peer_id):
            add_friend(peer_id, (peer or {}).get("address", ""), name, confirmed=False)
        # A friend request is a handshake: register the held message NOW,
        # before sending, so a fast peer accept can always find and reveal it.
        msg["held"] = True
        with _pending_lock:
            _pending_sent.setdefault(peer_id, []).append(msg)
    # Write to the peer's persistent socket (or hold until it reconnects).
    delivered = _write(peer_id, msg)
    if friend_request:
        _emit({"event": "friend-request", "outgoing": True, "to": peer_id, "toName": name,
               "text": text, "ts": msg["ts"], "mid": msg["mid"]})
        _diag("outbound-friend-request", to=peer_id[:12], name=name, text=text[:40])
    else:
        append_history(msg)
        _emit({"event": "message", "message": msg})
        _diag("outbound-message-sent", to=peer_id[:12], name=name, text=text[:40])
        if not delivered:
            _emit({"event": "error", "message": "%s is offline; message held until the connection returns" % name})
    return True


def send_control(peer_id: str, ctype: str, mid: str = "") -> bool:
    """Send a friend accept/reject / typing / read control message to a peer
    over its persistent socket (no per-message dial)."""
    payload = {
        "t": ctype,  # friendAccept / friendReject / typing / typingStopped / read
        "from": host_id(),
        "fromName": display_name(),
    }
    if mid:
        payload["mid"] = mid
    delivered = _write(peer_id, payload)
    if not delivered and ctype != "friendAccept":
        # Transient control (typing/read/reject) is dropped, not queued.
        _diag("send-control-dropped", peer=peer_id[:12], ctype=ctype)
    return delivered


def _notify_accept(peer_id: str) -> bool:
    """Send the friendAccept notification over the peer's persistent socket.
    If no socket is up yet, the message is held and flushed on reconnect — no
    reverse dial, no polling."""
    return send_control(peer_id, "friendAccept")


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
        ok = bool(token) and token == CONFIG.get("token")
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
            if not api_full_access():
                return self._send_json(403, {"ok": False, "error": "read access disabled"})
            return self._send_json(200, {"ok": True, "peers": peer_snapshot()})
        if parsed.path == "/messages":
            if not api_full_access():
                return self._send_json(403, {"ok": False, "error": "read access disabled"})
            return self._send_json(200, {"ok": True, "messages": history_snapshot()})
        if parsed.path == "/attachment":
            # Serving a registered file to a confirmed friend (token in the
            # query) is peer-to-peer file transfer, not script read-access — so
            # it must NOT be gated behind apiFullAccess. Auth is enforced above.
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
            srv = http.server.ThreadingHTTPServer((http_bind(), http_port()), _ApiHandler)
            srv.socket = ensure_tls().wrap_socket(srv.socket, server_side=True)
            _http_server = srv
            _http_server_thread = threading.Thread(target=srv.serve_forever, daemon=True)
            _http_server_thread.start()
            _emit({"event": "http", "enabled": True, "port": http_port(), "bind": http_bind()})
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
        elif kind == "clearAllChats":
            removed = clear_all_history()
            _emit({"event": "chat-cleared", "peer": "", "removed": removed})
        elif kind == "deleteMessage":
            ok = delete_message(str(cmd.get("mid", "")))
            _emit({"event": "message-deleted", "mid": str(cmd.get("mid", "")), "ok": ok})
        elif kind == "editMessage":
            ok = edit_message(str(cmd.get("mid", "")), str(cmd.get("text", "")))
            _emit({"event": "message-edited", "mid": str(cmd.get("mid", "")), "text": str(cmd.get("text", "")), "ok": ok})
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
            name = _safe_filename(str(cmd.get("name", "download")))
            mid = str(cmd.get("mid", ""))
            sha256 = str(cmd.get("sha256", ""))
            # The sender's `from` id is their cert fingerprint — the same
            # identity the message transport verifies against. Use it to pin
            # the download's TLS cert too (closes the unauthenticated gap).
            fingerprint = str(cmd.get("from", ""))
            save_to = os.path.join(CONFIG.get("downloadDir", os.path.expanduser("~/Downloads")), name)
            # Run the transfer off the stdin loop so a large file never blocks
            # message processing / presence. Progress + completion events come
            # back from the download thread.
            threading.Thread(
                target=_download_attachment,
                args=(peer, file_id, save_to, sha256, mid, fingerprint),
                daemon=True,
            ).start()
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
        elif kind == "setHttpBind":
            # Bind address for the HTTP API: "127.0.0.1" (default, loopback-only)
            # or "0.0.0.0" (LAN exposure). Applies on the next enable/restart.
            bind = str(cmd.get("bind") or "127.0.0.1")
            CONFIG["httpBind"] = bind
            _save_config()
            _emit({"event": "http-bind", "bind": bind})
        elif kind == "setApiFullAccess":
            CONFIG["apiFullAccess"] = bool(cmd.get("enabled"))
            _save_config()
            _emit({"event": "api-full-access", "enabled": bool(cmd.get("enabled"))})
        elif kind == "setPanelSize":
            size = str(cmd.get("size", "medium"))
            if size in ("small", "medium", "large", "xl", "full"):
                CONFIG["panelSize"] = size
                _save_config()
                _emit({"event": "panel-size", "size": size})
        elif kind == "setCustomSize":
            try:
                w = max(0, int(cmd.get("w", 0)))
                h = max(0, int(cmd.get("h", 0)))
            except (TypeError, ValueError):
                w, h = 0, 0
            CONFIG["customW"] = w
            CONFIG["customH"] = h
            _save_config()
            _emit({"event": "custom-size", "w": w, "h": h})
        elif kind == "setStatus":
            s = str(cmd.get("status", "available"))
            if s in STATUSES:
                CONFIG["status"] = s
                _save_config()
                broadcast_now()  # peers see the new status right away
                _emit({"event": "status", "status": s})
        elif kind == "setSoundEnabled":
            CONFIG["soundEnabled"] = bool(cmd.get("enabled"))
            _save_config()
            _emit({"event": "sound-enabled", "enabled": bool(cmd.get("enabled"))})
        elif kind == "setTypingEnabled":
            CONFIG["typingEnabled"] = bool(cmd.get("enabled"))
            _save_config()
            _emit({"event": "typing-enabled", "enabled": bool(cmd.get("enabled"))})
        elif kind == "setShowTyping":
            CONFIG["showTyping"] = bool(cmd.get("enabled"))
            _save_config()
            _emit({"event": "show-typing", "enabled": bool(cmd.get("enabled"))})
        elif kind == "setReadReceiptsEnabled":
            CONFIG["readReceiptsEnabled"] = bool(cmd.get("enabled"))
            _save_config()
            _emit({"event": "read-receipts-enabled", "enabled": bool(cmd.get("enabled"))})
        elif kind == "setShowReadReceipts":
            CONFIG["showReadReceipts"] = bool(cmd.get("enabled"))
            _save_config()
            _emit({"event": "show-read-receipts", "enabled": bool(cmd.get("enabled"))})
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
            else:
                # Going offline: drop all persistent sockets and stop dialing
                # (conn_loop also gates on is_online).
                _drop_all_conns()
        elif kind == "acceptFriend":
            pid = str(cmd.get("id", ""))
            peer = find_peer(pid)
            pname = peer["name"] if peer else friendly_name(pid)
            # Accept is LOCAL and unconditional: confirm the friend and reveal
            # the held messages right away. The notify-back to the sender is
            # just a courtesy — if they're momentarily unreachable (vanishing
            # peer), retry in the background until it lands so the handshake
            # completes on both sides.
            add_friend(pid, peer["address"] if peer else "", pname, confirmed=True)
            _emit({"event": "friend-accepted", "id": pid, "name": pname})
            with _pending_lock:
                held = _pending_first.pop(pid, [])
            _diag("accepted-friend-request", peer=pid[:12], name=pname, revealed=len(held))
            _reveal(held)
            if not _notify_accept(pid):
                _diag("accept-notify-failed", peer=pid[:12], name=pname, retrying=True)
        elif kind == "rejectFriend":
            pid = str(cmd.get("id", ""))
            # Rejecting = declining the relationship: send the reject notice and
            # REMOVE the peer from our friend list (no lingering pending record),
            # which emits a friends event so the UI reconciles the notification
            # banner and drops the request.
            send_control(pid, "friendReject")
            with _pending_lock:
                _pending_first.pop(pid, None)
            unfriend(pid)
            _emit({"event": "friend-rejected", "id": pid})
            _diag("rejected-friend-request", peer=pid[:12])
        elif kind == "unfriend":
            pid = str(cmd.get("id", ""))
            if unfriend(pid):
                _emit({"event": "friend-removed", "id": pid})
        elif kind == "typing":
            send_control(str(cmd.get("to", "")), "typing")
        elif kind == "typingStopped":
            send_control(str(cmd.get("to", "")), "typingStopped")
        elif kind == "readReceipt":
            send_control(str(cmd.get("to", "")), "read", str(cmd.get("mid", "")))


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
        "httpBind": http_bind(),
        "online": is_online(),
        "friends": friends_list(),
        "downloadDir": CONFIG.get("downloadDir", os.path.join(os.path.expanduser("~"), "Downloads")),
        "sendDelay": CONFIG.get("sendDelay", 0),
        "apiFullAccess": api_full_access(),
        "panelSize": panel_size(),
        "customW": int(CONFIG.get("customW", 0) or 0),
        "customH": int(CONFIG.get("customH", 0) or 0),
        "status": status(),
        "soundEnabled": sound_enabled(),
        "typingEnabled": typing_enabled(),
        "showTyping": show_typing(),
        "readReceiptsEnabled": read_receipts_enabled(),
        "showReadReceipts": show_read_receipts(),
        "logPath": _LOG_PATH,
    }


def main() -> None:
    load_config()
    load_history()
    _emit(_ready_event())
    if http_enabled():
        _start_http()
    threading.Thread(target=tcp_loop, daemon=True).start()
    threading.Thread(target=udp_loop, daemon=True).start()
    threading.Thread(target=conn_loop, daemon=True).start()
    stdin_loop()  # blocks until the shell closes stdin; also supervises the process lifetime


if __name__ == "__main__":
    main()
