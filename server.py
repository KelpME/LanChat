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

import base64  # noqa: F401  (used by extracted modules via server.<name>)
import hashlib
import http.server  # noqa: F401  (BaseHTTPRequestHandler base for http_api)
import json
import os
import random
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile  # noqa: F401  (re-imported locally in atomic_write)
import threading
import time
import urllib.parse  # noqa: F401  (used by http_api via server.<name>)

import attachments
import history
import http_api

# Subsystem modules extracted from this file (Commit 2). They are imported
# here at module top level; each of them imports server only INSIDE function
# bodies (deferred, late-bound), so there is no import-time cycle.
import identity
import rooms

# attachments.py — attachment registry + socket file transfer
from attachments import (  # noqa: F401
    _DL_TTL_S,
    ATT_CHUNK_RAW,
    _dl,
    _dl_begin,
    _dl_chunk,
    _dl_finish,
    _file_sha256,
    _finalize_download,
    _remove_file,
    _safe_filename,
    _serve_attachment,
    get_attachment,
    register_attachment,
)

# history.py — message history + at-rest crypto (constants re-exported: tests
# and the daemon log path read them as server module attributes)
from history import (  # noqa: F401
    HISTORY_KEY,
    HISTORY_LIMIT,
    HISTORY_MAGIC,
    HISTORY_PATH,
    STATE_DIR,
    _has_mid,
    _hist_crypto_ok,
    _hist_key,
    _history_decrypt,
    _history_encrypt,
    _history_write_bytes,
    _save_history_locked,
    append_history,
    clear_all_history,
    clear_history_for_peer,
    delete_message,
    edit_message,
    history_for_peer,
    history_for_room,
    history_snapshot,
    load_history,
)

# http_api.py — optional token-authenticated HTTP API
from http_api import _ApiHandler, _start_http, _stop_http  # noqa: F401

# Re-export shims: the moved functions keep working BY SYMBOL for internal
# callers (handle_command, _handle_incoming, main, ...) and for tests that
# read them as server module attributes. identity/history/attachments/
# http_api operate on the SAME State instance (see the .init(STATE) calls
# below), so state stays shared and live.
# identity.py — TLS cert + identity-proof pair (re-exports: the test contract
# and internal callers resolve these as server.<name>; noqa = intentional)
from identity import (  # noqa: F401
    CERT_DIR,
    CERT_KEY,
    CERT_PEM,
    _cert_fingerprint_of_pem,
    _gen_cert,
    _load_priv_key,
    _our_cert_pem,
    _sign,
    _verify,
    cert_fingerprint,
    ensure_tls,
    host_id,
)
from naming import _SKATE_TRICKS, _TRICK_MODIFIERS, friendly_name  # noqa: E402

# --------------------------------------------------------------------------
# Paths & config
# --------------------------------------------------------------------------


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "omarchy")
CONFIG_PATH = os.path.join(CONFIG_DIR, "lanchat.json")

DEFAULT_PORT = 4812
DEFAULT_HTTP_PORT = 4814
PEER_TIMEOUT_S = 6.0       # drop a peer after this long without a hello
BROADCAST_INTERVAL_S = 3.0

# Transport hardening (1.2.2): bound an individual connection's buffered input
# and the number of concurrent inbound connections, so a malicious/flooding LAN
# peer can't exhaust memory or threads.
MAX_FRAME_BUF = 512 * 1024   # a peer must send a newline within this many bytes
MAX_INBOUND_CONNS = 64       # cap concurrent inbound reader threads

# Version of the plugin/daemon. This is the SINGLE source of truth; manifest.json
# is stamped from it by `make bump-version` (scripts/bump_version.py). Never edit
# the version by hand in either file — bump with `make bump-version` (or
# `make bump-version NEW=x.y.z`) so the two can't drift. `make check` verifies
# they still match.
# Bump when behaviour changes; breaking changes should bump the major number.
# The reported version derives from the checked-out git commit (short hash) so
# both machines can verify they're running the same code; falls back to the
# manifest version when git isn't available (e.g. a bare copy).
#
# 1.3.0 — public-network safety: visibility model. New `visibility` setting
#   ("open" discoverable / "private" invisible by default) and `acceptRequests`
#   toggle. In private mode we don't broadcast/scan or respond to hello probes,
#   and friend requests carry the requester's verified cert fingerprint for the
#   UI to confirm before accepting.
# 1.2.3 — at-rest encryption: message history is now AES-256-GCM encrypted with
#   a dedicated 0600 key, so history.json isn't readable as plaintext. Protects
#   the file in isolation (backup/copy/sync); not against full device compromise.
# 1.2.2 — transport hardening: bound per-connection buffered input (512KB) and
#   concurrent inbound connections (64) so a flooding LAN peer can't exhaust
#   memory or threads.
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
# 1.5.3 — firewall reachability indicator + open/close controls. The daemon
#   detects whether port 4812 (udp+tcp) is open inbound and reports it in the
#   ready event; Settings shows a status dot + Open/Close buttons. The install
#   helper now opens the port at install time via the scoped sudoers rule,
#   piggybacking the admin prompt already used for cryptography.
# 1.5.4 — single firewall toggle button (replaces Open/Close pair): it opens
#   the port when closed and closes it when open, matching the current state.
# 1.5.5 — persistent firewall warning in the peers-online bar (daemon running
#   but port 4812 blocked); refreshes firewall state when the panel opens.
# 1.5.6 — word-wrap the peers-online + firewall alert lines.
# 1.5.7 — firewall toggle now uses polkit (pkexec): each open/close prompts for
#   a password and creates NO permanent sudoers rule; the LAN subnet is baked
#   into the ufw rule (never the internet). Status read is best-effort without
#   admin (reports Unknown when it can't read rules, so it never nags).
# 1.5.8 — reorganize Settings into labeled sections (Identity, Presence, Chat,
#   Appearance, Agents, Reachability, Developer); right-justify the Status and
#   Panel-size option buttons; improve My ID text contrast.
# 1.5.9 — brighten Settings section headers (Color.popups.text instead of
#   muted) so the group titles are no longer dark.
# 1.5.10 — center Settings section titles, use the accent color (same as My
#   ID), and underline them.
# 1.5.11 — right-justify the My ID fingerprint value (aligns against the copy
#   button, matching the other right-aligned controls).
# 1.5.12 — "Save to" row now shows the full path (with parent folders, e.g.
#   /home/tmo/Downloads) instead of only the folder name; middle-elide when long.
# 1.5.13 — "Save to" path keeps the folder + one parent visible and truncates
#   the leading part (…/parent/folder) so the tail stays readable.
# 1.5.14 — bar widget: add a pending-friend-request badge (lower-right,
#   accent-colored); shift both badges right so they clear the status icon
#   whose fill color reflects presence.
# 1.5.15 — firewall prompt: run pkexec on a tiny helper script instead of an
#   inline bash -c ufw string, so the polkit authorization message is short and
#   readable instead of truncated.
# 1.5.16 — systemd: add lanchat.path + lanchat-restart.service so the daemon
#   restarts automatically when server.py/scripts change (e.g. after an update),
#   so a running daemon never keeps reporting a stale version.
#  1.5.19 — thread header: per-chat actions only show when they can actually
#   act (Unfriend only for a confirmed friend; Clear chat only when the peer has
#   history). Update button: when a badge shows, clicking now APPLIES the update
#   (git fetch origin main + fast-forward, the safe path) — refusing if local
#   edits block it, and surfacing a "Discard & update" clean-install button; auto-
#   restarts the daemon (via lanchat.path) and the shell so the new UI loads.
#  1.5.20 — docs: the thread-header update button now applies the update on click
#   (fetch + fast-forward, daemon/shell reload) rather than only checking; docs
#   updated to match.

VERSION = "1.5.33"
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

class State:
    """Owns the daemon's mutable singletons.

    State now OWNS the shared state as real instance fields (no
    module-global delegation): storage lives on the instance, and
    module-level aliases are late-bound via PEP 562 module __getattr__
    so rebinding (tests, load_config) keeps working exactly as before.
    Future subsystems attach coherent state here instead of
    free-floating globals.
    """

    def __init__(self):
        # shared cross-cutting state
        self.config = {}
        self.peers = {}
        self.conns = {}
        self.pending_sent = {}
        self.pending_first = {}
        self.udp_sock = None
        self.stdout = sys.stdout
        self.socket_clients = set()
        self.out_lock = threading.Lock()
        # state moving to its own module in Commit 2 (kept here for now)
        self.history = []
        self.hist_crypto = None
        self.attachments = {}
        self.rooms = {}            # roomId -> room (authoritative on the owner's daemon)
        self.rooms_cache = {}      # roomId -> last-known authoritative room (members)
        self.rooms_lock = threading.Lock()
        self.priv_key = None
        # locks (moved off module globals — this is the race-hazard fix)
        self.socket_clients_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self.priv_key_lock = threading.Lock()
        self.hist_lock = threading.Lock()
        self.peers_lock = threading.Lock()
        self.conns_lock = threading.Lock()
        self.inbound_conns_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.att_lock = threading.Lock()
        self.dl_lock = threading.Lock()
STATE = State()


def __getattr__(name):
    """PEP 562: late-bound read aliases to the live STATE fields.

    Built fresh on every lookup so aliases track STATE even after
    load_config rebinds STATE.config. Item-assignment through an alias
    (srv.CONFIG["k"] = v, _s._peers[id] = ...) reaches the live STATE
    objects.
    """
    _ALIASES = {"CONFIG": STATE.config, "_peers": STATE.peers, "_stdout": STATE.stdout,
                "_udp_sock": STATE.udp_sock, "_conns": STATE.conns, "_history": STATE.history,
                "_pending_sent": STATE.pending_sent, "_pending_first": STATE.pending_first,
                "_socket_clients": STATE.socket_clients, "_attachments": STATE.attachments,
                "_hist_crypto": STATE.hist_crypto, "_priv_key": STATE.priv_key}
    if name in _ALIASES:
        return _ALIASES[name]
    raise AttributeError(f"module 'server' has no attribute {name!r}")


# Bind the extracted subsystem modules to the shared State. None of them call
# back into server at init time (their `import server` is deferred into
# function bodies), so this is safe right after STATE exists.
identity.init(STATE)
history.init(STATE)
attachments.init(STATE)
rooms.init(STATE)
http_api.init(STATE)




# Unix-socket control channel (systemd mode). When the daemon runs under
# systemd there is no stdin/stdout pipe to the shell; the QML talks to a
# bridge process that connects here. Events are broadcast to every connected
# client; if none are connected we fall back to stdout (legacy stdin mode,
# used by the test harness).



NAME_MAX = 32  # maximum length of a display name (generated or custom)


def atomic_write(path: str, text: str) -> None:
    """Write a file atomically (write temp + rename) so a crash mid-write
    never leaves a truncated/corrupt file. Caller is responsible for making
    the parent directory exist."""
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
    """Write one newline-delimited JSON event to every connected control client.

    In socket mode (systemd) this broadcasts to all connected QML bridges. If
    no socket clients are connected we fall back to stdout — the legacy
    stdin/stdout mode used by the test harness. Thread-safe.
    """
    line = json.dumps(event, separators=(",", ":")) + "\n"
    with STATE.socket_clients_lock:
        clients = list(STATE.socket_clients)
    if clients:
        for f in clients:
            try:
                f.write(line)
                f.flush()
            except (BrokenPipeError, OSError):
                _drop_socket_client(f)
        return
    with STATE.out_lock:
        try:
            STATE.stdout.write(line)
            STATE.stdout.flush()
        except (BrokenPipeError, OSError):
            # The shell went away; nothing more we can do.
            os._exit(0)


def _drop_socket_client(f) -> None:
    with STATE.socket_clients_lock:
        STATE.socket_clients.discard(f)
    try:
        f.close()
    except OSError:
        pass


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------
# A timestamped diagnostic log (in addition to the stdout event stream) so a
# failing handshake / vanishing peer / one-way delivery can be traced after
# the fact. Written best-effort; never raises.

_LOG_PATH = os.path.join(STATE_DIR, "daemon.log")



def _log(msg: str) -> None:
    """Append one timestamped line to the diagnostic log (thread-safe, best-effort)."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with STATE.log_lock:
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
        atomic_write(CONFIG_PATH, json.dumps(STATE.config, indent=2) + "\n")
    except OSError:
        pass


def load_config() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            STATE.config = json.load(f)
    else:
        STATE.config = {
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
    STATE.config.setdefault("httpEnabled", False)
    STATE.config.setdefault("httpPort", DEFAULT_HTTP_PORT)
    # API bind address. Loopback-only by default so the token-authenticated API
    # isn't exposed to the LAN; set to "0.0.0.0" to allow remote agents
    # (e.g. other machines holding the token) to reach it.
    STATE.config.setdefault("httpBind", "127.0.0.1")
    # Discovery / friend-request model (1.3).
    #   visibility: "open"   — broadcast + scan, anyone can discover and
    #                request you (trusted-LAN behavior).
    #                "private" — invisible on discovery (no broadcast/scan);
    #                you connect by adding fingerprints directly.
    #   acceptRequests: whether inbound friend requests are accepted (and shown
    #                with the requester's verified identity) or rejected.
    STATE.config.setdefault("visibility", "private")
    STATE.config.setdefault("acceptRequests", True)
    # Default self-name: a deterministic friendly {modifier}{trick} name. If a
    # config was written without a displayName (or it's null/empty), fill in a
    # skateboard name — NEVER fall back to the bare hostname (a machine name is
    # not a friendly display name). Persisted so it sticks.
    if not STATE.config.get("displayName"):
        STATE.config["displayName"] = friendly_name(socket.gethostname())
        _save_config()
    # Full API access to chat data (read history/peers) vs send-only.
    # When False, the agent can send messages to friends but cannot read
    # history, list peers, or download attachments.
    STATE.config.setdefault("apiFullAccess", False)
    # Panel size: "small" | "medium" | "large" | "xl" | "full".
    STATE.config.setdefault("panelSize", "medium")
    # Manual pixel override for panel size (0 = follow the preset).
    STATE.config.setdefault("customW", 0)
    STATE.config.setdefault("customH", 0)
    # Left peer-column width set by the draggable divider (0 = UI default).
    STATE.config.setdefault("peerColW", 0)
    # User status: "available" | "dnd" | "away" | "brb". Broadcast to friends.
    STATE.config.setdefault("status", "available")
    # Play a sound on incoming messages.
    STATE.config.setdefault("soundEnabled", True)
    # Presence indicators (each direction independently toggleable).
    STATE.config.setdefault("typingEnabled", True)        # send my typing to peers
    STATE.config.setdefault("showTyping", True)           # show peers' typing in my UI
    STATE.config.setdefault("readReceiptsEnabled", True)  # send my read receipts to peers
    STATE.config.setdefault("showReadReceipts", True)     # show read receipts in my UI
    # Online presence + friend list (persisted).
    STATE.config.setdefault("online", True)
    STATE.config.setdefault("friends", [])
    # Attachment download folder (defaults to the system Downloads dir).
    STATE.config.setdefault("downloadDir", os.path.join(os.path.expanduser("~"), "Downloads"))
    # Send-delay/undo window in seconds (0 = disabled).
    STATE.config.setdefault("sendDelay", 0)
    # Stable, unique per-install id (independent of hostname so two machines
    # with the same hostname still distinguish each other).
    STATE.config.setdefault("id", secrets.token_hex(6))
    _save_config()

    token = str(STATE.config.get("token", "")).strip()
    if len(token) < 8:
        _emit({"event": "error", "message": "lanchat token must be at least 8 chars. Edit ~/.config/omarchy/lanchat.json"})
    STATE.config["token"] = token


# host_id/_gen_cert/ensure_tls/cert_fingerprint and the identity-proof pair
# (_sign/_verify/_cert_fingerprint_of_pem/_our_cert_pem/_load_priv_key) moved
# to identity.py (Commit 2) and are re-exported from it below.

def display_name() -> str:
    # Never return a bare hostname as the display name — always a friendly
    # skateboard name (or the user's custom name). load_config ensures a
    # friendly default is persisted, so this fallback is only a safety net.
    name = STATE.config.get("displayName")
    if name:
        return str(name)
    return friendly_name(socket.gethostname())


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





# The identity-proof helpers (_our_cert_pem/_load_priv_key/_sign/_verify/
# _cert_fingerprint_of_pem) moved to identity.py (Commit 2) and are
# re-exported from it below.


def port() -> int:
    try:
        return int(STATE.config.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        return DEFAULT_PORT


def http_enabled() -> bool:
    return bool(STATE.config.get("httpEnabled", False))


def api_full_access() -> bool:
    """Whether the API can read chat data (history/peers/attachments) or is send-only."""
    return bool(STATE.config.get("apiFullAccess", False))


def panel_size() -> str:
    size = str(STATE.config.get("panelSize", "medium"))
    return size if size in ("small", "medium", "large", "xl", "full") else "medium"


STATUSES = ("available", "dnd", "away", "brb")


def status() -> str:
    s = str(STATE.config.get("status", "available"))
    return s if s in STATUSES else "available"


def sound_enabled() -> bool:
    return bool(STATE.config.get("soundEnabled", True))


def typing_enabled() -> bool:
    return bool(STATE.config.get("typingEnabled", True))


def show_typing() -> bool:
    return bool(STATE.config.get("showTyping", True))


def read_receipts_enabled() -> bool:
    return bool(STATE.config.get("readReceiptsEnabled", True))


def show_read_receipts() -> bool:
    return bool(STATE.config.get("showReadReceipts", True))


def http_port() -> int:
    try:
        return int(STATE.config.get("httpPort", DEFAULT_HTTP_PORT))
    except (TypeError, ValueError):
        return DEFAULT_HTTP_PORT


def http_bind() -> str:
    """Loopback by default; "0.0.0.0" opts into LAN exposure."""
    return str(STATE.config.get("httpBind") or "127.0.0.1")


def visibility() -> str:
    """"open" (discoverable) or "private" (invisible)."""
    return str(STATE.config.get("visibility") or "private")


def accept_requests() -> bool:
    """Whether inbound friend requests are accepted."""
    return bool(STATE.config.get("acceptRequests", True))


# --------------------------------------------------------------------------
# Message history (per-machine persistence) — moved to history.py (Commit 2);
# all symbols re-exported at the top of this file.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Peers (discovered via UDP)
# --------------------------------------------------------------------------





def peer_snapshot() -> list:
    with STATE.peers_lock:
        return sorted(
            STATE.peers.values(),
            key=lambda p: p["name"].lower(),
        )


def upsert_peer(pid: str, name: str, address: str, pport: int, phttp: object = None, pstatus: str = "available", pversion: str = "") -> None:
    now = time.time()
    changed = False
    with STATE.peers_lock:
        existed = pid in STATE.peers
        prev = STATE.peers.get(pid)
        prev_version = prev.get("version", "") if existed else ""
        newrec = {
            "id": pid,
            "name": name,
            "address": address,
            "port": pport,
            "httpPort": phttp,
            "status": pstatus if pstatus in STATUSES else "available",
            "lastSeen": int(now * 1000),
            "version": pversion or prev_version,
        }
        # Emit only when something meaningful changed (a new peer, or a change
        # to name/address/port/status/version). A pure discovery heartbeat
        # (peers re-broadcast every ~3s) only bumps lastSeen and must NOT emit —
        # otherwise the UI rebuilds the peer list on every hello, which
        # re-renders the panel and makes hovered controls flicker.
        if not existed or any(
            newrec[k] != prev.get(k)
            for k in ("name", "address", "port", "httpPort", "status", "version")
        ):
            changed = True
        STATE.peers[pid] = newrec
    if changed:
        if not existed:
            _diag("peer-discovered", id=pid[:12], name=name, address=address, port=pport, version=pversion)
        _emit({"event": "peer", "peer": STATE.peers[pid]})
    # If a confirmed friend broadcasts with a real name, sync it into their
    # friend record so an "Unknown" (added-by-fingerprint) friend gets their
    # real display name once discovery connects them.
    if name and name != "Unknown":
        _sync_friend_name(pid, name)


def _sync_friend_name(pid: str, name: str) -> None:
    """Update a confirmed friend's stored name if we now know a real one."""
    friends = STATE.config.get("friends", [])
    changed = False
    for f in friends:
        if f.get("id") == pid and (not f.get("name") or f.get("name") == "Unknown"):
            f["name"] = name
            changed = True
            break
    if changed:
        STATE.config["friends"] = friends
        _save_config()
        _emit({"event": "friends", "friends": friends_list()})


def expire_peers() -> None:
    now = time.time()
    gone = []
    with STATE.peers_lock:
        for pid in list(STATE.peers.keys()):
            age = now - STATE.peers[pid]["lastSeen"] / 1000.0
            if age > PEER_TIMEOUT_S:
                gone.append((pid, age))
                del STATE.peers[pid]
    for pid, age in gone:
        _drop_conn(pid)  # close any persistent socket to the expired peer
        _diag("peer-expired", id=pid[:12], age_s=round(age, 1), timeout_s=PEER_TIMEOUT_S)
        _emit({"event": "peer-gone", "id": pid})


def find_peer(pid: str):
    with STATE.peers_lock:
        return STATE.peers.get(pid)


# --------------------------------------------------------------------------
# Friends (Path A handshake) + online presence
# --------------------------------------------------------------------------

def friends_list() -> list:
    return list(STATE.config.get("friends", []))


def _friends_lock():
    return threading.Lock()


def is_friend(pid: str, address: str = "") -> bool:
    for f in STATE.config.get("friends", []):
        if f.get("id") == pid and f.get("confirmed"):
            return True
    return False


def is_pending(pid: str) -> bool:
    """True if we've sent this peer a friend request but they haven't accepted."""
    for f in STATE.config.get("friends", []):
        if f.get("id") == pid and not f.get("confirmed"):
            return True
    return False


def is_online() -> bool:
    return bool(STATE.config.get("online", True))


def add_friend(pid: str, address: str, name: str, confirmed: bool) -> None:
    friends = STATE.config.get("friends", [])
    for f in friends:
        if f.get("id") == pid:
            f["address"] = address
            f["name"] = name
            f["confirmed"] = confirmed
            break
    else:
        friends.append({"id": pid, "address": address, "name": name, "confirmed": confirmed})
    STATE.config["friends"] = friends
    _save_config()
    _emit({"event": "friends", "friends": friends_list()})


def unfriend(pid: str) -> bool:
    """Remove a peer from friends (unfriend). Returns True if they were removed."""
    friends = STATE.config.get("friends", [])
    before = len(friends)
    friends = [f for f in friends if f.get("id") != pid]
    STATE.config["friends"] = friends
    _save_config()
    _emit({"event": "friends", "friends": friends_list()})
    return len(friends) != before


def _do_unfriend(pid: str) -> bool:
    """Remove a peer and clear their chat history + surface the UI events.

    Shared by the local unfriend command and the remote (notified) unfriend
    path so both sides reconcile identically: the friend drops, the history
    clears, and the UI gets friend-removed + chat-cleared.
    """
    removed = unfriend(pid)
    history_cleared = clear_history_for_peer(pid)
    _emit({"event": "friend-removed", "id": pid})
    _emit({"event": "chat-cleared", "peer": pid, "removed": history_cleared})
    return removed


def _unfriend_if_unconfirmed(pid: str) -> bool:
    """Remove a peer ONLY if they are a pending (unconfirmed) request, never a
    confirmed friend. Returns True if a record was removed."""
    friends = STATE.config.get("friends", [])
    for f in friends:
        if f.get("id") == pid:
            if f.get("confirmed"):
                return False
            break
    else:
        return False
    return unfriend(pid)


def is_trusted(pid: str, address: str = "") -> bool:
    """Accept traffic only from confirmed friends or pending-request peers.

    Trust is keyed on the peer's identity (cert fingerprint id), not the IP
    address — so a forged/unknown id from the same IP as a friend is NOT
    trusted.
    """
    if not is_online():
        return False
    for f in STATE.config.get("friends", []):
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
        # Process this packet inside a broad exception guard: a malformed packet
        # or a missing-dependency error in a handler (e.g. a cryptography import)
        # must NEVER kill the UDP listener thread — that silently stops all
        # discovery/friend traffic (the .51 bug: peers visible but friend
        # requests never surface, no error shown). One bad packet is skipped,
        # not fatal to the whole listener.
        try:
            # No token on discovery — any machine on the LAN can be seen, and the
            # friend handshake is the gate that decides who can actually message us.
            t = pkt.get("t")
            if t == "hello":
                pid = str(pkt.get("id", addr[0]))
                # Skip ourselves — our own broadcast/scan echoes back on loopback.
                if pid == host_id():
                    continue
                # (1.3) Visibility controls whether WE broadcast (are discoverable),
                # NOT whether we can see others. A private device still listens and
                # records devices that broadcast, so it can see open peers on the
                # network — it just doesn't announce itself or reply, so those peers
                # don't see it back. Confirmed friends are always accepted too.
                hidden = visibility() != "open"
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
                # one-way (they see us, we don't see them). In private mode we do
                # NOT pong, so we stay invisible to the devices we discover.
                if not hidden:
                    _udp_send(sock, {"t": "pong"}, target=addr[0])
            elif t == "friend-request":
                # A stranger bootstraps friendship over UDP (no TCP connection yet —
                # the TCP path is chicken-and-egg for first contact). Because UDP is
                # unauthenticated, the request MUST be signed: we verify the sender
                # owns the private key for its claimed cert id before registering it.
                _handle_udp_friend_request(sock, pkt, addr[0])
            elif t == "friend-accept":
                # The accepter's reply to a friend request also rides UDP (a TCP
                # reply is held forever when no connection exists — the one-way
                # handshake bug). Verify the signed accept and confirm the friend.
                _handle_udp_friend_accept(sock, pkt, addr[0])
            elif t == "friend-cancel":
                # The requester withdrew their friend request. Verify the signed
                # cancel and clear the pending request + banner on this side.
                _handle_udp_friend_cancel(sock, pkt, addr[0])
            elif t == "friend-reject":
                # The accepter declined the request. Verify the signed reject so
                # the original requester learns they were denied (a TCP reject is
                # dropped when no connection exists — the one-way bug) and clears
                # its pending state + banner.
                _handle_udp_friend_reject(sock, pkt, addr[0])
            elif t == "friend-unfriend":
                # The peer unfriended us. Verify the signed unfriend so both
                # sides drop the link even when no TCP connection exists
                # (mirror of the one-way bug: A unfriends B, B keeps A).
                _handle_udp_friend_unfriend(sock, pkt, addr[0])
        except Exception:
            # Never let one packet kill the discovery listener. Log (rate-safely
            # is hard here; just one line per bad packet is acceptable and rare).
            try:
                _diag("udp-listener-skipped-bad-packet", addr=addr[0])
            except Exception:
                pass


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


def _send_udp_friend_request(sock: socket.socket, pid: str) -> bool:
    """Send a SIGNED friend request to a peer over UDP (no TCP needed).

    Friendship is the bootstrap — the first contact between strangers who have
    no TCP trust yet. So the request goes over the discovery channel (UDP).
    Because UDP is unauthenticated, the request is signed with our private key
    over (id + nonce): the recipient verifies we own the key for our claimed
    cert id, exactly like the TCP challenge-response but without needing an
    established connection. Returns True if sent.
    """
    # Resolve the peer's IP from the peer list (or friend record) — the sender
    # passes the peer's ID (fingerprint), not its address.
    peer = find_peer(pid)
    target = (peer or {}).get("address", "")
    tport = int((peer or {}).get("port") or DEFAULT_PORT)
    if not target:
        for f in STATE.config.get("friends", []):
            if f.get("id") == pid and f.get("address"):
                target = f.get("address")
                tport = int(f.get("port") or DEFAULT_PORT)
                break
    if not target:
        _diag("udp-friend-request-failed", to=pid[:12], reason="no-address")
        return False
    nonce = secrets.token_hex(16)
    payload = {"t": "friend-request",
               "id": host_id(),
               "name": display_name(),
               "cert": _our_cert_pem(),
               "nonce": nonce,
               "sig": _sign((host_id() + nonce).encode("utf-8")),
               "port": port()}
    try:
        sock.sendto(json.dumps(payload).encode("utf-8"), (target, tport))
        _diag("udp-friend-request-sent", to=target, port=tport, nonce=nonce[:8])
        return True
    except OSError as e:
        _diag("udp-friend-request-failed", to=target, errno=getattr(e, "errno", None))
        return False


def _handle_udp_friend_request(sock: socket.socket, pkt: dict, addr: str) -> None:
    """Verify + register an inbound UDP friend request.

    The sender must prove they own the private key for the claimed cert id:
      - sha256(cert) == id          (the cert matches the claimed identity)
      - verify(sig, id+nonce, cert) (they hold the key for that cert)
    This prevents a UDP spoof from impersonating someone. On success the
    request is surfaced (verified fingerprint) for the user to accept.
    """
    claimed = str(pkt.get("id") or "")
    cert_pem = str(pkt.get("cert") or "")
    nonce = str(pkt.get("nonce") or "")
    sig = str(pkt.get("sig") or "")
    name = str(pkt.get("name") or friendly_name(claimed))
    pport = int(pkt.get("port") or DEFAULT_PORT)
    # Reject our own request echoing back.
    if not claimed or claimed == host_id():
        return
    # Verify the cert matches the claimed id, and the signature proves key
    # ownership. Both must hold or the request is forged — drop it.
    if _cert_fingerprint_of_pem(cert_pem) != claimed:
        _diag("udp-friend-request-rejected", from_id=claimed[:12], reason="bad-identity")
        return
    if not nonce or not _verify(cert_pem, (claimed + nonce).encode("utf-8"), sig):
        _diag("udp-friend-request-rejected", from_id=claimed[:12], reason="bad-signature")
        return
    # Honored requests: only if we accept incoming requests.
    if not accept_requests():
        _diag("udp-friend-request-rejected", from_id=claimed[:12], reason="requests-disabled")
        return
    # Register the peer (so we know their address) and surface the request with
    # the VERIFIED fingerprint — the same verified-request UI as the TCP path.
    upsert_peer(claimed, name, addr, pport)
    _emit({"event": "friend-request", "from": claimed, "name": name,
           "fingerprint": claimed, "text": "wants to add you as a friend", "udp": True})
    _diag("udp-friend-request", from_id=claimed[:12], name=name, addr=addr)


def _send_udp_friend_accept(sock: socket.socket, pid: str) -> bool:
    """Send a SIGNED friend-accept back to a peer over UDP (no TCP needed).

    The friend handshake is bidirectional bootstrap: the requester sends a
    signed UDP request, and the accepter must reply over the SAME channel —
    a TCP reply is held forever when no connection exists yet (the one-way
    bug). Like the request, the accept is signed so the recipient can verify
    it's genuinely from the peer they friended.
    """
    peer = find_peer(pid)
    target = (peer or {}).get("address", "")
    tport = int((peer or {}).get("port") or DEFAULT_PORT)
    if not target:
        for f in STATE.config.get("friends", []):
            if f.get("id") == pid and f.get("address"):
                target = f.get("address")
                tport = int(f.get("port") or DEFAULT_PORT)
                break
    if not target:
        _diag("udp-friend-accept-failed", to=pid[:12], reason="no-address")
        return False
    nonce = secrets.token_hex(16)
    payload = {"t": "friend-accept",
               "id": host_id(),
               "name": display_name(),
               "cert": _our_cert_pem(),
               "nonce": nonce,
               "sig": _sign((host_id() + nonce).encode("utf-8")),
               "port": port(),
               "to": pid}
    try:
        sock.sendto(json.dumps(payload).encode("utf-8"), (target, tport))
        _diag("udp-friend-accept-sent", to=target, port=tport, nonce=nonce[:8])
        return True
    except OSError as e:
        _diag("udp-friend-accept-failed", to=target, errno=getattr(e, "errno", None))
        return False


def _handle_udp_friend_accept(sock: socket.socket, pkt: dict, addr: str) -> None:
    """Verify + process an inbound UDP friend-accept.

    The accepter proves ownership of its claimed cert id (same signature
    scheme as the request). On success we mark the peer as a CONFIRMED friend
    and reveal any held messages — completing the handshake on our side
    without needing a TCP connection.
    """
    claimed = str(pkt.get("id") or "")
    cert_pem = str(pkt.get("cert") or "")
    nonce = str(pkt.get("nonce") or "")
    sig = str(pkt.get("sig") or "")
    name = str(pkt.get("name") or friendly_name(claimed))
    # We only accept a friend-accept for someone we actually requested.
    if not claimed or claimed == host_id():
        return
    if _cert_fingerprint_of_pem(cert_pem) != claimed:
        _diag("udp-friend-accept-rejected", from_id=claimed[:12], reason="bad-identity")
        return
    if not nonce or not _verify(cert_pem, (claimed + nonce).encode("utf-8"), sig):
        _diag("udp-friend-accept-rejected", from_id=claimed[:12], reason="bad-signature")
        return
    # Confirm the friendship locally and reveal any held messages.
    peer = find_peer(claimed)
    pname = (peer or {}).get("name") or name
    add_friend(claimed, addr, pname, confirmed=True)
    _emit({"event": "friend-accepted", "id": claimed, "name": pname})
    with STATE.pending_lock:
        held = STATE.pending_sent.pop(claimed, [])
    _diag("udp-friend-accepted", peer=claimed[:12], name=pname, revealed=len(held))
    _reveal(held)


def _send_udp_friend_cancel(sock: socket.socket, pid: str) -> bool:
    """Send a SIGNED friend-request cancel back to a peer over UDP (no TCP needed).

    The handshake is bidirectional bootstrap: the requester sent a signed UDP
    request, so a withdrawal must ride the SAME channel — a TCP cancel is
    dropped when no connection exists yet (the one-way bug). Like the request,
    the cancel is signed so the recipient can verify it's genuinely from the
    peer who originally requested them. Returns True if sent.
    """
    peer = find_peer(pid)
    target = (peer or {}).get("address", "")
    tport = int((peer or {}).get("port") or DEFAULT_PORT)
    if not target:
        for f in STATE.config.get("friends", []):
            if f.get("id") == pid and f.get("address"):
                target = f.get("address")
                tport = int(f.get("port") or DEFAULT_PORT)
                break
    if not target:
        _diag("udp-friend-cancel-failed", to=pid[:12], reason="no-address")
        return False
    nonce = secrets.token_hex(16)
    payload = {"t": "friend-cancel",
               "id": host_id(),
               "name": display_name(),
               "cert": _our_cert_pem(),
               "nonce": nonce,
               "sig": _sign((host_id() + nonce).encode("utf-8")),
               "port": port(),
               "to": pid}
    try:
        sock.sendto(json.dumps(payload).encode("utf-8"), (target, tport))
        _diag("udp-friend-cancel-sent", to=target, port=tport, nonce=nonce[:8])
        return True
    except OSError as e:
        _diag("udp-friend-cancel-failed", to=target, errno=getattr(e, "errno", None))
        return False


def _handle_udp_friend_cancel(sock: socket.socket, pkt: dict, addr: str) -> None:
    """Verify + process an inbound UDP friend-cancel (a request that was withdrawn).

    The canceller proves ownership of its claimed cert id (same signature scheme
    as the request). On success we drop the pending request: discard the peer's
    held messages and clear the incoming banner so the user no longer sees a
    request they could Accept.
    """
    claimed = str(pkt.get("id") or "")
    cert_pem = str(pkt.get("cert") or "")
    nonce = str(pkt.get("nonce") or "")
    sig = str(pkt.get("sig") or "")
    name = str(pkt.get("name") or friendly_name(claimed))
    if not claimed or claimed == host_id():
        return
    if _cert_fingerprint_of_pem(cert_pem) != claimed:
        _diag("udp-friend-cancel-rejected", from_id=claimed[:12], reason="bad-identity")
        return
    if not nonce or not _verify(cert_pem, (claimed + nonce).encode("utf-8"), sig):
        _diag("udp-friend-cancel-rejected", from_id=claimed[:12], reason="bad-signature")
        return
    # Withdrawn: drop the peer's held inbound messages and clear the incoming
    # banner. If the canceller was merely a pending (unconfirmed) request we
    # recorded, remove that record too — but NEVER unfriend a confirmed friend
    # (a confirmed relationship is not retractable by a stray cancel).
    with STATE.pending_lock:
        STATE.pending_first.pop(claimed, None)
    _unfriend_if_unconfirmed(claimed)
    _emit({"event": "friend-rejected", "id": claimed, "name": name})
    _diag("udp-friend-cancelled", peer=claimed[:12], name=name)


def _send_udp_friend_reject(sock: socket.socket, pid: str) -> bool:
    """Send a SIGNED friend-request decline back to a peer over UDP.

    Rejecting = declining the request. The reject must ride the SAME signed UDP
    channel as the request/accept: a TCP reject (`send_control`) is dropped when
    no connection exists yet, so the requester would never learn they were denied
    (the one-way bug) and its "Waiting to accept" banner would stick forever.
    """
    peer = find_peer(pid)
    target = (peer or {}).get("address", "")
    tport = int((peer or {}).get("port") or DEFAULT_PORT)
    if not target:
        for f in STATE.config.get("friends", []):
            if f.get("id") == pid and f.get("address"):
                target = f.get("address")
                tport = int(f.get("port") or DEFAULT_PORT)
                break
    if not target:
        _diag("udp-friend-reject-failed", to=pid[:12], reason="no-address")
        return False
    nonce = secrets.token_hex(16)
    payload = {"t": "friend-reject",
               "id": host_id(),
               "name": display_name(),
               "cert": _our_cert_pem(),
               "nonce": nonce,
               "sig": _sign((host_id() + nonce).encode("utf-8")),
               "port": port(),
               "to": pid}
    try:
        sock.sendto(json.dumps(payload).encode("utf-8"), (target, tport))
        _diag("udp-friend-reject-sent", to=target, port=tport, nonce=nonce[:8])
        return True
    except OSError as e:
        _diag("udp-friend-reject-failed", to=target, errno=getattr(e, "errno", None))
        return False


def _send_udp_friend_unfriend(sock: socket.socket, pid: str) -> bool:
    """Send a SIGNED friend-unfriend to a peer over UDP (no TCP needed).

    When we unfriend someone, we notify the peer over the SAME bootstrap
    channel (UDP) so BOTH sides drop the link — otherwise B would keep us as a
    friend after we unfriend them (the one-way bug, mirror of the handshake).
    Like the other friend packets, it's signed so the recipient can verify
    it's genuinely from the peer they friended. Returns True if sent.
    """
    peer = find_peer(pid)
    target = (peer or {}).get("address", "")
    tport = int((peer or {}).get("port") or DEFAULT_PORT)
    if not target:
        for f in STATE.config.get("friends", []):
            if f.get("id") == pid and f.get("address"):
                target = f.get("address")
                tport = int(f.get("port") or DEFAULT_PORT)
                break
    if not target:
        _diag("udp-friend-unfriend-failed", to=pid[:12], reason="no-address")
        return False
    nonce = secrets.token_hex(16)
    payload = {"t": "friend-unfriend",
               "id": host_id(),
               "name": display_name(),
               "cert": _our_cert_pem(),
               "nonce": nonce,
               "sig": _sign((host_id() + nonce).encode("utf-8")),
               "port": port(),
               "to": pid}
    try:
        sock.sendto(json.dumps(payload).encode("utf-8"), (target, tport))
        _diag("udp-friend-unfriend-sent", to=target, port=tport, nonce=nonce[:8])
        return True
    except OSError as e:
        _diag("udp-friend-unfriend-failed", to=target, errno=getattr(e, "errno", None))
        return False


def _handle_udp_friend_reject(sock: socket.socket, pkt: dict, addr: str) -> None:
    """Verify + process an inbound UDP friend-reject (the request was declined).

    We were the requester and the peer declined. The peer proves ownership of its
    claimed cert id (same signature scheme). On success we drop our outbound
    pending state and clear the "Waiting to accept" banner — the peer is NOT
    added as a friend, and we never confirm them.
    """
    claimed = str(pkt.get("id") or "")
    cert_pem = str(pkt.get("cert") or "")
    nonce = str(pkt.get("nonce") or "")
    sig = str(pkt.get("sig") or "")
    name = str(pkt.get("name") or friendly_name(claimed))
    if not claimed or claimed == host_id():
        return
    if _cert_fingerprint_of_pem(cert_pem) != claimed:
        _diag("udp-friend-reject-rejected", from_id=claimed[:12], reason="bad-identity")
        return
    if not nonce or not _verify(cert_pem, (claimed + nonce).encode("utf-8"), sig):
        _diag("udp-friend-reject-rejected", from_id=claimed[:12], reason="bad-signature")
        return
    # Declined: drop our held-outgoing content and clear the banner. Never add
    # or confirm them as a friend.
    with STATE.pending_lock:
        STATE.pending_sent.pop(claimed, None)
    _unfriend_if_unconfirmed(claimed)
    _emit({"event": "friend-rejected", "id": claimed, "name": name})
    _diag("udp-friend-rejected", peer=claimed[:12], name=name)


def _handle_udp_friend_unfriend(sock: socket.socket, pkt: dict, addr: str) -> None:
    """Verify + process an inbound UDP friend-unfriend (we were unfriended).

    The peer proves ownership of its claimed cert id (same signature scheme as
    the other friend packets). On success we remove them as a CONFIRMED friend
    unconditionally and clear their history — mirroring the local unfriend so
    both sides drop the link even when no TCP connection exists. A spurious
    (unverified) packet is dropped: we never remove a friend on unauthenticated
    UDP.
    """
    claimed = str(pkt.get("id") or "")
    cert_pem = str(pkt.get("cert") or "")
    nonce = str(pkt.get("nonce") or "")
    sig = str(pkt.get("sig") or "")
    name = str(pkt.get("name") or friendly_name(claimed))
    if not claimed or claimed == host_id():
        return
    if _cert_fingerprint_of_pem(cert_pem) != claimed:
        _diag("udp-friend-unfriend-rejected", from_id=claimed[:12], reason="bad-identity")
        return
    if not nonce or not _verify(cert_pem, (claimed + nonce).encode("utf-8"), sig):
        _diag("udp-friend-unfriend-rejected", from_id=claimed[:12], reason="bad-signature")
        return
    # Verified: they unfriended us. Drop the confirmed friend + clear history.
    _do_unfriend(claimed)
    _diag("udp-friend-unfriended", peer=claimed[:12], name=name)


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
    with STATE.peers_lock:
        known = [(p["address"], p["port"]) for p in STATE.peers.values()]
    for addr, pport in known:
        _udp_send(sock, {"t": "hello"}, target=addr)


def udp_loop() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind(("", port()))
    except OSError as e:
        _emit({"event": "error", "message": "lanchat UDP bind failed on port %d: %s" % (port(), e)})
        return
    STATE.udp_sock = sock
    threading.Thread(target=_udp_listener, args=(sock,), daemon=True).start()

    last_broadcast = 0.0
    scanned_once = False
    # In private visibility we are invisible: no broadcast, no scan, and we do
    # NOT re-announce to known peers either (that would still leak our presence
    # to anyone sniffing). Existing established friend sockets are unaffected;
    # we simply stop announcing ourselves on the network.
    hidden = visibility() != "open"
    while True:
        now = time.time()
        # (1.3) Recompute the visibility flag each tick — it is NOT a startup
        # constant. setVisibility writes CONFIG live, so a device that starts
        # private and later flips "Discoverable" on must START broadcasting
        # immediately (same tick); if hidden were captured once here it would
        # stay True forever and the device would remain undiscoverable.
        hidden = visibility() != "open"
        # Only announce ourselves while online (appear offline otherwise) and
        # only when visible on discovery.
        if is_online() and not hidden and now - last_broadcast >= BROADCAST_INTERVAL_S:
            _udp_send(sock, {"t": "hello"})
            last_broadcast = now
            # Reliable steady-state discovery: on networks where UDP broadcast
            # is filtered, peers only ever appear via the one-time scan. Keep
            # them alive by re-announcing DIRECTLY to every known peer every
            # broadcast interval — unicast always works, unlike broadcast.
            _announce_to_known(sock)
        # Subnet scan ONCE shortly after startup, as a broadcast fallback for
        # networks where broadcasts are filtered. It is NOT repeated: blind
        # unicasting to every host on the /24 every few seconds floods the UDP
        # send queue and chokes inbound traffic. Broadcast + unicast-to-known
        # handle steady-state discovery; the scan only seeds peers that
        # broadcast filtering hides.
        if is_online() and not hidden and not scanned_once and now - last_broadcast >= 3.0:
            _scan_subnet(sock)
            scanned_once = True
        expire_peers()
        time.sleep(0.5)





def broadcast_now() -> None:
    """Immediately announce ourselves so a name change reaches peers right away."""
    if STATE.udp_sock is not None and visibility() == "open":
        _udp_send(STATE.udp_sock, {"t": "hello"})


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



_RECONNECT_MAX_BACKOFF = 16.0


def _conn(pid: str) -> dict:
    with STATE.conns_lock:
        c = STATE.conns.get(pid)
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
            STATE.conns[pid] = c
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
    with STATE.conns_lock:
        pids = list(STATE.conns.keys())
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
            if len(buf) > MAX_FRAME_BUF:
                # Peer flooded without a newline — drop it to bound memory.
                _log("outbound-buffer-overflow peer=%s" % pid[:12])
                return
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


def _reader_inbound_wrapper(sock) -> None:
    """Run _reader_inbound and always release the connection slot (also on
    the buffer-overflow / identity-reject early-return paths)."""
    try:
        _reader_inbound(sock)
    finally:
        _conn_slot_release()


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
            if len(buf) > MAX_FRAME_BUF:
                # Peer flooded without a newline — drop it to bound memory.
                _log("inbound-buffer-overflow addr=%s" % addr)
                return
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
    with STATE.peers_lock:
        for pid, p in STATE.peers.items():
            targets[pid] = (p["address"], p["port"])
    for f in STATE.config.get("friends", []):
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

# Cap concurrent inbound connections so a connection-flooding peer can't spawn
# unbounded reader threads. Guarded by a lock; decremented when the reader exits.
_inbound_conns = 0



def _conn_slot_taken() -> bool:
    """Try to take an inbound-connection slot. True if under the cap; False if full."""
    global _inbound_conns
    with STATE.inbound_conns_lock:
        if _inbound_conns >= MAX_INBOUND_CONNS:
            return False
        _inbound_conns += 1
        return True


def _conn_slot_release() -> None:
    global _inbound_conns
    with STATE.inbound_conns_lock:
        if _inbound_conns > 0:
            _inbound_conns -= 1


def tcp_loop() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("", port()))
        srv.listen(16)
    except OSError as e:
        _emit({"event": "error", "message": "lanchat TCP bind failed on port %d: %s" % (port(), e)})
        return
    # Always serve the CURRENT cert, never a stale one loaded once at boot.
    # If the cert is regenerated while this daemon runs (e.g. a reinstall
    # wiped + regenerated lanchat-certs), host_id() reads the new cert fresh
    # but a cached tls_ctx would keep serving the old one — a fingerprint
    # mismatch that silently breaks every peer connection. Reload per accept
    # so the served cert always matches the announced identity.
    def _current_tls_ctx() -> ssl.SSLContext:
        ensure_tls()  # generate if missing
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_PEM, CERT_KEY)
        return ctx

    while True:
        try:
            raw_conn, addr = srv.accept()
            conn = _current_tls_ctx().wrap_socket(raw_conn, server_side=True)
        except (OSError, ssl.SSLError):
            continue
        if not _conn_slot_taken():
            # Over the connection cap — refuse and drop.
            _log("inbound-conn-limit addr=%s conns=%d" % (addr[0], MAX_INBOUND_CONNS))
            _close_sock(conn)
            continue
        threading.Thread(target=_reader_inbound_wrapper, args=(conn,), daemon=True).start()


# Handshake hold: pid -> list of held messages awaiting the recipient's accept.
# _pending_first = inbound requests we received and are holding (revealed on accept).
# _pending_sent  = outbound requests we sent and are holding (revealed on accept).





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
            with STATE.pending_lock:
                held = STATE.pending_sent.pop(pid, [])
            _diag("inbound-friend-accept", peer=pid[:12], name=pname, revealed=len(held))
            _reveal(held)
        return
    if msg.get("t") == "friendReject":
        pid = str(msg.get("from", ""))
        # They declined: drop our held-outgoing messages and any pending
        # (unconfirmed) record so we never treat them as a friend.
        with STATE.pending_lock:
            STATE.pending_sent.pop(pid, None)
        _unfriend_if_unconfirmed(pid)
        _emit({"event": "friend-rejected", "id": pid, "name": str(msg.get("fromName") or friendly_name(pid))})
        _diag("inbound-friend-reject", peer=pid[:12])
        return
    if msg.get("t") == "friendRemove":
        # They unfriended us over TCP (the UDP path uses friend-unfriend; this
        # is the fallback when the socket exists). Drop the confirmed friend +
        # clear history so both sides remove the link.
        pid = str(msg.get("from", ""))
        if pid:
            _do_unfriend(pid)
            _diag("inbound-friend-remove", peer=pid[:12])
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
    # ---- attachment file transfer (over the authenticated socket) ----
    # ---- room envelope (group chat rooms) ----
    if msg.get("t") == "room":
        rooms.handle_room_msg(msg, addr)
        return
    if msg.get("t") == "roomFile":
        rooms.handle_room_file_msg(msg, addr)
        return
    if msg.get("t") == "attachmentRequest":
        # Recipient wants a file we registered. Stream it back over the socket
        # (sender side). The connection is already authenticated, so the
        # requester is the friend we trust — but still gate on is_trusted so a
        # stranger can't drain our registered files.
        from_pid = str(msg.get("from", ""))
        if from_pid and is_trusted(from_pid):
            threading.Thread(
                target=_serve_attachment,
                args=(from_pid, str(msg.get("fileId", "")), str(msg.get("mid", ""))),
                daemon=True,
            ).start()
        return
    if msg.get("t") in ("attachmentChunk", "attachmentEnd", "attachmentError"):
        # Sender streaming the file back to us (recipient side). Chunks append
        # to the registered .part file; End finalizes + verifies sha256 + renames.
        from_pid = str(msg.get("from", ""))
        file_id = str(msg.get("fileId", ""))
        mid = str(msg.get("mid", ""))
        if msg.get("t") == "attachmentChunk":
            ok, total, written = _dl_chunk(
                file_id, from_pid, str(msg.get("data", "")), int(msg.get("total") or 0))
            if ok:
                _emit({"event": "attachment-progress", "fileId": file_id, "mid": mid,
                       "bytes": written, "total": total})
        elif msg.get("t") == "attachmentEnd":
            _finalize_download(_dl_finish(file_id, from_pid, True), mid, file_id)
        else:
            _finalize_download(_dl_finish(file_id, from_pid, False), mid, file_id,
                               error=str(msg.get("error") or "sender aborted"))
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
    # Carry the room id (group chat) through so a room message renders in the
    # room's thread, not the 1:1 thread with the sender. Any new message field
    # MUST be carried through this rebuild — dropped fields vanish silently.
    room_id = str(msg.get("room") or "")
    if room_id:
        message["room"] = room_id
        # A room message only renders if the sender is in one of OUR room
        # copies (authoritative or cached). Enforce so a stranger can't inject
        # room-scoped content into the UI by claiming a room id.
        if rooms.get_room(room_id) is None:
            _diag("inbound-dropped", from_id=pid[:12], reason="unknown-room", room=room_id[:12])
            return
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
        # (1.3) Request gating: when the user has disabled accepting friend
        # requests, a stranger's request is silently dropped. The requester is
        # never added as pending, so they can't even get a trust foothold.
        if not accept_requests():
            _log("inbound-friend-request-rejected from=%s reason=requests-disabled" % pid[:12])
            return
        if not is_pending(pid):
            add_friend(pid, addr[0], from_name, confirmed=False)
        # Do NOT surface the message content yet — hold it so the receiver
        # sees only a friend request until they accept. Assign a mid now so
        # the accept can reveal the same message (replace in the UI).
        if not message.get("mid"):
            message["mid"] = secrets.token_hex(8)
        message["held"] = True
        with STATE.pending_lock:
            STATE.pending_first.setdefault(pid, []).append(message)
        # Emit the request WITH the requester's verified cert fingerprint
        # (pid is the identity `_reader_inbound` cryptographically proved),
        # so the UI can show it and require confirmation it matches what the
        # user expected before accepting (1.3 Option B).
        _emit({"event": "friend-request", "from": pid, "fromName": from_name,
               "text": text, "ts": ts, "mid": message["mid"], "fingerprint": pid})
        _diag("inbound-friend-request", peer=pid[:12], name=from_name, text=text[:40])
        return
    append_history(message)
    _emit({"event": "message", "message": message})
    _diag("inbound-message", peer=pid[:12], name=from_name, text=text[:40])


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------





# Attachment registry + socket file transfer (_safe_filename, register/get,
# _dl_* reassembly, _serve_attachment) moved to attachments.py (Commit 2);
# all symbols re-exported at the top of this file.


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


def send_message(peer_id: str, text: str, attachment: dict = None) -> bool:
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
    # Write to the peer's persistent socket (or hold until it reconnects).
    delivered = _write(peer_id, msg)
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
    """Send the friendAccept notification to a peer.

    This is the RETURN half of the friend handshake. Like the request, the
    accept must ride UDP (signed) — a TCP delivery is held forever when no
    connection exists yet (the one-way handshake bug: requester sees the
    request "sent", accepter accepts locally, but the accept never reaches the
    requester). Falls back to the TCP socket if UDP is unavailable.
    """
    if STATE.udp_sock is not None:
        if _send_udp_friend_accept(STATE.udp_sock, peer_id):
            return True
    return send_control(peer_id, "friendAccept")


# --------------------------------------------------------------------------
# Optional HTTP API — moved to http_api.py (Commit 2); _ApiHandler,
# _start_http and _stop_http are re-exported at the top of this file.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Firewall status detection + open/close (port 4812)
# --------------------------------------------------------------------------

def _run_cmd(args, timeout=5.0):
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired):
        return -1, "", ""


def _firewall_status() -> dict:
    """Detect whether lanchat's port 4812 (udp+tcp) is reachable inbound.

    Returns {open, backend, detail}:
      open: True  -> 4812 allowed in (or no active firewall)
            False -> a firewall is active and 4812 is NOT allowed in
            None  -> unknown (couldn't read the firewall state)
      backend: "ufw" | "firewalld" | "none"
      detail: human-readable explanation for the UI tooltip.
    """
    # ufw active?
    rc, _, _ = _run_cmd(["systemctl", "is-active", "ufw"])
    ufw_active = rc == 0
    # firewalld active?
    rc2, _, _ = _run_cmd(["systemctl", "is-active", "firewalld"])
    fwld_active = rc2 == 0

    if not ufw_active and not fwld_active:
        return {"open": True, "backend": "none",
                "detail": "No active firewall — port 4812 is reachable."}

    if ufw_active:
        # Try to read the real rule set WITHOUT prompting: `sudo -n` succeeds
        # only if a sudoers rule exists (e.g. an older `make firewall-open`).
        # With polkit-every-time there is no standing rule, so this usually
        # fails and we report Unknown — the toggle itself still works via
        # pkexec (which prompts). We never prompt here: reading status on
        # every panel open must not nag.
        rc, out, err = _run_cmd(["sudo", "-n", "ufw", "status", "numbered"])
        if rc != 0:
            return {"open": None, "backend": "ufw",
                    "detail": "UFW active but can't read rules without admin (status unknown; the toggle will prompt for a password)."}
        udp_ok = "4812/udp" in out and "ALLOW" in out
        tcp_ok = "4812/tcp" in out and "ALLOW" in out
        if udp_ok and tcp_ok:
            return {"open": True, "backend": "ufw",
                    "detail": "UFW allows 4812/udp + 4812/tcp from the LAN."}
        missing = []
        if not udp_ok:
            missing.append("udp")
        if not tcp_ok:
            missing.append("tcp")
        return {"open": False, "backend": "ufw",
                "detail": "UFW is blocking 4812/%s — open the port to be reachable." % "+".join(missing)}

    # firewalld active (lanchat's scripts manage ufw only).
    return {"open": False, "backend": "firewalld",
            "detail": "firewalld is active — lanchat's scripts manage ufw only; open 4812/udp+tcp via firewall-cmd."}


def _firewall_script(action: str) -> dict:
    """Run scripts/lanchat-firewall.sh open|close and return a status dict.

    The script uses pkexec (polkit), so each action pops a password prompt and
    leaves NO permanent sudoers rule. If the user cancels the prompt, pkexec
    returns non-zero and we surface a clear message.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "scripts", "lanchat-firewall.sh")
    rc, out, err = _run_cmd(["bash", script, action], timeout=60.0)
    if rc == 0:
        return _firewall_status()
    # pkexec failed or was cancelled.
    return {"open": None, "backend": "ufw",
            "detail": "Couldn't %s the port (no admin rights given). Re-run to get a password prompt, or open the port manually." % action,
            "error": (out + err).strip()[:200]}


# --------------------------------------------------------------------------
# stdin command loop
# --------------------------------------------------------------------------

def handle_command(cmd: dict) -> None:
    kind = cmd.get("cmd")
    if kind == "udpFriendRequest":
        # Send a friend request over UDP (signed) — the bootstrap path that
        # needs no pre-existing TCP connection. The recipient verifies our
        # signature and surfaces it. Falls back gracefully if UDP is down.
        to = str(cmd.get("to") or "")
        if to and STATE.udp_sock is not None:
            sent = _send_udp_friend_request(STATE.udp_sock, to)
            if sent:
                _emit({"event": "friend-request", "outgoing": True, "to": to,
                       "toName": str(cmd.get("name") or friendly_name(to)),
                       "text": "wants to add you as a friend"})
        return
    if kind == "cancelFriendRequest":
        # Withdraw a friend request WE sent that is still pending (the peer
        # hasn't accepted yet). Retracting = tell the peer their incoming
        # request is void (signed UDP cancel so it lands even with no TCP
        # connection), drop any held outbound content, and clear our own
        # pending-request banner. Only meaningful for an unconfirmed peer; if
        # they already accepted, this is a no-op banner clear.
        pid = str(cmd.get("id", ""))
        if pid and not is_friend(pid):
            with STATE.pending_lock:
                STATE.pending_sent.pop(pid, None)
            if STATE.udp_sock is not None:
                _send_udp_friend_cancel(STATE.udp_sock, pid)
        if pid:
            _emit({"event": "friend-rejected", "id": pid})
            _diag("cancelled-friend-request", peer=pid[:12])
        return
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
    elif kind == "roomHistory":
        room_id = str(cmd.get("roomId", ""))
        if room_id:
            page = history_for_room(room_id, int(cmd.get("offset", 0)), int(cmd.get("limit", 100)))
            _emit({"event": "roomHistory", "roomId": room_id, "total": page["total"],
                   "messages": page["messages"]})
    elif kind == "createRoom":
        room = rooms.create_room(str(cmd.get("name", "")))
        _emit({"event": "room-created", "roomId": room["roomId"], "name": room["name"]})
    elif kind == "roomSend":
        rooms.send_room_text(str(cmd.get("roomId", "")), str(cmd.get("text", "")))
    elif kind == "roomFile":
        # Room file post (any member — posting is NOT owner-gated; approved).
        # Shape mirrors the 1:1 send: a local path is registered here, or a
        # pre-built att (already registered) is passed through.
        room_id_f = str(cmd.get("roomId", ""))
        att_f = cmd.get("att")
        if not isinstance(att_f, dict) or not att_f.get("fileId"):
            path_f = str((cmd.get("attachment") or {}).get("path", ""))
            if not path_f:
                _emit({"event": "error", "message": "roomFile needs a file path or metadata"})
            else:
                fid = secrets.token_hex(8)
                fname = str((cmd.get("attachment") or {}).get("name") or os.path.basename(path_f))
                try:
                    fsize = os.path.getsize(path_f)
                except OSError:
                    fsize = 0
                register_attachment(fid, path_f, fname)
                att_f = {"name": fname, "size": fsize, "mime": "application/octet-stream",
                         "fileId": fid, "sha256": _file_sha256(path_f)}
                rooms.post_room_file(room_id_f, att_f)
        else:
            rooms.post_room_file(room_id_f, att_f)
    elif kind == "roomInvite":
        room = rooms.get_room(str(cmd.get("roomId", "")))
        if room is None:
            _emit({"event": "error", "message": "Room not found"})
        elif room.get("owner") != host_id():
            _emit({"event": "error", "message": "Only the room owner can invite"})
        else:
            rooms.owner_invite(room, str(cmd.get("peer", "")))
    elif kind == "roomAdd":
        room = rooms.get_room(str(cmd.get("roomId", "")))
        if room is None:
            _emit({"event": "error", "message": "Room not found"})
        elif room.get("owner") == host_id():
            # Owner-side add: requires a confirmed friend link (approved
            # decision #1 — the authoritative channel needs it anyway).
            peer_id = str(cmd.get("peer", ""))
            if not is_trusted(peer_id):
                _emit({"event": "error", "message": "Cannot add %s — befriend each other first" %
                       ((find_peer(peer_id) or {}).get("name") or friendly_name(peer_id))})
            else:
                rooms.owner_admit(room, peer_id, str(cmd.get("peerName", "")))
        else:
            # A member proposing: forward to the owner (owner executes).
            member = room.get("members", {}).get(host_id())
            if not member or not member.get("canInvite"):
                _emit({"event": "error", "message": "You do not have permission to add people to this room"})
            else:
                sent = _write(room["owner"], {"t": "room", "kind": "roomAdd", "roomId": room["roomId"],
                                              "from": host_id(), "fromName": display_name(),
                                              "peer": str(cmd.get("peer", ""))})
                if not sent:
                    _emit({"event": "error", "message": "host offline — changes frozen"})
    elif kind == "roomJoin":
        room = STATE.rooms_cache.get(str(cmd.get("roomId", "")))
        if room is None:
            _emit({"event": "error", "message": "Room not found"})
        else:
            sent = _write(room.get("owner", ""),
                          {"t": "room", "kind": "roomJoin", "roomId": room["roomId"],
                           "from": host_id(), "fromName": display_name()})
            if not sent:
                _emit({"event": "error", "message": "host offline — changes frozen"})
    elif kind == "roomLeave":
        room = rooms.get_room(str(cmd.get("roomId", "")))
        if room is None:
            _emit({"event": "error", "message": "Room not found"})
        else:
            rooms.member_leave(room, host_id())
    elif kind == "roomRemove":
        room = STATE.rooms.get(str(cmd.get("roomId", "")))
        if room is None or room.get("owner") != host_id():
            _emit({"event": "error", "message": "Only the room owner can remove members"})
        else:
            rooms.owner_remove(room, str(cmd.get("peer", "")))
    elif kind == "roomSetCanInvite":
        room = STATE.rooms.get(str(cmd.get("roomId", "")))
        if room is None or room.get("owner") != host_id():
            _emit({"event": "error", "message": "Only the room owner can change member permissions"})
        else:
            rooms.owner_set_can_invite(room, str(cmd.get("peer", "")), bool(cmd.get("allowed", False)))
    elif kind == "setRoomColor":
        room = rooms.get_room(str(cmd.get("roomId", "")))
        if room is None:
            _emit({"event": "error", "message": "Room not found"})
        else:
            rooms.member_set_color(room, host_id(), str(cmd.get("token", "theme")),
                                   str(cmd.get("hex", "")))
    elif kind == "toggleRoomColors":
        room = STATE.rooms.get(str(cmd.get("roomId", "")))
        if room is None or room.get("owner") != host_id():
            _emit({"event": "error", "message": "Only the room owner can change room colors"})
        else:
            rooms.owner_toggle_colors(room, bool(cmd.get("enabled", True)))
    elif kind == "roomList":
        _emit({"event": "room-list", "rooms": rooms.rooms_list()})
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
        STATE.config["downloadDir"] = str(cmd.get("dir", ""))
        _save_config()
        _emit({"event": "download-dir", "dir": STATE.config["downloadDir"]})
    elif kind == "setSendDelay":
        STATE.config["sendDelay"] = int(cmd.get("seconds", 0))
        _save_config()
        _emit({"event": "send-delay", "seconds": STATE.config["sendDelay"]})
    elif kind == "acceptAttachment":
        peer_id = str(cmd.get("from", ""))
        if not peer_id or find_peer(peer_id) is None:
            _emit({"event": "error", "message": "attachment sender not found"})
            return
        # Room trust gate (plans/ROOMS.md decision: friend-gated file accept):
        # room membership alone does NOT authorize byte transfer — the
        # requester must be a confirmed friend of the SENDER. Fail fast with
        # the same shape as "sender offline" so the Save bar returns to its
        # pre-save state and the UI shows the befriend notice instead; when
        # the friendship is established the SAME message offers Save again
        # (UI re-evaluates on friend events — no manual re-request).
        if cmd.get("room") and not is_trusted(peer_id):
            _emit({"event": "attachment-saved", "ok": False,
                   "mid": str(cmd.get("mid", "")), "fileId": str(cmd.get("fileId", "")),
                   "error": "not friends with sender — befriend them to accept this file"})
            _diag("room-file-trust-gate", peer=peer_id[:12],
                  room=str(cmd.get("room"))[:12])
            return
        file_id = str(cmd.get("fileId", ""))
        name = _safe_filename(str(cmd.get("name", "download")))
        mid = str(cmd.get("mid", ""))
        sha256 = str(cmd.get("sha256", ""))
        save_to = os.path.join(STATE.config.get("downloadDir", os.path.expanduser("~/Downloads")), name)
        if not _dl_begin(file_id, peer_id, save_to, sha256, mid, str(cmd.get("room", ""))):
            _emit({"event": "attachment-saved", "ok": False, "path": save_to,
                   "mid": mid, "fileId": file_id, "error": "cannot open download file"})
            return
        # Ask the sender to stream the file over our authenticated socket (no
        # HTTP server, no LAN bind, no token in a URL). The sender replies with
        # attachmentChunk/attachmentEnd/attachmentError messages on the socket,
        # which _handle_incoming routes into _dl_chunk/_dl_finish.
        sent = _write(peer_id, {"t": "attachmentRequest", "from": host_id(), "to": peer_id,
                                "fileId": file_id, "mid": mid})
        if not sent:
            # Sender's socket is down (their daemon went offline before we
            # could pull the file). The file exists only while the sender is
            # online and still has it registered, so fail fast instead of
            # hanging the Save bar on "Saving…". Clean up the .part and let the
            # user retry while the sender is back.
            _dl_finish(file_id, peer_id, False)
            _log("attachment-sender-offline file=%s peer=%s" % (name, peer_id[:12]))
            _emit({"event": "attachment-saved", "ok": False, "path": save_to,
                   "mid": mid, "fileId": file_id, "error": "sender offline — file not available"})
    elif kind == "list":
        _emit({"event": "peers", "peers": peer_snapshot()})
    elif kind == "setHttp":
        enabled = bool(cmd.get("enabled"))
        if enabled:
            _start_http()
        else:
            _stop_http()
        STATE.config["httpEnabled"] = enabled
        _save_config()
    elif kind == "setHttpBind":
        # Bind address for the HTTP API: "127.0.0.1" (default, loopback-only)
        # or "0.0.0.0" (LAN exposure). Applies on the next enable/restart.
        bind = str(cmd.get("bind") or "127.0.0.1")
        STATE.config["httpBind"] = bind
        _save_config()
        _emit({"event": "http-bind", "bind": bind})
    elif kind == "setApiFullAccess":
        STATE.config["apiFullAccess"] = bool(cmd.get("enabled"))
        _save_config()
        _emit({"event": "api-full-access", "enabled": bool(cmd.get("enabled"))})
    elif kind == "setVisibility":
        # "open" (discoverable) or "private" (invisible). Re-reads the flag
        # in the UDP loop each tick, so it takes effect immediately.
        vis = str(cmd.get("visibility") or "private")
        STATE.config["visibility"] = "open" if vis == "open" else "private"
        _save_config()
        _emit({"event": "visibility", "visibility": STATE.config["visibility"]})
    elif kind == "setAcceptRequests":
        STATE.config["acceptRequests"] = bool(cmd.get("enabled", True))
        _save_config()
        _emit({"event": "accept-requests", "enabled": bool(STATE.config["acceptRequests"])})
    elif kind == "setPanelSize":
        size = str(cmd.get("size", "medium"))
        if size in ("small", "medium", "large", "xl", "full"):
            STATE.config["panelSize"] = size
            _save_config()
            _emit({"event": "panel-size", "size": size})
    elif kind == "setCustomSize":
        try:
            w = max(0, int(cmd.get("w", 0)))
            h = max(0, int(cmd.get("h", 0)))
        except (TypeError, ValueError):
            w, h = 0, 0
        STATE.config["customW"] = w
        STATE.config["customH"] = h
        _save_config()
        _emit({"event": "custom-size", "w": w, "h": h})
    elif kind == "setPeerColW":
        # Left peer-column width from the draggable divider. 0 = fall back to
        # the UI's default; clamps to a sane range so a huge value can't
        # collapse the chat pane.
        try:
            w = max(0, int(cmd.get("w", 0)))
        except (TypeError, ValueError):
            w = 0
        STATE.config["peerColW"] = w
        _save_config()
        _emit({"event": "peer-col-w", "w": w})
    elif kind == "setStatus":
        s = str(cmd.get("status", "available"))
        if s in STATUSES:
            STATE.config["status"] = s
            _save_config()
            broadcast_now()  # peers see the new status right away
            _emit({"event": "status", "status": s})
    elif kind == "setSoundEnabled":
        STATE.config["soundEnabled"] = bool(cmd.get("enabled"))
        _save_config()
        _emit({"event": "sound-enabled", "enabled": bool(cmd.get("enabled"))})
    elif kind == "setTypingEnabled":
        STATE.config["typingEnabled"] = bool(cmd.get("enabled"))
        _save_config()
        _emit({"event": "typing-enabled", "enabled": bool(cmd.get("enabled"))})
    elif kind == "setShowTyping":
        STATE.config["showTyping"] = bool(cmd.get("enabled"))
        _save_config()
        _emit({"event": "show-typing", "enabled": bool(cmd.get("enabled"))})
    elif kind == "setReadReceiptsEnabled":
        STATE.config["readReceiptsEnabled"] = bool(cmd.get("enabled"))
        _save_config()
        _emit({"event": "read-receipts-enabled", "enabled": bool(cmd.get("enabled"))})
    elif kind == "setShowReadReceipts":
        STATE.config["showReadReceipts"] = bool(cmd.get("enabled"))
        _save_config()
        _emit({"event": "show-read-receipts", "enabled": bool(cmd.get("enabled"))})
    elif kind == "setName":
        name = str(cmd.get("name", "")).strip()[:NAME_MAX]
        if name:
            STATE.config["displayName"] = name
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
        STATE.config["displayName"] = name
        _save_config()
        broadcast_now()
        _emit(_ready_event())
    elif kind == "setOnline":
        on = bool(cmd.get("online"))
        STATE.config["online"] = on
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
        with STATE.pending_lock:
            held = STATE.pending_first.pop(pid, [])
        _diag("accepted-friend-request", peer=pid[:12], name=pname, revealed=len(held))
        _reveal(held)
        if not _notify_accept(pid):
            _diag("accept-notify-failed", peer=pid[:12], name=pname, retrying=True)
    elif kind == "setFriend":
        # (1.3) Add a confirmed friend directly by cert fingerprint (the
        # private-mode "add by fingerprint" path). Optional address/port
        # lets conn_loop dial immediately; without them we learn the
        # friend's address when their hello arrives (they're a confirmed
        # friend, so private-mode discovery accepts it).
        pid = str(cmd.get("id", "")).strip()
        if not pid:
            _emit({"event": "error", "message": "setFriend requires an id (cert fingerprint)"})
        else:
            peer = find_peer(pid)
            addr = str(cmd.get("address") or (peer or {}).get("address") or "")
            pname = str(cmd.get("name") or (peer or {}).get("name") or "Unknown")
            add_friend(pid, addr, pname, confirmed=True)
            if addr:
                upsert_peer(pid, pname, addr, int(cmd.get("port") or (peer or {}).get("port") or DEFAULT_PORT))
            _emit({"event": "friend-added", "id": pid, "name": pname})
            _diag("friend-added-by-fingerprint", peer=pid[:12], name=pname, addr=addr)
    elif kind == "rejectFriend":
        pid = str(cmd.get("id", ""))
        # Rejecting = declining the relationship. Notify the requester over
        # signed UDP so they learn they were denied even with no TCP connection
        # (a TCP reject is dropped when no socket exists — the one-way bug that
        # leaves the sender's "Waiting to accept" banner stuck). Then REMOVE the
        # peer from our friend list (no lingering pending record), which emits a
        # friends event so the UI reconciles the notification banner and drops
        # the request.
        if pid and STATE.udp_sock is not None:
            _send_udp_friend_reject(STATE.udp_sock, pid)
        send_control(pid, "friendReject")
        with STATE.pending_lock:
            STATE.pending_first.pop(pid, None)
        unfriend(pid)
        _emit({"event": "friend-rejected", "id": pid})
        _diag("rejected-friend-request", peer=pid[:12])
    elif kind == "unfriend":
        pid = str(cmd.get("id", ""))
        # Unfriend is LOCAL removal + a notify-back to the peer so BOTH sides
        # drop the link (a local-only unfriend leaves the peer thinking we're
        # still friends — the one-way bug, mirror of the handshake). The notify
        # rides signed UDP (friend-unfriend) and falls back to TCP (friendRemove)
        # if UDP is unavailable, mirroring the accept/reject paths.
        _do_unfriend(pid)
        if pid:
            if STATE.udp_sock is not None:
                if _send_udp_friend_unfriend(STATE.udp_sock, pid):
                    _diag("unfriend-notify-udp", peer=pid[:12])
                else:
                    send_control(pid, "friendRemove")
            else:
                send_control(pid, "friendRemove")
    elif kind == "typing":
        send_control(str(cmd.get("to", "")), "typing")
    elif kind == "typingStopped":
        send_control(str(cmd.get("to", "")), "typingStopped")
    elif kind == "readReceipt":
        send_control(str(cmd.get("to", "")), "read", str(cmd.get("mid", "")))
    elif kind == "firewallStatus":
        _emit({"event": "firewall-status", **_firewall_status()})
    elif kind == "firewallOpen":
        _emit({"event": "firewall-status", **_firewall_script("open")})
    elif kind == "firewallClose":
        _emit({"event": "firewall-status", **_firewall_script("close")})


def stdin_loop() -> None:
    """Legacy stdin command loop (no --socket). Reads JSON commands from stdin
    and dispatches them. Kept for the test harness and manual runs; the
    production/systemd path uses the unix-socket control channel instead."""
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            cmd = json.loads(raw)
        except ValueError:
            continue
        handle_command(cmd)


def _socket_control_path() -> str:
    """Unix-socket path for the control channel (systemd mode)."""
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(
        os.path.expanduser("~"), ".config", "omarchy")
    return os.path.join(base, "lanchat.sock")


def _socket_control_server() -> None:
    """Accept control-channel connections from QML bridge processes.

    Each client is registered for event broadcast and gets a fresh `ready`
    event on connect (so a bridge that connects after boot syncs state), then
    feeds every line it sends into handle_command — the same dispatch stdin
    used, so the command surface is identical across both transports.
    """
    path = _socket_control_path()
    try:
        os.unlink(path)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(path)
        os.chmod(path, 0o600)
    except OSError as e:
        _emit({"event": "error", "message": "lanchat control socket bind failed: %s" % e})
        return
    srv.listen(8)
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            continue
        f = conn.makefile("rw", encoding="utf-8", newline="\n")
        with STATE.socket_clients_lock:
            STATE.socket_clients.add(f)
        try:
            f.write(json.dumps(_ready_event(), separators=(",", ":")) + "\n")
            f.flush()
        except OSError:
            _drop_socket_client(f)
            try:
                conn.close()
            except OSError:
                pass
            continue
        threading.Thread(target=_socket_client_reader, args=(f, conn), daemon=True).start()


def _socket_client_reader(f, conn) -> None:
    """Read one control-channel client's command lines until it disconnects."""
    try:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                cmd = json.loads(raw)
            except ValueError:
                continue
            handle_command(cmd)
    except (OSError, ValueError):
        pass
    finally:
        _drop_socket_client(f)
        try:
            conn.close()
        except OSError:
            pass


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
        "visibility": visibility(),
        "acceptRequests": accept_requests(),
        "online": is_online(),
        "friends": friends_list(),
        "rooms": rooms.rooms_list(),
        "downloadDir": STATE.config.get("downloadDir", os.path.join(os.path.expanduser("~"), "Downloads")),
        "sendDelay": STATE.config.get("sendDelay", 0),
        "apiFullAccess": api_full_access(),
        "panelSize": panel_size(),
        "customW": int(STATE.config.get("customW", 0) or 0),
        "customH": int(STATE.config.get("customH", 0) or 0),
        "peerColW": int(STATE.config.get("peerColW", 0) or 0),
        "status": status(),
        "soundEnabled": sound_enabled(),
        "typingEnabled": typing_enabled(),
        "showTyping": show_typing(),
        "readReceiptsEnabled": read_receipts_enabled(),
        "showReadReceipts": show_read_receipts(),
        "logPath": _LOG_PATH,
        "firewall": _firewall_status(),
    }


def main() -> None:
    import argparse
    # Self-register under the name 'server' so deferred `import server`
    # statements inside the extracted modules (http_api.py, history.py,
    # attachments.py, identity.py) resolve to THIS running module when
    # server.py is executed directly as __main__ — otherwise Python would
    # load a second copy of server.py as module 'server' with its own
    # (empty) State, splitting daemon state. When server.py is imported
    # normally (tests), __name__ == "server" and this is a no-op.
    _SELF = sys.modules[__name__]
    if __name__ != "server" and "server" not in sys.modules:
        sys.modules["server"] = _SELF
    ap = argparse.ArgumentParser(description="Lanchat daemon")
    ap.add_argument("--socket", action="store_true",
                    help="run under systemd: serve the unix-socket control channel "
                         "instead of reading commands from stdin")
    args = ap.parse_args()

    # Hard dependency check: server.py requires the `cryptography` library
    # (cert fingerprinting, TLS identity proof, storage encryption). If it's
    # missing from the Python running the daemon, importing it deep inside a
    # background thread (e.g. _udp_listener) raises ModuleNotFoundError and
    # silently kills that thread — the classic symptom: peers are visible
    # (UDP hello works) but friend requests/identity never surface, with no
    # visible error. Fail fast here with an actionable message instead.
    try:
        import cryptography  # noqa: F401
    except Exception as _dep_err:
        _msg = (
            "Lanchat requires the 'cryptography' Python library, but it is not "
            "installed for the interpreter running this daemon (%s). "
            "Install it (e.g. 'pacman -S python-cryptography' or "
            "'pip install cryptography') and restart lanchat.service."
            % sys.executable)
        print(_msg, file=sys.stderr)
        try:
            _emit({"event": "error", "message": _msg})
        except Exception:
            pass
        return 1

    # Diagnose identity consistency at startup: the daemon's announced id
    # (host_id = cert_fingerprint of CERT_PEM) must match the cert it will
    # serve on TCP. A mismatch means the daemon is running with a different
    # HOME (CERT_PEM resolves elsewhere) or a second daemon holds the port —
    # both silently break every peer connection (the recurring .51 issue).
    try:
        _diag("identity", home=os.path.expanduser("~"),
              cert_path=CERT_PEM, cert_exists=os.path.exists(CERT_PEM),
              announced_id=host_id()[:16],
              served_cert_fp=_cert_fingerprint_of_pem(_our_cert_pem())[:16])
        if os.path.exists(CERT_PEM):
            _announced = host_id()
            _served = _cert_fingerprint_of_pem(_our_cert_pem())
            if _announced != _served:
                _diag("identity-mismatch", announced=_announced[:16], served=_served[:16],
                      hint="daemon HOME or multiple daemons on port")
    except Exception as _e:
        pass

    load_config()
    load_history()
    rooms.load_rooms()
    if args.socket:
        # systemd mode: the control channel is a unix socket, not stdin. Start
        # the socket server first so the ready event below reaches a connected
        # bridge (and any client that connects later gets its own ready event).
        threading.Thread(target=_socket_control_server, daemon=True).start()
    _emit(_ready_event())
    if http_enabled():
        _start_http()
    threading.Thread(target=tcp_loop, daemon=True).start()
    threading.Thread(target=udp_loop, daemon=True).start()
    threading.Thread(target=conn_loop, daemon=True).start()
    if args.socket:
        # Keep the process alive; systemd supervises it. If every control
        # client drops and none reconnects, still run (network daemon).
        while True:
            time.sleep(3600)
    stdin_loop()  # blocks until the shell closes stdin; also supervises the process lifetime


if __name__ == "__main__":
    main()
