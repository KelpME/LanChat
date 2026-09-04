#!/usr/bin/env python3
"""Message history for KelpME.lanchat — extracted verbatim from server.py (Commit 2).

Per-machine persistence of chat messages. At-rest encryption (1.2.3): history
is encrypted with AES-256-GCM so the file on disk isn't human-readable. The
key is a dedicated random 32-byte key at 0600 (HISTORY_KEY). This protects
against the history file being read in isolation (a backup, a casual copy,
sync, a grabbed file) — NOT against full compromise of the machine (an
attacker who can read history.key can also read history.json). If
`cryptography` is unavailable, it degrades to plaintext.

Ownership: STATE.history, STATE.hist_crypto, STATE.hist_lock (the fields stay
on State; this module operates on them through the shared State instance).
server.py re-exports this module's symbols, so callers keep calling them via
server.
"""

import json
import os
import secrets
import tempfile

# init(state) wiring: STATE is bound once by server.py at import time
# (history.init(STATE)). Every former server-side STATE.x reference reads the
# same State instance. This module needs no deferred `import server` calls —
# it only uses stdlib, its own constants, and STATE.

STATE = None


def init(state):
    """Bind this module's STATE to the daemon's shared State instance."""
    global STATE
    STATE = state


# --------------------------------------------------------------------------
# Paths & limits
# --------------------------------------------------------------------------

STATE_DIR = os.path.join(os.path.expanduser("~"), ".local", "state", "lanchat")
HISTORY_PATH = os.path.join(STATE_DIR, "history.json")
HISTORY_LIMIT = 500
HISTORY_MAGIC = b"LANCHIST1"   # 9-byte magic prefix, kept simple
HISTORY_KEY = os.path.join(STATE_DIR, "history.key")

_seen_mids = set()    # mids already appended to history (inbound dedupe)


def _hist_crypto_ok() -> bool:
    if STATE.hist_crypto is None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
            STATE.hist_crypto = callable(_AESGCM)  # reference so ruff keeps the import
        except Exception:
            STATE.hist_crypto = False
    return STATE.hist_crypto


def _hist_key() -> bytes:
    """Load or generate the 32-byte AES key (0600)."""
    if os.path.exists(HISTORY_KEY):
        with open(HISTORY_KEY, "rb") as f:
            k = f.read()
        if len(k) == 32:
            return k
    k = secrets.token_bytes(32)
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".hk-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(k)
        os.chmod(tmp, 0o600)
        os.replace(tmp, HISTORY_KEY)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return k


def _history_encrypt(plain: bytes) -> str:
    """Encrypt history bytes to a base64 string for storage."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _hist_key()
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, plain, None)
    import base64
    return base64.b64encode(HISTORY_MAGIC + nonce + ct).decode("ascii")


def _history_decrypt(b64: str) -> bytes:
    """Decrypt a base64 history string back to bytes, or raise on failure."""
    import base64

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(b64)
    if raw[:9] != HISTORY_MAGIC or len(raw) < 9 + 12 + 16:
        raise ValueError("bad history blob")
    nonce = raw[9:21]
    ct = raw[21:]
    return AESGCM(_hist_key()).decrypt(nonce, ct, None)


def _history_write_bytes(data: bytes) -> None:
    """Atomically write bytes to HISTORY_PATH (binary sibling of atomic_write)."""
    d = os.path.dirname(HISTORY_PATH) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, HISTORY_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_history() -> None:
    STATE.history = []
    if not os.path.exists(HISTORY_PATH):
        return
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return
    # Try decrypted first (current format), then plaintext (pre-1.2.3).
    data = None
    if _hist_crypto_ok() and text:
        try:
            data = json.loads(_history_decrypt(text).decode("utf-8"))
        except Exception:
            data = None
    if data is None:
        try:
            data = json.loads(text)  # legacy plaintext
        except (ValueError, OSError):
            data = None
    if isinstance(data, list):
        STATE.history = data[-HISTORY_LIMIT:]
    else:
        STATE.history = []


def append_history(message: dict) -> None:
    if not message.get("mid"):
        message["mid"] = secrets.token_hex(8)
    with STATE.hist_lock:
        _seen_mids.add(message["mid"])
        STATE.history.append(message)
        if len(STATE.history) > HISTORY_LIMIT:
            STATE.history = STATE.history[-HISTORY_LIMIT:]
        _save_history_locked()


def _save_history_locked() -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        plain = json.dumps(STATE.history, separators=(",", ":")).encode("utf-8")
        if _hist_crypto_ok():
            _history_write_bytes(_history_encrypt(plain).encode("ascii"))
        else:
            _history_write_bytes(plain)
    except OSError:
        pass


def history_snapshot() -> list:
    with STATE.hist_lock:
        return list(STATE.history)


def _has_mid(mid: str) -> bool:
    """True if a message with this mid is already in history (dedupe on
    reconnect / re-delivery)."""
    with STATE.hist_lock:
        return mid in _seen_mids


def history_for_peer(peer_id: str, offset: int = 0, limit: int = 100) -> dict:
    """Lazy-load a peer's thread, newest-last, paged by offset/limit."""
    with STATE.hist_lock:
        peer_msgs = [m for m in STATE.history if (m.get("to") == peer_id or m.get("from") == peer_id)]
    total = len(peer_msgs)
    start = max(0, total - offset - limit)
    page = peer_msgs[start:max(start + limit, total - offset)] if total else []
    return {"peer": peer_id, "total": total, "messages": page}


def history_for_room(room_id: str, offset: int = 0, limit: int = 100) -> dict:
    """Room thread: messages carrying the room field (group chat), newest-last,
    paged by offset/limit — same shape as history_for_peer."""
    with STATE.hist_lock:
        room_msgs = [m for m in STATE.history if m.get("room") == room_id]
    total = len(room_msgs)
    start = max(0, total - offset - limit)
    page = room_msgs[start:max(start + limit, total - offset)] if total else []
    return {"room": room_id, "total": total, "messages": page}


def clear_history_for_peer(peer_id: str) -> int:
    with STATE.hist_lock:
        before = len(STATE.history)
        STATE.history = [m for m in STATE.history if not (m.get("to") == peer_id or m.get("from") == peer_id)]
        removed = before - len(STATE.history)
        _save_history_locked()
    return removed


def clear_all_history() -> int:
    """Clear every conversation (both sent and received messages)."""
    with STATE.hist_lock:
        removed = len(STATE.history)
        STATE.history = []
        _save_history_locked()
    return removed


def delete_message(mid: str) -> bool:
    with STATE.hist_lock:
        before = len(STATE.history)
        STATE.history = [m for m in STATE.history if m.get("mid") != mid]
        removed = before != len(STATE.history)
        if removed:
            _save_history_locked()
    return removed


def edit_message(mid: str, new_text: str) -> bool:
    """Replace a message's text (by mid). Returns True if found and edited."""
    new_text = new_text.strip()
    if not new_text:
        return False
    with STATE.hist_lock:
        for m in STATE.history:
            if m.get("mid") == mid:
                m["text"] = new_text
                m["edited"] = True
                _save_history_locked()
                return True
    return False
