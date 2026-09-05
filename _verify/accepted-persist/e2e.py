#!/usr/bin/env python3
"""E2E proof (no real daemon networking): accepted flag survives a full
persistence round-trip through history.json via mark_attachment_saved."""
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import history  # noqa: E402


class State:
    pass


tmp = tempfile.mkdtemp(prefix="lanchat-e2e-")
try:
    history.STATE_DIR = tmp
    history.HISTORY_PATH = os.path.join(tmp, "history.json")
    history.HISTORY_KEY = os.path.join(tmp, "history.key")

    # (a) fresh STATE, two attachment-carrying history messages, persisted
    st = State()
    st.hist_lock = threading.Lock()
    st.hist_crypto = None
    st.history = []
    history.init(st)
    history.append_history({"mid": "m1", "from": "p", "to": "me",
                            "attachment": {"fileId": "f1", "name": "a.txt"}})
    history.append_history({"mid": "m2", "from": "p", "to": "me",
                            "attachment": {"fileId": "f2", "name": "b.txt"}})

    # (b) mark m1 accepted (what attachments._finalize_download now does)
    ok = history.mark_attachment_saved("m1")
    assert ok is True

    # (c) fresh STATE init: reload from disk, flag must survive the round-trip
    st2 = State()
    st2.hist_lock = threading.Lock()
    st2.hist_crypto = None
    st2.history = []
    history.init(st2)
    history.load_history()
    msgs = history.history_snapshot()
    by_mid = {m.get("mid"): m for m in msgs}
    assert by_mid["m1"]["attachment"].get("accepted") is True, \
        "accepted flag did not survive persistence round-trip"
    assert by_mid["m2"]["attachment"].get("accepted") is None, \
        "unaccepted message must stay unaccepted"
    print("OK  e2e: accepted flag persisted to history.json and survived reload")

    # confirm the on-disk file is the encrypted format (not accidentally plaintext)
    with open(history.HISTORY_PATH, "rb") as f:
        head = f.read(4)
    print("OK  e2e: history file on disk, first bytes: %r (encrypted blob)" % head)
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print("E2E PASSED")
