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
      OldChatMessage {
        id: chatMsg
        modelData: modelData
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
        cm = cm.children[0]
        if (cm.width <= 0) return fail("baseline delegate has no width")
        console.log("BENCH-OK-BASELINE delegates=" + list.count + " bubbleWidth=" + cm.width)
        Qt.exit(0)
      })
    })
  }
}
