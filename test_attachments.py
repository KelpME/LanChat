#!/usr/bin/env python3
"""Recipient-side attachment accept flow, end to end.

Two daemons (A sender, B receiver) become friends. A registers + serves a file
and sends an attachment message; B accepts it. Verifies:
  - attachment-progress events stream in with byte counts
  - attachment-saved ok=true echoes the message mid
  - the file lands in B's downloadDir with exact bytes (sha256 verified)
  - a hostile ../ name is sanitized to a safe basename before saving
  - a wrong sha256 is rejected (no file saved, no .part leftover)
  - _safe_filename unit behaviour

Run: python3 test_attachments.py
"""
import json
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

TOKEN = "test-shared-secret-token"
HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_persistent import Daemon, make_home, _cert_fp  # noqa: E402


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

        # ---- 3.5) unauthenticated server (wrong cert) is rejected ----------
        # A rogue HTTPS server serving a DIFFERENT cert must be refused by the
        # recipient's fingerprint check — the download that closes the auth gap.
        import server as _s
        _s.CONFIG["token"] = TOKEN
        _s._stdout = open(os.devnull, "w")  # keep in-process event noise out
        rogue_dir = tempfile.mkdtemp(prefix="lnc-rogue-")
        try:
            # Build a fresh self-signed cert (not A's) for the rogue server.
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            import datetime as _dt
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subj = issr = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "rogue")])
            cert = (x509.CertificateBuilder()
                    .subject_name(subj).issuer_name(issr)
                    .public_key(key.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(_dt.datetime.utcnow())
                    .not_valid_after(_dt.datetime.utcnow() + _dt.timedelta(days=1))
                    .sign(key, hashes.SHA256()))
            rogue_key = os.path.join(rogue_dir, "k.pem")
            rogue_cert = os.path.join(rogue_dir, "c.pem")
            open(rogue_key, "wb").write(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption()))
            open(rogue_cert, "wb").write(cert.public_bytes(serialization.Encoding.PEM))
            rogue_fp = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
            # Serve a file over the rogue cert.
            import http.server as _http
            import ssl as _ssl
            import socket as _socket
            rogue_src = os.path.join(rogue_dir, "secret.txt")
            open(rogue_src, "wb").write(b"stolen bytes")
            rid = _s.secrets.token_hex(8)
            _s.register_attachment(rid, rogue_src, "secret.txt")
            # Quiet server: the receiver closes the connection on a mismatched
            # cert, and a plain ThreadingHTTPServer would print the broken pipe.
            class _QuietServer(_http.ThreadingHTTPServer):
                def handle_error(self, request, client_address):
                    pass
            rsrv = _QuietServer(("127.0.0.1", 0), _s._ApiHandler)
            rctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
            rctx.load_cert_chain(rogue_cert, rogue_key)
            rsrv.socket = rctx.wrap_socket(rsrv.socket, server_side=True)
            rport = rsrv.server_address[1]
            threading.Thread(target=rsrv.serve_forever, daemon=True).start()
            rogue_peer = {"address": "127.0.0.1", "httpPort": rport,
                          "name": "Rogue", "id": "0" * 64}

            # Expect fingerprint pinning (ida = A's real fingerprint) to refuse it.
            out1 = os.path.join(dldir, "rogue1.txt")
            ok1 = _s._download_attachment(rogue_peer, rid, out1,
                                          expected_sha256="", mid="m1",
                                          expected_fingerprint=ida)
            assert ok1 is False, "rogue cert with a DIFFERENT fingerprint must be refused"
            assert not os.path.exists(out1), "rogue-cert download must not save a file"
            assert not os.path.exists(out1 + ".part"), "rogue-cert download left .part"
            # Match the rogue cert's OWN fingerprint -> it is trusted and succeeds.
            out2 = os.path.join(dldir, "rogue2.txt")
            ok2 = _s._download_attachment(rogue_peer, rid, out2,
                                          expected_sha256="", mid="m2",
                                          expected_fingerprint=rogue_fp)
            assert ok2 is True, "cert matching its own fingerprint should succeed"
            with open(out2, "rb") as f:
                assert f.read() == b"stolen bytes", "rogue-trusted download bytes differ"
            print("OK  download refuses a DIFFERENT cert (fingerprint pinning); trusts a matching one")
        finally:
            rsrv.shutdown(); rsrv.server_close()
            shutil.rmtree(rogue_dir, ignore_errors=True)

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
