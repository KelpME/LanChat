import QtQuick
import QtQuick.Controls
import qs.Commons
// Panel's `Button` (with the `fontSize` property) resolves to the omarchy
// Ui override — the extracted file must import it itself (move artifact).
import qs.Ui

// One room message: meta row (sender + time, member color tint), the
// member-tinted text bubble with luminance-derived ink, and the room-file
// bubble with per-member status chips and the Save/befriend row. Extracted
// verbatim from Panel.qml's roomList delegate (zero behavior change) — the
// delegate wrapper stays in Panel.qml so the ListView contract (required
// modelData, roomList.width) is unchanged. Inputs: modelData (the message),
// maxWidth (0.8 * room list width), selectedRoom (panel-level room state).
// The panel-provided timeLabel helper is passed in (same function the inline
// delegate called before the extraction).
Column {
  id: roomMessage

  // The message object from the room model (same shape Panel passed before).
  required property var modelData
  // Bubble max width = the room list's width * 0.8 (was `roomList.width * 0.8`).
  property real maxWidth: 400
  // The room currently open (panel state, was `root.selectedRoom`).
  property var selectedRoom: null

  // Panel-provided helpers, kept single-sourced on the root (same functions
  // the inline delegate called before the extraction).
  property var timeLabel: function(ts) { return "" }

  width: maxWidth / 0.8
  spacing: Style.spacing.xs

  // Meta row: sender + time, tinted with the sender's
  // member color when the room's colors are enabled.
  Text {
    anchors.left: modelData.outgoing ? undefined : parent.left
    anchors.right: modelData.outgoing ? parent.right : undefined
    text: (modelData.outgoing ? "You · " : modelData.fromName + " · ")
          + roomMessage.timeLabel(modelData.ts)
    color: {
      if (!roomMessage.selectedRoom || !roomMessage.selectedRoom.colorsEnabled) return Color.muted
      var mem = (roomMessage.selectedRoom.members || {})[modelData.from]
      var col = Lanchat.roomMemberColor(mem)
      return col !== "" ? col : Color.muted
    }
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }

  // Text bubble: member color tints the bubble; the INK is
  // derived from the bubble background's luminance so text
  // always passes contrast (the approved mechanism — a
  // low-contrast color choice yields adapted text, never an
  // excluded swatch). Same function local and remote.
  Rectangle {
    readonly property color memberTint: {
      if (!roomMessage.selectedRoom || !roomMessage.selectedRoom.colorsEnabled) return "transparent"
      var mem = (roomMessage.selectedRoom.members || {})[modelData.from]
      var col = Lanchat.roomMemberColor(mem)
      return col !== "" ? col : "transparent"
    }
    readonly property color bubbleBase: modelData.outgoing
      ? Style.selectedAccentFill : Style.normalFill
    // Composite the member tint over the base bubble fill
    // at 25% (low-alpha tint, so the derived ink always
    // clears the contrast ratio).
    readonly property color bubbleColor: {
      var t = memberTint
      if (t === "transparent") return bubbleBase
      return Qt.rgba(t.r * 0.25 + bubbleBase.r * 0.75,
                     t.g * 0.25 + bubbleBase.g * 0.75,
                     t.b * 0.25 + bubbleBase.b * 0.75,
                     Math.max(bubbleBase.a, 0.85))
    }
    // Relative luminance (sRGB-linearized) of the bubble
    // fill; ink flips dark/light at L 0.35.
    function rlin(c) {
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
    }
    readonly property real lum: 0.2126 * rlin(bubbleColor.r)
      + 0.7152 * rlin(bubbleColor.g) + 0.0722 * rlin(bubbleColor.b)
    readonly property color ink: lum > 0.35 ? Color.background : Color.popups.text

    visible: !!(modelData.text !== "") && !(modelData.attachment && modelData.attachment.name)
    width: Math.min(roomMessage.maxWidth,
                    roomMsgText.implicitWidth + Style.space(28) + Style.space(20))
    height: roomMsgText.implicitHeight + Style.space(18)
    radius: Math.max(Style.cornerRadius, Style.space(6))
    anchors.left: modelData.outgoing ? undefined : parent.left
    anchors.right: modelData.outgoing ? parent.right : undefined
    border.width: modelData.outgoing ? 0 : 1
    border.color: Style.normalBorderColor
    color: bubbleColor

    Text {
      id: roomMsgText
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(14)
      anchors.rightMargin: Style.space(14)
      text: modelData.text
      color: parent.ink
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      wrapMode: Text.Wrap
    }
  }

  // Room-file bubble: metadata + Save when the sender↔me
  // friend link exists; the befriend notice when it doesn't
  // (the alert re-evaluates on friend events — Save
  // reappears without a manual re-request).
  Rectangle {
    id: fileBubbleRect
    // !! coerces to a real bool: `undefined && x` would fail
    // the bool assignment (journal: "Unable to assign
    // [undefined] to bool") and leave visible at its
    // default TRUE — rendering a phantom file bubble on
    // every plain room text message.
    visible: !!(modelData.attachment && modelData.attachment.name)
    readonly property var att: modelData.attachment || {}
    readonly property bool senderIsFriend:
      Lanchat.isConfirmedFriend(modelData.from)
    readonly property bool fileDownloading:
      Lanchat.dlActive && Lanchat.dlFileId === att.fileId
    readonly property var statusMap:
      Lanchat.roomFileStatuses[Lanchat.selectedRoomId + "|" + (modelData.mid || "")] || {}
    function rlin2(c) {
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
    }
    readonly property color fileInk: {
      var c = Style.normalFill
      if (!modelData.outgoing && roomMessage.selectedRoom && roomMessage.selectedRoom.colorsEnabled) {
        var mem = (roomMessage.selectedRoom.members || {})[modelData.from]
        var col = Lanchat.roomMemberColor(mem)
        if (col !== "") {
          c = Qt.rgba(col.r * 0.25 + c.r * 0.75,
                      col.g * 0.25 + c.g * 0.75,
                      col.b * 0.25 + c.b * 0.75, 1)
        }
      }
      var lum = 0.2126 * rlin2(c.r) + 0.7152 * rlin2(c.g) + 0.0722 * rlin2(c.b)
      return lum > 0.35 ? Color.background : Color.popups.text
    }

    width: Math.min(roomMessage.maxWidth, Style.space(340))
    height: fileCol.implicitHeight + Style.space(20)
    radius: Math.max(Style.cornerRadius, Style.space(6))
    anchors.left: modelData.outgoing ? undefined : parent.left
    anchors.right: modelData.outgoing ? parent.right : undefined
    border.width: modelData.outgoing ? 0 : 1
    border.color: Style.normalBorderColor
    color: Style.normalFill

    Column {
      id: fileCol
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.topMargin: Style.space(10)
      anchors.leftMargin: Style.space(14)
      anchors.rightMargin: Style.space(14)
      spacing: Style.space(4)

      Text {
        width: parent.width
        text: "\uD83D\uDCCE " + (parent.parent.att.name || "file")
        color: parent.parent.fileInk
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        elide: Text.ElideMiddle
      }
      // Caption: the sender's typed text rides the file
      // message (one bubble), shown only when present.
      Text {
        visible: (parent.parent.parent.modelData.text || "") !== ""
        width: parent.width
        text: parent.parent.parent.modelData.text
        color: parent.parent.fileInk
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        wrapMode: Text.Wrap
      }
      Text {
        width: parent.width
        text: (parent.parent.att.size >= 1024
          ? Math.round(parent.parent.att.size / 1024) + " KB"
          : (parent.parent.att.size || 0) + " B")
        color: parent.parent.fileInk
        opacity: 0.8
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }

      // Sender-side per-member delivery chips (daemon-driven
      // roomFileStatus reports; "…" for members with no
      // report yet — surfaced honestly, never client-guessed).
      Flow {
        visible: modelData.outgoing && roomMessage.selectedRoom
        width: parent.width
        spacing: Style.space(4)

        Repeater {
          model: roomMessage.selectedRoom ? Object.keys(roomMessage.selectedRoom.members || {}) : []
          delegate: Rectangle {
            id: statusChip
            required property var modelData
            readonly property var st:
              (fileBubbleRef.statusMap || {})[modelData] || null
            width: roomStatusText.implicitWidth + Style.space(10)
            height: Style.space(16)
            radius: height / 2
            color: st && st.status === "saved" ? Color.accent : Style.pressedFill
            visible: modelData !== Lanchat.myId
            Text {
              id: roomStatusText
              anchors.centerIn: parent
              text: statusChip.st
                ? (statusChip.st.status === "saved" ? "✓ " : "! ")
                  + (statusChip.st.name || statusChip.modelData.slice(0, 8))
                : "… " + statusChip.modelData.slice(0, 8)
              color: statusChip.st && statusChip.st.status === "saved"
                ? Color.background : Color.popups.text
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }
      }

      // Save / notice row (non-outgoing only).
      Row {
        visible: !modelData.outgoing
        spacing: Style.space(6)

        Button {
          visible: fileBubbleRef.senderIsFriend
          text: fileBubbleRef.fileDownloading ? "Saving…" : "Save"
          enabled: !fileBubbleRef.fileDownloading
          fontSize: Style.font.caption
          onClicked: Lanchat.acceptRoomAttachment(modelData.from,
            fileBubbleRef.att.fileId, fileBubbleRef.att.name,
            modelData.mid, fileBubbleRef.att.sha256 || "",
            Lanchat.selectedRoomId)
        }
        Text {
          visible: !fileBubbleRef.senderIsFriend
          text: "⚠ Befriend " + (modelData.fromName || "the sender")
                + " to accept this file"
          color: Color.urgent
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          wrapMode: Text.WrapAtWordBoundaryOrAnywhere
        }
      }
    }
    // Reference back to the bubble's properties from the
    // delegates (fileBubbleRef = the Rectangle above). Direct
    // id reference — the old `fileCol.parent` indirection
    // threw "fileBubbleRef is not defined" at runtime.
    readonly property var fileBubbleRef: fileBubbleRect
  }
}
