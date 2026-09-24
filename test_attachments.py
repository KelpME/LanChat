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

            # ---- 1b) TTL scales with file size (1:1 with the extracted
            # formula, so the scaling is exercised at real sizes without
            # writing gigabytes to disk).
            # NOTE: read through att.STATE, not the local `st` — an earlier
            # test (busy mapping) re-inits attachments with its own State, so
            # the module-global STATE is not this test's `st` object.
            small_exp = att.ATT_TTL_BASE_S
            got = att.STATE.attachments["smallid"]["expires"] - time.time()
            assert att.ATT_TTL_BASE_S - 2 <= got <= att.ATT_TTL_BASE_S + 5, \
                "small file TTL should be base %s, got %s" % (small_exp, got)
            # The scaling must stay meaningful under the CONSERVATIVE ceiling:
            # a file at the ceiling in force lands on the 1 h cap, a mid-size
            # file lands between base and cap (not pinned to base), and a small
            # file stays at base. Run the probe against the REAL ceiling (the
            # helper reads the live module attribute) and restore it after —
            # no gigabytes are written, only arithmetic is checked.
            cap_for_ttl = att.ATT_MAX_BYTES_DEFAULT
            att.ATT_MAX_BYTES = cap_for_ttl
            try:
                ttl_at_cap = att._registration_ttl(cap_for_ttl)
                ttl_mid = att._registration_ttl(cap_for_ttl // 4)
                ttl_small = att._registration_ttl(1024)
            finally:
                att.ATT_MAX_BYTES = real_cap
            assert ttl_at_cap == att.ATT_TTL_MAX_S, \
                "a file at the per-file ceiling must land on the TTL cap, got %s" % ttl_at_cap
            assert att.ATT_TTL_BASE_S < ttl_mid < att.ATT_TTL_MAX_S, \
                "mid-size file must scale between base and cap, got %s" % ttl_mid
            assert ttl_small == att.ATT_TTL_BASE_S, \
                "small file must keep the base TTL, got %s" % ttl_small
            # The scaling step is derived from the ceiling, so raising the
            # ceiling scales the window coarser instead of leaving it pinned.
            att.ATT_MAX_BYTES = 100 * 1024 * 1024 * 1024
            try:
                ttl_big_cap = att._registration_ttl(100 * 1024 * 1024 * 1024)
                ttl_big_mid = att._registration_ttl(10 * 1024 * 1024 * 1024)
            finally:
                att.ATT_MAX_BYTES = real_cap
            assert ttl_big_cap == att.ATT_TTL_MAX_S and ttl_big_mid > att.ATT_TTL_BASE_S, \
                "raised ceiling must scale the TTL window too (%s, %s)" % (ttl_big_cap, ttl_big_mid)
            # Explicit ttl wins over scaling (existing test convention).
            ok = att.register_attachment("ttlid", small, "t.bin", ttl=30.0)
            got30 = att.STATE.attachments["ttlid"]["expires"] - time.time()
            assert ok and 28 <= got30 <= 32, "explicit ttl must win, got %s" % got30
            print("OK  registration TTL: base for small, scales mid, caps at 1h for a "
                  "%d-byte file, explicit wins" % cap_for_ttl)

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
    """Unit test: when _dl_begin refuses with reason per-peer, max-concurrent,
    disk-budget or disk-unknown, the server refusal mapping produces the right
    peer/UI strings; local I/O failures keep the old strings. Also pins the
    conservative limit defaults and the capacity gates themselves."""
    import attachments as att

    # Capture the daemon log lines the refusal paths write, so a capacity
    # refusal is provably diagnosable (not just a silent False).
    import server as srv
    logged_budget = []
    _real_log = srv._log
    srv._log = lambda m: logged_budget.append(m)
    try:
        _busy_error_mapping_body(att, logged_budget)
    finally:
        srv._log = _real_log


def _busy_error_mapping_body(att, logged_budget):
    # The REAL mapping server.py uses — not a local copy — so a change to the
    # production mapping breaks this test.
    for reason, want_err, want_ui in (
            ("per-peer", "busy", "attachment transfer busy — try again shortly"),
            ("max-concurrent", "busy", "attachment transfer busy — try again shortly"),
            ("disk-budget", "disk limit",
             "attachment too large for the allowed disk space"),
            ("disk-unknown", "cannot open download file",
             "cannot verify free disk space — download refused"),
            ("open-failed", "cannot open download file", "cannot open download file"),
            (None, "cannot open download file", "cannot open download file")):
        got_err, got_ui = att.refusal_error(reason)
        assert got_err == want_err and got_ui == want_ui, \
            "reason %r mapped to (%r, %r)" % (reason, got_err, got_ui)
    assert att.ATT_MAX_CONCURRENT == 8 and att.ATT_MAX_PER_PEER == 2, \
        "unexpected cap constants"
    # The per-file ceiling must stay CONSERVATIVE: it was 100 GiB, which made
    # the aggregate budget decorative (one transfer could be enormous and still
    # sit under the budget). Default is 4 GiB and must never be larger than the
    # aggregate budget, or the budget could not bind.
    assert att.ATT_MAX_BYTES == att.ATT_MAX_BYTES_DEFAULT, \
        "unexpected size ceiling"
    assert att.ATT_MAX_BYTES_DEFAULT == 4 * 1024 * 1024 * 1024, \
        "per-file ceiling must be the conservative 4 GiB default"
    assert att.ATT_MAX_RESERVED_BYTES_DEFAULT == 8 * 1024 * 1024 * 1024, \
        "aggregate budget must be 8 GiB"
    assert att.ATT_MIN_FREE_BYTES_DEFAULT == 4 * 1024 * 1024 * 1024, \
        "keep-free floor must be 4 GiB"
    assert att.ATT_MAX_BYTES_DEFAULT <= att.ATT_MAX_RESERVED_BYTES_DEFAULT, \
        "a per-file cap above the aggregate budget makes the budget unenforceable"
    assert att.ATT_LIMIT_MIN < att.ATT_LIMIT_HARD_MAX, "limit clamp bounds"
    # _dl_begin itself sets last_refusal_reason=None on success (unit level).
    import tempfile

    # Capacity gates, exercised with small numbers so the math is tangible and
    # the test writes nothing to disk beyond an empty .part. The three gates are
    # independent by design: per-file ceiling (cap), aggregate budget, and the
    # filesystem floor each get their own refusal case below.
    class _State:
        pass

    CAP = 4000     # per-file ceiling for this block
    BUDGET = 1500  # aggregate budget across active transfers
    FLOOR = 64     # keep-free floor (tiny: the real filesystem dwarfs it)

    real_budget = att.ATT_MAX_RESERVED_BYTES
    real_cap = att.ATT_MAX_BYTES
    real_floor = att.ATT_MIN_FREE_BYTES
    att.ATT_MAX_RESERVED_BYTES = BUDGET
    att.ATT_MAX_BYTES = CAP
    att.ATT_MIN_FREE_BYTES = FLOOR
    st2 = _State()
    st2.att_lock = threading.Lock(); st2.attachments = {}; st2.dl_lock = threading.Lock()
    att.init(st2)
    tmp = tempfile.mkdtemp(prefix="lanchat-budget-")
    try:
        s1 = os.path.join(tmp, "b1.bin")
        assert att._dl_begin("budget_a", "pb", s1, "", "mb1", "", 1200), \
            "first transfer within the budget must pass"
        with st2.dl_lock:
            assert att._dl["budget_a"]["total"] == 1200, \
                "declared total must be reserved at accept time"
        s2 = os.path.join(tmp, "b2.bin")
        ok = att._dl_begin("budget_b", "pc", s2, "", "mb2", "", 400)
        assert ok is False and att.last_refusal_reason == "disk-budget", \
            "second transfer must hit the aggregate budget, got %r" % att.last_refusal_reason
        assert not os.path.exists(s2 + ".part"), "budget-refused transfer opened .part"
        print("OK  aggregate disk budget refuses transfer beyond the cap")

        # NO FIRST-TRANSFER EXEMPTION (the reported blocker): a lone transfer
        # whose declared total passes the budget is refused even with
        # reserved == 0, because nothing else is on the board. The old code's
        # `reserved > 0` guard let exactly this through.
        for fid in list(att._dl):
            att._dl_finish(fid, att._dl[fid]["peer"], False)
        assert not att._dl, "cleanup left transfers registered"
        s3 = os.path.join(tmp, "b3.bin")
        ok = att._dl_begin("budget_solo", "psolo", s3, "", "mb3", "", BUDGET + 1)
        assert ok is False and att.last_refusal_reason == "disk-budget", \
            "solo transfer over the aggregate budget must be refused (reserved==0), got %r" \
            % att.last_refusal_reason
        assert not os.path.exists(s3 + ".part"), "refused transfer opened .part"
        assert any("reason=disk-budget" in m for m in logged_budget), \
            "budget refusal not logged with its numbers"
        assert any("budget=%d" % BUDGET in m for m in logged_budget), \
            "budget refusal log must carry the numbers that produced it"
        print("OK  first/only transfer is no longer exempt from the budget")

        # A declared total over the PER-FILE cap is refused at accept time,
        # before a single chunk is requested.
        s4 = os.path.join(tmp, "b4.bin")
        ok = att._dl_begin("budget_cap", "pcap", s4, "", "mb4", "", CAP + 1)
        assert ok is False and att.last_refusal_reason == "disk-budget", \
            "over-ceiling declared total must be refused at begin, got %r" % att.last_refusal_reason
        assert not os.path.exists(s4 + ".part"), "over-ceiling begin left a .part"
        print("OK  declared total over the per-file cap refused before any byte")

        # FILESYSTEM CAPACITY gate: a declared total must fit what the
        # destination filesystem can actually hold above the keep-free floor.
        # Move the floor to just under the real free space so the total no
        # longer fits — the same arithmetic a nearly-full disk produces.
        free_now = att._probe_free(os.path.join(tmp, "probe.bin"))
        assert free_now is not None and free_now > 0, "real filesystem must be probeable"
        att.ATT_MIN_FREE_BYTES = free_now - 10
        s5 = os.path.join(tmp, "b5.bin")
        ok = att._dl_begin("budget_disk", "pdisk", s5, "", "mb5", "", 500)
        assert ok is False and att.last_refusal_reason == "disk-budget", \
            "total that does not fit above the keep-free floor must be refused, got %r" \
            % att.last_refusal_reason
        assert not os.path.exists(s5 + ".part"), "capacity-refused transfer opened .part"
        att.ATT_MIN_FREE_BYTES = FLOOR
        print("OK  declared total checked against real filesystem capacity")

        # MISSING DESTINATION DIRECTORY: the download dir may not exist yet
        # (a fresh install, or a setDownloadDir to a path the user deleted).
        # _dl_begin must create it and then measure THAT directory — the old
        # order probed a path that didn't exist, and a failed probe used to
        # return 1 EiB (fail open). Assert the dir is created and the probe
        # succeeds against it.
        fresh_dir = os.path.join(tmp, "nested", "dl")
        assert not os.path.exists(fresh_dir), "precondition: fresh dir must not exist"
        s6 = os.path.join(fresh_dir, "fresh.bin")
        assert att._dl_begin("budget_fresh", "pfresh", s6, "", "mb6", "", 300), \
            "transfer into a not-yet-existing download dir must be accepted after creating it"
        assert os.path.isdir(fresh_dir), "_dl_begin did not create the download directory"
        assert att._probe_free(s6) is not None, "probe failed against the created directory"
        print("OK  download dir created before the free-space probe (no fail-open)")

        # PROBE FAILURE FAILS CLOSED: if the filesystem cannot be inspected the
        # transfer is refused with reason disk-unknown — never treated as
        # 'plenty of room'.
        real_probe = att._probe_free
        att._probe_free = lambda path: None
        try:
            s7 = os.path.join(tmp, "b7.bin")
            ok = att._dl_begin("probe_fail", "pfail", s7, "", "mb7", "", 100)
            assert ok is False and att.last_refusal_reason == "disk-unknown", \
                "unprobeable filesystem must refuse (fail closed), got %r" % att.last_refusal_reason
            assert not os.path.exists(s7 + ".part"), "probe-failed transfer left a .part behind"
            print("OK  free-space probe failure fails closed (disk-unknown)")
        finally:
            att._probe_free = real_probe

        # MID-TRANSFER RECHECK: available capacity is re-read while writing, so
        # a transfer that started on a healthy disk is torn down before it lands
        # on the keep-free floor. The fake probe models the disk filling up
        # (this transfer plus other writers) between chunks.
        real_interval = att.ATT_DISK_CHECK_INTERVAL_BYTES
        att.ATT_DISK_CHECK_INTERVAL_BYTES = 10
        s8 = os.path.join(tmp, "b8.bin")
        assert att._dl_begin("fill_disk", "pfill", s8, "", "mb8", "", 1000), \
            "fill-disk transfer must be accepted to start"
        _fake_free = {"v": 1000}

        def _filling_free(path):
            # 250 bytes of usable capacity disappear at every re-read: enough
            # that the transfer must be stopped before free drops below FLOOR.
            _fake_free["v"] -= 250
            return _fake_free["v"]
        real_probe2 = att._probe_free
        att._probe_free = _filling_free
        try:
            wrote = 0
            aborted = False
            for _ in range(12):
                ok, _t, wrote = att._dl_chunk(
                    "fill_disk", "pfill", base64.b64encode(b"z" * 100).decode(), 1000)
                if not ok:
                    aborted = True
                    break
            assert aborted, "transfer kept writing after available capacity dropped below the floor"
            assert wrote < 1000, "abort happened only after the whole file was written"
            with st2.dl_lock:
                assert "fill_disk" not in att._dl, "low-disk abort did not tear the transfer down"
            assert not os.path.exists(s8 + ".part"), "low-disk abort left a .part behind"
            assert any("reason=low-disk" in m for m in logged_budget), \
                "mid-transfer capacity abort not logged"
            print("OK  capacity rechecked while writing; transfer aborted at the floor "
                  "(wrote %d of 1000 bytes before stopping)" % wrote)
        finally:
            att._probe_free = real_probe2
            att.ATT_DISK_CHECK_INTERVAL_BYTES = real_interval
            for fid in list(att._dl):
                att._dl_finish(fid, att._dl[fid]["peer"], False)
    finally:
        for fid in list(att._dl):
            att._dl_finish(fid, att._dl[fid]["peer"], False)
        att.ATT_MAX_RESERVED_BYTES = real_budget
        att.ATT_MAX_BYTES = real_cap
        att.ATT_MIN_FREE_BYTES = real_floor
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


def test_attachment_limit_config():
    """Unit test: attachments.apply_config_limits() is the ONLY way a config
    moves the disk limits, and it is fail-closed:
      - absent / non-numeric / zero / negative / absurd values keep the current
        numbers (a limit can never be switched off),
      - a valid override is adopted and clamped into [ATT_LIMIT_MIN, HARD_MAX],
      - a per-file ceiling above the aggregate budget is pulled back to the
        budget, because otherwise the budget could not bind,
      - the effective numbers are returned so the daemon can log them."""
    import attachments as att

    real = (att.ATT_MAX_BYTES, att.ATT_MAX_RESERVED_BYTES, att.ATT_MIN_FREE_BYTES)
    try:
        # Baseline: defaults.
        eff = att.apply_config_limits({})
        assert eff["ATT_MAX_BYTES"] == att.ATT_MAX_BYTES_DEFAULT, \
            "empty config must keep the default ceiling, got %r" % (eff,)
        assert eff["ATT_MAX_RESERVED_BYTES"] == att.ATT_MAX_RESERVED_BYTES_DEFAULT
        assert eff["ATT_MIN_FREE_BYTES"] == att.ATT_MIN_FREE_BYTES_DEFAULT

        # A legitimate override is adopted (a user who moves bigger files).
        eff = att.apply_config_limits({"attachmentMaxBytes": 17179869184,      # 16 GiB
                                       "attachmentMaxReservedBytes": 34359738368})  # 32 GiB
        assert eff["ATT_MAX_BYTES"] == 17179869184 and att.ATT_MAX_BYTES == 17179869184, \
            "override must be adopted, got %r" % (eff,)
        assert eff["ATT_MAX_RESERVED_BYTES"] == 34359738368

        # An override that is larger than the aggregate budget is clamped DOWN
        # to it (gate sanity: the budget has to be able to bind).
        eff = att.apply_config_limits({"attachmentMaxBytes": 40 * 1024 ** 3,
                                       "attachmentMaxReservedBytes": 8 * 1024 ** 3})
        assert eff["ATT_MAX_BYTES"] == 8 * 1024 ** 3, \
            "per-file cap must not exceed the aggregate budget, got %r" % (eff,)

        # Junk can't disable a gate: each case must leave the limit >= MIN.
        def _reset_defaults():
            att.ATT_MAX_BYTES = att.ATT_MAX_BYTES_DEFAULT
            att.ATT_MAX_RESERVED_BYTES = att.ATT_MAX_RESERVED_BYTES_DEFAULT
            att.ATT_MIN_FREE_BYTES = att.ATT_MIN_FREE_BYTES_DEFAULT

        for bad in (0, -1, -10 ** 12, True, False, "", "abc", None, [], {}):
            _reset_defaults()
            eff = att.apply_config_limits({"attachmentMaxBytes": bad,
                                           "attachmentMinFreeBytes": bad})
            assert eff["ATT_MAX_BYTES"] == att.ATT_MAX_BYTES_DEFAULT and \
                   eff["ATT_MIN_FREE_BYTES"] == att.ATT_MIN_FREE_BYTES_DEFAULT, \
                "value %r must be refused, not applied: %r" % (bad, eff)
            assert att.ATT_MAX_BYTES >= att.ATT_LIMIT_MIN, \
                "value %r left the live ceiling unbounded" % (bad,)

        # Absurdly large is clamped to the hard max, never adopted raw.
        eff = att.apply_config_limits({"attachmentMaxReservedBytes": 10 ** 18,
                                       "attachmentMaxBytes": 10 ** 18})
        assert eff["ATT_MAX_RESERVED_BYTES"] == att.ATT_LIMIT_HARD_MAX, \
            "override must clamp to the hard max, got %r" % (eff,)

        # A tiny-but-valid value is honoured (limits may be tightened too),
        # as long as it is >= ATT_LIMIT_MIN.
        eff = att.apply_config_limits({"attachmentMaxBytes": att.ATT_LIMIT_MIN,
                                       "attachmentMaxReservedBytes": att.ATT_LIMIT_MIN,
                                       "attachmentMinFreeBytes": att.ATT_LIMIT_MIN})
        assert eff["ATT_MAX_BYTES"] == att.ATT_LIMIT_MIN, \
            "a tightened limit must be honoured, got %r" % (eff,)
        print("OK  attachment limits are config-overridable and fail closed on junk")
    finally:
        att.ATT_MAX_BYTES, att.ATT_MAX_RESERVED_BYTES, att.ATT_MIN_FREE_BYTES = real
        assert att.ATT_MAX_BYTES == att.ATT_MAX_BYTES_DEFAULT, "restore failed"


def test_sender_side_over_cap():
    """Unit test (in-process daemon, isolated state): a SEND whose file is over
    the per-file ceiling must surface an error event and must NOT send a message
    carrying attachment metadata the recipient could never pull (the silent
    half-send). Text + over-cap file still sends the text with no attachment."""
    import tempfile

    import attachments as _att
    import history as _h
    import server as _s

    tmp = tempfile.mkdtemp(prefix="lanchat-send-cap-")
    iso = os.path.join(tmp, "state"); os.makedirs(iso, exist_ok=True)
    saved = (_h.STATE_DIR, _h.HISTORY_PATH, _h.HISTORY_KEY, _s.STATE_DIR, _s._LOG_PATH)
    _h.STATE_DIR = iso
    _h.HISTORY_PATH = os.path.join(iso, "history.json")
    _h.HISTORY_KEY = os.path.join(iso, "history.key")
    _s.STATE_DIR = iso
    _s._LOG_PATH = os.path.join(iso, "daemon.log")

    _s.CONFIG["token"] = TOKEN
    _s.STATE.stdout = open(os.devnull, "w")
    _att.init(_s.STATE)

    # A peer to "send" to; sockets are stubbed so nothing leaves the process.
    peer = "deadbeefcafe0000"
    _s._peers[peer] = {"id": peer, "name": "Target", "address": "127.0.0.1",
                       "port": 1, "httpPort": None, "status": "available",
                       "lastSeen": int(time.time() * 1000), "version": ""}

    sent = []
    captured = []
    real_write, real_emit = _s._write, _s._emit
    _s._write = lambda pid, obj: (sent.append(obj), True)[1]
    _s._emit = lambda e: captured.append(e)
    real_cap = _att.ATT_MAX_BYTES
    _att.ATT_MAX_BYTES = 1024  # 1 KiB ceiling for this case
    try:
        big = os.path.join(tmp, "toobig.bin")
        with open(big, "wb") as f:
            f.write(b"z" * 4096)

        # (a) file-only send, over the cap -> error event, NO message sent.
        _s.handle_command({"cmd": "send", "to": peer, "text": "",
                           "attachment": {"path": big, "name": "toobig.bin"}})
        errs = [e for e in captured if e.get("event") == "error"]
        assert errs, "over-cap send must surface an error: %r" % (captured,)
        assert "limit" in (errs[0].get("message") or "").lower(), \
            "error must name the limit: %r" % (errs[0],)
        assert not [m for m in sent if m.get("t") == "msg"], \
            "over-cap file-only send must not emit a message: %r" % (sent,)
        assert not any(m.get("attachment") for m in sent), \
            "over-cap send must not advertise attachment metadata: %r" % (sent,)

        # (b) text + over-cap file -> the text still sends, attachment dropped.
        sent.clear(); captured.clear()
        _s.handle_command({"cmd": "send", "to": peer, "text": "the note",
                           "attachment": {"path": big, "name": "toobig.bin"}})
        msgs = [m for m in sent if m.get("t") == "msg"]
        assert msgs and msgs[0].get("text") == "the note", \
            "text must still send alongside a refused file: %r" % (sent,)
        assert not msgs[0].get("attachment"), \
            "refused file must not be attached to the message: %r" % (msgs[0],)

        # (c) an under-cap file still sends normally (no regression).
        sent.clear(); captured.clear()
        small = os.path.join(tmp, "ok.bin")
        with open(small, "wb") as f:
            f.write(b"ok")
        _s.handle_command({"cmd": "send", "to": peer, "text": "small",
                           "attachment": {"path": small, "name": "ok.bin"}})
        msgs = [m for m in sent if m.get("t") == "msg"]
        assert msgs and msgs[0].get("attachment", {}).get("name") == "ok.bin", \
            "under-cap file must still attach normally: %r" % (sent,)
        assert not [e for e in captured if e.get("event") == "error"], \
            "under-cap send must not error: %r" % (captured,)
        print("OK  over-cap SEND is refused visibly (no silent half-send); "
              "text survives; small files unaffected")
    finally:
        _s._write, _s._emit = real_write, real_emit
        _att.ATT_MAX_BYTES = real_cap
        _h.STATE_DIR, _h.HISTORY_PATH, _h.HISTORY_KEY, _s.STATE_DIR, _s._LOG_PATH = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_settings_attachment_max():
    """Unit test: the Settings-menu command setAttachmentMax applies the
    per-file ceiling live AND persists it; raising it past the aggregate
    budget scales the budget to 2x (never silently clamped); junk is refused
    refused with a visible error; ready events carry the effective value."""
    import tempfile

    import attachments as _att
    import history as _h
    import server as _s

    tmp = tempfile.mkdtemp(prefix="lanchat-setmax-")
    iso = os.path.join(tmp, "state"); os.makedirs(iso, exist_ok=True)
    saved = (_h.STATE_DIR, _h.HISTORY_PATH, _h.HISTORY_KEY, _s.STATE_DIR, _s._LOG_PATH,
             _s.CONFIG_PATH)
    _h.STATE_DIR = iso
    _h.HISTORY_PATH = os.path.join(iso, "history.json")
    _h.HISTORY_KEY = os.path.join(iso, "history.key")
    _s.STATE_DIR = iso
    _s._LOG_PATH = os.path.join(iso, "daemon.log")
    _s.CONFIG_PATH = os.path.join(iso, "lanchat.json")  # never the real config

    _s.CONFIG["token"] = TOKEN
    _s.STATE.stdout = open(os.devnull, "w")
    _att.init(_s.STATE)
    _s.STATE.config.setdefault("attachmentMaxBytes", _att.ATT_MAX_BYTES_DEFAULT)
    _s.STATE.config.setdefault("attachmentMaxReservedBytes", _att.ATT_MAX_RESERVED_BYTES_DEFAULT)
    _s.STATE.config.setdefault("attachmentMinFreeBytes", _att.ATT_MIN_FREE_BYTES_DEFAULT)
    _att.apply_config_limits(_s.STATE.config)

    captured = []
    real_emit = _s._emit
    _s._emit = lambda e: captured.append(e)
    real = (_att.ATT_MAX_BYTES, _att.ATT_MAX_RESERVED_BYTES, _att.ATT_MIN_FREE_BYTES)
    try:
        # Raise to 16 GiB: ceiling applied, budget scaled to 2x, persisted.
        _s.handle_command({"cmd": "setAttachmentMax", "gib": 16})
        assert _att.ATT_MAX_BYTES == 16 * 1024 ** 3, \
            "ceiling not applied: %d" % _att.ATT_MAX_BYTES
        assert _att.ATT_MAX_RESERVED_BYTES == 32 * 1024 ** 3, \
            "budget must scale to 2x the ceiling, got %d" % _att.ATT_MAX_RESERVED_BYTES
        assert _s.STATE.config["attachmentMaxBytes"] == 16 * 1024 ** 3, "not persisted"
        lim = [e for e in captured if e.get("event") == "attachment-limits"]
        assert lim and lim[-1]["perFileBytes"] == 16 * 1024 ** 3, \
            "effective values must be echoed: %r" % (captured,)
        # The ready event carries the ceiling for UI init.
        ready = _s._ready_event()
        assert ready["attachmentMaxBytes"] == 16 * 1024 ** 3, "ready event missing ceiling"

        # Junk: 0 / negative -> visible error, limits unchanged.
        captured.clear()
        _s.handle_command({"cmd": "setAttachmentMax", "gib": 0})
        errs = [e for e in captured if e.get("event") == "error"]
        assert errs, "junk value must surface an error: %r" % (captured,)
        assert _att.ATT_MAX_BYTES == 16 * 1024 ** 3, "junk must not change the limit"

        # Absurdly large is clamped to the hard max (1 TiB), budget follows.
        _s.handle_command({"cmd": "setAttachmentMax", "gib": 100000})
        assert _att.ATT_MAX_BYTES == _att.ATT_LIMIT_HARD_MAX, \
            "override must clamp to the hard max, got %d" % _att.ATT_MAX_BYTES
        print("OK  Settings setAttachmentMax: applies live, persists, scales budget, "
              "refuses junk, echoes effective values")
    finally:
        _s._emit = real_emit
        _att.ATT_MAX_BYTES, _att.ATT_MAX_RESERVED_BYTES, _att.ATT_MIN_FREE_BYTES = real
        _h.STATE_DIR, _h.HISTORY_PATH, _h.HISTORY_KEY, _s.STATE_DIR, _s._LOG_PATH, \
            _s.CONFIG_PATH = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_stale_attachment_gone():
    """Regression: pressing Save on an attachment whose sender copy is long
    gone used to be swallowed silently (sender replies attachmentError 'not
    found' before any transfer began -> no event, no log) and the Save bar
    stayed forever. Now: the failure is surfaced AND the history message is
    flagged gone (persisted), which the UI bar scan skips. A mid-stream error
    for an ACTIVE transfer keeps the old retryable behavior (no gone flag)."""
    import tempfile

    import attachments as _att
    import history as _h
    import server as _s

    tmp = tempfile.mkdtemp(prefix="lanchat-gone-")
    iso = os.path.join(tmp, "state"); os.makedirs(iso, exist_ok=True)
    saved = (_h.STATE_DIR, _h.HISTORY_PATH, _h.HISTORY_KEY, _s.STATE_DIR, _s._LOG_PATH)
    _h.STATE_DIR = iso
    _h.HISTORY_PATH = os.path.join(iso, "history.json")
    _h.HISTORY_KEY = os.path.join(iso, "history.key")
    _s.STATE_DIR = iso
    _s._LOG_PATH = os.path.join(iso, "daemon.log")
    _s.CONFIG["token"] = TOKEN
    _s.STATE.stdout = open(os.devnull, "w")
    _att.init(_s.STATE)

    peer = "deadbeefcafe0001"
    _s.append_history({"mid": "mgone", "from": peer, "to": _s.host_id(),
                       "text": "old file", "outgoing": False,
                       "attachment": {"name": "old.bin", "size": 10,
                                      "mime": "application/octet-stream",
                                      "fileId": "gone1", "sha256": ""}})
    captured = []
    real_emit = _s._emit
    _s._emit = lambda e: captured.append(e)
    try:
        # (a) sender replies "not found" for a transfer that never began.
        _s._handle_incoming({"t": "attachmentError", "from": peer,
                             "fileId": "gone1", "mid": "mgone",
                             "error": "not found"}, ("127.0.0.1", 1))
        evs = [e for e in captured if e.get("event") == "attachment-saved"]
        assert evs and evs[0].get("ok") is False, \
            "unmatched attachmentError must surface: %r" % (captured,)
        assert evs[0].get("gone") is True, "gone must be flagged: %r" % (evs[0],)
        assert "no longer" in (evs[0].get("error") or ""), \
            "user-facing error must say the file is gone: %r" % (evs[0],)
        with _s.STATE.hist_lock:
            m = [x for x in _s.STATE.history if x.get("mid") == "mgone"][0]
            assert m["attachment"].get("gone") is True, "history not flagged gone"
        # (b) a NON-gone error (transient) surfaces but does not flag gone.
        _s.append_history({"mid": "mtrans", "from": peer, "to": _s.host_id(),
                           "text": "x", "outgoing": False,
                           "attachment": {"name": "t.bin", "size": 1,
                                          "mime": "a/b", "fileId": "tr1", "sha256": ""}})
        captured.clear()
        _s._handle_incoming({"t": "attachmentError", "from": peer,
                             "fileId": "tr1", "mid": "mtrans",
                             "error": "socket down"}, ("127.0.0.1", 1))
        evs = [e for e in captured if e.get("event") == "attachment-saved"]
        assert evs and evs[0].get("ok") is False and evs[0].get("gone") is not True, \
            "transient error must not flag gone: %r" % (evs,)
        with _s.STATE.hist_lock:
            m = [x for x in _s.STATE.history if x.get("mid") == "mtrans"][0]
            assert not m["attachment"].get("gone"), "transient error flagged gone"
        # (c) mark_attachment_gone persists across a reload.
        _s2 = type("S", (), {})()
        _s2.hist_lock = threading.Lock(); _s2.history = []; _s2.hist_crypto = None
        _h.init(_s2)
        _h.load_history()
        with _s2.hist_lock:
            m = [x for x in _s2.history if x.get("mid") == "mgone"][0]
            assert m["attachment"].get("gone") is True, "gone flag not persisted"
        # (d) an error for an ACTIVE transfer keeps the old abort path (with
        # its own event, no gone flag) — peer-checked teardown.
        ok_dir = os.path.join(tmp, "dl"); os.makedirs(ok_dir)
        assert _s._dl_begin("live1", peer, os.path.join(ok_dir, "l.bin"), "", "mlive")
        captured.clear()
        _s._handle_incoming({"t": "attachmentError", "from": peer,
                             "fileId": "live1", "mid": "mlive",
                             "error": "sender aborted"}, ("127.0.0.1", 1))
        evs = [e for e in captured if e.get("event") == "attachment-saved"]
        assert evs and evs[0].get("ok") is False and "gone" not in evs[0], \
            "active-transfer abort must use the old path: %r" % (evs,)
        assert not os.path.exists(os.path.join(ok_dir, "l.bin.part")), "abort left .part"
        print("OK  stale Save bar: sender-gone reply surfaces, flags gone (persisted); "
              "transient errors stay retryable")
    finally:
        _s._emit = real_emit
        _h.STATE_DIR, _h.HISTORY_PATH, _h.HISTORY_KEY, _s.STATE_DIR, _s._LOG_PATH = saved
        shutil.rmtree(tmp, ignore_errors=True)


def test_attachment_dismiss():
    """Unit test: the Save-bar ✕ (dismissAttachment) flags the message's
    attachment dismissed in history (persisted across reloads) and echoes an
    attachment-dismissed event so the UI always clears its bar — including for
    an unknown mid (idempotent)."""
    import tempfile

    import history as _h
    import server as _s

    tmp = tempfile.mkdtemp(prefix="lanchat-dismiss-")
    iso = os.path.join(tmp, "state"); os.makedirs(iso, exist_ok=True)
    saved = (_h.STATE_DIR, _h.HISTORY_PATH, _h.HISTORY_KEY, _s.STATE_DIR, _s._LOG_PATH)
    _h.STATE_DIR = iso
    _h.HISTORY_PATH = os.path.join(iso, "history.json")
    _h.HISTORY_KEY = os.path.join(iso, "history.key")
    _s.STATE_DIR = iso
    _s._LOG_PATH = os.path.join(iso, "daemon.log")
    _s.CONFIG["token"] = TOKEN
    _s.STATE.stdout = open(os.devnull, "w")
    # history.STATE must be THE server State: earlier tests rebind it to stubs.
    _h.init(_s.STATE)

    _s.append_history({"mid": "mdis", "from": "peerz", "to": _s.host_id(),
                       "text": "file", "outgoing": False,
                       "attachment": {"name": "a.bin", "size": 1,
                                      "mime": "a/b", "fileId": "d1", "sha256": ""}})
    captured = []
    real_emit = _s._emit
    _s._emit = lambda e: captured.append(e)
    try:
        _s.handle_command({"cmd": "dismissAttachment", "mid": "mdis"})
        evs = [e for e in captured if e.get("event") == "attachment-dismissed"]
        assert evs and evs[0]["mid"] == "mdis", "dismiss event must echo: %r" % (captured,)
        with _s.STATE.hist_lock:
            m = [x for x in _s.STATE.history if x.get("mid") == "mdis"][0]
            assert m["attachment"].get("dismissed") is True, "history not flagged dismissed"
        # Idempotent: unknown mid still echoes (UI clears its bar regardless).
        captured.clear()
        _s.handle_command({"cmd": "dismissAttachment", "mid": "nope"})
        evs = [e for e in captured if e.get("event") == "attachment-dismissed"]
        assert evs and evs[0]["mid"] == "nope", "unknown mid must still echo: %r" % (captured,)
        # Persists across a reload.
        _s2 = type("S", (), {})()
        _s2.hist_lock = threading.Lock(); _s2.history = []; _s2.hist_crypto = None
        _h.init(_s2)
        _h.load_history()
        with _s2.hist_lock:
            m = [x for x in _s2.history if x.get("mid") == "mdis"][0]
            assert m["attachment"].get("dismissed") is True, "dismissed flag not persisted"
        print("OK  Save-bar ✕: dismissed flag persisted, event echoes, idempotent")
    finally:
        _s._emit = real_emit
        _h.STATE_DIR, _h.HISTORY_PATH, _h.HISTORY_KEY, _s.STATE_DIR, _s._LOG_PATH = saved
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_mark_attachment_saved()
    test_busy_error_mapping()
    test_attachment_limit_config()
    test_sender_side_over_cap()
    test_settings_attachment_max()
    test_stale_attachment_gone()
    test_attachment_dismiss()
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

        # ---- 3.8) over-capacity file is refused BEFORE bytes are requested --
        # The reviewer's scenario: an authenticated peer advertises a file
        # larger than the recipient's limits. The recipient must refuse at
        # accept time using the size already on the message metadata — not open
        # a .part, not send an attachmentRequest, not stream a single byte.
        import attachments as _att
        import history as _h
        import server as _s
        _s.CONFIG["token"] = TOKEN
        _s.STATE.stdout = open(os.devnull, "w")
        _att.init(_s.STATE)

        # Redirect the IN-PROCESS module's state/log/history paths into the
        # test's temp home before anything is appended or logged — the daemon
        # subprocesses already get HOME=home, but `import server` here points at
        # the real ~/.local/state/lanchat, and a test must never touch it.
        iso = os.path.join(hb, "iso-state"); os.makedirs(iso, exist_ok=True)
        _restore = [(_h.STATE_DIR, _h.HISTORY_PATH, _h.HISTORY_KEY,
                     _s.STATE_DIR, _s._LOG_PATH)]
        _h.STATE_DIR = iso
        _h.HISTORY_PATH = os.path.join(iso, "history.json")
        _h.HISTORY_KEY = os.path.join(iso, "history.key")
        _s.STATE_DIR = iso
        _s._LOG_PATH = os.path.join(iso, "daemon.log")

        # Peer known to the recipient (friend handshake happened above), but we
        # capture rather than rely on its socket: the refusal must happen before
        # any socket write.
        _s._peers[ida] = {"id": ida, "name": "Alpha", "address": "127.0.0.1",
                          "port": 4991, "httpPort": None, "status": "available",
                          "lastSeen": int(time.time() * 1000), "version": ""}

        cap_dir = os.path.join(hb, "dl-cap"); os.makedirs(cap_dir, exist_ok=True)
        _s.CONFIG["downloadDir"] = cap_dir

        # A message in the recipient's history advertising a 1 TiB file, the way
        # a real inbound attachment message carries {name,size,fileId,sha256}.
        BIG = 1024 * 1024 * 1024 * 1024
        _s.append_history({"mid": "mbig", "from": ida, "to": _s.host_id(),
                           "text": "huge file", "outgoing": False,
                           "attachment": {"name": "huge.bin", "size": BIG,
                                          "mime": "application/octet-stream",
                                          "fileId": "big1", "sha256": ""}})

        real_cap = _att.ATT_MAX_BYTES
        _att.ATT_MAX_BYTES = 10 * 1024 * 1024  # recipient allows 10 MiB

        # Record what the accept path actually hands to _dl_begin: the declared
        # total must arrive there, not be discovered mid-stream.
        seen_args = {}
        real_begin = _s._dl_begin

        def _spy_begin(file_id, peer_id, save_to, sha256, mid, room="", total=0):
            seen_args["file_id"] = file_id
            seen_args["total"] = total
            return real_begin(file_id, peer_id, save_to, sha256, mid, room, total)

        # A peer that got this far would get chunks; prove it never does.
        writes = []
        real_write = _s._write

        def _spy_write(pid, obj):
            writes.append(obj)
            return real_write(pid, obj)

        captured = []
        orig_emit, orig_log = _s._emit, _s._log
        logs = []
        _s._emit = lambda e: captured.append(e)
        _s._log = lambda m: logs.append(m)
        _s._write = _spy_write
        _s._dl_begin = _spy_begin
        try:
            _s.handle_command({"cmd": "acceptAttachment", "from": ida, "fileId": "big1",
                               "name": "huge.bin", "mid": "mbig", "sha256": ""})
        finally:
            _s._emit, _s._log = orig_emit, orig_log
            _s._write = real_write
            _s._dl_begin = real_begin
            _att.ATT_MAX_BYTES = real_cap
            _h.STATE_DIR, _h.HISTORY_PATH, _h.HISTORY_KEY, _s.STATE_DIR, _s._LOG_PATH = _restore[0]

        assert seen_args.get("total") == BIG, \
            "accept path must pass the advertised size into _dl_begin, got %r" % (seen_args,)
        assert seen_args.get("file_id") == "big1", "wrong transfer was targeted: %r" % (seen_args,)
        assert _att.last_refusal_reason == "disk-budget", \
            "over-capacity accept must refuse with disk-budget, got %r" % _att.last_refusal_reason
        saved_ev = [e for e in captured if e.get("event") == "attachment-saved"]
        assert saved_ev and saved_ev[0].get("ok") is False, \
            "over-capacity accept must emit attachment-saved ok:false: %r" % (captured,)
        assert "disk" in (saved_ev[0].get("error") or "").lower(), \
            "user-facing error should name the disk limit: %r" % (saved_ev[0],)
        # No bytes were requested: the recipient never sent attachmentRequest.
        assert not [w for w in writes if w.get("t") == "attachmentRequest"], \
            "recipient asked the sender to stream an over-capacity file: %r" % (writes,)
        assert [w for w in writes if w.get("t") == "attachmentError"], \
            "sender must be told the pull failed: %r" % (writes,)
        # No residue: no final file, no .part.
        assert not os.path.exists(os.path.join(cap_dir, "huge.bin")), "refused file was saved"
        assert not os.path.exists(os.path.join(cap_dir, "huge.bin.part")), "refused file left .part"
        assert not _att._dl, "refused transfer left a live reassembly entry"
        assert any("reason=disk-budget" in m for m in logs), \
            "capacity refusal must be logged: %r" % (logs,)
        print("OK  over-capacity attachment refused at accept time "
              "(no attachmentRequest, no bytes, no .part)")

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
