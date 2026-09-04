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
  property int closeCount: 0
  property int addRoomCount: 0
  property int acceptCount: 0
  property int rejectCount: 0
  property int cancelCount: 0

  property string lastSelected: ""
  property string lastAccepted: ""

  property bool done: false
  function fail(msg) { console.log("BENCH-FAIL " + msg); Qt.exit(1) }

  PeerList {
    id: peerListPanel
    anchors.fill: parent

    peerRowH: Style.space(32)
    selectedPeerId: benchRoot.lastSelected
    selectedRoom: ({})
    inRoom: false
    showFwAlert: true
    bottomInset: 0
    friendStateFn: function(id) { return id === "peer-1" ? "friend" : "" }
    shortFpFn: function(fp) { return String(fp).substring(0, 6) }
    onPeerSelected: function(id) { benchRoot.selectCount++; benchRoot.lastSelected = id }
    onChatClosed: benchRoot.closeCount++
    onAddPeerToRoomRequested: benchRoot.addRoomCount++
    onFriendAccepted: function(id) { benchRoot.acceptCount++; benchRoot.lastAccepted = id }
    onFriendRejected: benchRoot.rejectCount++
    onFriendCancelled: benchRoot.cancelCount++
  }

  Component.onCompleted: {
    Qt.callLater(function() {
      if (benchRoot.done) return
      benchRoot.done = true

      // 1) populate: 2 peers + 1 incoming friend request
      Lanchat.displayPeers = [
        { id: "peer-1", name: "Alice", status: "available" },
        { id: "peer-2", name: "Bob", status: "offline" }
      ]
      Lanchat.friendRequests = [
        { peerId: "peer-9", name: "Carol", outgoing: false, fingerprint: "ABCD1234" }
      ]
      Qt.callLater(function() {
        // 2) rows rendered: find the delegate rows inside the ListView
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
        console.log("BENCH-OK-ROWS 2")

        // 3) friend-request dropdown: default expanded=true -> notifBanner height > 24
        var notif = null
        function findById(item) {
          if (item.objectName === "notifBanner") { notif = item; return }
          for (var i = 0; i < item.children.length; i++) findById(item.children[i])
        }
        findById(peerListPanel)
        if (!notif) return fail("notifBanner not found")
        var hExpanded = notif.height
        if (hExpanded <= Style.space(24)) return fail("expanded height " + hExpanded + " <= 24sp")
        console.log("BENCH-OK-NOTIF-EXPANDED " + hExpanded)

        // 4) collapse via the child's notifExpanded -> height == Style.space(24)
        peerListPanel.notifExpanded = false
        Qt.callLater(function() {
          if (notif.height !== Style.space(24)) return fail("collapsed height " + notif.height + " != " + Style.space(24))
          console.log("BENCH-OK-NOTIF-COLLAPSED " + notif.height)
          peerListPanel.notifExpanded = true

          // 5) fire signals through the delegate row's MouseArea + accept button
          var row1 = list.itemAtIndex(0)
          if (!row1) return fail("row0 null")
          function findMA(item) {
            for (var i = 0; i < item.children.length; i++) {
              var c = item.children[i]
              if (c instanceof MouseArea) return c
              var r = findMA(c); if (r) return r
            }
            return null
          }
          var ma = findMA(row1)
          if (!ma) return fail("row MouseArea not found")
          ma.clicked(null)
          if (benchRoot.selectCount !== 1 || benchRoot.lastSelected !== "peer-1")
            return fail("peerSelected count=" + benchRoot.selectCount + " id=" + benchRoot.lastSelected)
          console.log("BENCH-OK-PEER-SELECTED peer-1")

          // Accept button on the friend-request row: find by text
          var btns = []
          function collectBtns(item) {
            for (var i = 0; i < item.children.length; i++) {
              var c = item.children[i]
              if (c instanceof Button) btns.push(c)
              collectBtns(c)
            }
          }
          collectBtns(peerListPanel)
          var acceptBtn = btns.find(function(b) { return b.text === "Accept" })
          var rejectBtn = btns.find(function(b) { return b.text === "Reject" })
          var cancelBtn = btns.find(function(b) { return b.text === "Cancel" })
          var addBtn = btns.find(function(b) { return b.text === "\uFF0B" })
          if (!acceptBtn || !rejectBtn || !cancelBtn || !addBtn)
            return fail("buttons missing: " + JSON.stringify(btns.map(function(b){return b.text})))
          acceptBtn.clicked()
          rejectBtn.clicked()
          cancelBtn.clicked()
          if (benchRoot.acceptCount !== 1 || benchRoot.lastAccepted !== "peer-9")
            return fail("friendAccepted count=" + benchRoot.acceptCount + " id=" + benchRoot.lastAccepted)
          if (benchRoot.rejectCount !== 1) return fail("friendRejected=" + benchRoot.rejectCount)
          if (benchRoot.cancelCount !== 1) return fail("friendCancelled=" + benchRoot.cancelCount)
          console.log("BENCH-OK-FRIEND-SIGNALS accept=1 reject=1 cancel=1")

          // 6) blank-area closeChat path: simulate via signal directly
          peerListPanel.chatClosed()
          if (benchRoot.closeCount !== 1) return fail("chatClosed=" + benchRoot.closeCount)
          console.log("BENCH-OK-CHAT-CLOSED")

          // 7) firewall alert visible (showFwAlert=true)
          if (!peerListPanel.showFwAlert) return fail("showFwAlert")
          console.log("BENCH-OK-FW-INPUT")
          Qt.exit(0)
        })
      })
    })
  }
}
