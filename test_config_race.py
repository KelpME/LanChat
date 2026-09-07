#!/usr/bin/env python3
"""Config-write integrity under concurrency.

The daemon writes STATE.config['friends'] from several threads (UDP receive ->
add_friend, discovery -> _sync_friend_name/upsert, control -> unfriend) with
_save_config() dumping the whole config to disk. If an add_friend append lands
in memory but its dump is beaten to the file by an older snapshot, a JUST-
ADDED friend is present in memory yet missing from the on-disk config — and a
subsequent daemon restart (an update!) reloads the stale file and the friend
vanishes from the UI.

This hammer runs the same three mutators from many threads and asserts the
on-disk config ALWAYS converges to contain every confirmed friend that was
added. Regression guard: if the config_lock is removed, this is expected to
fail under pressure.

Run from repo root: python3 test_config_race.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading

_tmp = tempfile.mkdtemp(prefix="lnc-cfg-race-")
os.makedirs(os.path.join(_tmp, ".config", "omarchy"), exist_ok=True)
open(os.path.join(_tmp, ".config", "omarchy", "lanchat.json"), "w").write(json.dumps(
    {"token": "x" * 16, "port": 41901, "displayName": "c", "httpPort": 41902,
     "visibility": "open", "friends": []}))
os.environ["HOME"] = _tmp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib  # noqa: E402

import server as S  # noqa: E402

importlib.reload(S)

N_ROUNDS = 300
THREADS = 6
added = [set() for _ in range(THREADS)]
barrier = threading.Barrier(THREADS)


def worker(idx, role):
    barrier.wait()
    for i in range(N_ROUNDS):
        friend = f"{role}-{idx}-{i}"
        if role == "add":
            S.add_friend(friend, "10.0.0.1", friend, True)
            added[idx].add(friend)
        elif role == "sync":
            # discovery-style: upsert a peer + sync an existing friend name
            S.upsert_peer(f"peer-{idx}-{i}", "Peer", "10.0.0.2", S.DEFAULT_PORT,
                          pstatus="available")
            S._sync_friend_name("add-0-0", "KnownName")  # may no-op
        elif role == "unfriend":
            # control-style: rebuild the list (the detached-list suspect)
            S.unfriend(f"nope-{idx}-{i}")  # never existed; exercises rebuild+save


roles = ["add", "add", "sync", "sync", "unfriend", "unfriend"]
ts = [threading.Thread(target=worker, args=(i, roles[i])) for i in range(THREADS)]
for t in ts: t.start()
for t in ts: t.join()

# everyone added is a confirmed friend that must survive to disk
expected = set()
for a in added: expected |= a

in_mem = {f["id"] for f in S.STATE.config.get("friends", [])}
on_disk = set()
try:
    on_disk = {f["id"] for f in json.load(open(S.CONFIG_PATH)).get("friends", [])}
except Exception:
    on_disk = set()

missing_mem = expected - in_mem
missing_disk = expected - on_disk
print("confirmed friends added:  %d" % len(expected))
print("in-memory friends:        %d (missing %d)" % (len(in_mem), len(missing_mem)))
print("on-disk friends:          %d (missing %d)" % (len(on_disk), len(missing_disk)))

if missing_disk or missing_mem:
    print("FAIL: %d friend(s) lost on disk, %d lost in memory"
          % (len(missing_disk), len(missing_mem)))
    shutil.rmtree(_tmp, ignore_errors=True)
    sys.exit(1)

print("OK: every confirmed friend survived to memory AND disk under %d-thread load"
      % THREADS)
shutil.rmtree(_tmp, ignore_errors=True)
sys.exit(0)
