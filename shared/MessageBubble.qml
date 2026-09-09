import QtQuick
import qs.Commons

// The chat bubble shared by 1:1 (ChatMessage) and rooms (RoomMessage):
// one Column inside the bubble Rectangle, three rows —
//   1. hover buttons row (copy, edit), justified toward the conversation
//      INSIDE edge: left on outgoing, right on received (mirror)
//   2. the message text
//   3. the timestamp row, justified to the same inside edge
// The bubble sizes to its content Column. 1:1 defaults are the verbatim
// ChatMessage styling; rooms pass bubbleColorOverride / textColor / timeInk
// for the member-color bubble (bubbleColorOverride "transparent" is the
// sentinel meaning "use the 1:1 fills"). Read ✓ is NOT appended here —
// callers bake it into the timeLine string.
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
  // bubble fill (border.width keeps the outgoing rule). Must be `var`, not
  // `color`: a color-typed property coerces the sentinel string into a
  // transparent QColor before the comparison, and a color OBJECT never ===
  // the string "transparent" (journal-proven trap) — the sentinel could
  // never be detected and the bubble would paint #00000000.
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

  // ---- the bubble: one Column, three rows inside -------------------------
  Rectangle {
    id: bubble
    visible: messageBubble.bubbleVisible
    readonly property bool overrideFill: messageBubble.bubbleColorOverride !== "transparent"
    readonly property real bubbleMaxWidth: messageBubble.maxWidth
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
    x: modelData.outgoing ? messageBubble.width - width : 0
    border.width: modelData.outgoing ? 0 : 1
    border.color: Style.normalBorderColor
    color: overrideFill
      ? messageBubble.bubbleColorOverride
      : (modelData.outgoing ? Style.selectedAccentFill : Style.normalFill)

    Column {
      id: innerCol
      x: bubble.bubblePaddingX
      y: bubble.bubblePaddingY
      // Fills the bubble's content box; the bubble width is driven by
      // messageText's NATURAL width (implicitWidth — the unwrapped line),
      // so this never feeds back into the text's wrapping decision.
      // Max() with the overlay rows' natural widths: for very short
      // messages ("ok") the buttons/time rows are WIDER than the text —
      // without this they'd be x-negative and clipped by the bubble.
      width: Math.max(bubble.width - bubble.bubblePaddingX * 2 - bubble.bubbleExtra,
                      btnRow.implicitWidth,
                      timeText.implicitWidth)
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

        Text {
          text: "\uF040"
          color: messageBubble.textColor
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          readonly property bool showEdit: messageBubble.editEnabled
            && modelData.outgoing
            && (messageBubble.hovered || messageBubble.editingMid === modelData.mid)
          opacity: showEdit ? 0.85 : 0.0

          MouseArea {
            anchors.fill: parent
            enabled: parent.showEdit
            onClicked: messageBubble.editRequested(modelData.mid, modelData.text)
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

      // ---- row 3: timestamp, same inside edge ------------------------------
      // Outgoing fill is a translucent accent wash (alpha 0.18) — a dimmed
      // light text on it is invisible against a light wallpaper. The room
      // call site passes its luminance-derived ink via timeInk; 1:1 keeps
      // the body ink (timeInk defaults to textColor) at full opacity on
      // outgoing; received side keeps 0.7.
      Text {
        id: timeText
        x: modelData.outgoing ? innerCol.width - implicitWidth : 0
        text: messageBubble.timeLine
        color: messageBubble.timeInk
        opacity: modelData.outgoing ? 1.0 : 0.7
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
    }
  }
}
