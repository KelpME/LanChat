#!/usr/bin/env python3
"""Attachments for kelpme.lanchat — extracted verbatim from server.py (Commit 2).

Attachment registration (metadata + TTL) and peer-to-peer file transfer: files
are carried over the SAME persistent TLS socket as messages (the peer is
already authenticated by the connect handshake), NOT over a separate HTTP
server. This removes the HTTP / loopback-bind / port dependency that made
cross-LAN saves fail: no new port, no LAN bind, no token in a URL, no extra
firewall rule, and no separate cert-pinning path — the socket transport's
identity proof covers the file bytes too. The sender streams the registered
file as base64 chunks; the recipient reassembles to downloadDir, verifies the
sha256, and renames atomically.

Wire messages (all over the friend's active socket, `from`/`to` like any msg):
  attachmentRequest  recipient -> sender : {fileId, mid}
  attachmentChunk    sender   -> recipient: {fileId, mid, seq, total, data=b64}
  attachmentEnd      sender   -> recipient: {fileId, mid, total}
  attachmentError    either   -> other   : {fileId, mid, error}

Ownership: STATE.attachments, STATE.att_lock, STATE.dl_lock, and the
module-private _dl reassembly dict (the fields stay on State; this module
operates on them through the shared State instance). server.py re-exports
this module's symbols, so callers keep calling them via server.
"""

import base64
import hashlib
import os
import time

# init(state) wiring: STATE is bound once by server.py at import time
# (attachments.init(STATE)). Calls into server-resident helpers (_emit, _log,
# _write, status, host_id, get_attachment's callers, ...) use a deferred
# `import server` inside the function body — late-bound, no import cycle,
# monkeypatch-safe.

STATE = None


def init(state):
    """Bind this module's STATE to the daemon's shared State instance."""
    global STATE
    STATE = state


# raw bytes per chunk; base64 ~1.33x, well under MAX_FRAME_BUF
ATT_CHUNK_RAW = 128 * 1024

# Recipient-side limits: a hard ceiling on any single attachment and on how
# many transfers may reassemble at once. A trusted-but-malicious friend could
# otherwise stream chunks until the recipient's disk is exhausted.
ATT_MAX_BYTES = 100 * 1024 * 1024 * 1024
ATT_MAX_CONCURRENT = 8
ATT_MAX_PER_PEER = 2

# Registration-TTL scaling: 10 min base, +10 min per 10 GiB, 1 h ceiling.
# A pull registration must outlive however long the recipient needs to click
# Save on a huge file; transfers in flight are not killed by this expiry.
ATT_TTL_BASE_S = 600.0
ATT_TTL_STEP_S = 600.0
ATT_TTL_SCALE_BYTES = 10 * 1024 * 1024 * 1024
ATT_TTL_MAX_S = 3600.0

# Recipient-side reassembly state, keyed by fileId.
_dl = {}                 # fileId -> {save_to,tmp,fh,mid,sha256,total,written,peer,ts}
# Last-known pull peer per fileId, remembered past _dl_finish pop so the
# room-file delivery report can address the sender after completion.
_last_dl_peer = {}

# Refusal reason from the most recent _dl_begin call: "per-peer",
# "max-concurrent", "open-failed", or None on success. Read (right after a
# False return) by server.acceptAttachment to send the right error.
last_refusal_reason = None

_DL_TTL_S = 600.0


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


def register_attachment(file_id: str, path: str, name: str, ttl: float = 0.0) -> bool:
    """Register a local file for peer pull. Refuses (returns False) when the
    file exceeds ATT_MAX_BYTES — callers ignore the return value today, so the
    refusal must stay raise-free and silent-but-logged.

    ttl=0 (the default) scales the registration window with file size: the
    pull must start AND the sender must keep serving while the recipient's
    Save runs, so a 100 GiB file needs hours, not the 600s of a small drop.
    An explicit ttl (tests, callers that know better) always wins."""
    import server
    try:
        size = os.stat(path).st_size
    except OSError:
        size = 0  # same tolerance as the send/roomFile callers (size 0)
    if size > ATT_MAX_BYTES:
        server._log("attachment-register-refused file=%s size=%d max=%d"
                    % (os.path.basename(path), size, ATT_MAX_BYTES))
        return False
    if ttl <= 0:
        # Base 600s covers small files; every ATT_TTL_SCALE_BYTES beyond the
        # first buys one more ATT_TTL_STEP_S, capped at ATT_TTL_MAX_S. At the
        # 100 GiB ceiling that lands on the cap (1h) — enough to start the
        # pull comfortably; a running transfer is never killed mid-stream by
        # this expiry (only the initial attachmentRequest lookup uses it).
        steps = max(0, size - ATT_CHUNK_RAW) // ATT_TTL_SCALE_BYTES
        ttl = min(ATT_TTL_BASE_S + steps * ATT_TTL_STEP_S, ATT_TTL_MAX_S)
    with STATE.att_lock:
        STATE.attachments[file_id] = {"path": path, "name": name, "expires": time.time() + ttl}
    return True


def get_attachment(file_id: str):
    with STATE.att_lock:
        a = STATE.attachments.get(file_id)
        if a and a["expires"] > time.time():
            return a
        return None


def _dl_begin(file_id: str, peer_id: str, save_to: str, sha256: str, mid: str, room: str = "") -> bool:
    """Register an in-progress download and open its .part file. Purges any
    stale transfer first. room = the room id for a room-file pull
    (used to report delivery status back to the sender). Returns True on
    success, False on any failure (including the transfer caps). On False,
    last_refusal_reason holds "per-peer", "max-concurrent", or "open-failed"
    so callers can distinguish a busy-busy refusal from a local I/O error."""
    global last_refusal_reason
    now = time.time()
    with STATE.dl_lock:
        for fid in list(_dl):
            if now - _dl[fid]["ts"] > _DL_TTL_S:
                old = _dl.pop(fid)
                try:
                    old["fh"].close()
                except OSError:
                    pass
                _remove_file(old["tmp"])
        peer_count = sum(1 for e in _dl.values() if e.get("peer") == peer_id)
        if peer_count >= ATT_MAX_PER_PEER:
            import server
            last_refusal_reason = "per-peer"
            server._log("attachment-dl-refused file=%s peer=%s reason=per-peer"
                        % (file_id[:12], peer_id[:12]))
            return False
        if len(_dl) >= ATT_MAX_CONCURRENT:
            import server
            last_refusal_reason = "max-concurrent"
            server._log("attachment-dl-refused file=%s peer=%s reason=max-concurrent"
                        % (file_id[:12], peer_id[:12]))
            return False
        try:
            os.makedirs(os.path.dirname(save_to) or ".", exist_ok=True)
        except OSError:
            last_refusal_reason = "open-failed"
            return False
        tmp = save_to + ".part"
        try:
            fh = open(tmp, "wb")
        except OSError:
            last_refusal_reason = "open-failed"
            return False
        last_refusal_reason = None
        _dl[file_id] = {"save_to": save_to, "tmp": tmp, "fh": fh, "mid": mid,
                        "sha256": sha256, "total": 0, "written": 0,
                        "peer": peer_id, "ts": now, "room": room}
        _last_dl_peer[file_id] = peer_id
    return True


def _dl_chunk(file_id: str, peer_id: str, data_b64: str, total: int):
    """Decode + append one chunk from the sender. Returns (ok, total, written).

    Enforces ATT_MAX_BYTES two ways: a declared total over the ceiling aborts
    immediately, and the cumulative write can never pass the ceiling (the
    backstop for a sender that lies about, or never sends, the total)."""
    with STATE.dl_lock:
        d = _dl.get(file_id)
        if not d or d.get("peer") != peer_id:
            return (False, 0, 0)
        if total:
            if total > ATT_MAX_BYTES:
                _dl_abort_locked(file_id, d)
                return (False, total, d["written"])
            d["total"] = total
        try:
            raw = base64.b64decode(data_b64)
        except Exception:
            return (False, d["total"], d["written"])
        if d["written"] + len(raw) > ATT_MAX_BYTES:
            _dl_abort_locked(file_id, d)
            return (False, d["total"], d["written"])
        if d["total"] and d["written"] + len(raw) > d["total"]:
            _dl_abort_locked(file_id, d)
            return (False, d["total"], d["written"])
        try:
            d["fh"].write(raw)
        except OSError:
            # Write failed — usually disk-full on a huge transfer. Tear the
            # transfer down now (same as a size-limit abort) instead of
            # leaving a possibly-gigantic .part for the TTL purge to collect.
            _dl_abort_locked(file_id, d)
            return (False, d["total"], d["written"])
        d["written"] += len(raw)
        d["ts"] = time.time()
        return (True, d["total"], d["written"])


def _dl_abort_locked(file_id: str, d: dict) -> None:
    """Tear down a transfer whose chunk crossed a size limit. Caller holds
    STATE.dl_lock. Closes the .part handle, removes the partial file, and pops
    the entry so the sender cannot keep streaming into it."""
    _dl.pop(file_id, None)
    try:
        d["fh"].close()
    except OSError:
        pass
    _remove_file(d["tmp"])
    import server
    server._log("attachment-dl-aborted file=%s peer=%s reason=size-limit"
                % (file_id[:12], d.get("peer", "")[:12]))


def refusal_error(reason):
    """Map a _dl_begin refusal reason to (peerError, uiError). Busy refusals
    (per-peer / global cap) are transient and get a distinct, retryable
    message; local I/O failures keep the historical strings."""
    if reason in ("per-peer", "max-concurrent"):
        return ("busy", "attachment transfer busy — try again shortly")
    return ("cannot open download file", "cannot open download file")


def _dl_finish(file_id: str, peer_id: str, ok: bool):
    """Complete (or abort) a transfer. Returns (status, save_to, mid, total, room):
    status in ('saved','mismatch','aborted'), or None if the transfer was not
    registered for this peer (unknown/stale). room = the room id of a room-file
    pull ('' for a plain 1:1 attachment)."""
    with STATE.dl_lock:
        d = _dl.pop(file_id, None)
    if not d or d.get("peer") != peer_id:
        return None
    try:
        d["fh"].close()
    except OSError:
        pass
    save_to, tmp = d["save_to"], d["tmp"]
    if not ok:
        _remove_file(tmp)
        return ("aborted", save_to, d["mid"], d["total"], d.get("room", ""))
    # Completeness: if the sender told us the total size, require every byte.
    # A dropped trailing chunk would otherwise go unnoticed even if it hashed
    # the same (extremely unlikely) — this is the size half of the check.
    if d["total"] > 0 and d["written"] != d["total"]:
        _remove_file(tmp)
        return ("incomplete", save_to, d["mid"], d["total"], d.get("room", ""))
    digest = _file_sha256(tmp)
    if d["sha256"] and digest != d["sha256"]:
        _remove_file(tmp)
        return ("mismatch", save_to, d["mid"], d["total"], d.get("room", ""))
    try:
        os.replace(tmp, save_to)
    except OSError:
        _remove_file(tmp)
        return ("aborted", save_to, d["mid"], d["total"], d.get("room", ""))
    return ("saved", save_to, d["mid"], d["total"], d.get("room", ""))


def _finalize_download(res, mid: str, file_id: str, error: str = "") -> None:
    """Map a _dl_finish result to the attachment-saved event the UI watches."""
    if res is None:
        return  # not our transfer (unknown/stale/other peer)
    status, save_to, _mid, _total, room_id = res
    if status == "saved":
        import server
        server._log("attachment-saved file=%s" % os.path.basename(save_to))
        server._emit({"event": "attachment-saved", "ok": True, "path": save_to,
               "mid": mid, "fileId": file_id})
        # Persist the accepted flag so the UI's pending-accept bar stays gone
        # after a history reload (deferred import — server.py runs as
        # __main__ and aliases sys.modules["server"] to itself).
        server.history.mark_attachment_saved(mid)
        # Room file: report the delivery back to the SENDER (mesh) so their UI
        # shows ✓ saved for this member. Daemon-driven per-member status, never
        # client-guessed. Only fires for a room-file pull (room id recorded on
        # the download).
        if room_id:
            server.rooms.report_file_status(room_id, _dl_peer(file_id),
                                            {"fileId": file_id, "mid": mid}, "saved")
    else:
        if status == "mismatch":
            msg = error or "checksum mismatch"
        elif status == "incomplete":
            msg = error or "incomplete transfer (bytes missing)"
        else:
            msg = error or "transfer aborted"
        import server
        server._emit({"event": "attachment-saved", "ok": False, "path": save_to,
               "mid": mid, "fileId": file_id, "error": msg})


def _dl_peer(file_id: str) -> str:
    """The peer a (just-finished) download was pulling from. _finalize_download
    calls this after _dl_finish popped the entry, so keep a last-peer memo."""
    with STATE.dl_lock:
        return _last_dl_peer.get(file_id, "")


def _serve_attachment(peer_id: str, file_id: str, mid: str) -> None:
    """Sender: stream a registered file to the requesting peer over the socket."""
    import server
    att = get_attachment(file_id)
    if not att:
        server._write(peer_id, {"t": "attachmentError", "from": server.host_id(), "to": peer_id,
                         "fileId": file_id, "mid": mid, "error": "not found"})
        return
    path, name = att["path"], att["name"]
    try:
        size = os.path.getsize(path)
    except OSError:
        server._write(peer_id, {"t": "attachmentError", "from": server.host_id(), "to": peer_id,
                         "fileId": file_id, "mid": mid, "error": "file missing"})
        return
    try:
        with open(path, "rb") as f:
            seq = 0
            while True:
                chunk = f.read(ATT_CHUNK_RAW)
                if not chunk:
                    break
                ok = server._write(peer_id, {
                    "t": "attachmentChunk", "from": server.host_id(), "to": peer_id,
                    "fileId": file_id, "mid": mid, "seq": seq, "total": size,
                    "data": base64.b64encode(chunk).decode("ascii")})
                if not ok:
                    server._log("attachment-stream-aborted peer=%s file=%s err=socket-down"
                         % (peer_id[:12], name))
                    return
                seq += 1
        server._write(peer_id, {"t": "attachmentEnd", "from": server.host_id(), "to": peer_id,
                         "fileId": file_id, "mid": mid, "total": size})
        server._log("attachment-streamed peer=%s file=%s size=%s" % (peer_id[:12], name, size))
    except OSError as e:
        server._log("attachment-stream-failed peer=%s file=%s err=%s" % (peer_id[:12], name, e))
        server._write(peer_id, {"t": "attachmentError", "from": server.host_id(), "to": peer_id,
                         "fileId": file_id, "mid": mid, "error": str(e)})
