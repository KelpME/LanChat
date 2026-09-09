import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 400; height: 600

  // Panel-side counters for the child's signals.
  property int selectCount: 0
  property int leaveCount: 0
  property int createCount: 0
  property string lastSelected: ""

  property bool done: false
  function fail(msg) { console.log("BENCH-FAIL " + msg); Qt.exit(1) }

  RoomListSection {
    id: roomListSection
    // bench switch: these asserts read height the frame after toggling;
    // the production animation defers that. Animation is sectionbench's job.
    animateSections: false
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom

    peerRowH: Style.space(32)
    hostHeight: benchRoot.height
    alertStackBottom: 0
    amRoomOwnerOfFn: function(roomId) { return roomId === "room-1" }
    selectRoomFn: function(roomId) { console.log("stub selectRoomFn", roomId) }
    leaveSelectedRoomFn: function() { console.log("stub leaveSelectedRoomFn") }
    onRoomSelected: function(roomId) { benchRoot.selectCount++; benchRoot.lastSelected = roomId }
    onRoomLeaveRequested: benchRoot.leaveCount++
    onRoomCreateRequested: benchRoot.createCount++
  }

  Component.onCompleted: {
    Qt.callLater(function() {
      if (benchRoot.done) return
      benchRoot.done = true

      // 1) populate: 1 room owned by myId with 1 member + 1 invite
      Lanchat.rooms = [{ roomId: "room-1", name: "testroom", owner: Lanchat.myId }]
      Lanchat.roomStates = { "room-1": { roomId: "room-1", name: "testroom", owner: Lanchat.myId, members: { "peer-9": { name: "Carol", canInvite: false } } } }
      Lanchat.selectedRoomId = "room-1"
      Qt.callLater(function() {
        // 2) room group header rendered
        var groups = []
        function collect(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c.objectName === undefined && c.toString().indexOf("RoomListSection") === -1 && c instanceof Column) groups.push(c)
            collect(c)
          }
        }
        // count room group headers via Buttons: expect the ＋ create button
        var btns = []
        function collectBtns(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof Button) btns.push(c)
            collectBtns(c)
          }
        }
        collectBtns(roomListSection)
        var createBtn = btns.find(function(b) { return b.text === "\uFF0B" })
        var leaveBtn = btns.find(function(b) { return b.text === "\u2715" })
        var collapseBtn = btns.find(function(b) { return b.text === "\u25BE" })
        if (!createBtn) return fail("create button missing; btns=" + JSON.stringify(btns.map(function(b){return b.text})))
        console.log("BENCH-OK-CREATE-BTN (rooms header visible: section height " + roomListSection.sectionHeight + ")")

        // 3) create signal via the ＋ button
        createBtn.clicked()
        if (benchRoot.createCount !== 1) return fail("roomCreateRequested count=" + benchRoot.createCount)
        console.log("BENCH-OK-CREATE-SIGNAL")

        // 4) expand, then room rows exist: leave ✕ + member collapse buttons appear
        roomListSection.expand()
        if (!roomListSection.expanded) return fail("expand() did not set expanded")
        Qt.callLater(function() {
          btns = []
          collectBtns(roomListSection)
          leaveBtn = btns.find(function(b) { return b.text === "\u2715" && b.tooltipText === "Leave this room" })
          if (!leaveBtn) return fail("leave button missing after expand")
          var canAddBtn = btns.find(function(b) { return b.text.indexOf("can add") === 0 })
          if (!canAddBtn) return fail("can-add toggle missing (owner controls)")
          console.log("BENCH-OK-ROOM-ROWS leave+canadd present")

          // 5) leave signal
          leaveBtn.clicked()
          if (benchRoot.leaveCount !== 1) return fail("roomLeaveRequested count=" + benchRoot.leaveCount)
          console.log("BENCH-OK-LEAVE-SIGNAL")

          // 6) roomSelected via the room-select MouseArea on the group header
          var ma = null
          function findMA(item) {
            for (var i = 0; i < item.children.length; i++) {
              var c = item.children[i]
              if (c instanceof MouseArea && c.anchors.fill === c.parent && !(c.tooltipText)) {
                // candidate: the group header MouseArea (parent is a Rectangle row, height == peerRowH)
                if (c.parent && Math.abs(c.parent.height - Style.space(32)) < 0.5) { ma = c; return }
              }
              if (findMA(c)) return
            }
          }
          findMA(roomListSection)
          if (!ma) return fail("room group MouseArea not found")
          ma.clicked(null)
          if (benchRoot.selectCount !== 1 || benchRoot.lastSelected !== "room-1")
            return fail("roomSelected count=" + benchRoot.selectCount + " id=" + benchRoot.lastSelected)
          console.log("BENCH-OK-ROOM-SELECTED room-1")

          // 7) sectionHeight tracks height; collapse via collapseSection()
          if (roomListSection.sectionHeight !== roomListSection.height) return fail("sectionHeight mismatch")
          var hExpanded = roomListSection.height
          if (hExpanded <= Style.space(26)) return fail("expanded height too small: " + hExpanded)

          // 8) A6: rooms body Flickable caps at the space above Settings
          // (hostHeight - alertStackBottom - header - 12sp) and is
          // interactive only when content overflows. This lives here, in
          // the DIRECT-component bench: the whole-Panel bench (sectionbench)
          // never finishes positioning the section's Repeater delegates
          // (root cause of the 816bd65-era A6 false failures — delegates
          // stack at y=0 there; same code lays out correctly here).
          var flick = null
          function findFlick(item) {
            if (flick) return
            for (var i = 0; i < item.children.length; i++) {
              var c = item.children[i]
              if (c instanceof Flickable) { flick = c; return }
              findFlick(c)
            }
          }
          findFlick(roomListSection)
          if (!flick) return fail("rooms body Flickable not found")
          var cap = benchRoot.height - 0 - Style.space(26) - Style.space(12)
          if (flick.height > cap + 0.5)
            return fail("A6: body height " + Math.round(flick.height) + " exceeds cap " + Math.round(cap))
          if (flick.height <= 0) return fail("A6: body height 0")
          // interactive iff content overflows the cap (either side fails if
          // the binding breaks in either direction)
          if (flick.interactive !== (flick.contentHeight > flick.height))
            return fail("A6: interactive=" + flick.interactive + " but contentHeight="
                        + flick.contentHeight + " vs height=" + flick.height)
          console.log("BENCH-OK-A6 flick capped: h=" + Math.round(flick.height)
                      + " contentH=" + Math.round(flick.contentHeight) + " cap=" + Math.round(cap)
                      + " interactive=" + flick.interactive)

          roomListSection.collapseSection()
          if (roomListSection.expanded) return fail("collapseSection() did not clear expanded")
          Qt.callLater(function() {
            if (roomListSection.height !== Style.space(26)) return fail("collapsed height " + roomListSection.height + " != header 26sp")
            if (roomListSection.sectionHeight !== roomListSection.height) return fail("sectionHeight mismatch after collapse")
            console.log("BENCH-OK-COLLAPSE " + roomListSection.height)

            // 9) invite row: populate an invite, expect Join + Decline ✕
            Lanchat.roomInvites = [{ roomId: "room-inv", name: "invroom",
              from: "peer-7", fromName: "Bob" }]
            Lanchat.rooms = Lanchat.rooms.concat([])
            roomListSection.expand()
            Qt.callLater(function() {
              btns = []
              collectBtns(roomListSection)
              var joinBtn = btns.find(function(b) { return b.text === "Join" })
              var declineBtn = btns.find(function(b) {
                return b.text === "\u2715" && b.tooltipText === "Decline this invite" })
              if (!joinBtn) return fail("invite Join button missing")
              if (!declineBtn) return fail("invite Decline button missing; btns="
                + JSON.stringify(btns.map(function(b){return b.text + "/" + (b.tooltipText || "")})))
              console.log("BENCH-OK-INVITE-BUTTONS")

              // 10) decline removes the row and calls roomForget
              declineBtn.clicked()
              if (Lanchat.roomInvites.length !== 0)
                return fail("decline left invite rows: " + JSON.stringify(Lanchat.roomInvites))
              // decline must have sent the daemon cmd too (stub mirrors it)
              if (Lanchat.forgetCount !== 1 || Lanchat.lastForgotten !== "room-inv")
                return fail("decline did not call roomForget(room-inv): count="
                            + Lanchat.forgetCount + " last=" + Lanchat.lastForgotten)
              console.log("BENCH-OK-DECLINE-CLEARS-ROW + roomForget sent")

              // 11) join-confirmed prune: re-add invite, then a room-state
              // snapshot that proves membership must prune it. Drive the
              // prune path directly (the room-state wire handler lives in
              // the real singleton, not this stub): same function the
              // handler calls, with the same membership evidence.
              Lanchat.roomInvites = [{ roomId: "room-inv", name: "invroom",
                from: "peer-7", fromName: "Bob" }]
              Lanchat.pruneRoomInvite("room-inv")
              if (Lanchat.roomInvites.length !== 0)
                return fail("pruneRoomInvite did not clear confirmed invite")
              // pruneRoomInvite must NOT touch other rooms' invites
              Lanchat.roomInvites = [{ roomId: "room-a", name: "a", from: "p", fromName: "A" },
                                     { roomId: "room-b", name: "b", from: "p", fromName: "B" }]
              Lanchat.pruneRoomInvite("room-a")
              if (Lanchat.roomInvites.length !== 1 || Lanchat.roomInvites[0].roomId !== "room-b")
                return fail("pruneRoomInvite removed wrong rows: " + JSON.stringify(Lanchat.roomInvites))
              console.log("BENCH-OK-JOIN-CONFIRMED-PRUNE")
              Qt.exit(0)
            })
          })
        })
      })
    })
  }
}
