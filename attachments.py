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

# ---- Attachment disk limits -------------------------------------------------
# A trusted-but-malicious friend must never be able to exhaust the recipient's
# disk, so the recipient enforces three independent gates: a conservative
# PER-FILE ceiling, an AGGREGATE budget across all active reassemblies, and a
# FREE-SPACE FLOOR measured on the destination filesystem — checked before the
# transfer is opened AND again while it writes.
#
# Each byte limit is overridable from ~/.config/omarchy/lanchat.json (see
# ATT_CONFIG_KEYS + apply_config_limits, which server.load_config calls).
# Every gate below reads the module attribute at CALL time, and tests may
# rebind it, so a change takes effect everywhere without touching the code.
# A config override may RAISE a limit, never remove one: apply_config_limits
# clamps every value into [floor, ATT_LIMIT_HARD_MAX].

# Per-file ceiling. Conservative on purpose: 100 GiB was a number only a
# dedicated file server needs, and it made the aggregate budget theoretical
# (one transfer could sit under a 64 GiB budget while still being huge).
ATT_MAX_BYTES_DEFAULT = 4 * 1024 * 1024 * 1024
ATT_MAX_BYTES = ATT_MAX_BYTES_DEFAULT

# Aggregate disk budget across ALL active reassemblies: the sum of reserved
# space (declared total where known, bytes actually written otherwise) may
# never exceed this. One peer can therefore never hold more than
# ATT_MAX_PER_PEER * ATT_MAX_BYTES, and the board can never promise more disk
# than the machine is willing to give attachments.
ATT_MAX_RESERVED_BYTES_DEFAULT = 8 * 1024 * 1024 * 1024
ATT_MAX_RESERVED_BYTES = ATT_MAX_RESERVED_BYTES_DEFAULT

# Keep at least this much free on the destination filesystem at all times —
# the safety floor that stops attachments from eating the last slice of the
# user's disk. Checked at begin AND during the write (see
# ATT_DISK_CHECK_INTERVAL_BYTES), so a transfer that starts on a healthy disk
# is torn down before it lands on the floor.
ATT_MIN_FREE_BYTES_DEFAULT = 4 * 1024 * 1024 * 1024
ATT_MIN_FREE_BYTES = ATT_MIN_FREE_BYTES_DEFAULT

# How often (bytes written) the free-space floor is re-checked mid-transfer.
# statvfs is cheap; chunk sizes are 128 KiB, so this is ~1 probe per 64 chunks.
ATT_DISK_CHECK_INTERVAL_BYTES = 8 * 1024 * 1024

# Transfer-count caps (not disk-sized, so not config-overridable).
ATT_MAX_CONCURRENT = 8
ATT_MAX_PER_PEER = 2

# Absolute bounds for a config override. An override can loosen a default; it
# can never switch a gate off or set a nonsense value. Anything outside this
# range is refused (the current value is kept).
ATT_LIMIT_MIN = 64 * 1024 * 1024                    # 64 MiB floor
ATT_LIMIT_HARD_MAX = 1024 * 1024 * 1024 * 1024      # 1 TiB ceiling

# (config key -> module attribute) pairs applied by apply_config_limits.
ATT_CONFIG_KEYS = (
    ("attachmentMaxBytes", "ATT_MAX_BYTES"),
    ("attachmentMaxReservedBytes", "ATT_MAX_RESERVED_BYTES"),
    ("attachmentMinFreeBytes", "ATT_MIN_FREE_BYTES"),
)

# How long a transfer may run with no declared total at all. The very first
# chunk from the real sender carries the total; this grace window only
# tolerates a request/first-chunk race, so it is short.
ATT_TOTAL_GRACE_S = 10.0

# Registration-TTL scaling: 10 min base, +10 min per scaling step, 1 h ceiling.
# A pull registration must outlive however long the recipient needs to click
# Save on a huge file; transfers in flight are not killed by this expiry.
# The step size DERIVES from the per-file ceiling (see register_attachment) so
# a file at whatever ceiling is in force — the conservative default or a config
# override — always scales toward the ceiling instead of freezing at base.
ATT_TTL_BASE_S = 600.0
ATT_TTL_STEP_S = 600.0
ATT_TTL_MIN_SCALE_BYTES = 512 * 1024 * 1024   # never scale finer than 512 MiB
ATT_TTL_MAX_S = 3600.0
# Steps from base to ceiling: (ATT_TTL_MAX_S - ATT_TTL_BASE_S) / ATT_TTL_STEP_S.
# The scale is derived so a file at the current per-file ceiling lands exactly
# on the ceiling TTL.
ATT_TTL_CAP_STEPS = int((ATT_TTL_MAX_S - ATT_TTL_BASE_S) // ATT_TTL_STEP_S)

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


def _registration_ttl(size: int) -> float:
    """Registration window for a file of `size` bytes: 600s base, one
    ATT_TTL_STEP_S per scaling step, capped at ATT_TTL_MAX_S.

    The step size is the per-file ceiling split into ATT_TTL_CAP_STEPS steps
    (never finer than ATT_TTL_MIN_SCALE_BYTES), so a file at whatever ceiling is
    in force — the conservative default or a raised config override — scales
    onto the 1 h cap instead of freezing at base. Extracted so the scaling is
    testable against arbitrary sizes without writing gigabytes to disk."""
    scale = max(ATT_TTL_MIN_SCALE_BYTES, ATT_MAX_BYTES // ATT_TTL_CAP_STEPS)
    steps = ((size + scale - 1) // scale) if size > ATT_CHUNK_RAW else 0
    return min(ATT_TTL_BASE_S + steps * ATT_TTL_STEP_S, ATT_TTL_MAX_S)


def register_attachment(file_id: str, path: str, name: str, ttl: float = 0.0) -> bool:
    """Register a local file for peer pull. Refuses (returns False) when the
    file exceeds ATT_MAX_BYTES — callers ignore the return value today, so the
    refusal must stay raise-free and silent-but-logged.

    ttl=0 (the default) scales the registration window with file size: the
    pull must start AND the sender must keep serving while the recipient's
    Save runs, so a multi-gigabyte file needs longer than the 600s of a small
    drop. An explicit ttl (tests, callers that know better) always wins."""
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
        # Base 600s covers small files; every scaling step beyond the first
        # buys one more ATT_TTL_STEP_S, capped at ATT_TTL_MAX_S. The step size
        # is the per-file ceiling divided into ~ATT_TTL_CAP_STEPS steps, so a
        # file at whatever ceiling is in force — the conservative 4 GiB default
        # or a raised config override — scales onto the 1 h cap instead of
        # freezing at base. A running transfer is never killed mid-stream by
        # this expiry (only the initial attachmentRequest lookup uses it).
        scale = max(ATT_TTL_MIN_SCALE_BYTES, ATT_MAX_BYTES // ATT_TTL_CAP_STEPS)
        steps = ((size + scale - 1) // scale) if size > ATT_CHUNK_RAW else 0
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


def fmt_bytes(n: int) -> str:
    """Human-readable byte count for limit messages (4 GiB, 512 MiB, 800 KB)."""
    n = int(n)
    for unit, size in (("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KB", 1024)):
        if n >= size and n % size == 0:
            return "%d %s" % (n // size, unit)
    if n >= 1024 ** 3:
        return "%.1f GiB" % (n / 1024 ** 3)
    if n >= 1024 ** 2:
        return "%.1f MiB" % (n / 1024 ** 2)
    return "%d B" % n


def apply_config_limits(config: dict) -> dict:
    """Adopt the attachment disk limits from the daemon config.

    Called by server.load_config() so ~/.config/omarchy/lanchat.json can raise
    or lower the limits on a machine that legitimately needs bigger files:

        "attachmentMaxBytes":         17179869184   # per-file ceiling
        "attachmentMaxReservedBytes": 34359738368   # aggregate budget
        "attachmentMinFreeBytes":      8589934592   # keep-free safety floor

    Fail-closed by construction: a missing/invalid/non-numeric value keeps the
    current value, and every accepted value is clamped into
    [ATT_LIMIT_MIN, ATT_LIMIT_HARD_MAX]. An override can move a limit; it can
    never disable a gate. Returns the effective values so the daemon log (and
    any test) can see exactly what is being enforced."""
    global ATT_MAX_BYTES, ATT_MAX_RESERVED_BYTES, ATT_MIN_FREE_BYTES
    limits = {"ATT_MAX_BYTES": ATT_MAX_BYTES,
              "ATT_MAX_RESERVED_BYTES": ATT_MAX_RESERVED_BYTES,
              "ATT_MIN_FREE_BYTES": ATT_MIN_FREE_BYTES}
    for cfg_key, attr in ATT_CONFIG_KEYS:
        raw = config.get(cfg_key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue  # absent or wrong type -> keep the current value
        value = int(raw)
        if value < ATT_LIMIT_MIN:
            continue  # zero/negative/tiny means "no limit" to a caller: refused
        limits[attr] = min(value, ATT_LIMIT_HARD_MAX)
    # Keep the gates sane relative to each other: a per-file ceiling bigger
    # than the aggregate budget would make the budget unenforceable.
    if limits["ATT_MAX_BYTES"] > limits["ATT_MAX_RESERVED_BYTES"]:
        limits["ATT_MAX_BYTES"] = limits["ATT_MAX_RESERVED_BYTES"]
    ATT_MAX_BYTES = limits["ATT_MAX_BYTES"]
    ATT_MAX_RESERVED_BYTES = limits["ATT_MAX_RESERVED_BYTES"]
    ATT_MIN_FREE_BYTES = limits["ATT_MIN_FREE_BYTES"]
    return dict(limits)


def _reserved_bytes_locked() -> int:
    """Sum of disk space committed by active transfers (caller holds
    dl_lock): the declared total when the sender has declared one, else the
    bytes actually written so far. Declared totals count at full value even
    before the bytes arrive — reserving is what bounds the worst case."""
    reserved = 0
    for e in _dl.values():
        reserved += e["total"] if e["total"] else e["written"]
    return reserved


def _probe_free(path: str):
    """Bytes free on the filesystem holding `path`, or None when the filesystem
    cannot be inspected.

    FAILS CLOSED: the old version returned 1 EiB on statvfs failure, which
    turned "cannot read the disk state" into "plenty of room" and let a missing
    or unreadable destination directory bypass the free-space guard entirely.
    Callers must treat None as a refusal."""
    try:
        st = os.statvfs(os.path.dirname(path) or ".")
    except OSError:
        return None
    return st.f_bavail * st.f_frsize


def _fits_reservation(reserved: int, need: int, free) -> bool:
    """One rule, used by BOTH gates (_dl_begin's acceptance test and
    _dl_chunk's re-check when a total arrives or capacity is re-read):
    a transfer is allowed only if
      1. it fits its own per-file ceiling,
      2. reserved + it fits the aggregate budget, and
      3. the filesystem can hold it while keeping ATT_MIN_FREE_BYTES free.
    free = None (capacity unknown) fails closed."""
    if need > ATT_MAX_BYTES:
        return False
    if reserved + need > ATT_MAX_RESERVED_BYTES:
        return False
    if free is None:
        return False
    return need <= free - ATT_MIN_FREE_BYTES


def _dl_begin(file_id: str, peer_id: str, save_to: str, sha256: str, mid: str, room: str = "",
              total: int = 0) -> bool:
    """Register an in-progress download and open its .part file. Purges any
    stale transfer first. room = the room id for a room-file pull
    (used to report delivery status back to the sender). total = the declared
    size when the caller already knows one (0 = not yet known; the sender's
    first chunk still carries it and _dl_chunk enforces it).

    Order matters and is the fix for the disk-exhaustion finding: the
    destination directory is CREATED and the .part file OPENED first, so every
    capacity decision below is made against a real, statvfs-able path — never
    against a directory that may not exist. Nothing is reserved until the open
    succeeds.

    Returns True on success, False on any failure. On False,
    last_refusal_reason holds "per-peer", "max-concurrent", "disk-budget",
    "disk-unknown" or "open-failed" so callers can distinguish a busy refusal
    from a capacity refusal from a local I/O error."""
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
        if total and (not isinstance(total, int) or total < 0):
            total = 0

        # 1) Destination first, not last: build the download directory and open
        #    the .part handle BEFORE any capacity decision, so the free-space
        #    probe below runs against the directory the bytes actually land in.
        #    (The old order probed a directory that might not exist yet, and a
        #    failed probe used to fail open.)
        tmp = save_to + ".part"
        fh = None
        try:
            os.makedirs(os.path.dirname(save_to) or ".", exist_ok=True)
            fh = open(tmp, "wb")
        except OSError:
            if fh is not None:
                try:
                    fh.close()
                except OSError:
                    pass
            _remove_file(tmp)
            last_refusal_reason = "open-failed"
            return False

        def _refuse(reason: str, detail: str = "") -> bool:
            """Refuse the transfer and undo the open we just did, so a refused
            download never leaves a .part or a reservation behind."""
            global last_refusal_reason   # a `global` in _dl_begin does NOT apply here
            import server
            try:
                fh.close()
            except OSError:
                pass
            _remove_file(tmp)
            last_refusal_reason = reason
            server._log("attachment-dl-refused file=%s peer=%s reason=%s%s"
                        % (file_id[:12], peer_id[:12], reason,
                           (" %s" % detail) if detail else ""))
            return False

        # 2) Read the capacity of the filesystem we just opened a file on.
        #    _probe_free() FAILS CLOSED (None) when the filesystem cannot be
        #    inspected — that is a refusal, not a free pass.
        free = _probe_free(tmp)
        if free is None:
            return _refuse("disk-unknown")

        reserved = _reserved_bytes_locked()
        # A declared total is a promise about disk, so it must clear every gate
        # BEFORE a byte is requested — this is the check the old code skipped
        # for the first transfer (its `reserved > 0` guard let reserved == 0
        # through with no test at all). No first-transfer exemption: a lone
        # transfer is still bound by its own ceiling, the aggregate budget, and
        # the free-space floor.
        if total:
            if not _fits_reservation(reserved, total, free):
                detail = "total=%d cap=%d budget=%d free=%d floor=%d" % (
                    total, ATT_MAX_BYTES, ATT_MAX_RESERVED_BYTES, free, ATT_MIN_FREE_BYTES)
                return _refuse("disk-budget", detail)
        else:
            # No total declared yet: reserve the transfer's worst case so the
            # unbounded reservation is only ever temporary — _dl_chunk's grace
            # window forces a bounded declared total before real bytes arrive.
            # The ceiling is also tested against this filesystem, so even a
            # transfer that never declares a total cannot push the disk below
            # the safety floor.
            need = min(ATT_MAX_BYTES, max(0, free - ATT_MIN_FREE_BYTES))
            if not _fits_reservation(reserved, need, free):
                detail = "worst-case=%d cap=%d budget=%d free=%d floor=%d" % (
                    need, ATT_MAX_BYTES, ATT_MAX_RESERVED_BYTES, free, ATT_MIN_FREE_BYTES)
                return _refuse("disk-budget", detail)

        last_refusal_reason = None
        _dl[file_id] = {"save_to": save_to, "tmp": tmp, "fh": fh, "mid": mid,
                        "sha256": sha256, "total": total, "written": 0,
                        "peer": peer_id, "ts": now, "room": room,
                        "next_disk_check": ATT_DISK_CHECK_INTERVAL_BYTES}
        _last_dl_peer[file_id] = peer_id
    return True


def _dl_chunk(file_id: str, peer_id: str, data_b64: str, total: int):
    """Decode + append one chunk from the sender. Returns (ok, total, written).

    Enforcement layers, all re-checked on every chunk:
      - the per-file ceiling: a declared total over ATT_MAX_BYTES aborts
        immediately, and the cumulative write can never pass it (the backstop
        for a sender that lies about, or never sends, the total);
      - the declared total is final once set, and bytes can never exceed it;
      - the aggregate reservation: when a total arrives mid-stream it replaces
        the bytes-written reservation, and the new figure must still fit the
        budget and the filesystem capacity (a sender cannot grow a transfer
        past the budget by declaring late);
      - the free-space FLOOR is re-read while writing (every
        ATT_DISK_CHECK_INTERVAL_BYTES), so a transfer that started on a healthy
        disk is torn down before it lands on the keep-free floor instead of
        grinding into ENOSPC."""
    with STATE.dl_lock:
        d = _dl.get(file_id)
        if not d or d.get("peer") != peer_id:
            return (False, 0, 0)
        if total:
            if not isinstance(total, int) or total < 0:
                _dl_abort_locked(file_id, d, "bad-total")
                return (False, 0, d["written"])
            if total > ATT_MAX_BYTES:
                _dl_abort_locked(file_id, d, "over-per-file-cap")
                return (False, total, d["written"])
            # A total, once declared, is final: a later chunk claiming a
            # different one means the sender is rewriting the deal mid-stream.
            if d["total"] and total != d["total"]:
                _dl_abort_locked(file_id, d, "total-rewritten")
                return (False, d["total"], d["written"])
            if not d["total"]:
                # First time this transfer declares its size: charge the full
                # declared total against the aggregate budget and the real
                # remaining capacity, replacing the bytes-written reservation.
                reserved = _reserved_bytes_locked() - d["written"]
                free = _probe_free(d["tmp"])
                if not _fits_reservation(reserved, total, free):
                    _dl_abort_locked(file_id, d, "disk-budget")
                    return (False, total, d["written"])
            d["total"] = total
        elif not d["total"] and time.time() - d["ts"] > ATT_TOTAL_GRACE_S:
            # No valid bounded declared total within the grace window — refuse
            # the chunk. The reservation math depends on totals being declared
            # promptly; a sender that never declares one doesn't get to write.
            _dl_abort_locked(file_id, d, "no-total")
            return (False, 0, d["written"])
        try:
            raw = base64.b64decode(data_b64)
        except Exception:
            return (False, d["total"], d["written"])
        if d["written"] + len(raw) > ATT_MAX_BYTES:
            _dl_abort_locked(file_id, d, "over-per-file-cap")
            return (False, d["total"], d["written"])
        if d["total"] and d["written"] + len(raw) > d["total"]:
            _dl_abort_locked(file_id, d, "over-declared-total")
            return (False, d["total"], d["written"])
        # Free-space floor re-check while writing: available capacity must
        # still cover this chunk AND leave the keep-free floor standing.
        # Unknown capacity (probe failed) fails closed — abort, don't guess.
        now = time.time()
        if d["written"] + len(raw) >= d.get("next_disk_check", 0):
            free = _probe_free(d["tmp"])
            if free is None or free - len(raw) < ATT_MIN_FREE_BYTES:
                _dl_abort_locked(file_id, d, "low-disk")
                return (False, d["total"], d["written"])
            d["next_disk_check"] = d["written"] + ATT_DISK_CHECK_INTERVAL_BYTES
        try:
            d["fh"].write(raw)
        except OSError:
            # Write failed — usually disk-full on a huge transfer. Tear the
            # transfer down now (same as a size-limit abort) instead of
            # leaving a possibly-gigantic .part for the TTL purge to collect.
            _dl_abort_locked(file_id, d, "write-failed")
            return (False, d["total"], d["written"])
        d["written"] += len(raw)
        d["ts"] = now
        return (True, d["total"], d["written"])


def _dl_abort_locked(file_id: str, d: dict, reason: str = "size-limit") -> None:
    """Tear down a transfer whose chunk crossed a limit. Caller holds
    STATE.dl_lock. Closes the .part handle, removes the partial file, and pops
    the entry so the sender cannot keep streaming into it."""
    _dl.pop(file_id, None)
    try:
        d["fh"].close()
    except OSError:
        pass
    _remove_file(d["tmp"])
    import server
    server._log("attachment-dl-aborted file=%s peer=%s reason=%s"
                % (file_id[:12], d.get("peer", "")[:12], reason))


def refusal_error(reason):
    """Map a _dl_begin refusal reason to (peerError, uiError). Busy refusals
    (per-peer / global cap) are transient and get a distinct, retryable
    message; capacity refusals tell the user which limit was hit; local I/O
    and unknown-capacity failures keep the historical strings."""
    if reason in ("per-peer", "max-concurrent"):
        return ("busy", "attachment transfer busy — try again shortly")
    if reason == "disk-budget":
        return ("disk limit", "attachment too large for the allowed disk space")
    if reason == "disk-unknown":
        return ("cannot open download file",
                "cannot verify free disk space — download refused")
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
