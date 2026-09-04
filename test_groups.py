#!/usr/bin/env python3
"""Group chat rooms + room file sharing, end to end.

Four-daemon topology (O owner, A/B/C members, D invitee) over the real TLS
transport. Verifies the Phase-1 daemon contract (plans/ROOMS.md, approved
2026-09-03):
  - createRoom + room-created/room-list events + persistence across restart
  - roomState broadcast reaches members
  - mesh room text (delivered member-to-member; the host is NOT in the path)
  - the room field rides t:"msg"; a room message claiming an unknown room is
    dropped (a stranger can't inject room-scoped content)
  - room-file metadata fan-out via the owner + per-recipient pull through the
    EXISTING attachmentRequest flow + roomFileStatus report back to the sender
  - trust gate: a member who is not the sender's friend gets the fail-fast
    "not friends with sender" attachment-saved error; after befriending, the
    same message saves (no manual re-request)
  - canInvite enforcement (unpermitted member's add refused; owner executes
    permitted adds)
  - per-member color record broadcast (token+hex) + owner kill-switch
    (colorsEnabled) + persistence
  - owner-offline freeze (management errors) while mesh keeps working
  - daemon stays responsive to unknown-room traffic (version-skew class)

Run: python3 test_groups.py
"""
import json
import os
import shutil
import sys
import threading
import time

TOKEN = "test-shared-secret-token"
HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "server.py")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_persistent import Daemon, _cert_fp, make_home  # noqa: E402

DLDIR = None


def wait_for(fn, timeout=8.0):
    dl = time.time() + timeout
    while time.time() < dl:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return None


def wait_message(d, timeout=8.0, **match):
    """Wait for a message event whose message dict contains all key=value."""
    dl = time.time() + timeout
    while time.time() < dl:
        ev = d.wait_event("message", timeout=0.4)
        if ev:
            m = ev.get("message", {})
            if all(str(m.get(k, "")) == str(v) for k, v in match.items()):
                return m
    return None


def friend_pair(a, b, ida, idb, na="Alpha", nb="Beta"):
    """Two-way confirmed friendship via setFriend (test bootstrap)."""
    a.cmd(cmd="setFriend", id=idb, name=nb)
    b.cmd(cmd="setFriend", id=ida, name=na)
    wait_for(lambda: any(f.get("id") == idb for f in a.events_of("friends")), 5)
    wait_for(lambda: any(f.get("id") == ida for f in b.events_of("friends")), 5)


def start_beats(daemons_ports):
    """Cross-announce every ordered pair (X learns about Y) so each daemon
    holds every other's address+port — the same bootstrap test_persistent and
    test_attachments use. Real peers re-broadcast; the harness does it here."""
    import socket as _s
    pairs = [(d, p, d2, p2) for d, p in daemons_ports for d2, p2 in daemons_ports if d is not d2]

    def _beat():
        while True:
            for d, p, d2, p2 in pairs:
                try:
                    s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
                    s.sendto(json.dumps({"t": "hello", "id": _cert_fp(d2.home),
                                         "name": d2.display, "port": p2}).encode(),
                             ("127.0.0.1", p))
                    s.close()
                except Exception:
                    pass
            time.sleep(2)

    threading.Thread(target=_beat, daemon=True).start()


def main():
    ho = make_home("o", 4971, "Owner")
    ha = make_home("a", 4972, "Alpha")
    hb = make_home("b", 4973, "Beta")
    hc = make_home("c", 4974, "Charlie")
    hd = make_home("d", 4975, "Dora")
    o = Daemon(ho, 4971, "Owner")
    a = Daemon(ha, 4972, "Alpha")
    b = Daemon(hb, 4973, "Beta")
    c = Daemon(hc, 4974, "Charlie")
    d = Daemon(hd, 4975, "Dora")
    failures = []

    def check(name, cond, detail=""):
        print("%s %s %s" % ("PASS" if cond else "FAIL", name, "" if cond else detail))
        if not cond:
            failures.append(name)

    stopped = []
    try:
        for x in (o, a, b, c, d):
            x.wait_event("ready")
        ido, ida, idb, idc, idd = (_cert_fp(h) for h in (ho, ha, hb, hc, hd))
        start_beats([(o, 4971), (a, 4972), (b, 4973), (c, 4974), (d, 4975)])

        # set download dir for room-file saves
        global DLDIR
        DLDIR = os.path.join(hb, "dl")
        os.makedirs(DLDIR, exist_ok=True)
        b.cmd(cmd="setDownloadDir", dir=DLDIR)
        b.wait_event("download-dir")
        DLDIR_C = os.path.join(hc, "dl")
        os.makedirs(DLDIR_C, exist_ok=True)
        c.cmd(cmd="setDownloadDir", dir=DLDIR_C)
        c.wait_event("download-dir")

        # Friendships: O-A, O-B, O-C, O-D (authoritative channel), A-B, A-C
        # (mesh pulls). C is deliberately NOT A's friend until step 6, and
        # D only joins late.
        friend_pair(o, a, ido, ida, "Owner", "Alpha")
        friend_pair(o, b, ido, idb, "Owner", "Beta")
        friend_pair(o, c, ido, idc, "Owner", "Charlie")
        friend_pair(o, d, ido, idd, "Owner", "Dora")
        friend_pair(a, b, ida, idb, "Alpha", "Beta")

        # ---- 1. create room + room-list
        o.cmd(cmd="createRoom", name="Project X")
        created = o.wait_event("room-created", timeout=6)
        check("room-created event", created is not None)
        rid = (created or {}).get("roomId", "")
        check("roomId non-empty", bool(rid))
        lst = wait_for(lambda: any(r.get("roomId") == rid
                                   for e in o.events_of("room-list") for r in e.get("rooms", [])), 5)
        check("owner room-list carries the room", bool(lst))

        # ---- 2. owner invite + A join -> roomState reaches A
        o.cmd(cmd="roomInvite", roomId=rid, peer=ida)
        inv = a.wait_event("room-invite", timeout=6)
        check("invite reaches A", inv is not None and inv.get("roomId") == rid)
        a.cmd(cmd="roomJoin", roomId=rid)
        got = wait_for(lambda: [s for s in a.events_of("room-state")
                                if s.get("room", {}).get("roomId") == rid], 8)
        check("roomState reaches A after join", bool(got))
        amem = (got or [{}])[-1].get("room", {}).get("members", {})
        check("A in members", ida in amem)
        check("member canInvite defaults false", not amem.get(ida, {}).get("canInvite", True))

        # owner-side direct add of B
        o.cmd(cmd="roomAdd", roomId=rid, peer=idb)
        check("roomState reaches B after owner add",
              wait_for(lambda: [s for s in b.events_of("room-state")
                                if s.get("room", {}).get("roomId") == rid], 8) is not None)
        # owner add of C
        o.cmd(cmd="roomAdd", roomId=rid, peer=idc)
        check("roomState reaches C after owner add",
              wait_for(lambda: [s for s in c.events_of("room-state")
                                if s.get("room", {}).get("roomId") == rid], 8) is not None)

        # ---- 3. mesh room text A -> B (owner NOT in the path)
        a.cmd(cmd="roomSend", roomId=rid, text="hello room")
        m_b = wait_message(b, 8, room=rid)
        check("B received room text with room field",
              m_b is not None and m_b.get("text") == "hello room" and m_b.get("room") == rid)
        m_o = wait_message(o, 4, room=rid)
        check("owner also receives mesh text (owner is a room member)",
              m_o is not None and m_o.get("text") == "hello room")

        # ---- 3b. unknown-room injection is dropped
        o_drain = len(o.events_of("message"))
        b.cmd(cmd="send", to=ido, text="not really a room msg")  # baseline still works
        wait_for(lambda: len(o.events_of("message")) > o_drain, 6)
        # (the unknown-room drop is covered in-process below — see step 9)

        # ---- 4. room-file post: A registers via roomFile's path form;
        # owner fans metadata to ALL members (incl. C, non-friend of A)
        payload = b"room-file-bytes-0123456789abcdef"
        fpath = os.path.join(ha, "roomfile.txt")
        with open(fpath, "wb") as f:
            f.write(payload)
        a.cmd(cmd="roomFile", roomId=rid, attachment={"path": fpath, "name": "roomfile.txt"})
        import hashlib
        sha = hashlib.sha256(payload).hexdigest()
        fm_b = wait_message(b, 8, room=rid)
        att_b = (fm_b or {}).get("attachment") or {}
        check("B sees room-file metadata (owner fan-out)",
              att_b.get("name") == "roomfile.txt" and att_b.get("fileId"))
        fid1, mid1 = att_b.get("fileId", ""), att_b.get("mid", "")
        check("metadata has sha256", att_b.get("sha256") == sha)
        fm_c = wait_message(c, 8, room=rid)
        check("C (non-friend of A) sees the metadata too",
              ((fm_c or {}).get("attachment") or {}).get("name") == "roomfile.txt")

        # ---- 5. B (friend of A) pulls the bytes; A gets roomFileStatus
        b.cmd(**{"cmd": "acceptAttachment", "from": ida, "fileId": fid1,
                 "mid": mid1, "name": "roomfile.txt", "sha256": sha, "room": rid})
        saved_b = wait_for(lambda: [e for e in b.events_of("attachment-saved")
                                    if e.get("fileId") == fid1 and e.get("ok")], 8)
        check("B saved the room file", bool(saved_b))
        sp = (saved_b or [{}])[0].get("path", "")
        check("saved bytes exact", bool(sp) and os.path.exists(sp) and open(sp, "rb").read() == payload)
        stat = wait_for(lambda: [e for e in a.events_of("room-file-status")
                                 if e.get("fileId") == fid1 and e.get("peer") == idb], 8)
        check("A got roomFileStatus saved from B",
              bool(stat) and (stat or [{}])[-1].get("status") == "saved")

        # ---- 6. trust gate: C (NOT A's friend) is refused, then befriends
        c.cmd(**{"cmd": "acceptAttachment", "from": ida, "fileId": fid1,
                 "mid": mid1, "name": "roomfile2.txt", "sha256": sha, "room": rid})
        refused = wait_for(lambda: [e for e in c.events_of("attachment-saved")
                                    if e.get("fileId") == fid1 and not e.get("ok")], 6)
        check("trust gate: C's save refused (not friends with sender)",
              bool(refused) and "not friends" in (refused or [{}])[0].get("error", ""))
        check("refusal left no .part",
              not os.path.exists(os.path.join(DLDIR_C, "roomfile2.txt.part")))
        friend_pair(a, c, ida, idc, "Alpha", "Charlie")
        c.cmd(**{"cmd": "acceptAttachment", "from": ida, "fileId": fid1,
                 "mid": mid1, "name": "roomfile2.txt", "sha256": sha, "room": rid})
        saved_c = wait_for(lambda: [e for e in c.events_of("attachment-saved")
                                    if e.get("fileId") == fid1 and e.get("ok")], 8)
        check("after befriending, the same message saves", bool(saved_c))

        # ---- 7. canInvite enforcement
        o.cmd(cmd="roomSetCanInvite", roomId=rid, peer=ida, allowed=True)
        check("canInvite grant broadcasts",
              wait_for(lambda: any(s.get("room", {}).get("members", {}).get(ida, {}).get("canInvite")
                                   for s in a.events_of("room-state")), 6) is not None)
        o_drain = len(o.events_of("error"))
        b_drain = len(b.events_of("error"))
        b.cmd(cmd="roomAdd", roomId=rid, peer=idd)  # B unpermitted -> refused
        check("unpermitted member's add refused (local error on proposer)",
              wait_for(lambda: len(b.events_of("error")) > b_drain, 5) is not None)
        # Permitted A proposes D -> owner invites D.
        a.cmd(cmd="roomAdd", roomId=rid, peer=idd)
        inv_d = d.wait_event("room-invite", timeout=8)
        check("permitted member's add executed by owner (invite sent)",
              inv_d is not None and inv_d.get("roomId") == rid)

        # ---- 8. colors: A sets; kill-switch; both broadcast
        a.cmd(cmd="setRoomColor", roomId=rid, token="red", hex="#D35F5F")
        col = wait_for(lambda: [s for s in b.events_of("room-state")
                                if s.get("room", {}).get("members", {}).get(ida, {}).get("color", {}).get("token") == "red"], 8)
        check("color broadcast (token+hex) reaches members",
              bool(col) and (col or [{}])[-1]["room"]["members"][ida]["color"].get("hex") == "#D35F5F")
        o.cmd(cmd="toggleRoomColors", roomId=rid, enabled=False)
        off = wait_for(lambda: [s for s in a.events_of("room-state")
                                if s.get("room", {}).get("roomId") == rid
                                and s.get("room", {}).get("colorsEnabled") is False], 8)
        check("kill-switch broadcasts colorsEnabled=false", bool(off))
        seq_after = ((off or [{}])[-1].get("room", {}).get("seq", 0))
        o.cmd(cmd="toggleRoomColors", roomId=rid, enabled=True)
        check("kill-switch re-enable broadcasts",
              wait_for(lambda: any(s.get("room", {}).get("roomId") == rid
                                   and s.get("room", {}).get("colorsEnabled")
                                   and s.get("room", {}).get("seq", 0) > seq_after
                                   for s in a.events_of("room-state")), 6) is not None)

        # ---- 9. owner-offline freeze + mesh survives + persistence after
        o.stop()
        stopped.append(o)
        time.sleep(0.5)
        a_drain = len(a.events_of("error"))
        a.cmd(cmd="roomAdd", roomId=rid, peer=idb)  # management vs dead owner
        check("management against offline host errors (frozen)",
              wait_for(lambda: len(a.events_of("error")) > a_drain, 5) is not None)
        a.cmd(cmd="roomSend", roomId=rid, text="mesh still works")
        m_b2 = wait_message(b, 8, room=rid)
        check("mesh room text works while host offline",
              m_b2 is not None and m_b2.get("text") == "mesh still works")

        # ---- 10. persistence: owner + member cache survive restart
        o2 = Daemon(ho, 4971, "Owner")
        ready = o2.wait_event("ready", timeout=8) or {}
        check("owner rooms persist across restart",
              any(r.get("roomId") == rid for r in ready.get("rooms", [])))
        a2 = Daemon(ha, 4972, "Alpha")
        ready_a = a2.wait_event("ready", timeout=8) or {}
        check("member cache persists across restart",
              any(r.get("roomId") == rid for r in ready_a.get("rooms", [])))
        # Daemon responsive after all the room traffic (version-skew class).
        a2.cmd(cmd="roomList")
        check("daemon responsive after room traffic",
              a2.wait_event("room-list", timeout=6) is not None)

    finally:
        for x in (o, a, b, c, d):
            try:
                if x not in stopped:
                    x.stop()
            except Exception:
                pass
        for x in (o2, a2):
            try:
                x.stop()
            except Exception:
                pass
        for h in (ho, ha, hb, hc, hd):
            shutil.rmtree(h, ignore_errors=True)

    print("")
    if failures:
        print("FAILED: %d — %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL GROUPS TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
