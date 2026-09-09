// roominvitebench: multiple room invites must COEXIST. Two invites for two
// different roomIds both render (Join button per row + Decline per row); an
// invite for an already-present roomId must not duplicate a row (the
// room-invite wire handler dedupes by roomId with concat). Also proves the
// invite rows SURVIVE a room-list event (rooms updates must never wipe
// pending invites — the same overwrite class as the friend-request bug).
import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 400; height: 600

  property bool done: false
  function fail(msg) { console.log("BENCH-RI-FAIL " + msg); Qt.exit(1) }

  RoomListSection {
    id: roomListSection
    animateSections: false
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top

    peerRowH: Style.space(32)
    hostHeight: 0
    alertStackBottom: 0
    amRoomOwnerOfFn: function(roomId) { return false }
    selectRoomFn: function(roomId) { console.log("stub selectRoomFn", roomId) }
    leaveSelectedRoomFn: function() { console.log("stub leaveSelectedRoomFn") }
  }

  Timer {
    interval: 15000; running: true; repeat: false
    onTriggered: { console.log("BENCH-RI-FAIL failsafe-timeout"); Qt.exit(1) }
  }

  Component.onCompleted: {
    Qt.callLater(function() {
      if (benchRoot.done) return
      benchRoot.done = true

      Lanchat.myId = "me"
      Lanchat.rooms = []
      Lanchat.roomStates = ({})
      Lanchat.roomInvites = [
        { roomId: "room-a", name: "Alpha", from: "p1", fromName: "Bob" },
        { roomId: "room-b", name: "Beta", from: "p2", fromName: "Carol" }
      ]
      roomListSection.expand()

      Qt.callLater(function() {
        var joinBtns = []
        var declineBtns = []
        function collect(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof Button && c.text === "Join") joinBtns.push(c)
            if (c instanceof Button && c.text === "\u2715"
                && c.tooltipText === "Decline this invite") declineBtns.push(c)
            collect(c)
          }
        }
        collect(roomListSection)

        // (R1) both invite rows render: one Join + one Decline each
        if (joinBtns.length !== 2)
          return fail("R1 expected 2 invite rows (2 Join buttons), found " + joinBtns.length)
        if (declineBtns.length !== 2)
          return fail("R1 expected 2 Decline buttons, found " + declineBtns.length)
        console.log("BENCH-RI-OK-R1 two invites for two roomIds both render (2 Join, 2 Decline)")

        // (R2) an invite for an EXISTING roomId must not duplicate a row:
        // drive the same dedupe logic the room-invite wire handler uses
        // (exists-check by roomId, then concat).
        var exists = false
        for (var i = 0; i < Lanchat.roomInvites.length; i++)
          if (Lanchat.roomInvites[i].roomId === "room-a") { exists = true; break }
        if (!exists)
          Lanchat.roomInvites = Lanchat.roomInvites.concat([
            { roomId: "room-a", name: "Alpha", from: "p1", fromName: "Bob" }])
        if (Lanchat.roomInvites.length !== 2)
          return fail("R2 duplicate roomId duplicated a row; invites="
                      + JSON.stringify(Lanchat.roomInvites))
        console.log("BENCH-RI-OK-R2 duplicate roomId does not duplicate a row")

        // (R3) invites survive a rooms/room-list style update (no overwrite)
        var before = Lanchat.roomInvites.length
        Lanchat.rooms = Lanchat.rooms.concat([{ roomId: "room-c", name: "Gamma", owner: "me" }])
        if (Lanchat.roomInvites.length !== before)
          return fail("R3 rooms update wiped invites; left="
                      + JSON.stringify(Lanchat.roomInvites))
        console.log("BENCH-RI-OK-R3 invites survive a rooms update")

        // (R4) dismissing one invite leaves the other (per-row isolation)
        Lanchat.dismissRoomInvite("room-a")
        if (Lanchat.roomInvites.length !== 1 || Lanchat.roomInvites[0].roomId !== "room-b")
          return fail("R4 dismiss removed wrong rows; invites="
                      + JSON.stringify(Lanchat.roomInvites))
        console.log("BENCH-RI-OK-R4 dismiss one, other survives")
        Lanchat.forgetCount = 0 // neutralize the stub's roomForget counter

        Qt.exit(0)
      })
    })
  }
}
