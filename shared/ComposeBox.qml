import QtQuick
import QtQuick.Controls
import Quickshell.Io
import qs.Commons
// Panel's `Button` (with the `fontSize` property) resolves to the omarchy
// Ui override — the extracted file must import it itself (move artifact).
import qs.Ui

// The compose box: staged-attachments preview (header + removable list) and
// the input row (attach button + text field with the in-input alert overlay).
// Extracted verbatim from Panel.qml (zero behavior change) — the wrapper
// instance stays in Panel.qml at the same position (id: composeBox) so the
// layout contract (undoBar anchors, thread height formulas) is unchanged.
// Root keeps send(); this child emits sendRequested() (same call site the
// inline buttons used before the extraction).
Rectangle {
  id: composeBox

  // Panel-level state the bindings below read (was `root.<name>` inline).
  property int pendingCount: 0
  property real pendingHeaderH: 0
  property real pendingRowH: 0
  property int pendingMaxVisible: 0
  property real pendingListH: 0
  property var pendingAttachments: []
  property var selectedPeer: null
  property bool inRoom: false
  property var selectedRoom: null
  property bool visibleChatAlert: false
  property string selectedPeerId: ""
  property var isImagePath: null

  // Emitted on Send (header button + input onAccepted) — the panel's send()
  // runs with the exact text currently in the field.
  signal sendRequested()
  // Attach button / "Add more" — the panel owns attachAndSend() (zenity
  // picker + panel close/reopen), so the Process stays on the root.
  signal attachRequested()
  // Cancel button — clears the staged list on the panel.
  signal cancelRequested()
  // Per-row remove — the panel owns the staged list array.
  signal removeRequested(int index)
  // Typing lifecycle from the input's handlers — the panel owns
  // typingTimer and the sendTypingStopped calls.
  signal typingStarted()
  signal typingStopped()

  // Input accessors — the panel reads/clears the text and focuses the field
  // without reaching into the child's internal ids.
  readonly property alias inputText: input.text
  function focusInput() { input.forceActiveFocus() }

  width: parent.width
  // Grows to fit the staged-attachment preview (header + list)
  // above the input when files are staged.
  height: Style.space(58) + (composeBox.pendingCount > 0 ? composeBox.pendingHeaderH + composeBox.pendingListH : 0)
  color: "transparent"
  Rectangle {
    anchors.top: parent.top
    anchors.left: parent.left
    anchors.right: parent.right
    height: 1
    color: Color.popups.border
  }

  Column {
    anchors.fill: parent

    // ---- staged-attachments preview (not yet sent) ----------
    Rectangle {
      visible: composeBox.pendingCount > 0
      width: parent.width
      height: composeBox.pendingHeaderH + composeBox.pendingListH
      color: Style.normalFill
      Rectangle {
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        height: 1
        color: Color.popups.border
      }

      Column {
        anchors.fill: parent

        // Header: count + actions.
        Row {
          width: parent.width
          height: composeBox.pendingHeaderH
          anchors.leftMargin: Style.spacing.sm
          anchors.rightMargin: Style.spacing.sm
          spacing: Style.spacing.sm

          Text {
            anchors.verticalCenter: parent.verticalCenter
            text: composeBox.pendingCount + (composeBox.pendingCount === 1 ? " file ready" : " files ready")
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            font.weight: Font.DemiBold
          }

          Item { width: 1; height: 1 }

          Button {
            anchors.verticalCenter: parent.verticalCenter
            text: "\u2795 Add more"
            onClicked: composeBox.attachRequested()
          }
          Button {
            anchors.verticalCenter: parent.verticalCenter
            text: "\u2713 Send"
            onClicked: composeBox.sendRequested()
            enabled: composeBox.selectedPeer !== null || composeBox.inRoom
          }
          Button {
            anchors.verticalCenter: parent.verticalCenter
            text: "\u2715 Clear"
            onClicked: composeBox.cancelRequested()
          }
        }

        // Removable file list (scrolls if more than a few staged).
        ListView {
          width: parent.width
          height: composeBox.pendingListH
          clip: true
          model: composeBox.pendingAttachments
          interactive: composeBox.pendingCount > composeBox.pendingMaxVisible

          delegate: Item {
            required property var modelData
            required property int index
            width: parent.width
            height: composeBox.pendingRowH

            Row {
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.spacing.sm
              anchors.rightMargin: Style.spacing.sm
              spacing: Style.spacing.sm

              // Image thumbnail (only for image files).
              Rectangle {
                width: composeBox.isImagePath(modelData.path) ? Style.space(36) : Style.space(36)
                height: Style.space(32)
                radius: Style.space(4)
                color: composeBox.isImagePath(modelData.path) ? "transparent" : Style.pressedFill
                border.width: composeBox.isImagePath(modelData.path) ? 1 : 0
                border.color: Color.popups.border
                clip: true

                Image {
                  visible: composeBox.isImagePath(modelData.path)
                  anchors.fill: parent
                  source: "file://" + modelData.path
                  fillMode: Image.PreserveAspectFit
                  layer.enabled: true
                  layer.smooth: true
                }
                Text {
                  visible: !composeBox.isImagePath(modelData.path)
                  anchors.centerIn: parent
                  text: "\uF0C6"  // nf-fa-paperclip
                  color: Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }

              Text {
                width: parent.width - Style.space(36) - Style.space(56)
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.name
                color: Color.popups.text
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                elide: Text.ElideMiddle
              }

              Item { width: 1; height: 1 }

              // Remove this file from the staging list.
              Button {
                anchors.verticalCenter: parent.verticalCenter
                text: "\u2715"
                onClicked: composeBox.removeRequested(index)
              }
            }
          }
        }
      }
    }

    // Input row (attach button + text field).
    Item {
      width: parent.width
      height: Style.space(58)
      anchors.leftMargin: Style.spacing.sm
      anchors.rightMargin: Style.spacing.sm

      // Attachment button.
      Button {
        id: attachBtn
        width: Style.space(34)
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: "\uF0C6"  // nf-fa-paperclip
        onClicked: composeBox.attachRequested()
        enabled: composeBox.selectedPeer !== null || composeBox.inRoom
      }

      TextField {
        id: input
        anchors.left: attachBtn.right
        anchors.leftMargin: Style.spacing.sm
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.topMargin: Style.spacing.md
        anchors.bottomMargin: Style.spacing.md
        placeholderText: composeBox.inRoom
          ? "Message #" + (composeBox.selectedRoom ? composeBox.selectedRoom.name : "room") + "…"
          : (composeBox.selectedPeer
            ? "Message " + composeBox.selectedPeer.name + "…"
            : "Select a peer to chat")
        enabled: composeBox.selectedPeer !== null || composeBox.inRoom
        onAccepted: composeBox.sendRequested()
        onTextChanged: {
          if (composeBox.selectedPeerId && text.length > 0) {
            Lanchat.sendTyping(composeBox.selectedPeerId)
            composeBox.typingStarted()
          } else {
            if (composeBox.selectedPeerId) Lanchat.sendTypingStopped(composeBox.selectedPeerId)
            composeBox.typingStopped()
          }
        }
        onEditingFinished: {
          if (composeBox.selectedPeerId) Lanchat.sendTypingStopped(composeBox.selectedPeerId)
          composeBox.typingStopped()
        }

        // ---- in-input chat alert (warnings, save results, notices)
        // A temporary overlay INSIDE the input box: shows the
        // alert text for a few seconds (chatAlertTimer on the
        // singleton auto-clears it), then the placeholder
        // returns. Layout never changes — nothing can be pushed
        // off-screen. Warnings tint the border urgent; non-error
        // alerts (save results, notices) keep the normal border.
        Rectangle {
          id: inputAlert
          anchors.fill: parent
          visible: composeBox.visibleChatAlert
          radius: Style.cornerRadius
          color: Color.popups.background
          border.width: 1
          border.color: Lanchat.chatAlertIsError ? Color.urgent : Style.normalBorderColor

          Text {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: Style.space(10)
            anchors.rightMargin: Style.space(10)
            text: Lanchat.chatAlert
            color: Lanchat.chatAlertIsError ? Color.urgent : Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
          }

          MouseArea {
            anchors.fill: parent
            onClicked: {
              // Clicking dismisses immediately and focuses the
              // input so typing is never blocked.
              Lanchat.chatAlert = ""
              input.forceActiveFocus()
            }
          }
        }
      }
    }
  }
}
