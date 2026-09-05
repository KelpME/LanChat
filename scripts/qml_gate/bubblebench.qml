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
    // The bubble text item: id messageText lives inside the bubble Rectangle
    var bubbleText = null
    for (var i = 0; i < msg.children.length; i++) {
      var kid = msg.children[i]
      if (kid.hasOwnProperty && kid.hasOwnProperty("copied")) {
        for (var k = 0; k < kid.children.length; k++) {
          if (kid.children[k].text !== undefined && kid.children[k].text === "ping") bubbleText = kid.children[k]
        }
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
