#!/usr/bin/env python3
"""Recipient-side attachment accept flow, end to end.

Two daemons (A sender, B receiver) become friends. A registers + serves a file
and sends an attachment message; B accepts it. Verifies:
  - attachment-progress events stream in with byte counts
  - attachment-saved ok=true echoes the message mid
  - the file lands in B's downloadDir with exact bytes (sha256 verified)
  - a hostile ../ name is sanitized to a safe basename before saving
  - a wrong sha256 is rejected (no file saved, no .part leftover)
  - the socket transport's trust gate: a stranger isn't trusted, an unknown
    fileId from a trusted peer yields attachmentError (no file)
  - _safe_filename unit behaviour

Run: python3 test_attachments.py
"""
import json
import os
import shutil
import socket
import sys
import threading
import time

TOKEN = "test-shared-secret-token"
HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_persistent import Daemon, _cert_fp, make_home  # noqa: E402


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


def main():
    ha = make_home("a", 4991, "Alpha"); hb = make_home("b", 4992, "Beta")
    a = Daemon(ha, 4991, "Alpha"); b = Daemon(hb, 4992, "Beta")
    try:
        a.wait_event("ready"); b.wait_event("ready")
        ida = _cert_fp(ha); idb = _cert_fp(hb)

        # Presence incl. each peer's HTTPS port + a heartbeat so neither peer
        # times out mid-transfer.
        def beat():
            while True:
                disco(a.port, idb, "Beta", 4992, 5002)
                disco(b.port, ida, "Alpha", 4991, 5001)
                time.sleep(1.5)
        threading.Thread(target=beat, daemon=True).start()
        time.sleep(1.0)

        # Friend handshake.
        a.cmd(cmd="send", to=idb, text="friend me", friend_request=True)
        b.wait_event("friend-request")
        b.cmd(cmd="acceptFriend", id=ida)
        assert a.wait_event("friend-accepted"), "friend handshake failed"
        wait_message(a); wait_message(b)  # drain the reveal messages

        # Sender serves files; receiver picks a download dir.
        a.cmd(cmd="setHttp", enabled=True)
        assert a.wait_event("http"), "A HTTP not enabled"
        dldir = os.path.join(hb, "dl"); os.makedirs(dldir)
        b.cmd(cmd="setDownloadDir", dir=dldir)
        assert b.wait_event("download-dir"), "download dir not set"

        # ---- 1) happy path: send + accept ----------------------------------
        payload = b"hello attachment\x00\xff binary bytes"
        src = os.path.join(ha, "hello.txt")
        with open(src, "wb") as f:
            f.write(payload)
        a.cmd(cmd="send", to=idb, text="here's a file", friend_request=False,
              attachment={"path": src, "name": "hello.txt"})
        msg = wait_message(b, with_attachment=True)
        assert msg and msg.get("attachment"), "B never got the attachment message"
        att = msg["attachment"]
        assert att.get("name") == "hello.txt" and att.get("sha256"), "attachment metadata incomplete"

        b.cmd(**{"cmd": "acceptAttachment", "from": ida, "fileId": att["fileId"], "name": att["name"],
                 "mid": msg["mid"], "sha256": att["sha256"]})
        saved = b.wait_event("attachment-saved")
        assert saved and saved.get("ok") is True, "attachment-saved ok not True: %r" % (saved,)
        assert saved.get("mid") == msg["mid"], "attachment-saved mid mismatch"
        dl = os.path.join(dldir, "hello.txt")
        assert os.path.exists(dl), "file not written to downloadDir"
        with open(dl, "rb") as f:
            assert f.read() == payload, "downloaded bytes differ from source"
        assert not os.path.exists(dl + ".part"), "leftover .part file"
        prog = b.events_of("attachment-progress")
        assert len(prog) >= 1, "no attachment-progress events emitted"
        assert prog[-1]["bytes"] == len(payload), "final progress bytes != file size"
        print("OK  happy path: %d progress events, file saved with exact bytes, mid echoed" % len(prog))

        # ---- 2) hostile name is sanitized to a safe basename ---------------
        a.cmd(cmd="send", to=idb, text="evil", friend_request=False,
              attachment={"path": src, "name": "../../evil.txt"})
        msg2 = wait_message(b, with_attachment=True)
        assert msg2, "traversal message not received"
        att2 = msg2["attachment"]
        b.cmd(**{"cmd": "acceptAttachment", "from": ida, "fileId": att2["fileId"], "name": att2["name"],
                 "mid": msg2["mid"], "sha256": att2["sha256"]})
        saved2 = b.wait_event("attachment-saved")
        assert saved2 and saved2.get("ok") is True, "traversal accept failed: %r" % (saved2,)
        assert os.path.exists(os.path.join(dldir, "evil.txt")), "sanitized file not in downloadDir"
        assert not os.path.exists(os.path.join(dldir, "..", "evil.txt")), "path traversal escaped downloadDir"
        print("OK  hostile ../ name sanitized -> saved inside downloadDir")

        # ---- 3) sha256 mismatch is rejected --------------------------------
        a.cmd(cmd="send", to=idb, text="corrupt", friend_request=False,
              attachment={"path": src, "name": "corrupt.txt"})
        msg3 = wait_message(b, with_attachment=True)
        assert msg3, "corrupt message not received"
        att3 = msg3["attachment"]
        b.cmd(**{"cmd": "acceptAttachment", "from": ida, "fileId": att3["fileId"], "name": att3["name"],
                 "mid": msg3["mid"], "sha256": "0" * 64})  # wrong digest
        saved3 = b.wait_event("attachment-saved")
        assert saved3 and saved3.get("ok") is False, "checksum mismatch should fail"
        assert "mismatch" in (saved3.get("error") or ""), "error should mention mismatch"
        assert not os.path.exists(os.path.join(dldir, "corrupt.txt")), "mismatch file must not be saved"
        assert not os.path.exists(os.path.join(dldir, "corrupt.txt.part")), "mismatch left .part behind"
        print("OK  sha256 mismatch rejected (no file, no .part)")

        # ---- 3.5) socket-path security: trust gate + unknown fileId ---------
        # File transfer now rides the authenticated message socket, so the
        # "different cert" MITM vector is gone (the peer's identity was proven
        # at connect). Security is enforced by two rules instead:
        #   (a) only an is_trusted peer's attachmentRequest is served, and
        #   (b) an unknown/expired fileId yields attachmentError, not bytes.
        import server as _s
        _s.CONFIG["token"] = TOKEN
        _s._stdout = open(os.devnull, "w")  # keep in-process event noise out

        # (a) A stranger (not a friend) must not be trusted to pull files.
        stranger_id = "f" * 64
        assert not _s.is_trusted(stranger_id), "stranger must not be trusted"

        # (b) Requesting an unknown fileId from a TRUSTED peer -> attachmentError.
        b.cmd(**{"cmd": "acceptAttachment", "from": ida, "fileId": "bogus00000000",
                 "name": "ghost.txt", "mid": "mghost", "sha256": ""})
        err = b.wait_event("attachment-saved")
        assert err and err.get("ok") is False, \
            "unknown fileId should fail: %r" % (err,)
        assert not os.path.exists(os.path.join(dldir, "ghost.txt")), \
            "unknown fileId must not save a file"
        assert not os.path.exists(os.path.join(dldir, "ghost.txt.part")), \
            "unknown fileId left a .part behind"
        print("OK  socket path: untrusted requester gated; unknown fileId -> attachmentError")

        # ---- 3.6) size check: incomplete transfer is rejected ---------------
        # Even if the sha256 somehow matched, a download missing bytes (dropped
        # trailing chunk) must be refused — written must equal the sender's
        # reported total.
        import server as _s
        _s.CONFIG["token"] = TOKEN
        _s._stdout = open(os.devnull, "w")
        inc_dir = os.path.join(hb, "dl-inc"); os.makedirs(inc_dir, exist_ok=True)
        inc_save = os.path.join(inc_dir, "partial.bin")
        assert _s._dl_begin("inc1", ida, inc_save, "", "minc"), "dl_begin failed"
        ok, total, written = _s._dl_chunk("inc1", ida,
                                          _s.base64.b64encode(b"SHORT").decode("ascii"), 100)
        assert ok, "chunk should be accepted"
        # Sender claimed total=100 but only 5 bytes arrived -> incomplete.
        status, *_ = _s._dl_finish("inc1", ida, True)
        assert status == "incomplete", "missing bytes must be flagged incomplete, got %r" % status
        assert not os.path.exists(inc_save), "incomplete transfer must not save a file"
        assert not os.path.exists(inc_save + ".part"), "incomplete transfer left .part"
        print("OK  size check: incomplete transfer (bytes missing) rejected, no file")

        # ---- 3.7) offline sender fail-fast ---------------------------------
        # If the sender's socket is down when we accept, the request can't be
        # written -> the acceptAttachment handler must emit ok:false (not hang
        # the Save bar on \"Saving…\") and clean up the .part. Test the branch
        # in-process (peer record present, no active socket) so we capture the
        # emitted event deterministically.
        import server as _s
        _s.CONFIG["token"] = TOKEN
        off_dir = os.path.join(hb, "dl-off"); os.makedirs(off_dir, exist_ok=True)
        _s.CONFIG["downloadDir"] = off_dir
        # Register the sender as a known peer but ensure its socket is down.
        _s._peers[ida] = {"id": ida, "name": "Alpha", "address": "127.0.0.1",
                          "port": 4991, "httpPort": None, "status": "available",
                          "lastSeen": int(time.time() * 1000), "version": ""}
        # Make sure no active socket exists for it.
        with _s._conn(ida)["lock"]:
            _s._conn(ida)["sock"] = None
        captured = []
        orig_emit = _s._emit
        _s._emit = lambda e: captured.append(e)
        _s.handle_command({"cmd": "acceptAttachment", "from": ida, "fileId": "off1",
                           "name": "gone.bin", "mid": "moff", "sha256": ""})
        _s._emit = orig_emit
        saved_ev = [e for e in captured if e.get("event") == "attachment-saved"]
        assert saved_ev and saved_ev[0].get("ok") is False, \
            "offline sender must fail fast: %r" % (captured,)
        assert "offline" in (saved_ev[0].get("error") or ""), \
            "offline error should mention offline: %r" % (saved_ev[0],)
        assert not os.path.exists(os.path.join(off_dir, "gone.bin")), \
            "offline sender must not save a file"
        assert not os.path.exists(os.path.join(off_dir, "gone.bin.part")), \
            "offline sender left .part behind"
        print("OK  offline sender fail-fast: attachment-saved ok:false, no hang, no .part")

        # ---- 4) _safe_filename unit behaviour ------------------------------
        import server as _s
        cases = {
            "hello.txt": "hello.txt",
            "../../etc/passwd": "passwd",
            "/etc/passwd": "passwd",
            "..\\..\\win.ini": "win.ini",
            ".../.hidden": "hidden",
            "a b\tc.txt": "a bc.txt",
            "": "download",
        }
        for raw, want in cases.items():
            got = _s._safe_filename(raw)
            assert got == want, "_safe_filename(%r) = %r, want %r" % (raw, got, want)
            assert "/" not in got and "\\" not in got and ".." not in got
        print("OK  _safe_filename sanitizes %d hostile names" % len(cases))

        print("\nALL ATTACHMENT TESTS PASSED")
        return 0
    finally:
        a.stop(); b.stop()
        shutil.rmtree(ha, ignore_errors=True); shutil.rmtree(hb, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
