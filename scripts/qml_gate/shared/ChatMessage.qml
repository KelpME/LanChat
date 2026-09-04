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

  // Meta row: who + when (+ edited marker + read state)
  Text {
    anchors.left: modelData.outgoing ? undefined : parent.left
    anchors.right: modelData.outgoing ? parent.right : undefined
    text: (modelData.outgoing ? "You · " : modelData.fromName + " · ") + chatMessage.timeLabel(modelData.ts)
      + (modelData.edited ? " (edited)" : "")
      + (modelData.outgoing && modelData.mid && Lanchat.readReceipts[modelData.mid] ? " · ✓" : "")
    color: Color.muted
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }

  // Message bubble. The text anchors to fill the bubble with a
  // set padding; the bubble grows with the text (no circular
  // width dependency that used to clip long messages).
  Rectangle {
    id: bubble
    // A held handshake request shows as a banner, not a text
    // bubble — hide the bubble so the content isn't leaked
    // before the friend accepts.
    visible: !(modelData.friendRequest && modelData.held)
    readonly property real bubbleMaxWidth: chatMessage.maxWidth
    readonly property real bubblePaddingX: Style.space(14)
    readonly property real bubblePaddingY: Style.space(9)
    readonly property bool hovered: bubbleMouse.containsMouse
    property bool copied: false

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

    // Edit button (outgoing messages only), shown on hover.
    Text {
      visible: modelData.outgoing && (parent.hovered || chatMessage.editingMid === modelData.mid)
      anchors.top: parent.top
      anchors.right: parent.right
      anchors.topMargin: Style.space(5)
      anchors.rightMargin: Style.space(22)
      text: "\uF040"
      color: Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      opacity: 0.8
      MouseArea {
        anchors.fill: parent
        onClicked: chatMessage.editRequested(modelData.mid, modelData.text)
      }
    }

    // Copy button: small icon in the top-right corner,
    // revealed on hover. Flashes a checkmark after copying.
    Text {
      anchors.top: parent.top
      anchors.right: parent.right
      anchors.topMargin: Style.space(5)
      anchors.rightMargin: Style.space(5)
      text: parent.copied ? "\u2713" : "\uF0C5"
      color: parent.copied ? Color.accent : Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      visible: parent.hovered || parent.copied
      opacity: parent.copied ? 1.0 : 0.8

      MouseArea {
        anchors.fill: parent
        onClicked: {
          chatMessage.copyRequested(modelData.text)
          bubble.copied = true
          copyReset.restart()
        }
      }
    }

    Timer {
      id: copyReset
      interval: 1500
      onTriggered: bubble.copied = false
    }
  }
}
