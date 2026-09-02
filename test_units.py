#!/usr/bin/env python3
"""Safety-net unit tests for pure, dependency-light leaf functions in server.py.

Pins existing behavior of: _safe_filename (attachment-name sanitisation),
history crypto (_hist_key/_history_encrypt/_history_decrypt), the identity
proof pair (_sign/_verify), cert fingerprints, VERSION/_git_version semantics,
and the friendly_name/display_name fallback. server.py and naming.py are
imported unmodified; the environment is fully sandboxed: HOME is pointed at a
fresh temp dir before `import server` so every config/cert/key/history file
the import or these tests produce lands in the temp dir, never the real $HOME.
"""

import base64
import hashlib
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Sandbox BEFORE importing server: it resolves CERT_DIR/STATE_DIR/HISTORY_PATH
# from $HOME at import time (and importing naming is side-effect free).
_REAL_HOME = os.path.expanduser("~")
_TMP_HOME = tempfile.mkdtemp(prefix="lanchat-units-")
os.environ["HOME"] = _TMP_HOME

import naming  # noqa: E402
import server  # noqa: E402


def _home_snapshot():
    """Paths of any real-$HOME lanchat files that exist right now."""
    paths = [
        os.path.join(_REAL_HOME, ".config", "omarchy", "lanchat.json"),
        os.path.join(_REAL_HOME, ".config", "omarchy", "lanchat-certs"),
        os.path.join(_REAL_HOME, ".local", "state", "lanchat"),
    ]
    found = set()
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in files:
                    found.add(os.path.join(root, f))
        elif os.path.exists(p):
            found.add(p)
    return found


def _check_home_clean(before):
    """No lanchat file may appear in (or vanish from) the real $HOME."""
    after = _home_snapshot()
    assert before == after, "suite touched the real $HOME: new=%s removed=%s" % (
        sorted(after - before),
        sorted(before - after),
    )


def main():
    real_before = _home_snapshot()
    try:
        _check_home_clean(real_before)

        # ---------------------------------------------------------------
        print("OK _safe_filename: exact reductions")
        sf = server._safe_filename
        assert sf("../../etc/passwd") == "passwd", sf("../../etc/passwd")
        assert sf("..") == "download", sf("..")
        assert sf("") == "download", sf("")
        assert sf("a/b\\c") == "c", sf("a/b\\c")
        assert sf("\x00bad") == "bad", sf("\x00bad")
        assert sf(" normal.txt ") == "normal.txt", sf(" normal.txt ")
        assert sf("...") == "download", sf("...")

        # ---------------------------------------------------------------
        print("OK _safe_filename: hostile corpus invariants")
        corpus = [
            "../..",
            "/abs/path.txt",
            "\\windows\\path",
            "..\\..\\evil",
            "a/b/../../c",
            "c\ttab",
            "nl\nline",
            "\x1f\x7f",
            "héllo/ünicode",
            "..unicode..",
            "\x00\x01\x02",
            "\\",
            "/",
            "//",
            "\\\\",
            "..//..",
            " . . . ",
            ".hidden/",
            "dir\\sub\\..\\file",
        ]
        for name in corpus:
            out = sf(name)
            assert "/" not in out, (name, out)
            assert "\\" not in out, (name, out)
            assert out not in ("..", "."), (name, out)
            assert out, (name, out)
        assert sf("\x00\x01\x02") == "download", "hostile-only input -> 'download'"
        assert sf("\t\n") == "download", "whitespace-only input -> 'download'"

        # ---------------------------------------------------------------
        print("OK history crypto: _hist_key (creates 32-byte key, 0600, stable)")
        key1 = server._hist_key()
        assert len(key1) == 32, len(key1)
        assert os.path.exists(server.HISTORY_KEY), server.HISTORY_KEY
        key2 = server._hist_key()
        assert key1 == key2, "hist key must be stable across calls"
        mode = os.stat(server.HISTORY_KEY).st_mode & 0o777
        assert mode == 0o600, oct(mode)

        print("OK history crypto: encrypt/decrypt round-trip")
        assert server.HISTORY_MAGIC == b"LANCHIST1", server.HISTORY_MAGIC
        ct1 = server._history_encrypt(b"hello world")
        ct2 = server._history_encrypt(b"hello world")
        assert isinstance(ct1, str), type(ct1)
        assert ct1 != ct2, "fresh nonce per call: ciphertexts must differ"
        assert server._history_decrypt(ct1) == b"hello world", ct1
        assert server._history_decrypt(ct2) == b"hello world", ct2
        empty_ct = server._history_encrypt(b"")
        assert server._history_decrypt(empty_ct) == b"", "empty plaintext round-trip"

        print("OK history crypto: decrypt failure modes")
        raw = bytearray(base64.b64decode(ct1))
        raw[len(raw) // 2] ^= 0xFF  # flip one bit in the middle
        tampered = base64.b64encode(bytes(raw)).decode("ascii")
        for label, blob in (
            ("tampered ciphertext", tampered),
            (
                "missing magic prefix",
                base64.b64encode(b"XXNOPE" + base64.b64decode(ct1)[6:]).decode("ascii"),
            ),
        ):
            try:
                server._history_decrypt(blob)
            except Exception:
                pass  # ValueError or InvalidTag both acceptable
            else:
                raise AssertionError("decrypt accepted %s" % label)
        # Key rotation: overwrite the key file with fresh random bytes -> old
        # ciphertexts must no longer decrypt.
        with open(server.HISTORY_KEY, "wb") as f:
            f.write(secrets.token_bytes(32))
        try:
            server._history_decrypt(ct1)
        except Exception:
            pass
        else:
            raise AssertionError("decrypt succeeded after key rotation")

        # ---------------------------------------------------------------
        print("OK sign/verify + cert fingerprints")
        server.ensure_tls()
        cert_pem_path = os.path.join(server.CERT_DIR, "cert.pem")
        assert os.path.exists(cert_pem_path), cert_pem_path
        assert os.path.exists(os.path.join(server.CERT_DIR, "key.pem")), "key.pem missing"
        pem = open(cert_pem_path).read()
        sig = server._sign(b"data")
        assert isinstance(sig, str), type(sig)
        assert server._verify(pem, b"data", sig) is True, "valid signature must verify"
        assert server._verify(pem, b"other data", sig) is False, "wrong data must not verify"
        bad_sig = ("0" if sig[0] != "0" else "1") + sig[1:]
        assert server._verify(pem, b"data", bad_sig) is False, "tampered sig must not verify"
        assert server._verify("not a cert", b"data", sig) is False, "garbage pem must not verify"

        fp = server.cert_fingerprint()
        assert isinstance(fp, str) and re.fullmatch(r"[0-9a-f]{64}", fp), fp
        assert fp == server._cert_fingerprint_of_pem(pem), "fingerprint must match PEM hash"
        assert server._cert_fingerprint_of_pem("not a cert") == "", "garbage pem -> empty fp"
        assert server._cert_fingerprint_of_pem(pem) == server._cert_fingerprint_of_pem(pem), (
            "fingerprint must be stable"
        )
        assert server.host_id() == fp, "host_id is defined as the cert fingerprint"

        # ---------------------------------------------------------------
        print("OK VERSION / _git_version semantics")
        assert re.fullmatch(r"\d+\.\d+\.\d+(-[0-9a-f]+)?", server.VERSION), server.VERSION
        assert server._git_version().startswith(server.VERSION), (
            server._git_version(),
            server.VERSION,
        )
        src = open(os.path.join(HERE, "server.py")).read()
        literal = re.search(r'VERSION = "(\d+\.\d+\.\d+)"', src)
        assert literal, "first VERSION literal not found in server.py"
        bare = literal.group(1)
        assert server.VERSION.split("-")[0] == bare, (server.VERSION, bare)
        chk = subprocess.run(
            [sys.executable, "scripts/bump_version.py", "--check"],
            cwd=HERE,
            capture_output=True,
            text=True,
        )
        assert chk.returncode == 0, (chk.returncode, chk.stdout, chk.stderr)

        # ---------------------------------------------------------------
        print("OK friendly_name / display_name fallback")
        expected = "HandplantShoveIt180"
        assert naming.friendly_name("abc") == expected, naming.friendly_name("abc")
        digest = hashlib.sha256(b"abc").digest()
        formula = (
            naming._TRICK_MODIFIERS[digest[1] % len(naming._TRICK_MODIFIERS)]
            + naming._SKATE_TRICKS[digest[0] % len(naming._SKATE_TRICKS)]
        )
        assert formula == expected, formula
        assert naming.friendly_name("abc") == naming.friendly_name("abc"), "deterministic"
        assert isinstance(naming.friendly_name("abc"), str) and naming.friendly_name("abc"), (
            "friendly_name result must be a non-empty str"
        )

        server.load_config()  # not run at import: main() calls it; we do it here
        initial = server.CONFIG.get("displayName")
        assert initial, "load_config must set a displayName"
        assert any(initial.startswith(m) for m in naming._TRICK_MODIFIERS), initial
        assert server.display_name() != "", "display_name must never be empty"
        assert server.display_name() != socket.gethostname(), "never a bare hostname"
        server.CONFIG["displayName"] = "My Custom Name"
        assert server.display_name() == "My Custom Name", "custom name must be preserved"

        _check_home_clean(real_before)
        print("ALL OK")
    finally:
        shutil.rmtree(_TMP_HOME, ignore_errors=True)
        os.environ["HOME"] = _REAL_HOME


if __name__ == "__main__":
    sys.exit(main())
