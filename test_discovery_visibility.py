#!/usr/bin/env python3
"""Regression test for a discovery bug: a daemon that STARTS in private
visibility must START broadcasting the moment "Discoverable" (visibility=open)
is flipped on — without a daemon restart.

Original bug: _udp_loop captured `hidden = visibility() != "open"` ONCE before
its while loop and never recomputed it inside. So a device that booted private
stayed hidden for the whole process lifetime even after setVisibility->open:
the config said open (UI showed Discoverable on) but the UDP loop never
re-broadcast, scanned, or re-announced — the device was undiscoverable. This is
exactly the reported symptom.

The pre-existing test suite only exercised the LISTENER side (injecting hello
packets and asserting the peer is recorded), never the BROADCAST side, which is
why the bug shipped. Real UDP broadcasts also don't loop back in a headless
sandbox, so this test stubs server._udp_send and drives the real udp_loop
in-process — it asserts the loop actually emits hellos after the flip.

Run: python3 test_discovery_visibility.py
"""
import os, socket, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server as srv


def main():
    home = tempfile.mkdtemp(prefix="lanchat-bcast-test-")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = home
    try:
        # Force private on boot (the bug trigger) — default is already private,
        # but be explicit.
        srv.CONFIG["visibility"] = "private"
        srv.CONFIG["online"] = True

        sent = []
        lock = threading.Lock()

        def fake_udp_send(sock, pkt, target=""):
            with lock:
                sent.append((dict(pkt), target))

        srv._udp_send = fake_udp_send

        # Drive the REAL udp_loop in-process. It binds a real UDP socket (which
        # is fine — nothing needs to reach it) and calls our stubbed _udp_send.
        t = threading.Thread(target=srv.udp_loop, daemon=True)
        t.start()

        def hello_count():
            with lock:
                return sum(1 for p, _ in sent if p.get("t") == "hello")

        # While private: the loop must NOT broadcast. Give it >1 broadcast
        # interval (3.0s) to prove it stays silent.
        time.sleep(3.6)
        c0 = hello_count()
        assert c0 == 0, "private daemon broadcast %d hellos (should be 0)" % c0
        print("OK  private daemon stays silent on the wire (0 hellos)")

        # Flip Discoverable on — the exact user scenario.
        srv.CONFIG["visibility"] = "open"
        # Wait > one broadcast interval (3.0s) for the next tick to fire.
        time.sleep(3.6)
        c1 = hello_count()
        assert c1 > 0, (
            "daemon did NOT start broadcasting after private->open flip — "
            "the reported bug (hidden flag never recomputed in the loop)")
        print("OK  daemon started broadcasting after visibility flip (%d hellos)" % c1)

        # Flip back to private — it must STOP again (flag recomputed each tick).
        srv.CONFIG["visibility"] = "private"
        time.sleep(3.6)
        c2 = hello_count()
        assert c1 == c2, "daemon kept broadcasting after returning to private (%d -> %d)" % (c1, c2)
        print("OK  daemon stops broadcasting again when flipped back to private")

        print("\nALL TESTS PASSED (broadcast-side visibility)")
        return 0
    finally:
        if old_home is not None:
            os.environ["HOME"] = old_home
        import shutil
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
