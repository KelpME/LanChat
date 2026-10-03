#!/usr/bin/env python3
"""Streamed attachment digests: send no longer hashes before sending.

Since the send paths build metadata with sha256="" and the digest is computed
while streaming (_serve_attachment) and carried on the attachmentEnd frame:

  - a full 1:1 send over the offline two-instance harness completes with
    attachment-saved ok=True even though the metadata digest is empty
    (the End frame populates it via attachments.set_expected_digest)
  - digest verification still fires when a digest IS present (wrong
    metadata digest -> mismatch, no file saved)
  - unit: set_expected_digest fills an empty expected digest only, never
    overwrites a non-empty one, ignores unknown fileId / wrong peer, and a
    _dl_finish with a now-populated digest mismatches on wrong bytes

Hand-checked constant: sha256(b"kelpme streamed digest payload\n")
    = f152a78475bdd8f35aa451f2fab31285795a7e1f5ce0582244181a1d701ef6c0

Run: python3 test_async_digest.py
"""
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time

TOKEN = "test-shared-secret-token"
HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_persistent import Daemon, _cert_fp, make_home  # noqa: E402

# Hand-computed above with hashlib.sha256 — do not regenerate casually.
PAYLOAD = b"kelpme streamed digest payload\n"
PAYLOAD_SHA = "f152a78475bdd8f35aa451f2fab31285795a7e1f5ce0582244181a1d701ef6c0"
assert hashlib.sha256(PAYLOAD).hexdigest() == PAYLOAD_SHA


def disco(port, pid, name, pport, phttp):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(json.dumps({"t": "hello", "id": pid, "name": name,
                         "port": pport, "httpPort": phttp}).encode(),
             ("127.0.0.1", port))
    s.close()


def wait_message(d, with_attachment=False, timeout=8.0):
    dl = time.time() + timeout
    while time.time() < dl:
        ev = d.wait_event("message")
        if ev and (not with_attachment or ev["message"].get("attachment")):
            return ev["message"]
    return None


def test_set_expected_digest_unit():
    """set_expected_digest: fill-empty-only, no overwrite, wrong peer/fileId
    ignored; _dl_finish then verifies against the populated digest."""
    import attachments as att

    class _State:
        pass

    st = _State()
    st.att_lock = threading.Lock()
    st.attachments = {}
    st.dl_lock = threading.Lock()
    att.init(st)

    import server as srv
    logged = []
    real_log = srv._log
    srv._log = lambda msg: logged.append(msg)
    tmp = tempfile.mkdtemp(prefix="lanchat-digest-unit-")
    try:
        # ---- 1) fills an EMPTY expected digest --------------------------
        save1 = os.path.join(tmp, "u1.bin")
        assert att._dl_begin("u1", "peerA", save1, "", "m1"), "_dl_begin u1 failed"
        att.set_expected_digest("u1", "peerA", PAYLOAD_SHA)
        with st.dl_lock:
            got = att._dl["u1"]["sha256"]
        assert got == PAYLOAD_SHA, "empty digest must be filled, got %r" % got
        print("OK  set_expected_digest fills an empty expected digest")

        # ---- 2) never overwrites a non-empty digest ---------------------
        old_digest = "ab" * 32
        save2 = os.path.join(tmp, "u2.bin")
        assert att._dl_begin("u2", "peerA", save2, old_digest, "m2")
        att.set_expected_digest("u2", "peerA", PAYLOAD_SHA)
        with st.dl_lock:
            got2 = att._dl["u2"]["sha256"]
        assert got2 == old_digest, \
            "metadata-provided digest must stay authoritative, got %r" % got2
        print("OK  set_expected_digest never overwrites a metadata digest")

        # ---- 3) unknown fileId and wrong peer are ignored ----------------
        att.set_expected_digest("nosuchfile", "peerA", PAYLOAD_SHA)  # no entry
        att.set_expected_digest("u1", "peerB", "cd" * 32)  # wrong peer
        with st.dl_lock:
            got3 = att._dl["u1"]["sha256"]
        assert got3 == PAYLOAD_SHA, "wrong-peer call must be ignored, got %r" % got3
        print("OK  set_expected_digest ignores unknown fileId / wrong peer")
        # Free the open slots — the per-peer concurrent-transfer limit would
        # otherwise refuse the next _dl_begin calls below.
        att._dl_finish("u1", "peerA", False)
        att._dl_finish("u2", "peerA", False)

        # ---- 4) empty digest arg is a no-op ------------------------------
        save4 = os.path.join(tmp, "u4.bin")
        assert att._dl_begin("u4", "peerA", save4, "", "m4")
        att.set_expected_digest("u4", "peerA", "")
        with st.dl_lock:
            got4 = att._dl["u4"]["sha256"]
        assert got4 == "", "empty digest must not touch the entry, got %r" % got4
        print("OK  set_expected_digest ignores an empty digest")
        att._dl_finish("u4", "peerA", False)

        # ---- 5) populated digest drives _dl_finish mismatch --------------
        save5 = os.path.join(tmp, "u5.bin")
        assert att._dl_begin("u5", "peerA", save5, "", "m5")
        data5 = base64_of(b"hello bytes")
        ok, total, written = att._dl_chunk("u5", "peerA", data5, 11)
        assert ok and written == 11, "chunk write failed (%r)" % ((ok, total, written),)
        # The bytes on disk hash to sha256(b"hello bytes"); tell the transfer
        # to expect something else -> mismatch.
        wrong = hashlib.sha256(b"different bytes").hexdigest()
        att.set_expected_digest("u5", "peerA", wrong)
        res = att._dl_finish("u5", "peerA", True)
        assert res and res[0] == "mismatch", \
            "populated wrong digest must mismatch, got %r" % (res,)
        assert not os.path.exists(save5 + ".part"), "mismatch left .part behind"
        # And the CORRECT digest (via End frame) lets the same bytes save.
        assert att._dl_begin("u5b", "peerA", save5, "", "m5b")
        ok, _, _ = att._dl_chunk("u5b", "peerA", data5, 11)
        assert ok
        att.set_expected_digest("u5b", "peerA",
                                hashlib.sha256(b"hello bytes").hexdigest())
        res5b = att._dl_finish("u5b", "peerA", True)
        assert res5b and res5b[0] == "saved", "correct digest must save: %r" % (res5b,)
        with open(save5, "rb") as f:
            assert f.read() == b"hello bytes", "saved bytes corrupted"
        print("OK  _dl_finish verifies against the End-frame-populated digest")
    finally:
        srv._log = real_log
        shutil.rmtree(tmp, ignore_errors=True)


def base64_of(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode("ascii")


def test_two_instance_streamed_digest():
    """Full 1:1 send with an EMPTY metadata digest: End frame carries the
    streamed digest, transfer completes saved; a PRESENT wrong digest still
    mismatches."""
    ha = make_home("dg-a", 4995, "DigestA")
    hb = make_home("dg-b", 4996, "DigestB")
    a = Daemon(ha, 4995, "DigestA")
    b = Daemon(hb, 4996, "DigestB")
    try:
        a.wait_event("ready")
        b.wait_event("ready")
        ida = _cert_fp(ha)
        idb = _cert_fp(hb)

        def beat():
            while True:
                disco(a.port, idb, "DigestB", 4996, 5006)
                disco(b.port, ida, "DigestA", 4995, 5005)
                time.sleep(1.5)
        threading.Thread(target=beat, daemon=True).start()
        time.sleep(1.0)

        # Friend handshake over the signed UDP path (the legacy TCP
        # send...friend_request kwarg has been dead since a760353).
        a.cmd(cmd="udpFriendRequest", to=idb, name="DigestB")
        b.wait_event("friend-request", timeout=6)
        b.cmd(cmd="acceptFriend", id=ida)
        assert a.wait_event("friend-accepted"), "friend handshake failed"

        a.cmd(cmd="setHttp", enabled=True)
        assert a.wait_event("http"), "A HTTP not enabled"
        dldir = os.path.join(hb, "dl")
        os.makedirs(dldir)
        b.cmd(cmd="setDownloadDir", dir=dldir)
        assert b.wait_event("download-dir"), "download dir not set"

        src = os.path.join(ha, "streamed.bin")
        with open(src, "wb") as f:
            f.write(PAYLOAD)

        # ---- 1) send now carries an EMPTY metadata digest (wire shape kept) --
        a.cmd(cmd="send", to=idb, text="streamed digest", friend_request=False,
              attachment={"path": src, "name": "streamed.bin"})
        msg = wait_message(b, with_attachment=True)
        assert msg and msg.get("attachment"), "B never got the attachment message"
        att = msg["attachment"]
        assert att.get("fileId") and att.get("name") == "streamed.bin", \
            "attachment metadata incomplete: %r" % att
        assert att.get("sha256") == "", \
            "metadata digest must be empty (streamed instead), got %r" % att.get("sha256")

        # Accept with an empty digest — the End frame supplies it.
        b.cmd(**{"cmd": "acceptAttachment", "from": ida, "fileId": att["fileId"],
                 "name": att["name"], "mid": msg["mid"], "sha256": ""})
        saved = b.wait_event("attachment-saved")
        assert saved and saved.get("ok") is True, \
            "streamed-digest transfer must save, got %r" % (saved,)
        assert saved.get("mid") == msg["mid"], "attachment-saved mid mismatch"
        dl = os.path.join(dldir, "streamed.bin")
        assert os.path.exists(dl), "file not written to downloadDir"
        with open(dl, "rb") as f:
            got_bytes = f.read()
        assert got_bytes == PAYLOAD, "downloaded bytes differ from source"
        assert hashlib.sha256(got_bytes).hexdigest() == PAYLOAD_SHA, \
            "saved file digest mismatch vs hand-computed constant"
        assert not os.path.exists(dl + ".part"), "leftover .part file"
        print("OK  empty metadata digest + End-frame digest -> saved, bytes exact")

        # ---- 2) verification still fires when a digest IS present ----------
        a.cmd(cmd="send", to=idb, text="corrupt", friend_request=False,
              attachment={"path": src, "name": "corrupt.bin"})
        msg2 = wait_message(b, with_attachment=True)
        assert msg2 and msg2.get("attachment"), "second message not received"
        att2 = msg2["attachment"]
        b.cmd(**{"cmd": "acceptAttachment", "from": ida, "fileId": att2["fileId"],
                 "name": att2["name"], "mid": msg2["mid"], "sha256": "0" * 64})
        saved2 = b.wait_event("attachment-saved")
        assert saved2 and saved2.get("ok") is False, \
            "wrong present digest must mismatch, got %r" % (saved2,)
        assert "mismatch" in (saved2.get("error") or ""), \
            "error should mention mismatch: %r" % (saved2,)
        assert not os.path.exists(os.path.join(dldir, "corrupt.bin")), \
            "mismatch file must not be saved"
        assert not os.path.exists(os.path.join(dldir, "corrupt.bin.part")), \
            "mismatch left .part behind"
        print("OK  present wrong digest still rejected (mismatch, no .part)")
    finally:
        try:
            a.stop()
        except Exception:
            pass
        try:
            b.stop()
        except Exception:
            pass
        shutil.rmtree(ha, ignore_errors=True)
        shutil.rmtree(hb, ignore_errors=True)


def main():
    test_set_expected_digest_unit()
    test_two_instance_streamed_digest()
    print("ALL OK")


if __name__ == "__main__":
    main()
