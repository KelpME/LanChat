// Unread-indicator bench (visual): background tint on peer rows and room
// group headers when Lanchat.unreadByPeer / unreadByRoom carry an entry.
//   U1  read + unselected peer row paints transparent
//   U2  unread + unselected peer row paints Style.selectedAccentFill
//   U3  selecting the unread row paints Style.selectedFill (selection wins)
//   U4  deselect restores the unread tint
//   U5  clearing the map restores transparent
//   U6  unread room header: accent tint -> selectedFill when selected ->
//       transparent when cleared
import QtQuick
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 400; height: 600

  property bool done: false
  property string lastSelected: ""
  function fail(msg) { console.log("BENCH-UNREAD-FAIL " + msg); Qt.exit(1) }

  PeerList {
    id: peerListPanel
    anchors.top: parent.top
    anchors.left: parent.left
    anchors.right: parent.right
    height: 300

    peerRowH: Style.space(32)
    selectedPeerId: benchRoot.lastSelected
    selectedRoom: ({})
    inRoom: false
    showFwAlert: false
    friendStateFn: function(id) { return "friend" }
    shortFpFn: function(fp) { return String(fp).substring(0, 6) }
  }

  RoomListSection {
    id: roomListSection
    animateSections: false
    anchors.bottom: parent.bottom
    anchors.left: parent.left
    anchors.right: parent.right
    peerRowH: Style.space(32)
    hostHeight: 0
    alertStackBottom: 0
    expanded: true
  }

  Component.onCompleted: {
    Qt.callLater(function() {
      if (benchRoot.done) return
      benchRoot.done = true

      Lanchat.displayPeers = [
        { id: "peer-1", name: "Alice", status: "available" },
        { id: "peer-2", name: "Bob", status: "available" }
      ]
      Lanchat.rooms = [{ roomId: "room-1", name: "testroom", owner: Lanchat.myId }]
      Lanchat.roomStates = { "room-1": { roomId: "room-1", name: "testroom",
        owner: Lanchat.myId, members: { "peer-9": { name: "Carol", canInvite: false } } } }
      Lanchat.selectedRoomId = ""
      Lanchat.unreadByPeer = { "peer-2": 2 }
      Lanchat.unreadByRoom = { "room-1": 1 }

      Qt.callLater(function() {
        var list = null
        function findLV(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof ListView) list = c
            findLV(c)
          }
        }
        findLV(peerListPanel)
        if (!list) return fail("ListView not found")
        if (list.count !== 2) return fail("rows=" + list.count + " != 2")
        var row1 = list.itemAtIndex(0)
        var row2 = list.itemAtIndex(1)
        if (!row1 || !row2) return fail("rows null")

        if (!Qt.colorEqual(row1.color, "transparent"))
          return fail("U1 read row not transparent: " + row1.color)
        console.log("BENCH-UNREAD-OK-U1 peer-1 transparent")
        if (!Qt.colorEqual(row2.color, Style.selectedAccentFill))
          return fail("U2 unread row not accent tint: " + row2.color)
        console.log("BENCH-UNREAD-OK-U2 peer-2 accent tint")

        benchRoot.lastSelected = "peer-2"
        Qt.callLater(function() {
          if (!Qt.colorEqual(row2.color, Style.selectedFill))
            return fail("U3 selected row not selectedFill: " + row2.color)
          console.log("BENCH-UNREAD-OK-U3 selection wins")
          benchRoot.lastSelected = ""
          Qt.callLater(function() {
            if (!Qt.colorEqual(row2.color, Style.selectedAccentFill))
              return fail("U4 deselect did not restore tint: " + row2.color)
            console.log("BENCH-UNREAD-OK-U4 tint restored")
            Lanchat.unreadByPeer = {}
            Qt.callLater(function() {
              if (!Qt.colorEqual(row2.color, "transparent"))
                return fail("U5 cleared map still tinted: " + row2.color)
              console.log("BENCH-UNREAD-OK-U5 cleared")

              var hdr = null
              function findName(item) {
                if (hdr) return
                if (item.objectName === "roomGroupHeader") { hdr = item; return }
                for (var i = 0; i < item.children.length; i++) findName(item.children[i])
              }
              findName(roomListSection)
              if (!hdr) return fail("roomGroupHeader not found")
              if (!Qt.colorEqual(hdr.color, Style.selectedAccentFill))
                return fail("U6a room header not tinted: " + hdr.color)
              console.log("BENCH-UNREAD-OK-U6a room tint")
              Lanchat.selectedRoomId = "room-1"
              Qt.callLater(function() {
                if (!Qt.colorEqual(hdr.color, Style.selectedFill))
                  return fail("U6b room header not selectedFill: " + hdr.color)
                console.log("BENCH-UNREAD-OK-U6b room selected")
                Lanchat.selectedRoomId = ""
                Lanchat.unreadByRoom = {}
                Qt.callLater(function() {
                  if (!Qt.colorEqual(hdr.color, "transparent"))
                    return fail("U6c room header not cleared: " + hdr.color)
                  console.log("BENCH-UNREAD-OK-U6c room cleared")
                  Qt.exit(0)
                })
              })
            })
          })
        })
      })
    })
  }
}
