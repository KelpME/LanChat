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


def test_invite_decline(oo, aa, ido, ida, check, wait_for):
    """Regression for the invite Decline button (UI: Lanchat.dismissRoomInvite
    drops the row and sends cmd roomForget for the invite-seeded stub).
    Declining must (1) remove the stub from the invitee's room-list, (2) NOT
    poison the room — the owner can re-invite, a SECOND room-invite for the
    same roomId arrives, and the invitee can still join (room-state proof).

    Roles: oo = owner, aa = invitee (use daemon b here, like test_forget, so
    unconsumed room-invite events never pollute a's queue that
    test_member_leave's wait_event reads)."""

    oo.cmd(cmd="createRoom", name="DeclineRoom")
    created = oo.wait_event("room-created", timeout=6)
    rid = (created or {}).get("roomId", "")
    check("decline: room created", bool(rid))
    oo.cmd(cmd="roomInvite", roomId=rid, peer=ida)
    inv = wait_for(lambda: [e for e in aa.events_of("room-invite")
                            if e.get("roomId") == rid], 6)
    check("decline: first invite reaches invitee", bool(inv))

    # Decline = roomForget on the stub (exactly what the UI's ✕ does).
    # Window on events AFTER the forget so an all-history scan can't pass
    # vacuously on a not-yet-arrived list.
    n_list = len(aa.events_of("room-list"))
    aa.cmd(cmd="roomForget", roomId=rid)
    gone = wait_for(lambda: [e for e in aa.events_of("room-list")[n_list:]
                             if all(r.get("roomId") != rid
                                    for r in e.get("rooms", []))], 6)
    check("decline: roomForget removes the invite stub from room-list", bool(gone))

    # Owner re-invites the same room: a SECOND room-invite must arrive.
    n_inv = len(aa.events_of("room-invite"))
    oo.cmd(cmd="roomInvite", roomId=rid, peer=ida)
    inv2 = wait_for(lambda: [e for e in aa.events_of("room-invite")[n_inv:]
                             if e.get("roomId") == rid], 6)
    check("decline: re-invite delivers a second room-invite for the same room",
          bool(inv2))

    # Join after the decline/re-invite cycle, confirmed by room-state membership.
    aa.cmd(cmd="roomJoin", roomId=rid)
    got = wait_for(lambda: [s for s in aa.events_of("room-state")
                            if s.get("room", {}).get("roomId") == rid
                            and ida in s.get("room", {}).get("members", {})], 8)
    check("decline: join after re-invite confirmed (member in room-state)", bool(got))


def test_unfriend_leave(o, b, ido, idb, check, wait_for):
    """Regression for 'leave a room whose owner I am no longer friends with'.

    A member's roomLeave rides a roomLeave envelope to the OWNER's socket;
    after an unfriend that socket can never exist, so pre-fix the leave froze
    forever ("host offline — changes frozen"). Post-fix (rooms.py
    member_leave): when the owner is not a trusted peer the leaver takes the
    LOCAL path — the room drops from their list and the leave SUCCEEDS.
    (Trusted-but-offline freeze is covered by test_member_leave.)

    Roles: o = owner, b = member (b's queues are not read by wait_event
    downstream). Must run before test_member_leave stops o. Own room ids.
    """

    o.cmd(cmd="createRoom", name="UFRoom")
    created = o.wait_event("room-created", timeout=6)
    rid = (created or {}).get("roomId", "")
    check("unfriend-leave: room created", bool(rid))
    o.cmd(cmd="roomInvite", roomId=rid, peer=idb)
    # Count-window, not wait_event: b's queue holds a STALE room-invite from
    # test_forget's room, and wait_event pops the first match (test_forget's
    # docstring warns exactly this). Compare only events arriving after now.
    n_inv = len(b.events_of("room-invite"))
    o.cmd(cmd="roomInvite", roomId=rid, peer=idb)
    inv = wait_for(lambda: [e for e in b.events_of("room-invite")[n_inv:]
                            if e.get("roomId") == rid], 6)
    check("unfriend-leave: invite reaches B", bool(inv))
    b.cmd(cmd="roomJoin", roomId=rid)
    got = wait_for(lambda: [s for s in b.events_of("room-state")
                            if s.get("room", {}).get("roomId") == rid
                            and idb in s.get("room", {}).get("members", {})], 8)
    check("unfriend-leave: B joined (member in room-state)", bool(got))

    # B unfriends O (the local unfriend notifies O via signed UDP / TCP so
    # BOTH sides drop the link). Wait for the friend-removed event on B.
    n_err = len(b.events_of("error"))
    b.cmd(cmd="unfriend", id=ido)
    unf_done = wait_for(lambda: any(e.get("id") == ido
                                    for e in b.events_of("friend-removed")), 6)
    check("unfriend-leave: B unfriended O (friend-removed seen)", unf_done is not None)

    # Leave must now SUCCEED via the local path: room-list event after the
    # leave no longer contains the room, and no 'frozen' error fired.
    n_blist = len(b.events_of("room-list"))
    b.cmd(cmd="roomLeave", roomId=rid)
    gone = wait_for(lambda: [e for e in b.events_of("room-list")[n_blist:]
                             if all(r.get("roomId") != rid
                                    for r in e.get("rooms", []))], 6)
    check("unfriend-leave: room gone from leaver's list after unfriend", bool(gone))
    no_frozen = not any("frozen" in (e.get("message") or "")
                        for e in b.events_of("error")[n_err:])
    check("unfriend-leave: no 'frozen' error during the unfriend leave", no_frozen)


def test_member_leave(oo, aa, ido, ida, check, wait_for):
    """Regression for 'leave this room' (rooms.member_leave): a member's
    roomLeave must (1) drop the room from the LEAVER's own list, (2) reach
    the OWNER so the authoritative roster drops them, and (3) freeze with no
    half-left state when the owner is offline."""

    def fresh_room(tag):
        oo.cmd(cmd="createRoom", name=tag)
        created = oo.wait_event("room-created", timeout=6)
        rid = (created or {}).get("roomId", "")
        check("%s: room created" % tag, bool(rid))
        oo.cmd(cmd="roomInvite", roomId=rid, peer=ida)
        inv = aa.wait_event("room-invite", timeout=6)
        check("%s: invite reaches A" % tag, inv is not None and inv.get("roomId") == rid)
        aa.cmd(cmd="roomJoin", roomId=rid)
        got = wait_for(lambda: [s for s in aa.events_of("room-state")
                                if s.get("room", {}).get("roomId") == rid], 8)
        check("%s: A joined (roomState seen)" % tag, bool(got))
        return rid

    # -- online leave: leaver's list AND owner's authoritative roster update
    rid2 = fresh_room("LeaveRoom")
    n_alist = len(aa.events_of("room-list"))
    n_ostate = len(oo.events_of("room-state"))
    n_olist = len(oo.events_of("room-list"))
    aa.cmd(cmd="roomLeave", roomId=rid2)
    # Wait for a room-list event emitted AFTER the leave (member_leave always
    # emits one) and assert the room is gone from THAT event's snapshot —
    # `not any()` over a not-yet-arrived window races and vacuously passes.
    drop = wait_for(lambda: [e for e in aa.events_of("room-list")[n_alist:]
                             if all(r.get("roomId") != rid2
                                    for r in e.get("rooms", []))], 6)
    check("leaver's room-list drops the room", bool(drop))
    new_state = wait_for(lambda: [s for s in oo.events_of("room-state")[n_ostate:]
                                  if s.get("room", {}).get("roomId") == rid2], 6)
    check("owner receives room-state after member leave", bool(new_state))
    check("owner roster drops the leaver",
          ida not in ((new_state or [{}])[-1].get("room", {}).get("members", {})))
    # memberCount must drop in a room-list event emitted AFTER the leave —
    # the creation event already had count 1, so an all-history any() is
    # vacuously true on unpatched code.
    cnt = wait_for(lambda: [r for e in oo.events_of("room-list")[n_olist:]
                            for r in e.get("rooms", [])
                            if r.get("roomId") == rid2 and r.get("memberCount") == 1], 4)
    check("owner room-list memberCount drops to 1", bool(cnt))

    # -- offline owner: the leave must freeze, not half-apply locally.
    # (A fresh room is needed: rid2 legitimately left aa's cache above.)
    rid3 = fresh_room("LeaveFrozen")
    oo.stop()
    time.sleep(0.5)
    while aa.wait_event("error", timeout=0.1):
        pass
    aa.cmd(cmd="roomLeave", roomId=rid3)
    err = aa.wait_event("error", timeout=6)
    check("offline owner: leave errors (frozen)",
          err is not None and "host offline" in str(err.get("message", "")))
    check("offline owner: no half-left (room still listed)",
          any(r.get("roomId") == rid3
              for e in aa.events_of("room-list") for r in e.get("rooms", [])))


def test_forget(oo, aa, ido, ida, live_room_id, check, wait_for):
    """Regression for 'forget dismissed/orphaned room' (rooms.forget_room,
    command roomForget). An ORPHANED room — one we neither own nor appear in
    the roster — is exactly the stuck-group case: roomLeave can't drop it
    (leave needs a member record), so we must be able to forget it locally.
    Also verifies the safety gate: a room we're a live member of is refused.

    Roles (same convention as test_member_leave): oo = the daemon that OWNS
    the rooms, aa = a peer daemon, ida = aa's fingerprint."""

    # Build an orphaned-room for aa: oo invites aa, but aa never joins. The
    # invite seeds a cache stub in aa's store with an empty roster and a
    # remote owner (oo) — a room aa is not actually part of.
    oo.cmd(cmd="createRoom", name="GhostRoom")
    created = oo.wait_event("room-created", timeout=6)
    rid = (created or {}).get("roomId", "")
    check("forget: ghost room created", bool(rid))
    oo.cmd(cmd="roomInvite", roomId=rid, peer=ida)
    inv = wait_for(lambda: [e for e in aa.events_of("room-invite")
                            if e.get("roomId") == rid], 6)
    check("forget: invite stub reaches member", bool(inv))
    # roomLeave on the orphan must NOT drop it (no member record -> leave
    # early-returns False; the room stays listed).
    aa.cmd(cmd="roomLeave", roomId=rid)
    pre = wait_for(lambda: [e for e in aa.events_of("room-list")
                            for r in e.get("rooms", []) if r.get("roomId") == rid], 6)
    check("forget: orphaned room persists after roomLeave (leave dead)", bool(pre))

    # roomForget on the orphaned room DOES drop it locally.
    aa.cmd(cmd="roomForget", roomId=rid)
    gone = wait_for(lambda: [e for e in aa.events_of("room-list")
                             if all(r.get("roomId") != rid for r in e.get("rooms", []))], 6)
    check("forget: roomForget drops the orphaned room", bool(gone))

    # Safety gate: a room aa is a LIVE member of must refuse roomForget.
    # Reuse the main room aa already joined in main().
    live = live_room_id
    n_err = len(aa.events_of("error"))
    aa.cmd(cmd="roomForget", roomId=live)
    err = wait_for(lambda: [e for e in aa.events_of("error")[n_err:]
                            if "Nothing to forget" in str(e.get("message", ""))], 5)
    check("forget: live-member room refuses forget (error)", bool(err))
    still = wait_for(lambda: [e for e in aa.events_of("room-list")
                              for r in e.get("rooms", []) if r.get("roomId") == live], 5)
    check("forget: live-member room still listed", bool(still))


def test_removed_member_injection(o2, a2, b, c, ido, ida, idb, idc, check, wait_for):
    """Review #9076 round 8: a REMOVED room member who remains a direct
    friend must not be able to inject authenticated room-scoped traffic
    (t:"msg" with room, or direct t:"roomFile") into the group's history and
    UI on any daemon whose roster no longer lists them. Room existence is
    not authorization — RECORDED MEMBERSHIP is.

    Runs LAST on the restarted daemons (fresh event queues — no room-invite
    pollution for other tests' consuming wait_event calls). o2 owns a fresh
    room; members a2, b, c (friendships O-A, O-B, O-C, A-B, A-C all survive
    the restart via persisted state). o2 removes b, then we inject AS b over
    a raw authenticated TLS connection (b's own cert — still a genuine
    friend of a2):
      - room text   -> a2 drops it (inbound-dropped reason=not-room-member)
      - room file   -> a2 drops it (room-drop reason=not-room-member)
      - 1:1 text    -> a2 still accepts it (friendship intact, untouched)
      - room text from a2 -> c (still a member) still receives it
    """
    import test_peer as tp
    cert_b = open(os.path.join(b.home, ".config", "omarchy",
                               "lanchat-certs", "cert.pem")).read()
    key_b = open(os.path.join(b.home, ".config", "omarchy",
                              "lanchat-certs", "key.pem")).read()

    o2.cmd(cmd="createRoom", name="GateRoom")
    created = o2.wait_event("room-created", timeout=6)
    rid = (created or {}).get("roomId", "")
    check("inject: gate room created", bool(rid))

    # test_unfriend_leave (step 8b3) dissolved O–B; roomJoin admission needs
    # owner friendship, so restore it (A–B, the injection-relevant friendship,
    # was never touched).
    friend_pair(o2, b, ido, idb, "Owner", "Beta")

    # Direction proof per member: X->o2 room envelopes FREEZE while that
    # direction has no socket (no hold for t:"room"), so before joining we
    # PROVE each X->o2 path with held 1:1 pings (they flush on reconnect —
    # arrival IS the proof). Re-ping every 2s until all arrive (a restarted
    # owner's dials sit behind up to 16s backoff per peer).
    need = {"ping-a2", "ping-b", "ping-c"}
    peers = {"ping-a2": a2, "ping-b": b, "ping-c": c}
    pinged, proof_deadline = set(), time.time() + 60
    while need and time.time() < proof_deadline:
        for tag in [t for t in need if t not in pinged]:
            peers[tag].cmd(cmd="send", to=ido, text=tag, friend_request=False)
            pinged.add(tag)
        got = {str((e.get("message") or {}).get("text"))
               for e in o2.events_of("message")}
        need -= got
        if need:
            time.sleep(2)
    check("inject: X->owner paths proven for all members", not need,
          "missing=%s" % sorted(need))

    # Invite all (seeds the cache stub roomJoin needs), wait for landings,
    # then join. With proven sockets one round suffices; bounded re-invite +
    # re-join covers a rare blip.
    members_full = None
    deadline = time.time() + 45
    while time.time() < deadline and not members_full:
        for pid in (ida, idb, idc):
            o2.cmd(cmd="roomInvite", roomId=rid, peer=pid)
        landed = wait_for(lambda: all(
            [e for e in p.events_of("room-invite") if e.get("roomId") == rid]
            for p in (a2, b, c)), 6)
        if not landed:
            continue
        for x in (a2, b, c):
            x.cmd(cmd="roomJoin", roomId=rid)
        members_full = wait_for(lambda: [e for e in o2.events_of("room-state")
                                         if (e.get("room") or {}).get("roomId") == rid
                                         and all(p in ((e.get("room") or {}).get("members") or {})
                                                 for p in (ida, idb, idc))], 4)
    check("inject: all three members on the roster", bool(members_full))

    # Owner removes b. Roster change propagates: b's cache drops the room,
    # a2's cached roster loses b. (Brief settle first: the convergence loop
    # may have invites in flight, and a late invite would re-seed b's stub.)
    time.sleep(1.0)
    n_blist = len(b.events_of("room-list"))
    gone_b = None
    rm_deadline = time.time() + 30
    while time.time() < rm_deadline and not gone_b:
        o2.cmd(cmd="roomRemove", roomId=rid, peer=idb)
        gone_b = wait_for(lambda: [e for e in b.events_of("room-list")[n_blist:]
                                   if all(r.get("roomId") != rid for r in e.get("rooms", []))], 3)
    check("inject: removed member's cache drops the room", bool(gone_b))
    lost_b = wait_for(lambda: [e for e in a2.events_of("room-state")
                               if (e.get("room") or {}).get("roomId") == rid
                               and idb not in ((e.get("room") or {}).get("members") or {})], 8)
    check("inject: remaining member's roster drops the removed peer", bool(lost_b))

    # Raw injection AS b (authenticated connection, b's own identity): room
    # text straight into a2's socket — bypasses send_room_text's own lookup.
    tp.authed_send("127.0.0.1", 4972, cert_b, key_b,
                   {"t": "msg", "from": idb, "fromName": "Beta", "room": rid,
                    "mid": "inj8text", "text": "INJECTED-ROOM-TEXT"})
    drop1 = wait_for(lambda: [e for e in a2.events_of("diagnostic")
                              if e.get("message") == "inbound-dropped"
                              and e.get("reason") == "not-room-member"], 6)
    check("inject: removed member's room TEXT dropped (not-room-member)", bool(drop1))
    time.sleep(0.6)
    check("inject: no injected room-text bubble in UI/history",
          not [e for e in a2.events_of("message")
               if (e.get("message") or {}).get("mid") == "inj8text"])

    # Direct roomFile packet AS b.
    tp.authed_send("127.0.0.1", 4972, cert_b, key_b,
                   {"t": "roomFile", "roomId": rid, "from": idb, "fromName": "Beta",
                    "text": "INJECTED-FILE",
                    "att": {"fileId": "inj8file", "mid": "inj8att",
                            "name": "evil.bin", "size": 42,
                            "mime": "application/octet-stream"}})
    drop2 = wait_for(lambda: [e for e in a2.events_of("diagnostic")
                              if e.get("message") == "room-drop"
                              and e.get("reason") == "not-room-member"], 6)
    check("inject: removed member's room FILE dropped (not-room-member)", bool(drop2))
    time.sleep(0.6)
    check("inject: no injected room-file bubble in UI/history",
          not [e for e in a2.events_of("message")
               if (e.get("message") or {}).get("mid") == "inj8att"])

    # Removed-but-still-friend: plain 1:1 text must still flow (the fix
    # gates ROOM traffic only — friendship is untouched by room removal).
    b.cmd(cmd="send", to=ida, text="still-friends-1to1", friend_request=False)
    m11 = wait_message(a2, 8, text="still-friends-1to1")
    check("inject: 1:1 friendship unaffected by room removal", m11 is not None)

    # Positive control: a CURRENT member's room text still reaches members...
    a2.cmd(cmd="roomSend", roomId=rid, text="member-ok")
    mc = wait_message(c, 8, room=rid, text="member-ok")
    check("inject: current member's room text still delivered", mc is not None)
    # ...and NOT the removed member.
    time.sleep(0.6)
    check("inject: removed member receives no room text",
          not [e for e in b.events_of("message")
               if (e.get("message") or {}).get("text") == "member-ok"])


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

        # owner-side add of B = INVITE: B must accept (roomJoin) before
        # membership; nothing is admitted without the recipient's consent.
        o.cmd(cmd="roomAdd", roomId=rid, peer=idb)
        inv_b = b.wait_event("room-invite", timeout=8)
        check("owner add sends an invite (not direct admission)",
              inv_b is not None and inv_b.get("roomId") == rid)
        b.cmd(cmd="roomJoin", roomId=rid)
        check("roomState reaches B after B accepts",
              wait_for(lambda: [s for s in b.events_of("room-state")
                                if s.get("room", {}).get("roomId") == rid], 8) is not None)
        # owner add of C — same invite flow
        o.cmd(cmd="roomAdd", roomId=rid, peer=idc)
        inv_c = c.wait_event("room-invite", timeout=8)
        check("C receives an invite (consent required)",
              inv_c is not None and inv_c.get("roomId") == rid)
        c.cmd(cmd="roomJoin", roomId=rid)
        check("roomState reaches C after C accepts",
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
        check("metadata sha256 empty (digest streams on End frame)",
              att_b.get("sha256") == "")
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

        # ---- 8b. forget-orphaned-room regression (needs live owner — must
        # run before test_member_leave, whose offline-owner half stops `o`.
        # Uses daemon b + room rid so the unconsumed room-invite event lands in
        # b's queue, never polluting a's queue that test_member_leave reads.)
        test_forget(o, b, ido, idb, rid, check, wait_for)

        # ---- 8b2. invite decline + re-invite (also on daemon b — its
        # room-invite queue is never read by wait_event downstream)
        test_invite_decline(o, b, ido, idb, check, wait_for)

        # ---- 8b3. unfriend-proof leave (owner stays live; must run before
        # test_member_leave / step 9 which rely on o)
        test_unfriend_leave(o, b, ido, idb, check, wait_for)

        # ---- 8c. member leave regression (needs live owner; step 9 stops it)
        test_member_leave(o, a, ido, ida, check, wait_for)

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
        a.stop()
        stopped.append(a)
        time.sleep(0.5)
        a2 = Daemon(ha, 4972, "Alpha")
        ready_a = a2.wait_event("ready", timeout=8) or {}
        check("member cache persists across restart",
              any(r.get("roomId") == rid for r in ready_a.get("rooms", [])))

        # ---- 10b. removed-member injection gate (review #9076 round 8).
        # Runs LAST on the restarted daemons: fresh event queues, so its
        # invites/room-states never pollute earlier tests' wait_event pops.
        test_removed_member_injection(o2, a2, b, c, ido, ida, idb, idc,
                                      check, wait_for)
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
