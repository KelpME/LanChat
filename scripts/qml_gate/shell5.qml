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
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom

    peerRowH: Style.space(32)
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
          roomListSection.collapseSection()
          if (roomListSection.expanded) return fail("collapseSection() did not clear expanded")
          Qt.callLater(function() {
            if (roomListSection.height !== Style.space(26)) return fail("collapsed height " + roomListSection.height + " != header 26sp")
            if (roomListSection.sectionHeight !== roomListSection.height) return fail("sectionHeight mismatch after collapse")
            console.log("BENCH-OK-COLLAPSE " + roomListSection.height)
            Qt.exit(0)
          })
        })
      })
    })
  }
}
