import QtQuick
import qs.Commons

// One 1:1 thread message — a thin wrapper around the shared MessageBubble.
// The bubble layout itself (Rectangle + innerCol rows, hover copy/edit
// glyphs, hug logic) lives in shared/MessageBubble.qml; this file only
// composes the 1:1 timestamp line and holds the held-friendRequest guard.
// Inputs: modelData (the message), maxWidth (0.8 * thread list width),
// editingMid (panel-level mid being edited). Signals bubble back to the
// panel, which owns the state.
Column {
  id: chatMessage

  // The message object from the thread model (same shape Panel passed before).
  required property var modelData
  // Bubble max width = the thread list's width * 0.8 (was `list.width * 0.8`).
  property real maxWidth: 400
  // Mid currently being edited (panel state, was `root.editingMid`).
  property string editingMid: ""

  // Panel-provided helpers, kept single-sourced on the root (same functions
  // the inline delegate called before the extraction).
  property var timeLabel: function(ts) { return "" }

  signal editRequested(string mid, string text)
  signal copyRequested(string text)

  width: parent.width

  readonly property string timeLine: chatMessage.timeLabel(modelData.ts)
    + (modelData.edited ? " (edited)" : "")
    + (modelData.outgoing && modelData.mid && Lanchat.readReceipts[modelData.mid] ? " ✓" : "")

  // A held handshake request shows as a banner, not a text bubble — the
  // wrapper is hidden so the content isn't leaked before the friend accepts.
  Column {
    width: parent.width
    visible: !(modelData.friendRequest && modelData.held)

    MessageBubble {
      modelData: chatMessage.modelData
      maxWidth: chatMessage.maxWidth
      editingMid: chatMessage.editingMid
      timeLine: chatMessage.timeLine
      editEnabled: true
      onEditRequested: function(mid, text) { chatMessage.editRequested(mid, text) }
      onCopyRequested: function(text) { chatMessage.copyRequested(text) }
    }
  }
}
