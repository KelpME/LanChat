#!/usr/bin/env python3
"""Regression test: the served TLS cert must always match the daemon's
announced identity (host_id), even if the cert is regenerated while the daemon
runs. A stale cached tls_ctx would keep serving the OLD cert after a reinstall
wiped + regenerated lanchat-certs, causing fingerprint-mismatch on every peer
connection (the exact bug that made friend requests silently fail)."""
import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")


def main():
    home = tempfile.mkdtemp(prefix="lanchat-certregen-")
    cfg = os.path.join(home, ".config", "omarchy")
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "lanchat.json"), "w") as f:
        json.dump({"token": "x" * 16, "port": 4917, "displayName": "CertTest"}, f)
    env = dict(os.environ, HOME=home, XDG_RUNTIME_DIR=tempfile.mkdtemp(prefix="lanchat-cert-rt-"))

    daemon = subprocess.Popen([sys.executable, SRV, "--socket"], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(2)
        assert daemon.poll() is None, "daemon exited early"

        def served_fingerprint():
            raw = socket.create_connection(("127.0.0.1", 4917), timeout=5)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            s = ctx.wrap_socket(raw)
            fp = hashlib.sha256(s.getpeercert(binary_form=True)).hexdigest()
            s.close()
            return fp

        def announced_id():
            # The daemon's host_id is the fingerprint of its current cert.pem.
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization
            with open(os.path.join(cfg, "lanchat-certs", "cert.pem"), "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read())
            return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()

        # Initial state: served cert == announced id.
        fp1 = served_fingerprint()
        id1 = announced_id()
        assert fp1 == id1, "served cert mismatch at start: %s vs %s" % (fp1[:16], id1[:16])
        print("OK  served cert matches announced identity at start")

        # Regenerate the cert (simulates reinstall wiping + regenerating
        # lanchat-certs while the daemon keeps running).
        certdir = os.path.join(cfg, "lanchat-certs")
        shutil.rmtree(certdir)
        os.makedirs(certdir, exist_ok=True)
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", os.path.join(certdir, "key.pem"),
             "-out", os.path.join(certdir, "cert.pem"), "-days", "3650",
             "-subj", "/CN=lanchat-regen"],
            check=True, capture_output=True)

        # Without a restart, the daemon should now serve the NEW cert (matching
        # the new host_id), not a stale cached one.
        id2 = announced_id()
        assert id1 != id2, "test needs regenerated cert to differ"
        fp2 = served_fingerprint()
        assert fp2 == id2, "stale cert served after regen: served %s vs announced %s (fingerprint-mismatch bug)" % (fp2[:16], id2[:16])
        print("OK  served cert tracks the regenerated cert (no stale fingerprint)")

        print("\nALL CERT-RELOAD TESTS PASSED")
        return 0
    finally:
        try:
            daemon.terminate()
            daemon.wait(timeout=5)
        except Exception:
            pass
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
