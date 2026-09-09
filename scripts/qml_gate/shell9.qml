// Unread LOGIC bench: drives the REAL shared/Lanchat.qml. run.sh copies that
// file next to this shell as LanchatReal.qml with the `pragma Singleton`
// line stripped, so it can be instantiated directly; it is created with
// manageDaemon=false so Component.onCompleted spawns no ensure/daemon
// processes. Feeds onDaemonLine the same JSON the bridge emits and asserts
// the unread maps.
//   L1  panel closed, 1:1 incoming -> unreadByPeer[from]=1, unreadCount=1
//   L2  second message accumulates to 2
//   L3  clearPeerUnread removes exactly that key
//   L4  panel open + that peer selected -> NOT marked
//   L5  panel open, OTHER peer -> marked
//   L6  room message, room not selected -> unreadByRoom[room]=1, never the
//       peer map; message stored in roomMessages
//   L7  room selected -> NOT marked
//   L8  clearRoomUnread removes the key
//   L9  outgoing message never marked
//   L10 1:1 message never lands in the room map
import QtQuick
import qs.Commons

Item {
  id: benchRoot
  property bool done: false
  property var real1: null
  function fail(msg) { console.log("BENCH-LU-FAIL " + msg); Qt.exit(1) }

  Timer {
    interval: 15000; running: true; repeat: false
    onTriggered: { console.log("BENCH-LU-FAIL failsafe-timeout"); Qt.exit(1) }
  }

  function mkMsg(mid, from, room, outgoing) {
    return { mid: mid, from: from, fromName: "N", text: "t" + mid,
             ts: 1, outgoing: !!outgoing, room: room || "" }
  }

  function mapSize(map) {
    var n = 0; for (var k in map) n++
    return n
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

    L.onDaemonLine(JSON.stringify({ event: "message", message: mkMsg("m1", "peer-2", "") }))
    if ((L.unreadByPeer["peer-2"] || 0) !== 1)
      return fail("L1 unreadByPeer=" + JSON.stringify(L.unreadByPeer))
    if (L.unreadCount !== 1) return fail("L1 unreadCount=" + L.unreadCount)
    console.log("BENCH-LU-OK-L1")

    L.onDaemonLine(JSON.stringify({ event: "message", message: mkMsg("m2", "peer-2", "") }))
    if (L.unreadByPeer["peer-2"] !== 2) return fail("L2")
    console.log("BENCH-LU-OK-L2")

    L.clearPeerUnread("peer-2")
    if (mapSize(L.unreadByPeer) !== 0) return fail("L3 " + JSON.stringify(L.unreadByPeer))
    console.log("BENCH-LU-OK-L3")

    L.panelOpen = true
    L.selectedPeerId = "peer-2"
    L.onDaemonLine(JSON.stringify({ event: "message", message: mkMsg("m3", "peer-2", "") }))
    if (mapSize(L.unreadByPeer) !== 0) return fail("L4 " + JSON.stringify(L.unreadByPeer))
    console.log("BENCH-LU-OK-L4")

    L.onDaemonLine(JSON.stringify({ event: "message", message: mkMsg("m4", "peer-3", "") }))
    if ((L.unreadByPeer["peer-3"] || 0) !== 1) return fail("L5")
    console.log("BENCH-LU-OK-L5")

    L.onDaemonLine(JSON.stringify({ event: "message", message: mkMsg("m5", "peer-2", "room-1") }))
    if ((L.unreadByRoom["room-1"] || 0) !== 1)
      return fail("L6 room=" + JSON.stringify(L.unreadByRoom))
    if (mapSize(L.unreadByPeer) !== 1) return fail("L6 leaked into peer map")
    if (L.roomMessages.length !== 1) return fail("L6 roomMessages=" + L.roomMessages.length)
    console.log("BENCH-LU-OK-L6")

    L.selectedRoomId = "room-1"
    L.onDaemonLine(JSON.stringify({ event: "message", message: mkMsg("m6", "peer-2", "room-1") }))
    if (L.unreadByRoom["room-1"] !== 1) return fail("L7")
    console.log("BENCH-LU-OK-L7")

    L.clearRoomUnread("room-1")
    if (mapSize(L.unreadByRoom) !== 0) return fail("L8")
    console.log("BENCH-LU-OK-L8")

    L.onDaemonLine(JSON.stringify({ event: "message", message: mkMsg("m7", "peer-4", "", true) }))
    if (mapSize(L.unreadByPeer) !== 1 || mapSize(L.unreadByRoom) !== 0)
      return fail("L9 peer=" + JSON.stringify(L.unreadByPeer))
    console.log("BENCH-LU-OK-L9")

    L.onDaemonLine(JSON.stringify({ event: "message", message: mkMsg("m8", "peer-5", "") }))
    if (mapSize(L.unreadByRoom) !== 0) return fail("L10")
    console.log("BENCH-LU-OK-L10")

    Qt.exit(0)
  }
}
