#!/usr/bin/env python3
"""Group chat rooms for KelpME.lanchat.

Host-authoritative room state (membership, per-member canInvite permission,
member colors, colorsEnabled kill-switch) persisted on the OWNER's daemon,
plus a last-known cache on every member so the roster never blanks while the
host is offline (host-offline freezes management only — chat text and file
bytes are mesh and never involve the host; see plans/ROOMS.md, approved
2026-09-03; owner-transfer/co-owner/LWW is DEFERRED to a follow-up — the seq
counter is kept so that follow-up can reconcile without a format change).

All group traffic rides the existing authenticated TLS socket (no new ports,
no UDP for group state); this module owns NO transport — server.py's
_handle_incoming/handle_command call into it, and _write stays the only
send path.

Ownership: STATE.rooms (owner's authoritative dict roomId->room),
STATE.rooms_cache (member's last-known copy), STATE.rooms_lock. At-rest
crypto reuses the history module's AES-256-GCM helpers (degrades to plaintext
when `cryptography` is unavailable, same as history.json).
"""

import json
import os
import secrets
import time

# init(state) wiring: STATE is bound once by server.py at import time
# (rooms.init(STATE)). Calls into server-resident helpers (_emit, _write,
# host_id, display_name, find_peer, is_trusted, ...) use a deferred
# `import server` inside the function body — late-bound, no import cycle,
# monkeypatch-safe (same pattern as attachments.py/history.py).

STATE = None


def init(state):
    """Bind this module's STATE to the daemon's shared State instance."""
    global STATE
    STATE = state


# --------------------------------------------------------------------------
# Paths & limits
# --------------------------------------------------------------------------

STATE_DIR = os.path.join(os.path.expanduser("~"), ".local", "state", "lanchat")
ROOMS_PATH = os.path.join(STATE_DIR, "rooms.json")           # owner's authoritative copy
ROOMS_CACHE_PATH = os.path.join(STATE_DIR, "rooms-cache.json")  # member's last-known copy
ROOMS_MAGIC = b"LANCHROOM1"   # distinct magic so the two stores never decode each other
NAME_MAX = 48                 # room display-name cap


# --------------------------------------------------------------------------
# Persistence (AES-GCM at rest via the history module's helpers)
# --------------------------------------------------------------------------

def _rooms_crypto_ok() -> bool:
    """Reuse history's at-rest crypto availability check."""
    import server  # deferred, late-bound
    return server._hist_crypto_ok()


def _rooms_key() -> bytes:
    """Dedicated 32-byte key for rooms stores (0600), separate from history's."""
    import server  # deferred, late-bound
    key_path = os.path.join(STATE_DIR, "rooms.key")
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            k = f.read()
        if len(k) == 32:
            return k
    k = secrets.token_bytes(32)
    fd, tmp = secrets.token_hex(8), None
    tmp = key_path + "." + fd + ".tmp"
    with open(tmp, "wb") as f:
        f.write(k)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, key_path)
    server._log("rooms-key-generated")
    return k


def _rooms_encrypt(plain: bytes) -> str:
    if not _rooms_crypto_ok():
        return plain.decode("utf-8")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_rooms_key()).encrypt(nonce, plain, None)
    import base64
    return base64.b64encode(ROOMS_MAGIC + nonce + ct).decode("ascii")


def _rooms_decrypt(b64: str):
    """Return plaintext bytes, or None when the payload is unreadable."""
    import base64

    if not _rooms_crypto_ok():
        return b64.encode("utf-8")
    try:
        raw = base64.b64decode(b64.encode("ascii"))
    except Exception:
        return None
    if raw[:10] != ROOMS_MAGIC or len(raw) < 10 + 12 + 16:
        return None
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        return AESGCM(_rooms_key()).decrypt(raw[10:22], raw[22:], None)
    except Exception:
        return None


def _rooms_write_bytes(data: bytes, path: str) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + "." + secrets.token_hex(6) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _load_store(path: str) -> dict:
    """Load + decrypt a rooms store. Unreadable/legacy content starts fresh
    (matches load_history's resilience: a corrupt store never bricks the UI)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return {}
    data = None
    if raw.startswith(ROOMS_MAGIC.decode("ascii", "replace")) or True:
        # try JSON first (plaintext), then the encrypted envelope
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            plain = _rooms_decrypt(raw)
            if plain is not None:
                try:
                    data = json.loads(plain.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    data = None
    return data if isinstance(data, dict) else {}


def _save_store(path: str, store: dict) -> None:
    import server  # deferred, late-bound
    server.atomic_write(path, _rooms_encrypt(json.dumps(store, separators=(",", ":")).encode("utf-8")))


def load_rooms() -> None:
    """Owner: load authoritative rooms. Member: load the last-known cache."""
    STATE.rooms = _load_store(ROOMS_PATH)
    STATE.rooms_cache = _load_store(ROOMS_CACHE_PATH)


# --------------------------------------------------------------------------
# Accessors
# --------------------------------------------------------------------------

def rooms_lock():
    return STATE.rooms_lock


def my_rooms() -> dict:
    """The rooms THIS daemon knows: authoritative copy if we own any, plus
    the cached copy for rooms owned elsewhere (a daemon can be owner of some
    rooms and a plain member of others)."""
    merged = dict(STATE.rooms_cache)
    merged.update(STATE.rooms)
    return merged


def get_room(room_id: str):
    return my_rooms().get(room_id)


def room_summary(r: dict) -> dict:
    return {"roomId": r.get("roomId", ""), "name": r.get("name", ""),
            "owner": r.get("owner", ""), "seq": int(r.get("seq", 0)),
            "colorsEnabled": bool(r.get("colorsEnabled", True)),
            "memberCount": len(r.get("members", {}))}


def rooms_list() -> list:
    return [room_summary(r) for r in my_rooms().values()]


def _new_room(name: str, owner: str) -> dict:
    return {"roomId": secrets.token_hex(12), "name": str(name)[:NAME_MAX],
            "owner": owner, "created": int(time.time() * 1000), "seq": 1,
            "colorsEnabled": True,
            "members": {owner: {"name": "", "canInvite": True,
                                "color": {"token": "theme", "hex": ""}}}}


def _bump(room: dict) -> None:
    room["seq"] = int(room.get("seq", 0)) + 1


def _persist_owner() -> None:
    _save_store(ROOMS_PATH, STATE.rooms)


def _persist_cache() -> None:
    _save_store(ROOMS_CACHE_PATH, STATE.rooms_cache)


def _emit_room_list() -> None:
    import server  # deferred, late-bound
    server._emit({"event": "room-list", "rooms": rooms_list()})


def _send_room_state(room: dict, to_pid: str = "") -> None:
    """Send the authoritative snapshot to one member (or every member when
    to_pid is empty) over their friend socket. Best-effort: an offline member
    re-syncs via roomJoin/roomState on reconnect."""
    import server  # deferred, late-bound
    targets = [to_pid] if to_pid else [m for m in room.get("members", {}) if m != room.get("owner")]
    for pid in targets:
        if not pid:
            continue
        server._write(pid, {"t": "room", "kind": "roomState", "roomId": room["roomId"],
                            "from": server.host_id(), "fromName": server.display_name(),
                            "room": room})


def _broadcast_room_state(room: dict) -> None:
    _persist_owner()
    _send_room_state(room)
    # The owner's own UI needs the snapshot too (members get it on the wire).
    import server  # deferred, late-bound
    server._emit({"event": "room-state", "room": room})
    _emit_room_list()


def _err(message: str) -> None:
    import server  # deferred, late-bound
    server._emit({"event": "error", "message": message})


# --------------------------------------------------------------------------
# Owner-side state machine (every mutation validates + bumps seq + persists)
# --------------------------------------------------------------------------

def create_room(name: str) -> dict:
    import server  # deferred, late-bound
    name = (name or "").strip() or (server.display_name() + "'s room")
    room = _new_room(name, server.host_id())
    room["members"][room["owner"]]["name"] = server.display_name()
    with rooms_lock():
        STATE.rooms[room["roomId"]] = room
        _persist_owner()
    _emit_room_list()
    server._diag("room-created", roomId=room["roomId"][:12], name=name)
    return room


def _is_owner(room: dict, pid: str) -> bool:
    return room.get("owner") == pid


def owner_invite(room: dict, peer_id: str) -> bool:
    """OWNER: invite a (would-be) member. The invite rides the inviter/owner's
    authenticated socket; joining additionally requires the invitee to be a
    CONFIRMED friend of the owner (roomJoin is refused until then) so the
    authoritative roomState always has a socket."""
    import server  # deferred, late-bound
    peer = server.find_peer(peer_id)
    pname = (peer or {}).get("name") or server.friendly_name(peer_id)
    ok = server._write(peer_id, {"t": "room", "kind": "roomInvite", "roomId": room["roomId"],
                                 "from": server.host_id(), "fromName": server.display_name(),
                                 "name": room.get("name", ""), "ownerName": server.display_name()})
    if ok:
        server._diag("room-invite-sent", roomId=room["roomId"][:12], peer=peer_id[:12], name=pname)
    else:
        _err("%s is offline; they can be invited once their connection returns" % pname)
    return ok


def owner_admit(room: dict, peer_id: str, peer_name: str = "") -> bool:
    """OWNER: add a confirmed member (after roomJoin). Idempotent."""
    import server  # deferred, late-bound
    peer = server.find_peer(peer_id)
    pname = peer_name or (peer or {}).get("name") or server.friendly_name(peer_id)
    with rooms_lock():
        if peer_id not in room["members"]:
            room["members"][peer_id] = {"name": pname, "canInvite": False,
                                        "color": {"token": "theme", "hex": ""}}
            _bump(room)
        _persist_owner()
    # Broadcast members + emit the snapshot to the OWNER's own UI too —
    # without the local emit the roster never re-renders and the drag-add
    # looks like a no-op even though the member joined (verified live).
    _send_room_state(room)
    server._emit({"event": "room-state", "room": room})
    _emit_room_list()
    server._diag("room-member-added", roomId=room["roomId"][:12], peer=peer_id[:12], name=pname)
    return True


def owner_remove(room: dict, peer_id: str) -> bool:
    """OWNER: kick a member. The removed peer is told so their copy drops."""
    import server  # deferred, late-bound
    if peer_id == room.get("owner"):
        return False
    with rooms_lock():
        if peer_id not in room["members"]:
            return False
        del room["members"][peer_id]
        _bump(room)
        _persist_owner()
    server._write(peer_id, {"t": "room", "kind": "roomRemove", "roomId": room["roomId"],
                            "from": server.host_id(), "fromName": server.display_name()})
    _send_room_state(room)
    server._emit({"event": "room-state", "room": room})
    _emit_room_list()
    server._diag("room-member-removed", roomId=room["roomId"][:12], peer=peer_id[:12])
    return True


def owner_set_can_invite(room: dict, peer_id: str, allowed: bool) -> bool:
    """OWNER: per-member add-people permission toggle."""
    import server  # deferred, late-bound
    if peer_id == room.get("owner") or peer_id not in room.get("members", {}):
        return False
    with rooms_lock():
        room["members"][peer_id]["canInvite"] = bool(allowed)
        _bump(room)
        _persist_owner()
    _send_room_state(room)
    server._emit({"event": "room-state", "room": room})
    _emit_room_list()
    server._diag("room-perm-changed", roomId=room["roomId"][:12], peer=peer_id[:12], canInvite=bool(allowed))
    return True


def member_set_color(room: dict, peer_id: str, token: str, hexv: str) -> bool:
    """A member (or the owner themselves) sets THEIR color: a palette token
    (or the literal 'theme' = match my theme accent) + the current resolved
    hex so every viewer renders the identical color. The owner validates and
    rebroadcasts."""
    import server  # deferred, late-bound
    if peer_id not in room.get("members", {}):
        return False
    token = str(token or "theme")[:32]
    hexv = str(hexv or "")
    if token != "theme" and not (hexv.startswith("#") and len(hexv) in (4, 7)):
        return False
    with rooms_lock():
        room["members"][peer_id]["color"] = {"token": token, "hex": hexv}
        _bump(room)
        if _is_owner(room, peer_id):
            _persist_owner()
        else:
            STATE.rooms_cache[room["roomId"]] = room
            _persist_cache()
    _send_room_state(room)
    server._emit({"event": "room-state", "room": room})
    _emit_room_list()
    server._diag("room-color-set", roomId=room["roomId"][:12], peer=peer_id[:12], token=token)
    return True


def owner_toggle_colors(room: dict, enabled: bool) -> bool:
    """OWNER kill-switch: when off, room rendering falls back to the standard
    theme palette (approved decision #3)."""
    import server  # deferred, late-bound
    with rooms_lock():
        room["colorsEnabled"] = bool(enabled)
        _bump(room)
        _persist_owner()
    _send_room_state(room)
    server._emit({"event": "room-state", "room": room})
    _emit_room_list()
    return True


def member_leave(room: dict, peer_id: str) -> bool:
    """A member (or the owner) leaves. Owner leaving DISBANDS the room for
    everyone (v1 has no owner-transfer to hand it to — approved)."""
    import server  # deferred, late-bound
    with rooms_lock():
        if peer_id not in room.get("members", {}):
            return False
        was_owner = _is_owner(room, peer_id)
        del room["members"][peer_id]
        _bump(room)
        if was_owner or not room["members"]:
            STATE.rooms.pop(room["roomId"], None)
            _persist_owner()
            remaining = list(room.get("members", {}).keys())
        else:
            STATE.rooms_cache[room["roomId"]] = room
            _persist_cache()
            remaining = []
    if was_owner or not room["members"]:
        # Tell the rest the room is gone.
        for pid in remaining:
            server._write(pid, {"t": "room", "kind": "roomRemove", "roomId": room["roomId"],
                                "from": server.host_id(), "fromName": server.display_name()})
    else:
        _send_room_state(room)
    _emit_room_list()
    server._diag("room-left", roomId=room["roomId"][:12], peer=peer_id[:12], owner=was_owner)
    return True


# --------------------------------------------------------------------------
# Inbound wire kinds (t:"room") — server.py's _handle_incoming calls these
# --------------------------------------------------------------------------

def handle_room_msg(msg: dict, addr) -> None:
    """Route an inbound t:"room" envelope. The connection is already
    authenticated (from = proven fingerprint), same trust level as chat."""
    import server  # deferred, late-bound
    kind = str(msg.get("kind", ""))
    from_pid = str(msg.get("from", ""))
    room_id = str(msg.get("roomId", ""))
    if not room_id:
        return
    if kind == "roomState":
        # Authoritative snapshot from the room owner: replace our cache copy
        # unless ours is somehow newer (strict LWW on seq for the deferred
        # owner-transfer follow-up).
        room = msg.get("room")
        if not isinstance(room, dict) or str(room.get("roomId", "")) != room_id:
            return
        with rooms_lock():
            mine = STATE.rooms_cache.get(room_id)
            if STATE.rooms.get(room_id) is not None:
                return  # we are the owner; our copy is authoritative
            if mine and int(mine.get("seq", 0)) > int(room.get("seq", 0)):
                return
            STATE.rooms_cache[room_id] = room
            _persist_cache()
        _emit_room_list()
        server._emit({"event": "room-state", "room": room})
        server._diag("room-state-synced", roomId=room_id[:12], seq=room.get("seq"))
        return
    if kind == "roomInvite":
        # Owner inviting us. Seed our cache with the stub (roomId/name/owner)
        # so the UI can render the invite AND roomJoin can address the owner
        # — the authoritative roomState replaces this stub on join.
        with rooms_lock():
            if room_id not in STATE.rooms_cache and room_id not in STATE.rooms:
                STATE.rooms_cache[room_id] = {
                    "roomId": room_id, "name": str(msg.get("name", "")),
                    "owner": from_pid, "created": int(time.time() * 1000),
                    "seq": 0, "colorsEnabled": True, "members": {}}
                _persist_cache()
        _emit_room_list()
        server._emit({"event": "room-invite", "roomId": room_id, "name": str(msg.get("name", "")),
                      "from": from_pid, "fromName": str(msg.get("fromName") or server.friendly_name(from_pid))})
        return
    if kind == "roomFile":
        # Room-file metadata. The OWNER rebroadcasts to every member
        # (authoritative fan-out, approved decision #2); a member renders the
        # offer bubble. Bytes stay sender-served (attachmentRequest flow).
        room = STATE.rooms.get(room_id)
        if room is not None and _is_owner(room, server.host_id()):
            fan_out_room_file(room, msg)
        else:
            handle_room_file_msg(msg, addr)
        return
    if kind == "roomJoin":
        # Only the owner processes joins, and only for a room they own.
        room = STATE.rooms.get(room_id)
        if room is None or not _is_owner(room, server.host_id()):
            return
        # Admission rule (approved decision #1): every member must be a
        # CONFIRMED friend of the owner so authoritative roomState has a
        # socket; a join from a non-friend is refused.
        if not server.is_trusted(from_pid):
            _err("Cannot add %s — befriend each other first" %
                 (server.find_peer(from_pid) or {}).get("name", server.friendly_name(from_pid)))
            return
        owner_admit(room, from_pid, str(msg.get("fromName") or ""))
        return
    if kind == "roomAdd":
        # A canInvite member proposes an add; the OWNER executes it
        # (approved decision #1). Non-owner rooms ignore this.
        room = STATE.rooms.get(room_id)
        if room is None or not _is_owner(room, server.host_id()):
            return
        member = room.get("members", {}).get(from_pid)
        if not member or not member.get("canInvite"):
            _err("You do not have permission to add people to this room")
            return
        peer_id = str(msg.get("peer", ""))
        if not peer_id or not server.is_trusted(peer_id):
            _err("Cannot add %s — befriend each other first" %
                 (server.find_peer(peer_id) or {}).get("name", server.friendly_name(peer_id)))
            return
        owner_invite(room, peer_id)
        return
    if kind == "roomRemove":
        # We were removed (or the room was disbanded by the owner leaving).
        with rooms_lock():
            removed = STATE.rooms_cache.pop(room_id, None)
            _persist_cache()
        if removed is not None:
            _emit_room_list()
            server._diag("room-removed-locally", roomId=room_id[:12])
        return
    if kind == "roomFileStatus":
        # Per-recipient delivery report, straight to the file's sender (mesh):
        # the UI shows saved/error per member on the file bubble. Daemon-driven,
        # never client-guessed.
        server._emit({"event": "room-file-status", "roomId": room_id,
                      "fileId": str(msg.get("fileId", "")), "mid": str(msg.get("mid", "")),
                      "peer": from_pid,
                      "peerName": str(msg.get("fromName") or server.friendly_name(from_pid)),
                      "status": str(msg.get("status", "")), "error": str(msg.get("error", ""))})
        return


# --------------------------------------------------------------------------
# Room-file metadata fan-out (Option A: sender-served bytes, metadata rides
# the owner's authoritative channel — approved decision #2)
# --------------------------------------------------------------------------

def post_room_file(room_id: str, att: dict) -> bool:
    """Sender side: register the attachment (done by caller via
    attachments.register_attachment) and hand the metadata to the owner for
    authoritative fan-out. When WE are the owner, fan out directly."""
    import server  # deferred, late-bound
    room = STATE.rooms.get(room_id) or STATE.rooms_cache.get(room_id)
    if room is None:
        _err("Room not found")
        return False
    envelope = {"t": "room", "kind": "roomFile", "roomId": room_id,
                "from": server.host_id(), "fromName": server.display_name(),
                "att": att}
    owner = room.get("owner")
    if owner == server.host_id():
        fan_out_room_file(room, envelope)
        return True
    if not server._write(owner, envelope):
        _err("host offline — changes frozen")
        return False
    # Show our own copy immediately (the owner's broadcast won't echo to us).
    emit_room_file_local(room, envelope)
    return True


def fan_out_room_file(room: dict, envelope: dict) -> None:
    """OWNER: broadcast file metadata to ALL members (authoritative room
    event) and show it locally. Members who are not friends of the sender
    still SEE the offer — that is what makes the befriend-to-accept notice
    reachable (approved decision #2)."""
    import server  # deferred, late-bound
    for pid in room.get("members", {}):
        if pid == server.host_id():
            continue
        server._write(pid, dict(envelope))
    emit_room_file_local(room, envelope)


def emit_room_file_local(room: dict, envelope: dict) -> None:
    """Show the room-file metadata as a room message bubble locally (the same
    shape a recipient gets from the inbound path)."""
    import server  # deferred, late-bound
    att = envelope.get("att") or {}
    message = {
        "from": envelope.get("from", ""), "fromName": envelope.get("fromName", ""),
        "text": "", "ts": int(time.time() * 1000), "outgoing": envelope.get("from") == server.host_id(),
        "mid": str(att.get("mid") or secrets.token_hex(8)), "room": room.get("roomId", ""),
        "attachment": att,
    }
    server.append_history(message)
    server._emit({"event": "message", "message": message})


def handle_room_file_msg(msg: dict, addr) -> None:
    """Inbound roomFile metadata (from the owner's authoritative broadcast).
    Carried as a room message bubble; bytes are NOT here — they are pulled
    from the original sender via the existing attachmentRequest flow."""
    import server  # deferred, late-bound
    att = msg.get("att")
    if not isinstance(att, dict):
        return
    room_id = str(msg.get("roomId", ""))
    message = {
        "from": str(msg.get("from", "")),
        "fromName": str(msg.get("fromName") or server.friendly_name(msg.get("from", ""))),
        "text": "", "ts": int(time.time() * 1000), "outgoing": False,
        "mid": str(att.get("mid") or secrets.token_hex(8)), "room": room_id,
        "attachment": att,
    }
    append_room_message(message)


def append_room_message(message: dict) -> None:
    """Persist + show an inbound room message (text or file metadata)."""
    import server  # deferred, late-bound
    server.append_history(message)
    server._emit({"event": "message", "message": message})


def report_file_status(room_id: str, sender_pid: str, att: dict, status: str, error: str = "") -> None:
    """Recipient → sender (mesh): per-recipient delivery report for a room
    file. Only possible for someone who could pull (a friend of the sender),
    so this always has a socket."""
    import server  # deferred, late-bound
    server._write(sender_pid, {"t": "room", "kind": "roomFileStatus", "roomId": room_id,
                               "from": server.host_id(), "fromName": server.display_name(),
                               "fileId": str(att.get("fileId", "")), "mid": str(att.get("mid", "")),
                               "status": status, "error": error})


# --------------------------------------------------------------------------
# Mesh text fan-out (approved decision #4: text never routes through the host)
# --------------------------------------------------------------------------

def send_room_text(room_id: str, text: str) -> bool:
    """Fan a room text message out to every member we have a confirmed friend
    socket to. Held-on-offline (existing _write behavior) applies per member.
    The host is NOT in the path."""
    import server  # deferred, late-bound
    room = STATE.rooms.get(room_id) or STATE.rooms_cache.get(room_id)
    if room is None:
        _err("Room not found")
        return False
    if not str(text or "").strip():
        return False
    msg = {"t": "msg", "from": server.host_id(), "fromName": server.display_name(),
           "text": str(text), "ts": int(time.time() * 1000), "outgoing": True,
           "mid": secrets.token_hex(8), "room": room_id}
    delivered_any = False
    for pid in room.get("members", {}):
        if pid == server.host_id():
            continue
        if server._write(pid, msg):
            delivered_any = True
    append_room_message(msg)
    server._diag("room-text-sent", roomId=room_id[:12], to=len(room.get("members", {})) - 1,
                 delivered=delivered_any)
    return True
