import QtQuick
import qs.Commons

// One 1:1 thread message: meta row (who + when + edited + read state) and the
// bubble with hover edit/copy buttons. Extracted verbatim from Panel.qml's
// thread delegate (zero behavior change) — the delegate wrapper stays in
// Panel.qml so the ListView contract (required modelData, list.width) is
// unchanged. Inputs: modelData (the message), maxWidth (0.8 * thread list
// width), editingMid (panel-level mid being edited). Signals bubble back to
// the panel, which owns the state (same handlers as before).
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

  width: maxWidth / 0.8
  spacing: Style.spacing.xs

  // Layout (1.5.70): ONE column, three rows —
  //   1. hover buttons row (copy, edit), justified toward the conversation
  //      INSIDE edge: left on outgoing, right on received (mirror)
  //   2. the bubble (message contents)
  //   3. the timestamp row, justified to the same inside edge
  // No overlays — nothing floats over the bubble, so no crowding or
  // contrast fights with the fill. Sender name lives on the dividers;
  // edited marker + read ✓ ride the timestamp row.
  readonly property string timeLine: chatMessage.timeLabel(modelData.ts)
    + (modelData.edited ? " (edited)" : "")
    + (modelData.outgoing && modelData.mid && Lanchat.readReceipts[modelData.mid] ? " ✓" : "")
  // Hover state lives on the root now (covers the buttons row too).
  readonly property bool hovered: msgHover.containsMouse
  property bool copied: false

  // Whole-delegate hover probe (buttons only, NoButton — never steals
  // clicks from the bubble or its children).
  MouseArea {
    id: msgHover
    anchors.fill: parent
    hoverEnabled: true
    acceptedButtons: Qt.NoButton
  }

  // ---- row 1: buttons, justified toward the inside edge -----------------
  // Fixed row height so showing/hiding the glyphs never shifts layout.
  Row {
    spacing: Style.space(10)
    height: Style.space(14)
    anchors.left: modelData.outgoing ? parent.left : undefined
    anchors.right: modelData.outgoing ? undefined : parent.right

    // Copy (both voices); flashes a checkmark after copying.
    Text {
      text: chatMessage.copied ? "\u2713" : "\uF0C5"
      color: chatMessage.copied ? Color.accent : Color.popups.text
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      visible: chatMessage.hovered || chatMessage.copied
      opacity: chatMessage.copied ? 1.0 : 0.85

      MouseArea {
        anchors.fill: parent
        onClicked: {
          chatMessage.copyRequested(modelData.text)
          chatMessage.copied = true
          copyReset.restart()
        }
      }
    }

    // Edit (outgoing only, on hover).
    Text {
      visible: modelData.outgoing && (chatMessage.hovered || chatMessage.editingMid === modelData.mid)
      text: "\uF040"
      color: Color.popups.text
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      opacity: 0.85

      MouseArea {
        anchors.fill: parent
        onClicked: chatMessage.editRequested(modelData.mid, modelData.text)
      }
    }
  }

  Timer {
    id: copyReset
    interval: 1500
    onTriggered: chatMessage.copied = false
  }

  // ---- row 2: the bubble (message contents) -----------------------------
  Rectangle {
    id: bubble
    // A held handshake request shows as a banner, not a text
    // bubble — hide the bubble so the content isn't leaked
    // before the friend accepts.
    visible: !(modelData.friendRequest && modelData.held)
    readonly property real bubbleMaxWidth: chatMessage.maxWidth
    readonly property real bubblePaddingX: Style.space(14)
    readonly property real bubblePaddingY: Style.space(9)

    width: Math.min(bubbleMaxWidth, messageText.implicitWidth + bubblePaddingX * 2 + Style.space(20))
    height: messageText.implicitHeight + bubblePaddingY * 2
    radius: Math.max(Style.cornerRadius, Style.space(6))
    anchors.left: modelData.outgoing ? undefined : parent.left
    anchors.right: modelData.outgoing ? parent.right : undefined
    border.width: modelData.outgoing ? 0 : 1
    border.color: Style.normalBorderColor
    color: modelData.outgoing
      ? Style.selectedAccentFill
      : Style.normalFill

    MouseArea {
      id: bubbleMouse
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.NoButton
    }

    Text {
      id: messageText
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: parent.bubblePaddingX
      anchors.rightMargin: parent.bubblePaddingX + Style.space(12)
      // Show the attachment (paperclip + name) so an attachment-only
      // message isn't a blank bubble; text + attachment stack.
      text: (modelData.attachment && modelData.attachment.name)
        ? ((modelData.text ? modelData.text + "\n" : "") + "\uD83D\uDCCE " + modelData.attachment.name)
        : modelData.text
      color: Color.popups.text
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      wrapMode: Text.Wrap
    }
  }

  // ---- row 3: timestamp, justified to the same inside edge --------------
  Text {
    anchors.left: modelData.outgoing ? parent.left : undefined
    anchors.right: modelData.outgoing ? undefined : parent.right
    text: chatMessage.timeLine
    color: Color.popups.text
    opacity: 0.7
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }
}
