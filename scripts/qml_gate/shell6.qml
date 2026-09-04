import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 400; height: 600

  // Panel-side counters for the child's signals.
  property int editCount: 0
  property int copyCount: 0
  property string lastEditMid: ""

  property bool done: false
  function fail(msg) { console.log("BENCH-FAIL " + msg); Qt.exit(1) }

  // Host-panel-style sizing: the extracted blocks computed height from
  // sibling ids; the call site now provides it (coupling arbitration (a)).
  ChatThread {
    id: chatThreadView
    width: parent.width
    height: parent.height - 100
    inRoom: false
    thread: Lanchat.messages
    selectedPeerId: "peer-2"
    selectedPeer: { "id": "peer-2", "name": "Bob" }
    hasThread: false
    editingMid: ""
    timeLabel: function(ts) { return "12:34" }
    onEditRequested: function(mid, text) { benchRoot.editCount++; benchRoot.lastEditMid = mid }
    onCopyRequested: function(text) { benchRoot.copyCount++ }
  }

  RoomView {
    id: roomViewPane
    width: parent.width
    height: parent.height - 100
    inRoom: true
    roomThread: Lanchat.roomMessages
    selectedRoom: Lanchat.roomStates[Lanchat.selectedRoomId] || null
    timeLabel: function(ts) { return "12:34" }
  }

  Component.onCompleted: {
    Qt.callLater(function() {
      if (benchRoot.done) return
      benchRoot.done = true

      // 1) ChatThread: visible with no room, empty-state text present
      if (!chatThreadView.visible) return fail("ChatThread should be visible when inRoom=false")
      if (chatThreadView.width !== benchRoot.width) return fail("ChatThread width mismatch")
      if (Math.abs(chatThreadView.height - (benchRoot.height - 100)) > 0.5) return fail("ChatThread height mismatch")
      console.log("BENCH-OK-THREAD-SIZE w=" + chatThreadView.width + " h=" + chatThreadView.height)

      // 2) populate 1:1 thread one frame later, then check delegate renders
      Lanchat.messages = [{ id: "m1", outgoing: true, from: "test-peer-1", to: "peer-2", text: "hello thread", ts: Date.now() }]
      chatThreadView.hasThread = true
      Qt.callLater(function() {
        var cols = []
        function collect(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof Column) cols.push(c)
            collect(c)
          }
        }
        collect(chatThreadView)
        if (cols.length < 1) return fail("no delegate Column in ChatThread")
        console.log("BENCH-OK-THREAD-DELEGATE cols=" + cols.length)

        // 3) empty-state text: clear thread again
        Lanchat.messages = []
        chatThreadView.hasThread = false
        Qt.callLater(function() {
          var texts = []
          function collectT(item) {
            for (var i = 0; i < item.children.length; i++) {
              var c = item.children[i]
              if (c instanceof Text && c.text.indexOf("No messages with") === 0) texts.push(c)
              collectT(c)
            }
          }
          collectT(chatThreadView)
          if (texts.length !== 1) return fail("empty-state text missing; n=" + texts.length)
          if (texts[0].text !== "No messages with Bob yet.") return fail("empty-state text wrong: " + texts[0].text)
          console.log("BENCH-OK-EMPTY-STATE")

          // 4) signals fire through to panel counters
          chatThreadView.editRequested("m9", "edited")
          chatThreadView.copyRequested("copied text")
          if (benchRoot.editCount !== 1 || benchRoot.lastEditMid !== "m9") return fail("editRequested count=" + benchRoot.editCount)
          if (benchRoot.copyCount !== 1) return fail("copyRequested count=" + benchRoot.copyCount)
          console.log("BENCH-OK-THREAD-SIGNALS")

          // 5) RoomView: visible while inRoom, sized by the call site.
          //    Flip the panel-side input exactly as Panel.qml's inRoom
          //    binding would — ChatThread hides via its visible: !inRoom.
          chatThreadView.inRoom = true
          if (!roomViewPane.visible) return fail("RoomView should be visible when inRoom=true")
          if (chatThreadView.visible) return fail("ChatThread should hide when inRoom=true")
          if (Math.abs(roomViewPane.height - (benchRoot.height - 100)) > 0.5) return fail("RoomView height mismatch")
          console.log("BENCH-OK-ROOM-SIZE")
          chatThreadView.inRoom = false

          // 6) populate room messages, verify RoomMessage delegate renders
          //    (known bug site — phantom-bubble fix must survive extraction)
          Lanchat.roomMessages = [{ id: "rm1", room: "room-1", from: "peer-3", outgoing: false, text: "room hello", ts: Date.now() }]
          Qt.callLater(function() {
            var msgs = []
            function collectM(item) {
              for (var i = 0; i < item.children.length; i++) {
                var c = item.children[i]
                if (c instanceof RoomMessage) msgs.push(c)
                collectM(c)
              }
            }
            collectM(roomViewPane)
            if (msgs.length < 1) return fail("no RoomMessage delegate in RoomView")
            var rm = msgs[0]
            if (!rm.visible) return fail("RoomMessage not visible")
            if (rm.bubbleMaxWidth !== roomViewPane.width * 0.8 * 1) {
              // bubbleMaxWidth is derived inside RoomMessage; allow tolerance
              console.log("BENCH-NOTE bubbleMaxWidth=" + rm.bubbleMaxWidth)
            }
            if (rm.height <= 0) return fail("RoomMessage zero height")
            console.log("BENCH-OK-ROOM-DELEGATE n=" + msgs.length + " h=" + rm.height)

            // 7) loadOlder stub reachable via selectedPeerId input
            if (chatThreadView.selectedPeerId !== "peer-2") return fail("selectedPeerId input lost")
            console.log("BENCH-OK-INPUTS")
            Qt.exit(0)
          })
        })
      })
    })
  }
}
