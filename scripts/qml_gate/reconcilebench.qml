// Reconcile LOGIC bench: drives the REAL shared/Lanchat.qml (copied next to
// this shell as LanchatReal.qml, pragma Singleton stripped, manageDaemon=false
// — same pattern as shell9). Asserts reconcileFriendRequests keeps pending
// requests until their peer is a CONFIRMED friend:
//   R1  two incoming requests coexist (UDP path never writes a friends entry)
//   R2  friends event with req-1 unconfirmed, req-2 absent -> BOTH survive
//       (the old "keep only if an unconfirmed entry exists" rule failed this)
//   R3  req-1 becomes CONFIRMED -> reconcile drops req-1, keeps req-2
//   R4  friend-rejected event for req-2 -> its row drops
import QtQuick
import qs.Commons

Item {
  id: benchRoot
  property bool done: false
  property var real1: null
  function fail(msg) { console.log("BENCH-RC-FAIL " + msg); Qt.exit(1) }

  Timer {
    interval: 15000; running: true; repeat: false
    onTriggered: { console.log("BENCH-RC-FAIL failsafe-timeout"); Qt.exit(1) }
  }

  Component.onCompleted: {
    Qt.callLater(function() {
      if (benchRoot.done) return
      benchRoot.done = true
      var comp = Qt.createComponent("LanchatReal.qml")
      if (comp.status === Component.Error)
        return fail("compile: " + comp.errorString())
      real1 = comp.createObject(benchRoot, {
        manageDaemon: false, soundEnabled: false, panelOpen: false,
        selectedPeerId: "", selectedRoomId: ""
      })
      if (!real1) return fail("create: " + comp.errorString())
      Qt.callLater(run)
    })
  }

  function run() {
    var L = benchRoot.real1

    // (R1) two incoming requests via the friend-request event path.
    L.onDaemonLine(JSON.stringify({ event: "friend-request", from: "req-1",
                                    name: "Bob", fingerprint: "req-1" }))
    L.onDaemonLine(JSON.stringify({ event: "friend-request", from: "req-2",
                                    name: "Carol", fingerprint: "req-2" }))
    if (L.friendRequests.length !== 2)
      return fail("R1 expected 2 pending, got " + JSON.stringify(L.friendRequests.map(function(r){ return r.peerId })))
    console.log("BENCH-RC-OK-R1 two requests coexist")

    // (R2) the friends event that wiped UDP-path requests under the old rule:
    // req-1 has an unconfirmed (TCP-path) record; req-2 has NO record at all.
    L.onDaemonLine(JSON.stringify({ event: "friends",
                                    friends: [{ id: "req-1", confirmed: false }] }))
    if (L.friendRequests.length !== 2)
      return fail("R2 reconcile dropped a request whose peer has no friends entry; left="
                  + JSON.stringify(L.friendRequests.map(function(r){ return r.peerId })))
    console.log("BENCH-RC-OK-R2 friends event keeps both (no confirmed peer)")

    // (R3) req-1 accepted (confirmed) -> its row drops, req-2 survives.
    L.onDaemonLine(JSON.stringify({ event: "friends",
                                    friends: [{ id: "req-1", confirmed: true }] }))
    var ids = L.friendRequests.map(function(r) { return r.peerId })
    if (ids.indexOf("req-1") >= 0)
      return fail("R3 confirmed peer not dropped; left=" + JSON.stringify(ids))
    if (ids.indexOf("req-2") < 0)
      return fail("R3 unrelated pending req-2 lost; left=" + JSON.stringify(ids))
    console.log("BENCH-RC-OK-R3 confirmed peer dropped, pending survives")

    // (R4) explicit reject removes the remaining row.
    L.onDaemonLine(JSON.stringify({ event: "friend-rejected", id: "req-2" }))
    if (L.friendRequests.length !== 0)
      return fail("R4 rejected request not dropped; left=" + JSON.stringify(L.friendRequests.map(function(r){ return r.peerId })))
    console.log("BENCH-RC-OK-R4 friend-rejected drops the row")

    console.log("BENCH-RC-PASS")
    Qt.exit(0)
  }
}
