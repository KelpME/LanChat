// RoomView: the full-width room chat pane — roomList ListView with the
// RoomMessage delegate. Shown while a room is selected (panel passes inRoom).
// Extracted from Panel.qml (step 6/8) with zero behavior change.
//
// Sizing: the original Item computed height from sibling ids (composeBox,
// threadHeader, chatAlertBar) that are file-local to Panel.qml — the Panel.qml
// call site now provides width/height (coupling arbitration option (a)).
// roomThread / selectedRoom / timeLabel are passed as typed inputs; selection
// state itself stays on the panel. Lanchat.* singleton reads are unchanged.
import QtQuick
import qs.Commons
import qs.Ui

Item {
  id: roomView

  // ---- inputs from the host panel (types verbatim from Panel.qml) ----
  property bool inRoom: false
  property var roomThread: []
  property var selectedRoom: null
  property var timeLabel: function(ts) { return "" }

  // Rooms gain hover-copy (the shared MessageBubble provides the glyph);
  // the panel wires this to its clipboard helper.
  signal copyRequested(string text)

  visible: inRoom
  width: parent.width

  ListView {
      id: roomList
      width: parent.width
      height: parent.height
      clip: true
      spacing: Style.spacing.sm
      model: roomView.roomThread

      header: Item { width: parent.width; height: Style.spacing.md }
      footer: Item { width: parent.width; height: Style.spacing.lg }
      onCountChanged: Qt.callLater(function() { positionViewAtEnd() })

      delegate: Column {
        id: roomMsgDelegate
        required property var modelData
        required property int index
        width: roomList.width
        spacing: Style.spacing.xs

        // Voice-change divider: sender name of the run above, a thin rule,
        // sender name of the run below (compared by `from`; outgoing is
        // derived per-peer so `from` is the stable identity here).
        TurnDivider {
          showDivider: roomMsgDelegate.index > 0
                       && !!roomView.roomThread[roomMsgDelegate.index - 1]
                       && roomView.roomThread[roomMsgDelegate.index - 1].from !== roomMsgDelegate.modelData.from
          topLabel: roomMsgDelegate.index > 0 && roomView.roomThread[roomMsgDelegate.index - 1]
                    ? (roomView.roomThread[roomMsgDelegate.index - 1].outgoing ? "You" : (roomView.roomThread[roomMsgDelegate.index - 1].fromName || "them"))
                    : ""
          bottomLabel: roomMsgDelegate.modelData.outgoing ? "You" : (roomMsgDelegate.modelData.fromName || "them")
        }

        RoomMessage {
          id: roomMsg
          modelData: roomMsgDelegate.modelData
          maxWidth: roomList.width * 0.8
          selectedRoom: roomView.selectedRoom
          timeLabel: roomView.timeLabel
          onCopyRequested: function(text) { roomView.copyRequested(text) }
        }
      }
    }
  }
