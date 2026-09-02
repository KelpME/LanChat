#!/usr/bin/env python3
"""TLS identity for KelpME.lanchat — extracted verbatim from server.py (Commit 2).

Each install generates a persistent self-signed certificate. The SHA-256
fingerprint of that cert is the device's true, stable identity — independent
of the cosmetic display name. The fingerprint is what friends are keyed on.

Ownership: STATE.priv_key, STATE.priv_key_lock (the fields stay on State;
this module operates on them through the shared State instance). server.py
re-exports this module's symbols, so callers keep calling them via server.
"""

import hashlib
import os
import ssl
import subprocess

# init(state) wiring: STATE is bound once by server.py at import time
# (identity.init(STATE)). Every former server-side STATE.x reference reads
# the same State instance, and deferred `import server` calls (e.g.
# server._emit) stay late-bound so tests can monkeypatch server._emit.
STATE = None


def init(state):
    """Bind this module's STATE to the daemon's shared State instance."""
    global STATE
    STATE = state


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

CERT_DIR = os.path.join(os.path.expanduser("~"), ".config", "omarchy", "lanchat-certs")
CERT_KEY = os.path.join(CERT_DIR, "key.pem")
CERT_PEM = os.path.join(CERT_DIR, "cert.pem")


def host_id() -> str:
    """The device's true, stable identity — the cert fingerprint.

    Unlike the cosmetic display name, this never changes (the cert persists),
    so renaming/re-rolling a name cannot break a friend link.
    """
    return cert_fingerprint()


# --------------------------------------------------------------------------
# TLS identity
# --------------------------------------------------------------------------

def _gen_cert() -> None:
    os.makedirs(CERT_DIR, exist_ok=True)
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", CERT_KEY, "-out", CERT_PEM, "-days", "3650",
             "-subj", "/CN=lanchat-%s" % str(STATE.config.get("id") or "peer"[:8])],
            check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        # Fallback: try the cryptography library if openssl isn't available.
        try:
            import datetime as _dt

            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "lanchat")])
            cert = (x509.CertificateBuilder()
                    .subject_name(subject).issuer_name(issuer)
                    .public_key(key.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(_dt.datetime.utcnow())
                    .not_valid_after(_dt.datetime.utcnow() + _dt.timedelta(days=3650))
                    .sign(key, hashes.SHA256()))
            with open(CERT_KEY, "wb") as f:
                f.write(key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
            with open(CERT_PEM, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
        except Exception as e:
            import server
            server._emit({"event": "error", "message": "lanchat TLS cert generation failed: %s" % e})


def ensure_tls() -> ssl.SSLContext:
    """Generate the cert if needed and return a server SSL context."""
    if not (os.path.exists(CERT_KEY) and os.path.exists(CERT_PEM)):
        _gen_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_PEM, CERT_KEY)
    return ctx


def cert_fingerprint() -> str:
    """SHA-256 fingerprint of our cert — the stable device identity."""
    if not os.path.exists(CERT_PEM):
        ensure_tls()
    with open(CERT_PEM, "rb") as f:
        data = f.read()
    # Parse the DER cert via the cryptography lib (robust across Python/OpenSSL).
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        cert = x509.load_pem_x509_certificate(data)
        return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    except Exception:
        # Fallback: raw PEM fingerprint (still stable per cert)
        return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Identity proof (challenge-response)
# --------------------------------------------------------------------------
# The TCP transport has no client certs (Python can't request a cert without
# CA-verifying it, which rejects self-signed peers). To close the
# unauthenticated-inbound spoofing gap, the dialing side proves it owns the
# private key for its claimed cert fingerprint: the receiver reads the peer's
# `identity` (claimed id + cert), sends a random `challenge` nonce, and the
# peer must return an `identityProof` — a signature over that nonce using the
# claimed cert's private key. A stranger who harvested a friend's fingerprint
# but not its key cannot sign the nonce, so impersonation is impossible.

def _our_cert_pem() -> str:
    if not os.path.exists(CERT_PEM):
        ensure_tls()
    with open(CERT_PEM, "r", encoding="utf-8") as f:
        return f.read()


def _load_priv_key():
    if STATE.priv_key is None:
        from cryptography.hazmat.primitives import serialization
        with STATE.priv_key_lock:
            if STATE.priv_key is None:
                if not os.path.exists(CERT_KEY):
                    ensure_tls()
                with open(CERT_KEY, "rb") as f:
                    STATE.priv_key = serialization.load_pem_private_key(f.read(), password=None)
    return STATE.priv_key


def _sign(data: bytes) -> str:
    """Sign bytes with our private key (RSA PKCS1v15-SHA256), hex-encoded."""
    import binascii

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    sig = _load_priv_key().sign(data, padding.PKCS1v15(), hashes.SHA256())
    return binascii.hexlify(sig).decode("ascii")


def _verify(cert_pem: str, data: bytes, sig_hex: str) -> bool:
    """Verify a signature over `data` using the public key in `cert_pem`."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
        pub = cert.public_key()
        sig = bytes.fromhex(sig_hex)
        pub.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def _cert_fingerprint_of_pem(cert_pem: str) -> str:
    """SHA-256 fingerprint of the cert in `cert_pem` (empty on parse failure)."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
        return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    except Exception:
        return ""
