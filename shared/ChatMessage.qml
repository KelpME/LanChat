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

  // Sender name now lives on the voice-change dividers (ChatThread); the
  // per-message time rides the bubble's top-right under the copy glyph,
  // in the same ink. (Edited marker + read ✓ keep riding the time line.)
  readonly property string timeLine: chatMessage.timeLabel(modelData.ts)
    + (modelData.edited ? " (edited)" : "")
    + (modelData.outgoing && modelData.mid && Lanchat.readReceipts[modelData.mid] ? " ✓" : "")

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
    // Height: text + vertical padding + the top clearance strip the text
    // now reserves against the corner glyphs (time sits under the copy
    // glyph, so the strip is glyph + gap + one caption line).
    height: messageText.implicitHeight + bubblePaddingY * 2 + Style.space(16)
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
      // Keep the first line clear of the corner glyphs (copy/edit/time
      // overlay the bubble's top strip).
      anchors.topMargin: Style.space(10)
    }

    // Edit button (outgoing only, on hover) — mirrors to the top-LEFT so
    // it sits beside the copy glyph; received bubbles have no edit.
    Text {
      visible: modelData.outgoing && (parent.hovered || chatMessage.editingMid === modelData.mid)
      anchors.top: parent.top
      anchors.left: parent.left
      anchors.topMargin: Style.space(5)
      anchors.leftMargin: Style.space(22)
      text: "\uF040"
      // Same contrast fix as the copy glyph: muted vanishes on the bubble
      // fill; popups.text is the message-body ink and always reads.
      color: Color.popups.text
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      opacity: 0.85
      MouseArea {
        anchors.fill: parent
        onClicked: chatMessage.editRequested(modelData.mid, modelData.text)
      }
    }

    // Copy button: mirrored to the TOP-LEFT on outgoing messages so sent
    // and received bubbles mirror each other (received keeps it top-right).
    // Revealed on hover; flashes a checkmark after copying.
    Text {
      anchors.top: parent.top
      anchors.left: modelData.outgoing ? parent.left : undefined
      anchors.right: modelData.outgoing ? undefined : parent.right
      anchors.topMargin: Style.space(5)
      anchors.leftMargin: Style.space(5)
      anchors.rightMargin: Style.space(5)
      text: parent.copied ? "\u2713" : "\uF0C5"
      // Contrast: the copy glyph sits on the bubble fill (normalFill /
      // selectedAccentFill), not the panel — Color.muted is tuned for the
      // panel background and disappears on the bubble. popups.text is the
      // same ink the message body uses, so the icon always reads; the
      // checkmark stays accent for the copied confirmation.
      color: parent.copied ? Color.accent : Color.popups.text
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      visible: parent.hovered || parent.copied
      opacity: parent.copied ? 1.0 : 0.85

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

    // Message time under the copy glyph, same ink (Color.popups.text);
    // mirrored: top-left on outgoing, top-right on received — with a
    // little gap under the glyph (topMargin 18 vs glyph 5+caption).
    Text {
      anchors.top: parent.top
      anchors.left: modelData.outgoing ? parent.left : undefined
      anchors.right: modelData.outgoing ? undefined : parent.right
      anchors.topMargin: Style.space(18)
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: Style.space(6)
      text: chatMessage.timeLine
      color: Color.popups.text
      opacity: 0.6
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
    }
  }
}
