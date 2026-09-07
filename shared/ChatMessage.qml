import QtQuick
import qs.Commons

// One 1:1 thread message. Inputs: modelData (the message), maxWidth (0.8 *
// thread list width), editingMid (panel-level mid being edited). Signals
// bubble back to the panel, which owns the state (same handlers as before).
//
// Layout (1.5.72): the BUBBLE is a Column with three rows INSIDE it —
//   1. hover buttons row (copy, edit), justified toward the conversation
//      INSIDE edge: left on outgoing, right on received (mirror)
//   2. the message text
//   3. the timestamp row, justified to the same inside edge
// The bubble sizes to its content Column; everything else on the root is
// passthrough. Sender name lives on the voice-change dividers; edited
// marker + read ✓ ride the timestamp row.
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
  readonly property bool hovered: msgHover.hovered
  property bool copied: false

  // Whole-bubble hover: a non-visual QObject, so it never participates in
  // the inner Column's layout.
  HoverHandler {
    id: msgHover
  }

  Timer {
    id: copyReset
    interval: 1500
    onTriggered: chatMessage.copied = false
  }

  // ---- the bubble: one Column, three rows inside -------------------------
  Rectangle {
    id: bubble
    // A held handshake request shows as a banner, not a text
    // bubble — hide the bubble so the content isn't leaked
    // before the friend accepts.
    visible: !(modelData.friendRequest && modelData.held)
    readonly property real bubbleMaxWidth: chatMessage.maxWidth
    readonly property real bubblePaddingX: Style.space(10)
    readonly property real bubblePaddingY: Style.space(6)
    readonly property real bubbleExtra: Style.space(6)

    // Hug the text: bubble = text's natural (unwrapped) width + padding,
    // capped. implicitWidth is wrap-independent, so there is no cycle —
    // short messages shrink the bubble, long ones grow to the cap and wrap.
    width: Math.min(bubbleMaxWidth,
                    messageText.implicitWidth + bubblePaddingX * 2 + bubbleExtra)
    height: innerCol.childrenRect.height + bubblePaddingY * 2
    radius: Math.max(Style.cornerRadius, Style.space(6))
    // Horizontal alignment via x — anchors on a Column child disable the
    // whole positioner ("Column will not function"): left on outgoing,
    // right on received.
    x: modelData.outgoing ? chatMessage.width - width : 0
    border.width: modelData.outgoing ? 0 : 1
    border.color: Style.normalBorderColor
    color: modelData.outgoing
      ? Style.selectedAccentFill
      : Style.normalFill

    Column {
      id: innerCol
      x: bubble.bubblePaddingX
      y: bubble.bubblePaddingY
      // Fills the bubble's content box; the bubble width is driven by
      // messageText's NATURAL width (implicitWidth — the unwrapped line),
      // so this never feeds back into the text's wrapping decision.
      width: bubble.width - bubble.bubblePaddingX * 2 - Style.space(6)
      spacing: Style.space(2)

      // ---- row 1: hover buttons, toward the inside edge ------------------
      // opacity (not visible) on the glyphs: a Row whose children are all
      // invisible is treated as EMPTY by the Column, which then stacks the
      // following rows over it (the 1.5.71 blank-message bug). MouseAreas
      // carry enabled guards so hidden glyphs stay unclickable.
      Row {
        id: btnRow
        spacing: Style.space(8)
        height: Style.space(13)
        x: modelData.outgoing ? innerCol.width - btnRow.implicitWidth : 0

        Text {
          text: chatMessage.copied ? "\u2713" : "\uF0C5"
          color: chatMessage.copied ? Color.accent : Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          opacity: (chatMessage.hovered || chatMessage.copied) ? (chatMessage.copied ? 1.0 : 0.85) : 0.0

          MouseArea {
            anchors.fill: parent
            enabled: chatMessage.hovered || chatMessage.copied
            onClicked: {
              chatMessage.copyRequested(modelData.text)
              chatMessage.copied = true
              copyReset.restart()
            }
          }
        }

        Text {
          text: "\uF040"
          color: Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          opacity: (modelData.outgoing && (chatMessage.hovered || chatMessage.editingMid === modelData.mid)) ? 0.85 : 0.0

          MouseArea {
            anchors.fill: parent
            enabled: modelData.outgoing && (chatMessage.hovered || chatMessage.editingMid === modelData.mid)
            onClicked: chatMessage.editRequested(modelData.mid, modelData.text)
          }
        }
      }

      // ---- row 2: the message text ----------------------------------------
      // Fill the inner width; the BUBBLE decides the width from the text's
      // natural implicitWidth (capped) — the text never constrains itself
      // through its own rendered width, so wrapping behaves exactly like
      // any chat app: grow to cap, then wrap.
      Text {
        id: messageText
        width: innerCol.width
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

      // ---- row 3: timestamp, same inside edge ------------------------------
      Text {
        x: modelData.outgoing ? innerCol.width - implicitWidth : 0
        text: chatMessage.timeLine
        color: Color.popups.text
        opacity: 0.7
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
    }
  }
}
