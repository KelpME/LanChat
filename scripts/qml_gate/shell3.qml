import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 800; height: 600

  // Panel-side counters for the child's signals.
  property int attachCount: 0
  property int sendCount: 0
  property int cancelCount: 0
  property int removeCount: 0
  property int typingStartCount: 0
  property int typingStopCount: 0

  property bool done: false
  function fail(msg) { console.log("BENCH-FAIL " + msg); Qt.exit(1) }

  ComposeBox {
    id: composeBox
    anchors.top: parent.top
    anchors.left: parent.left
    anchors.right: parent.right
    pendingCount: benchRoot.stagedCount
    pendingHeaderH: Style.space(36)
    pendingRowH: Style.space(32)
    pendingMaxVisible: 3
    pendingListH: benchRoot.stagedCount > 0 ? Style.space(32) * Math.min(benchRoot.stagedCount, 3) : 0
    pendingAttachments: benchRoot.staged
    selectedPeer: { "name": "Alice" }
    inRoom: false
    visibleChatAlert: false
    selectedPeerId: "peer-1"
    isImagePath: function(p) { return p.endsWith(".png") }

    onAttachRequested: benchRoot.attachCount++
    onSendRequested: benchRoot.sendCount++
    onCancelRequested: benchRoot.cancelCount++
    onRemoveRequested: function(index) { benchRoot.removeCount++; benchRoot.staged.splice(index, 1); benchRoot.staged = benchRoot.staged }
    onTypingStarted: benchRoot.typingStartCount++
    onTypingStopped: benchRoot.typingStopCount++
  }

  property var staged: []
  property int stagedCount: 0

  Component.onCompleted: {
    Qt.callLater(function() {
      if (benchRoot.done) return
      benchRoot.done = true

      // 1) empty: height == Style.space(58)
      var h0 = composeBox.height
      if (h0 !== Style.space(58)) return fail("empty height " + h0 + " != " + Style.space(58))
      console.log("BENCH-OK-EMPTY-HEIGHT " + h0)

      // 2) 2 staged: height == Style.space(58) + pendingHeaderH + pendingListH
      benchRoot.staged = [{ name: "a.png", path: "/tmp/a.png" }, { name: "b.zip", path: "/tmp/b.zip" }]
      benchRoot.stagedCount = 2
      Qt.callLater(function() {
        var exp = Style.space(58) + Style.space(36) + Style.space(32) * 2
        if (composeBox.height !== exp) return fail("staged height " + composeBox.height + " != " + exp)
        console.log("BENCH-OK-STAGED-HEIGHT " + composeBox.height)

        // 3) fire signals via child buttons
        // header Send button = 2nd Button in header row; Clear = 3rd.
        // Fire removeRequested through the list delegate's remove Button.
        // Simplest robust route: call the child's button handlers via findButtons.
        var btns = []
        function collect(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof Button) btns.push(c)
            collect(c)
          }
        }
        collect(composeBox)
        var texts = btns.map(function(b) { return b.text })
        console.log("DBG buttons=" + JSON.stringify(texts))
        var addMore = btns.find(function(b) { return b.text.indexOf("Add more") >= 0 })
        var sendBtn = btns.find(function(b) { return b.text.indexOf("Send") >= 0 })
        var clearBtn = btns.find(function(b) { return b.text.indexOf("Clear") >= 0 })
        var removeBtns = btns.filter(function(b) { return b.text === "\u2715" && b !== clearBtn })
        var attachBtn = btns.find(function(b) { return b.text === "\uF0C6" })
        if (!addMore || !sendBtn || !clearBtn || removeBtns.length < 2 || !attachBtn) return fail("buttons not found: " + JSON.stringify(texts))
        addMore.clicked()
        sendBtn.clicked()
        clearBtn.clicked()
        removeBtns[0].clicked()
        attachBtn.clicked()
        if (benchRoot.attachCount !== 2) return fail("attachCount=" + benchRoot.attachCount)
        if (benchRoot.sendCount !== 1) return fail("sendCount=" + benchRoot.sendCount)
        if (benchRoot.cancelCount !== 1) return fail("cancelCount=" + benchRoot.cancelCount)
        if (benchRoot.removeCount !== 1) return fail("removeCount=" + benchRoot.removeCount)
        console.log("BENCH-OK-SIGNALS attach=2 send=1 cancel=1 remove=1")

        // 4) typing signals via the input handlers
        // drive onTextChanged by writing through the internal TextField
        var tf = null
        function findTF(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof TextField) tf = c
            findTF(c)
          }
        }
        findTF(composeBox)
        if (!tf) return fail("TextField not found")
        tf.text = "hello"
        if (benchRoot.typingStartCount !== 1) return fail("typingStartCount=" + benchRoot.typingStartCount)
        tf.text = ""
        if (benchRoot.typingStopCount < 1) return fail("typingStopCount=" + benchRoot.typingStopCount)
        console.log("BENCH-OK-TYPING start=1 stop=" + benchRoot.typingStopCount)

        // 5) alias inputText + focusInput
        if (composeBox.inputText !== tf.text) return fail("alias mismatch")
        tf.text = "alias check"
        if (composeBox.inputText !== "alias check") return fail("alias did not reflect text")
        composeBox.focusInput()
        if (!tf.activeFocus) return fail("focusInput did not focus the input")
        console.log("BENCH-OK-INPUT-ACCESS")

        Qt.exit(0)
      })
    })
  }
}
