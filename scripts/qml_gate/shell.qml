import QtQuick
import "shared"

Item {
  id: benchRoot
  width: 800; height: 600
  property int signalEdits: 0
  property int signalCopies: 0
  property var lastEditArgs: []
  property var lastCopyArgs: []
  property bool done: false

  ListView {
    id: list
    width: parent.width; height: parent.height
    model: msgModel
    spacing: 4
    delegate: Column {
      required property var modelData
      width: list.width
      spacing: 4
      ChatMessage {
        id: chatMsg
        modelData: modelData
        maxWidth: list.width * 0.8
        editingMid: ""
        timeLabel: function(ts) { return Qt.formatTime(new Date(ts), "HH:mm") }
        onEditRequested: function(mid, text) { lastEditArgs = [mid, text]; signalEdits++ }
        onCopyRequested: function(text) { lastCopyArgs = [text]; signalCopies++ }
      }
    }
  }
  ListModel { id: msgModel }

  function fail(msg) { console.log("BENCH-FAIL " + msg); Qt.exit(1) }

  Component.onCompleted: {
    Qt.callLater(function() {
      if (benchRoot.done) return
      benchRoot.done = true
      msgModel.append({ mid: "m1", outgoing: true, fromName: "Me", ts: 1757000000000, text: "plain text message", edited: false })
      msgModel.append({ mid: "m2", outgoing: false, fromName: "Alice", ts: 1757000001000, text: "reply with attachment", edited: false, attachment: { name: "photo.png", size: 1234 } })
      msgModel.append({ mid: "m3", outgoing: false, fromName: "Bob", ts: 1757000002000, text: "", edited: false, attachment: { name: "file.zip", size: 999 } })
      msgModel.append({ mid: "m4", outgoing: true, fromName: "Me", ts: 1757000003000, text: "edited one", edited: true })

      Qt.callLater(function() {
        list.forceLayout()
        console.log("DBG count=" + list.count + " listH=" + list.height + " contentItem=" + (list.contentItem ? list.contentItem.children.length : "null"))
        var cm = list.itemAtIndex(0)
        if (!cm) return fail("no delegate instantiated")
        if (cm.children.length === 0 || cm.children[0].maxWidth === undefined) return fail("ChatMessage not instantiated: children=" + cm.children.length)
        cm = cm.children[0]
        if (Math.abs(cm.maxWidth - list.width * 0.8) > 0.5) return fail("maxWidth binding broken: " + cm.maxWidth)
        cm.editRequested("mX", "hello")
        cm.copyRequested("cliptext")
        if (signalEdits !== 1 || lastEditArgs[0] !== "mX" || lastEditArgs[1] !== "hello") return fail("edit signal wiring")
        if (signalCopies !== 1 || lastCopyArgs[0] !== "cliptext") return fail("copy signal wiring")
        console.log("BENCH-OK delegates=" + list.count + " edits=" + signalEdits + " copies=" + signalCopies)
        Qt.exit(0)
      })
    })
  }
}
