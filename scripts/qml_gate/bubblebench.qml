// Minimal repro bench for the blank-bubble bug: a ListView with ONE message
// whose delegate is the real ChatMessage. Asserts (after the model populates
// one frame late):
//   1. the delegate's modelData resolved (modelData.text === "ping")
//   2. bubbleText.text === "ping" (the binding resolved to the RIGHT modelData)
//   3. bubble is sized > 0
// RED = any assertion fails or modelData TypeErrors appear after populate.
import QtQuick
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 500; height: 600

  property bool done: false
  function fail(msg) { console.log("BENCH-BUBBLE-FAIL " + msg); Qt.exit(1) }

  ListView {
    id: lv
    anchors.fill: parent
    model: []
    spacing: 4

    delegate: Column {
      id: benchDelegate
      required property var modelData
      width: lv.width

      ChatMessage {
        id: chatMsg
        modelData: benchDelegate.modelData
        maxWidth: lv.width * 0.8
        timeLabel: function(ts) { return "12:00" }
      }
    }
  }

  Component.onCompleted: Qt.callLater(populate)

  function populate() {
    lv.model = [
      { mid: "m1", outgoing: false, fromName: "Peer", ts: 0, text: "ping",
        edited: false, attachment: null, friendRequest: false, held: false }
    ]
    Qt.callLater(verify)
  }

  function verify() {
    // Locate the ChatMessage instance through the delegate
    var msg = null
    function walk(n) {
      if (!n || msg) return
      if (n.maxWidth !== undefined && n.editRequested !== undefined) { msg = n; return }
      var kids = n.children
      if (kids) for (var i = 0; i < kids.length; i++) walk(kids[i])
      var res = n.resources
      if (res) for (var j = 0; j < res.length; j++) walk(res[j])
    }
    walk(lv.contentItem)
    if (!msg) return fail("ChatMessage instance not found in ListView")
    console.log("BENCH-BUBBLE delegate modelData = " + JSON.stringify(msg.modelData))
    if (!msg.modelData || msg.modelData.mid !== "m1")
      return fail("delegate's modelData did NOT resolve (mid=" + (msg.modelData && msg.modelData.mid) + ")")
    // The bubble text item: the bubble Rectangle is the child with a
    // `bubblePaddingX` property (1.5.72 layout: the three-row Column lives
    // INSIDE the bubble); its inner Column's "ping" text carries the
    // resolved message. (copied lives on the ChatMessage root.)
    // Post-refactor: ChatMessage is a thin wrapper; the bubble Rectangle
    // (with bubblePaddingX) and its inner Column live INSIDE the shared
    // MessageBubble, reachable through the wrapper's MessageBubble child.
    var bubbleText = null
    for (var i = 0; i < msg.children.length; i++) {
      var kid = msg.children[i]
      // The held-friendRequest wrapper Column holds the MessageBubble.
      var mb = null
      function findMB(n) {
        if (!n || mb) return
        if (n.bubbleRect !== undefined) { mb = n; return }
        var kids = n.children
        if (kids) for (var q = 0; q < kids.length; q++) findMB(kids[q])
      }
      findMB(kid)
      if (!mb) continue
      var bt = mb.bubbleRect.children[0] // innerCol
      for (var r = 0; r < bt.children.length; r++) {
        if (bt.children[r].text === "ping") bubbleText = bt.children[r]
      }
    }
    if (!bubbleText) return fail("bubble Text did not resolve modelData.text (blank bubble reproduced)")
    console.log("BENCH-BUBBLE-OK text resolved: '" + bubbleText.text + "'")
    Qt.exit(0)
  }

  Timer {
    interval: 8000
    running: true
    repeat: false
    onTriggered: { console.log("BENCH-BUBBLE-FAIL failsafe-timeout"); Qt.exit(1) }
  }
}
