#!/usr/bin/env python3
"""Receiver-side size refusal: the limit is named, not hidden.

Two daemons (A sender, B receiver) become friends. The receiver's per-file
ceiling is shrunk to 1 GiB via the Settings command, then A sends a file
larger than that and B accepts it. Verifies:
  - attachment-saved ok=false with an error that NAMES the max file size
    ("1 GiB") and points at Settings — not a generic disk-limit string
  - no file / .part is written for the refused transfer
  - a file UNDER the ceiling still saves fine (control)

The over-cap file is sparse: the refusal happens at _dl_begin BEFORE any
byte is requested, so the sender never streams it.

Run: python3 test_size_refusal.py
"""
import json
import os
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


def set_max_gib(d, gib):
    """Change a daemon's per-file ceiling and wait for the limits event."""
    d.cmd(cmd="setAttachmentMax", gib=gib)
    lim = d.wait_event("attachment-limits")
    assert lim and lim.get("perFileBytes") == gib * 1024 ** 3, \
        "attachment-limits did not confirm %d GiB: %r" % (gib, lim)


def main():
    ha = make_home("a", 4993, "Alpha"); hb = make_home("b", 4994, "Beta")
    a = Daemon(ha, 4993, "Alpha"); b = Daemon(hb, 4994, "Beta")
    try:
        a.wait_event("ready"); b.wait_event("ready")
        ida = _cert_fp(ha); idb = _cert_fp(hb)

        # Presence incl. each peer's HTTPS port + a heartbeat so neither peer
        # times out mid-test.
        def beat():
            while True:
                disco(a.port, idb, "Beta", 4994, 5004)
                disco(b.port, ida, "Alpha", 4993, 5003)
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

        # ---- 1) over-cap: refusal NAMES the limit and points at Settings --
        # Shrink the RECEIVER's ceiling to 1 GiB (Settings command, live).
        set_max_gib(b, 1)
        try:
            big = int(1.5 * 1024 ** 3)
            src = os.path.join(ha, "big.bin")
            with open(src, "wb") as f:
                f.truncate(big)  # sparse: metadata size without the bytes
            a.cmd(cmd="send", to=idb, text="too big", friend_request=False,
                  attachment={"path": src, "name": "big.bin"})
            msg = wait_message(b, with_attachment=True)
            assert msg and msg.get("attachment"), \
                "B never got the over-cap attachment message"
            assert msg["attachment"].get("size") == big, \
                "sender-advertised size missing: %r" % (msg["attachment"],)
            att = msg["attachment"]
            b.cmd(**{"cmd": "acceptAttachment", "from": ida,
                     "fileId": att["fileId"], "name": att["name"],
                     "mid": msg["mid"], "sha256": att["sha256"]})
            saved = b.wait_event("attachment-saved")
            assert saved and saved.get("ok") is False, \
                "over-cap accept must fail: %r" % (saved,)
            err = saved.get("error") or ""
            assert "max file size" in err, \
                "error must name the max file size: %r" % (err,)
            assert "1 GiB" in err, "error must name the limit itself: %r" % (err,)
            assert "Settings" in err, "error must point at Settings: %r" % (err,)
            assert "disk budget" not in err, \
                "per-file refusal must not read as a disk-budget refusal: %r" % (err,)
            assert not os.path.exists(os.path.join(dldir, "big.bin")), \
                "refused file must not be saved"
            assert not os.path.exists(os.path.join(dldir, "big.bin.part")), \
                "refused transfer left a .part behind"
            print("OK  over-cap refusal names the limit: %s" % err)
        finally:
            set_max_gib(b, 4)  # restore the default ceiling for the control

        # ---- 2) control: under the ceiling still saves ---------------------
        payload = b"small enough"
        src = os.path.join(ha, "small.txt")
        with open(src, "wb") as f:
            f.write(payload)
        a.cmd(cmd="send", to=idb, text="small file", friend_request=False,
              attachment={"path": src, "name": "small.txt"})
        msg = wait_message(b, with_attachment=True)
        assert msg and msg.get("attachment"), \
            "B never got the under-cap attachment message"
        att = msg["attachment"]
        b.cmd(**{"cmd": "acceptAttachment", "from": ida,
                 "fileId": att["fileId"], "name": att["name"],
                 "mid": msg["mid"], "sha256": att["sha256"]})
        saved = b.wait_event("attachment-saved")
        assert saved and saved.get("ok") is True, \
            "under-cap accept failed: %r" % (saved,)
        dl = os.path.join(dldir, "small.txt")
        with open(dl, "rb") as f:
            assert f.read() == payload, "downloaded bytes differ from source"
        print("OK  control: file under the ceiling saves fine")
    finally:
        a.stop(); b.stop()


if __name__ == "__main__":
    main()
    print("ALL OK test_size_refusal")
