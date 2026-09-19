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
import base64
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


def _hist_stub_state():
    """A minimal STATE stub for history-module unit tests (no daemon)."""
    import history as history_mod

    class _State:
        pass

    st = _State()
    st.hist_lock = threading.Lock()
    st.hist_crypto = None
    st.history = []
    return history_mod, st


def test_mark_attachment_saved():
    """mark_attachment_saved: in-place flag + persistence, and False for
    unknown / empty mid / attachment-less messages."""
    import tempfile

    history_mod, st = _hist_stub_state()
    tmp = tempfile.mkdtemp(prefix="lanchat-hist-test-")
    try:
        # Redirect the state files away from the real ~/.local/state/lanchat.
        history_mod.STATE_DIR = tmp
        history_mod.HISTORY_PATH = os.path.join(tmp, "history.json")
        history_mod.HISTORY_KEY = os.path.join(tmp, "history.key")
        history_mod.init(st)

        st.history = [
            {"mid": "m1", "from": "p", "to": "me",
             "attachment": {"fileId": "f1", "name": "a.txt"}},
            {"mid": "m2", "from": "p", "to": "me",
             "attachment": {"fileId": "f2", "name": "b.txt"}},
            {"mid": "m3", "from": "p", "to": "me"},  # no attachment
        ]
        m1 = st.history[0]

        # happy path: flag set IN PLACE on the shared dict, history re-persisted
        assert history_mod.mark_attachment_saved("m1") is True
        assert m1["attachment"]["accepted"] is True, "flag must be set in place"
        assert st.history[1]["attachment"].get("accepted") is None
        assert os.path.exists(history_mod.HISTORY_PATH), "history must re-persist"

        # negative paths: no match, no mid, no attachment
        assert history_mod.mark_attachment_saved("nope") is False
        assert history_mod.mark_attachment_saved("") is False
        assert history_mod.mark_attachment_saved("m3") is False

        # idempotent: second call still True
        assert history_mod.mark_attachment_saved("m1") is True
        print("OK  mark_attachment_saved: in-place flag + persist + negatives")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_attachment_limits():
    """Unit tests for the resource limits (no daemons needed).

    Covers: over-ceiling cumulative write aborts + .part removed; written >
    declared total rejection; declared total over ceiling aborts; 5th
    concurrent transfer rejected; over-ceiling register_attachment refusal."""
    import tempfile

    import attachments as att

    class _State:
        pass

    st = _State()
    st.att_lock = threading.Lock()
    st.attachments = {}
    st.dl_lock = threading.Lock()
    att.init(st)

    # Deferred `import server` inside _dl_abort_locked/_dl_begin must not hit
    # a live daemon: stub the log hook before importing server.
    import server as srv
    logged = []
    real_log = srv._log
    srv._log = lambda msg: logged.append(msg)
    try:
        # Shrink the ceiling for the limit tests: the real 100 GiB constant
        # is sanity-checked symbolically below, but exercising the abort paths
        # against a 1 MiB ceiling keeps the test fast and disk-free.
        real_cap = att.ATT_MAX_BYTES
        att.ATT_MAX_BYTES = 1024 * 1024
        real_cap_value = real_cap  # the true 100 GiB ceiling, for the TTL check
        tmp = tempfile.mkdtemp(prefix="lanchat-attlim-")
        try:
            # ---- 1) register_attachment refuses over-ceiling files --------
            big = os.path.join(tmp, "big.bin")
            with open(big, "wb") as f:
                f.seek(att.ATT_MAX_BYTES + 1)
                f.write(b"\0")
            ok = att.register_attachment("bigid", big, "big.bin")
            assert ok is False, "over-ceiling register_attachment must refuse"
            with st.att_lock:
                assert "bigid" not in st.attachments, "refused file must not register"
            small = os.path.join(tmp, "small.bin")
            with open(small, "wb") as f:
                f.write(b"ok")
            ok = att.register_attachment("smallid", small, "small.bin")
            assert ok is True, "under-ceiling register_attachment must succeed"
            print("OK  register_attachment refuses %d-byte file, allows small" %
                  (att.ATT_MAX_BYTES + 1))

            # ---- 1b) TTL scales with file size (tiny test ceiling in effect,
            # but the TTL math uses its own constants, so use real sizes via a
            # fake size through a small explicit probe: base for small file,
            # capped max for a hypothetically huge one — computed, not faked,
            # by checking the scaling formula against the module constants).
            # NOTE: read through att.STATE, not the local `st` — an earlier
            # test (busy mapping) re-inits attachments with its own State, so
            # the module-global STATE is not this test's `st` object.
            small_exp = att.ATT_TTL_BASE_S
            got = att.STATE.attachments["smallid"]["expires"] - time.time()
            assert att.ATT_TTL_BASE_S - 2 <= got <= att.ATT_TTL_BASE_S + 5, \
                "small file TTL should be base %s, got %s" % (small_exp, got)
            # The formula itself, evaluated against the REAL 100 GiB ceiling
            # (the module cap is shrunk for these tests — see above).
            size = real_cap_value
            steps = max(0, size - att.ATT_CHUNK_RAW) // att.ATT_TTL_SCALE_BYTES
            want = min(att.ATT_TTL_BASE_S + steps * att.ATT_TTL_STEP_S,
                       att.ATT_TTL_MAX_S)
            assert want == att.ATT_TTL_MAX_S, \
                "100 GiB must land on the TTL cap, got %s" % want
            # Explicit ttl wins over scaling (existing test convention).
            ok = att.register_attachment("ttlid", small, "t.bin", ttl=30.0)
            got30 = att.STATE.attachments["ttlid"]["expires"] - time.time()
            assert ok and 28 <= got30 <= 32, "explicit ttl must win, got %s" % got30
            print("OK  registration TTL: base for small, formula caps at 1h, explicit wins")

            # ---- 2) declared total over ceiling aborts at _dl_chunk -------
            save = os.path.join(tmp, "over-total.bin")
            assert att._dl_begin("t1", "peer1", save, "", "m1"), "_dl_begin failed"
            part = save + ".part"
            ok, total, written = att._dl_chunk("t1", "peer1", "", att.ATT_MAX_BYTES + 1)
            assert ok is False, "over-ceiling declared total must abort"
            assert not os.path.exists(part), "aborted transfer left .part behind"
            with st.dl_lock:
                assert "t1" not in att._dl, "aborted transfer not popped"
            print("OK  declared total over ATT_MAX_BYTES aborts and cleans up")

            # ---- 3) written > declared total rejection --------------------
            save = os.path.join(tmp, "over-write.bin")
            assert att._dl_begin("t2", "peer1", save, "", "m2")
            part = save + ".part"
            data = base64.b64encode(b"x" * 10).decode()
            ok, _, written = att._dl_chunk("t2", "peer1", data, 5)
            assert ok is False, "written > total must be rejected"
            assert not os.path.exists(part), "over-write left .part behind"
            with st.dl_lock:
                assert "t2" not in att._dl, "over-write transfer not popped"
            # A clean write at exactly the declared total still works.
            assert att._dl_begin("t3", "peer1", save, "", "m3")
            data3 = base64.b64encode(b"y" * 5).decode()
            ok, total, written = att._dl_chunk("t3", "peer1", data3, 5)
            assert ok and total == 5 and written == 5, "exact-total chunk failed"
            att._dl_finish("t3", "peer1", True)
            print("OK  written > declared total rejected; exact total accepted")

            # ---- 4) cumulative write over ceiling aborts ------------------
            save = os.path.join(tmp, "over-ceiling.bin")
            assert att._dl_begin("t4", "peer1", save, "", "m4")
            part = save + ".part"
            # Two chunks whose sum passes ATT_MAX_BYTES (sender never declared
            # a total, so this is the backstop path).
            half = att.ATT_MAX_BYTES // 2 + 1
            big_chunk = base64.b64encode(b"\0" * half).decode()
            ok, _, _ = att._dl_chunk("t4", "peer1", big_chunk, 0)
            assert ok, "first half-ceiling chunk rejected"
            ok, _, _ = att._dl_chunk("t4", "peer1", big_chunk, 0)
            assert ok is False, "cumulative write over ceiling must abort"
            assert not os.path.exists(part), "ceiling abort left .part behind"
            with st.dl_lock:
                assert "t4" not in att._dl, "ceiling abort not popped"
            # After the abort the sender can't stream into the dead entry.
            ok, _, _ = att._dl_chunk("t4", "peer1", big_chunk, 0)
            assert ok is False, "chunk into aborted transfer accepted"
            print("OK  cumulative write over ATT_MAX_BYTES aborts, entry unusable")

            # ---- 4b) disk-full on write tears the transfer down now --------
            save = os.path.join(tmp, "diskfull.bin")
            assert att._dl_begin("t4b", "peer1", save, "", "m4b")
            part = save + ".part"
            import attachments as _att_mod
            real_fh = _att_mod._dl["t4b"]["fh"]

            class _FullFH:
                # close() must exist and swallow errors — _dl_abort_locked
                # closes the handle before removing the .part.
                @staticmethod
                def write(_data):
                    raise OSError(28, "No space left on device")

                @staticmethod
                def close():
                    pass

            _att_mod._dl["t4b"]["fh"] = _FullFH()
            ok, _, _ = att._dl_chunk("t4b", "peer1", base64.b64encode(b"z" * 10).decode(), 0)
            assert ok is False, "disk-full write must fail the chunk"
            assert "t4b" not in _att_mod._dl, "disk-full transfer not torn down"
            assert not os.path.exists(part), "disk-full left a .part behind"
            del real_fh  # handle already closed by the abort path
            print("OK  disk-full write aborts immediately, .part removed")

            # ---- 5) per-peer (2) and global (8) caps ----------------------
            saves = []
            # (a) peer1 fills its per-peer floor of 2.
            for i in range(att.ATT_MAX_PER_PEER):
                s = os.path.join(tmp, "p1_%d.bin" % i)
                assert att._dl_begin("p1_%d" % i, "peer1", s, "", "mp1_%d" % i), \
                    "peer1 slot %d refused" % i
                saves.append(s)
            s3 = os.path.join(tmp, "p1_2.bin")
            ok = att._dl_begin("p1_2", "peer1", s3, "", "mp1_2")
            assert ok is False, "peer1 3rd transfer must be refused"
            assert att.last_refusal_reason == "per-peer", \
                "expected per-peer reason, got %r" % att.last_refusal_reason
            assert not os.path.exists(s3 + ".part"), "refused transfer opened .part"
            assert any("reason=per-peer" in m for m in logged), \
                "per-peer refusal not logged"
            print("OK  peer's 3rd transfer refused (per-peer cap = %d)" %
                  att.ATT_MAX_PER_PEER)
            # (b) another peer still gets slots while the board is not full.
            s2 = os.path.join(tmp, "p2_0.bin")
            assert att._dl_begin("p2_0", "peer2", s2, "", "mp2_0"), \
                "peer2 refused while board not full"
            saves.append(s2)
            # (c) fill the board to the global cap across distinct peers,
            # 2 slots each (peer1/peer2 already hold 2 and 1).
            n = 0
            while len(att._dl) < att.ATT_MAX_CONCURRENT:
                pid = "peerfill%d" % n
                if sum(1 for e in att._dl.values() if e["peer"] == pid) >= att.ATT_MAX_PER_PEER:
                    continue
                s = os.path.join(tmp, "fill%d.bin" % n)
                assert att._dl_begin("fill%d" % n, pid, s, "", "mf%d" % n), \
                    "fill transfer %d refused" % n
                saves.append(s)
                n += 1
            assert len(att._dl) == att.ATT_MAX_CONCURRENT, "board not at cap"
            s9 = os.path.join(tmp, "over.bin")
            ok = att._dl_begin("over", "peernew", s9, "", "mover")
            assert ok is False, "9th transfer must be refused"
            assert att.last_refusal_reason == "max-concurrent", \
                "expected max-concurrent reason, got %r" % att.last_refusal_reason
            assert not os.path.exists(s9 + ".part"), "refused transfer opened .part"
            assert any("reason=max-concurrent" in m for m in logged), \
                "max-concurrent refusal not logged"
            print("OK  %dth concurrent transfer refused (cap = %d)" %
                  (att.ATT_MAX_CONCURRENT + 1, att.ATT_MAX_CONCURRENT))
            # (d) after cleanup everything works again. (_dl_finish takes
            # dl_lock itself — never call it while holding the lock.)
            for fid in list(att._dl):
                att._dl_finish(fid, att._dl[fid]["peer"], False)
            assert not att._dl, "cap test left transfers registered"
            s_ok = os.path.join(tmp, "after.bin")
            assert att._dl_begin("after", "peer1", s_ok, "", "mafter"), \
                "transfer refused after cleanup"
            saves.append(s_ok)
            att._dl_finish("after", "peer1", False)
            print("OK  transfers work again after cleanup")
        finally:
            att.ATT_MAX_BYTES = real_cap
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        srv._log = real_log


def test_http_attachment_streaming():
    """http_api /attachment streams in ATT_HTTP_CHUNK chunks with a correct
    Content-Length, via a stub handler capturing wfile writes."""
    import io as io_mod
    import tempfile

    import http_api

    class _State:
        pass

    st = _State()
    st.att_lock = threading.Lock()
    st.attachments = {}
    st.config = {"token": "tok"}  # /attachment auth reads STATE.config["token"]
    http_api.init(st)
    import attachments as _att
    _att.init(st)  # server.get_attachment reads attachments.STATE, same stub

    tmp = tempfile.mkdtemp(prefix="lanchat-atthttp-")
    try:
        payload = os.urandom(http_api.ATT_HTTP_CHUNK * 2 + 137)
        path = os.path.join(tmp, "file.bin")
        with open(path, "wb") as f:
            f.write(payload)
        st.attachments["fid1"] = {"path": path, "name": "file.bin",
                                  "expires": time.time() + 60}

        # Track how big any single read/written buffer gets.
        writes = []

        class _StubWFile(io_mod.BufferedIOBase):
            def writable(self):
                return True

            def write(self, b):
                writes.append(len(b))
                return len(b)

        handler = object.__new__(http_api._ApiHandler)
        handler.path = "/attachment?fileId=fid1"
        handler.headers = {"Authorization": "Bearer tok"}
        handler.wfile = _StubWFile()
        handler.rfile = io_mod.BytesIO(b"")
        handler.client_address = ("127.0.0.1", 0)
        handler.command = "GET"
        handler.request_version = "HTTP/1.1"
        handler.headers = {"Authorization": "Bearer tok"}
        handler.close_connection = False
        sent = []
        handler.send_response = lambda code: sent.append(("status", code))
        handler.send_header = lambda k, v: sent.append((k, str(v)))
        handler.end_headers = lambda: sent.append(("end", None))

        handler.do_GET()

        codes = [v for k, v in sent if k == "status"]
        assert codes == [200], "expected 200, got %r" % codes
        cl = [v for k, v in sent if k == "Content-Length"]
        assert cl == [str(len(payload))], "Content-Length %r != %d" % (cl, len(payload))
        ct = [v for k, v in sent if k == "Content-Type"]
        assert ct == ["application/octet-stream"], "Content-Type %r" % ct
        cd = [v for k, v in sent if k == "Content-Disposition"]
        assert cd == ["attachment; filename=file.bin"], "Content-Disposition %r" % cd
        # Body only (headers were captured, not written to wfile).
        body_writes = [n for n in writes]
        assert sum(body_writes) == len(payload), \
            "streamed %d bytes, want %d" % (sum(body_writes), len(payload))
        assert max(body_writes) <= http_api.ATT_HTTP_CHUNK, \
            "single write %d exceeds chunk size %d" % (max(body_writes), http_api.ATT_HTTP_CHUNK)
        assert len(body_writes) > 1, "expected multi-chunk streaming, got 1 write"
        print("OK  /attachment streams %d bytes in %d chunks (max write %d), "
              "Content-Length exact" % (len(payload), len(body_writes), max(body_writes)))

        # Missing file: clean 404, no crash.
        st.attachments["fid2"] = {"path": os.path.join(tmp, "gone.bin"),
                                  "name": "gone.bin", "expires": time.time() + 60}
        writes.clear(); sent.clear()
        handler.path = "/attachment?fileId=fid2"
        handler.headers = {"Authorization": "Bearer tok"}
        handler._send_json = lambda code, obj: sent.append(("status", code))
        handler.do_GET()
        assert [v for k, v in sent if k == "status"] == [404], "missing file not 404"
        print("OK  /attachment missing file -> 404")

        # Query-string tokens are DEAD: same request with the token in the URL
        # must 401 — credentials ride the Authorization header only.
        writes.clear(); sent.clear()
        handler.path = "/attachment?fileId=fid1&token=tok"
        handler.headers = {}
        handler.do_GET()
        assert [v for k, v in sent if k == "status"] == [401], \
            "query-token auth must be refused"
        print("OK  /attachment rejects token-in-URL (header only)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_busy_error_mapping():
    """Unit test: when _dl_begin refuses with reason per-peer or
    max-concurrent, the server busy mapping produces error=="busy" and the
    user-facing attachment-saved string; other reasons keep the old strings."""
    import attachments as att

    # The REAL mapping server.py uses — not a local copy — so a change to the
    # production mapping breaks this test.
    for reason, want_err, want_ui in (
            ("per-peer", "busy", "attachment transfer busy — try again shortly"),
            ("max-concurrent", "busy", "attachment transfer busy — try again shortly"),
            ("open-failed", "cannot open download file", "cannot open download file"),
            (None, "cannot open download file", "cannot open download file")):
        got_err, got_ui = att.refusal_error(reason)
        assert got_err == want_err and got_ui == want_ui, \
            "reason %r mapped to (%r, %r)" % (reason, got_err, got_ui)
    assert att.ATT_MAX_CONCURRENT == 8 and att.ATT_MAX_PER_PEER == 2, \
        "unexpected cap constants"
    assert att.ATT_MAX_BYTES == 100 * 1024 * 1024 * 1024, \
        "unexpected size ceiling"
    # _dl_begin itself sets last_refusal_reason=None on success (unit level).
    import tempfile

    # Budget refusal: with one active transfer already reserving space,
    # a second is refused when the aggregate passes the budget. Shrink
    # the budget to make the math tangible.
    class _State:
        pass

    real_budget = att.ATT_MAX_RESERVED_BYTES
    att.ATT_MAX_RESERVED_BYTES = 1500  # bytes
    st2 = _State()
    st2.att_lock = threading.Lock(); st2.attachments = {}; st2.dl_lock = threading.Lock()
    att.init(st2)
    tmp = tempfile.mkdtemp(prefix="lanchat-budget-")
    try:
        s1 = os.path.join(tmp, "b1.bin")
        assert att._dl_begin("budget_a", "pb", s1, "", "mb1"), "first transfer must pass"
        with st2.dl_lock:
            att._dl["budget_a"]["total"] = 1200  # declared: reserves 1200
        s2 = os.path.join(tmp, "b2.bin")
        ok = att._dl_begin("budget_b", "pc", s2, "", "mb2")
        assert ok is False and att.last_refusal_reason == "disk-budget", \
            "second transfer must hit the aggregate budget, got %r" % att.last_refusal_reason
        assert not os.path.exists(s2 + ".part"), "budget-refused transfer opened .part"
        print("OK  aggregate disk budget refuses transfer beyond the cap")
    finally:
        att._dl_finish("budget_a", "pb", False)
        att.ATT_MAX_RESERVED_BYTES = real_budget
        shutil.rmtree(tmp, ignore_errors=True)

    # Grace window: a chunk with no declared total past the window aborts.
    att.init(st2)
    tmp2 = tempfile.mkdtemp(prefix="lanchat-grace-")
    try:
        sg = os.path.join(tmp2, "g.bin")
        assert att._dl_begin("grace_t", "pg", sg, "", "mg")
        with st2.dl_lock:
            att._dl["grace_t"]["ts"] = 0  # transfer looks ancient
        ok, _, _ = att._dl_chunk("grace_t", "pg",
                                 base64.b64encode(b"x").decode(), 0)
        assert ok is False, "chunk without declared total past grace must abort"
        with st2.dl_lock:
            assert "grace_t" not in att._dl, "grace abort not torn down"
        assert not os.path.exists(sg + ".part"), "grace abort left .part"
        print("OK  chunks without a bounded declared total refused after grace")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # Total is final: a changed total mid-stream aborts.
    att.init(st2)
    tmp3 = tempfile.mkdtemp(prefix="lanchat-total-")
    try:
        stt = os.path.join(tmp3, "t.bin")
        assert att._dl_begin("total_t", "pt", stt, "", "mt")
        ok, _, _ = att._dl_chunk("total_t", "pt",
                                 base64.b64encode(b"aa").decode(), 10)
        assert ok, "first chunk with total must write"
        ok, _, _ = att._dl_chunk("total_t", "pt",
                                 base64.b64encode(b"bb").decode(), 99)
        assert ok is False, "changed declared total must abort"
        with st2.dl_lock:
            assert "total_t" not in att._dl
        print("OK  declared total is final; rewriting it aborts")
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    class _State:
        pass

    st = _State()
    st.att_lock = threading.Lock()
    st.attachments = {}
    st.dl_lock = threading.Lock()
    att.init(st)
    tmp = tempfile.mkdtemp(prefix="lanchat-busy-test-")
    try:
        s = os.path.join(tmp, "ok.bin")
        assert att._dl_begin("busy_t", "peerx", s, "", "mb")
        assert att.last_refusal_reason is None, "success must clear the reason"
        att._dl_finish("busy_t", "peerx", False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK  busy error mapping: busy reasons -> 'busy', others unchanged")


def main():
    test_mark_attachment_saved()
    test_busy_error_mapping()
    test_attachment_limits()
    test_http_attachment_streaming()
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
        _s.STATE.stdout = open(os.devnull, "w")  # keep in-process event noise out

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
        import attachments as _am
        import server as _s
        _s.CONFIG["token"] = TOKEN
        _s.STATE.stdout = open(os.devnull, "w")
        # Earlier limit tests rebind attachments.STATE to a stub without
        # dl_lock; this block needs the real State's transfer machinery.
        _am.init(_s.STATE)
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
