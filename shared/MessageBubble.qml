import QtQuick
import qs.Commons

// The chat bubble shared by 1:1 (ChatMessage) and rooms (RoomMessage):
// one Column inside the bubble Rectangle, two rows —
//   1. the message text
//   2. the footer row: hover buttons (copy, edit) on one edge, the
//      timestamp on the opposite edge — justified away from each other.
// The bubble sizes to its content Column; the width hug formula takes the
// MAX of the text's natural width and the footer's combined width (buttons
// + gap + timestamp), so a short message's bubble still fits both on the
// footer line. 1:1 defaults are the verbatim ChatMessage styling; rooms
// pass bubbleColorOverride / textColor / timeInk for the member-color
// bubble (bubbleColorOverride "transparent" is the sentinel meaning "use
// the 1:1 fills"). Read ✓ is NOT appended here — callers bake it into the
// timeLine string.
Item {
  id: messageBubble

  required property var modelData
  property real maxWidth: 400
  property string editingMid: ""
  property string timeLine: ""
  property bool editEnabled: false
  // Room call sites render attachments as the separate file bubble and
  // pass false so no paperclip line is composed here.
  property bool showAttachmentInline: true
  // Sentinel fill override: anything but the STRING "transparent" IS the
  // bubble fill (border rules unchanged). Must be `var`, not `color`: a
  // color-typed property coerces the sentinel string into a transparent
  // QColor before the comparison, and a color OBJECT never === the string
  // "transparent" (journal-proven trap) — the sentinel could never be
  // detected and the bubble would paint #00000000.
  property var bubbleColorOverride: "transparent"
  property color textColor: Color.popups.text
  property color timeInk: textColor
  // Room call sites hide the text bubble entirely when the message has no
  // text or carries an attachment (the file bubble renders instead).
  property bool bubbleVisible: true

  signal editRequested(string mid, string text)
  signal copyRequested(string text)

  // Read-only handles for benches and callers: the bubble Rectangle
  // (fill color, border) and the message Text (ink).
  readonly property alias bubbleRect: bubble
  readonly property alias bubbleTextItem: messageText

  width: parent ? parent.width : 0
  height: bubble.visible ? bubble.height : 0

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
    onTriggered: messageBubble.copied = false
  }

  // ---- the bubble: one Column, two rows inside ----------------------------
  Rectangle {
    id: bubble
    visible: messageBubble.bubbleVisible
    readonly property bool overrideFill: messageBubble.bubbleColorOverride !== "transparent"
    readonly property real bubbleMaxWidth: messageBubble.maxWidth
    readonly property real bubblePaddingX: Style.space(10)
    readonly property real bubblePaddingY: Style.space(6)
    readonly property real bubbleExtra: Style.space(6)
    // Gap between the footer's two justified clusters (buttons | time).
    readonly property real footerGap: Style.space(8)

    // Hug the content: bubble = max(text natural width, footer combined
    // width) + padding, capped. implicitWidth is wrap-independent, so there
    // is no cycle — short messages shrink the bubble, long ones grow to the
    // cap and wrap. The footer term (buttons implicitWidth + gap + time
    // implicitWidth) IS the Operator's min-width requirement: the bubble is
    // never narrower than what fits buttons and timestamp on one line.
    width: Math.min(bubbleMaxWidth,
                    Math.max(messageText.implicitWidth,
                             btnRow.implicitWidth + bubble.footerGap + timeText.implicitWidth)
                    + bubblePaddingX * 2 + bubbleExtra)
    height: innerCol.childrenRect.height + bubblePaddingY * 2
    radius: Math.max(Style.cornerRadius, Style.space(6))
    // Horizontal alignment via x — anchors on a Column child disable the
    // whole positioner ("Column will not function"): left on outgoing,
    // right on received.
    x: modelData.outgoing ? messageBubble.width - width : 0
    // Both sides carry the same 1px border now (Operator request: sent
    // bubbles get the outline too).
    border.width: 1
    border.color: Style.normalBorderColor
    color: overrideFill
      ? messageBubble.bubbleColorOverride
      : (modelData.outgoing ? Style.selectedAccentFill : Style.normalFill)

    Column {
      id: innerCol
      x: bubble.bubblePaddingX
      y: bubble.bubblePaddingY
      // Fills the bubble's content box; the bubble width is driven by the
      // hug formula above (text natural width vs footer, MAXed) — the text
      // never constrains itself through its own rendered width, so wrapping
      // behaves exactly like any chat app: grow to cap, then wrap. The
      // footer row's own natural width can never exceed innerCol.width
      // because the hug formula guarantees the combined footer fits.
      width: bubble.width - bubble.bubblePaddingX * 2 - bubble.bubbleExtra
      spacing: Style.space(2)

      // ---- row 1: the message text ----------------------------------------
      // Fill the inner width; the BUBBLE decides the width from the text's
      // natural implicitWidth (capped) — the text never constrains itself
      // through its own rendered width, so wrapping behaves exactly like
      // any chat app: grow to cap, then wrap.
      Text {
        id: messageText
        width: innerCol.width
        // Show the attachment (paperclip + name) so an attachment-only
        // message isn't a blank bubble; text + attachment stack.
        // Rooms compose the paperclip line inside their file bubble
        // instead, so the inline composition is switchable.
        text: messageBubble.showAttachmentInline
          ? ((modelData.attachment && modelData.attachment.name)
             ? ((modelData.text ? modelData.text + "\n" : "") + "\uD83D\uDCCE " + modelData.attachment.name)
             : modelData.text)
          : (modelData.text || "")
        color: messageBubble.textColor
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        wrapMode: Text.Wrap
      }

      // ---- row 2: the footer — buttons justified OPPOSITE the timestamp --
      // Plain Item, NOT a Row: Row is a positioner and owns its children's
      // x, so the absolute x bindings below would fight it (observed: the
      // sent timestamp shoved under the button cluster). Item + bindings =
      // authoritative edges. Buttons sit on the inside edge (left on
      // received, right on sent); the timestamp hugs the other edge. The
      // bubble's hug formula guarantees room for both.
      // opacity (not visible) on the glyphs: a Row whose children are all
      // invisible is treated as EMPTY by a positioner, which then stacks
      // the following rows over it (the 1.5.71 blank-message bug).
      // MouseAreas carry enabled guards so hidden glyphs stay unclickable.
      Item {
        id: footerRow
        width: innerCol.width
        height: Style.space(13)

        // The glyph cluster: absolute x so the timestamp can own the other
        // edge.
        Row {
          id: btnRow
          objectName: "btnRow"
          spacing: Style.space(8)
          height: Style.space(13)
          x: modelData.outgoing ? footerRow.width - btnRow.width : 0

          // Outgoing order: edit ✎ first (rightmost edge on sent bubbles),
          // then copy. The edit glyph is visible:false on received messages
          // so it takes ZERO width there (an opacity-0 glyph still reserves
          // its slot — that phantom gap next to the copy button was the
          // Operator's second report).
          Text {
            text: "\uF040"
            visible: modelData.outgoing && messageBubble.editEnabled
            color: messageBubble.textColor
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            readonly property bool showEdit: visible
              && (messageBubble.hovered || messageBubble.editingMid === modelData.mid)
            opacity: showEdit ? 0.85 : 0.0

            MouseArea {
              anchors.fill: parent
              enabled: parent.showEdit
              onClicked: messageBubble.editRequested(modelData.mid, modelData.text)
            }
          }

          Text {
            text: messageBubble.copied ? "\u2713" : "\uF0C5"
            color: messageBubble.copied ? Color.accent : messageBubble.textColor
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            opacity: (messageBubble.hovered || messageBubble.copied) ? (messageBubble.copied ? 1.0 : 0.85) : 0.0

            MouseArea {
              anchors.fill: parent
              enabled: messageBubble.hovered || messageBubble.copied
              onClicked: {
                messageBubble.copyRequested(modelData.text)
                messageBubble.copied = true
                copyReset.restart()
              }
            }
          }
        }

        // Timestamp: hugs the edge OPPOSITE the buttons.
        Text {
          id: timeText
          x: modelData.outgoing ? 0 : footerRow.width - implicitWidth
          text: messageBubble.timeLine
          color: messageBubble.timeInk
          opacity: 0.7
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
    }
  }
}
