#!/usr/bin/env python3
"""Shared test helper: simulate a REAL lanchat peer over TLS, including the
1.2.0 identity handshake (send identity + sign the challenge). Raw TLS sockets
are no longer trusted on inbound, so any test that injects messages as if from a
peer must authenticate first.

Usage:
    import test_peer as tp
    cert_pem, key_pem = tp.make_certs(home_dir)          # fresh identity
    fp = tp.fingerprint(cert_pem)
    s = tp.authed_connect(addr, port, cert_pem, key_pem) # completed handshake
    s.sendall(...)                                        # then send real msgs
"""

import hashlib
import json
import os
import socket
import ssl
import subprocess


def make_certs(home_dir: str):
    """Generate a fresh self-signed cert+key in home_dir (openssl). Returns
    (cert_pem, key_pem)."""
    cert_dir = os.path.join(home_dir, ".config", "omarchy", "lanchat-certs")
    os.makedirs(cert_dir, exist_ok=True)
    cert = os.path.join(cert_dir, "cert.pem")
    key = os.path.join(cert_dir, "key.pem")
    if not (os.path.exists(cert) and os.path.exists(key)):
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", key, "-out", cert, "-days", "3650", "-subj", "/CN=test-peer"],
            check=True, capture_output=True,
        )
    with open(cert) as f:
        cert_pem = f.read()
    with open(key) as f:
        key_pem = f.read()
    return cert_pem, key_pem


def fingerprint(cert_pem: str) -> str:
    """SHA-256 fingerprint of a PEM cert — the device identity (== server.host_id)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    c = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
    return hashlib.sha256(c.public_bytes(serialization.Encoding.DER)).hexdigest()


def _sign(key_pem: str, data: bytes) -> str:
    import binascii

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_private_key(key_pem.encode("utf-8"), password=None)
    sig = key.sign(data, padding.PKCS1v15(), hashes.SHA256())
    return binascii.hexlify(sig).decode("ascii")


def authed_connect(addr, port, cert_pem: str, key_pem: str):
    """Open a TLS connection and complete the identity handshake. Returns the
    authenticated socket (peer may now send real messages)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    s = ctx.wrap_socket(socket.create_connection((addr, port), timeout=3))
    s.settimeout(3)
    s.sendall((json.dumps({"t": "identity", "from": fingerprint(cert_pem), "cert": cert_pem}) + "\n").encode())
    # Read the challenge (loop until we get it, ignoring any noise).
    buf = b""
    nonce = None
    while nonce is None:
        buf += s.recv(8192)
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.decode("utf-8", "replace")
            if '"challenge"' in line and '"nonce"' in line:
                nonce = json.loads(line)["nonce"]
                break
    s.sendall((json.dumps({"t": "identityProof", "sig": _sign(key_pem, nonce.encode("utf-8"))}) + "\n").encode())
    return s


def authed_send(addr, port, cert_pem: str, key_pem: str, payload: dict):
    """Open an authenticated connection, send one message, close. Returns ack bytes."""
    s = authed_connect(addr, port, cert_pem, key_pem)
    try:
        s.sendall(json.dumps(payload).encode() + b"\n")
        try:
            ack = s.recv(64)
        except Exception:
            ack = b""
        return ack
    finally:
        try:
            s.close()
        except OSError:
            pass
