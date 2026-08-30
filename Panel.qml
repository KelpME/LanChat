import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Ui
import "shared"

// Lanchat chat panel: a peer list on the left, the selected peer's thread and
// a compose box on the right. Hosted in a KeyboardPanel window anchored to the
// bar widget; all state comes from the shared Lanchat singleton.
Panel {
  id: root
  moduleName: "KelpME.lanchat"
  ipcTarget: "KelpME.lanchat"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null

  // The conversation currently on screen ("" = none selected).
  property string selectedPeerId: ""

  readonly property var selectedPeer: {
    var list = Lanchat.peers
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === selectedPeerId) return list[i]
    }
    return null
  }

  // Live-filtered thread for the selected peer, re-evaluated whenever the
  // shared message list is reassigned by the singleton.
  readonly property var thread: {
    var out = []
    var all = Lanchat.messages
    for (var i = 0; i < all.length; i++) {
      var m = all[i]
      var mine = m.outgoing && m.to === selectedPeerId
      var theirs = !m.outgoing && m.from === selectedPeerId
      if (mine || theirs) out.push(m)
    }
    return out
  }

  readonly property bool hasThread: thread.length > 0

  function selectPeer(id) {
    selectedPeerId = id
    list.positionViewAtEnd()
  }

  function send() {
    var text = input.text.trim()
    if (!text || !selectedPeerId) return
    Lanchat.send(selectedPeerId, text)
    input.text = ""
    list.positionViewAtEnd()
  }

  function timeLabel(ts) {
    var d = new Date(ts)
    return Qt.formatTime(d, "HH:mm")
  }

  onOpenedChanged: {
    Lanchat.panelOpen = root.opened
    if (root.opened) {
      Lanchat.clearUnread()
      if (selectedPeerId === "" && Lanchat.peers.length > 0)
        selectedPeerId = Lanchat.peers[0].id
      Qt.callLater(function() { list.positionViewAtEnd() })
    }
  }

  Component.onCompleted: Lanchat.panelOpen = false

  KeyboardPanel {
    id: win
    anchorItem: root.anchorItem
    bar: root.bar
    owner: root.hostWidget || root
    open: root.opened
    centerOnBar: true
    contentWidth: win.fittedContentWidth(Style.space(560))
    contentHeight: win.fittedContentHeight(Style.space(440))

    Rectangle {
      width: parent.width
      height: parent.height
      color: Color.popups.background

      Column {
        anchors.fill: parent

        // ---- header ------------------------------------------------------
        Rectangle {
          width: parent.width
          height: Style.space(40)
          color: "transparent"
          Rectangle {
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            height: 1
            color: Color.popups.border
          }

          Item {
            anchors.fill: parent
            anchors.leftMargin: Style.spacing.panelPadding
            anchors.rightMargin: Style.spacing.panelPadding

            Row {
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.spacing.sm

              Text {
                text: "Lanchat"
                color: Color.popups.text
                font.family: Style.font.family
                font.pixelSize: Style.font.subtitle
                font.weight: Font.Bold
              }

              Text {
                text: root.selectedPeer ? root.selectedPeer.name : ""
                color: Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                elide: Text.ElideRight
              }
            }

            Text {
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: (Lanchat.onlineCount === 1 ? "1 peer" : Lanchat.onlineCount + " peers") + " online"
              color: Lanchat.onlineCount > 0 ? Color.accent : Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }

        // ---- body: peer list + thread -------------------------------------
        Rectangle {
          width: parent.width
          height: parent.height - Style.space(40)
          color: "transparent"

          Row {
            anchors.fill: parent

            // Left: peer list
            Rectangle {
              width: Style.space(190)
              height: parent.height
              color: "transparent"

              ListView {
                id: peerList
                anchors.fill: parent
                anchors.margins: Style.spacing.sm
                clip: true
                model: Lanchat.peers
                spacing: Style.spacing.xs

                delegate: Rectangle {
                  required property var modelData
                  width: peerList.width
                  height: Style.spacing.controlHeight
                  radius: Style.cornerRadius
                  color: modelData.id === root.selectedPeerId
                    ? Style.selectedFill : "transparent"

                  MouseArea {
                    anchors.fill: parent
                    onClicked: root.selectPeer(modelData.id)
                  }

                  Rectangle {
                    width: Style.space(8)
                    height: Style.space(8)
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.sm
                    color: Color.accent
                  }

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.xl
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.sm
                    text: modelData.name
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    elide: Text.ElideRight
                  }
                }

                Text {
                  visible: Lanchat.peers.length === 0
                  anchors.centerIn: parent
                  text: "No peers online.\nThey'll appear here automatically."
                  color: Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  horizontalAlignment: Text.AlignHCenter
                }
              }
            }

            Rectangle {
              width: 1
              height: parent.height
              color: Color.popups.border
            }

            // Right: thread + compose
            Column {
              width: parent.width - Style.space(190) - 1
              height: parent.height

              ListView {
                id: list
                width: parent.width
                height: parent.height - composeBox.height
                clip: true
                spacing: Style.spacing.xs
                model: root.thread

                onCountChanged: Qt.callLater(function() { positionViewAtEnd() })

                delegate: Column {
                  required property var modelData
                  width: list.width
                  spacing: Style.spacing.xs

                  // Meta row: who + when
                  Text {
                    text: (modelData.outgoing ? "You · " : modelData.fromName + " · ") + root.timeLabel(modelData.ts)
                    color: Color.muted
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }

                  Rectangle {
                    width: Math.min(list.width * 0.78, messageText.implicitWidth + Style.space(24))
                    height: messageText.implicitHeight + Style.space(12)
                    radius: Style.cornerRadius
                    anchors.left: modelData.outgoing ? undefined : parent.left
                    anchors.right: modelData.outgoing ? parent.right : undefined
                    color: modelData.outgoing
                      ? Style.selectedAccentFill
                      : Style.normalFill

                    Text {
                      id: messageText
                      anchors.centerIn: parent
                      width: list.width * 0.78 - Style.space(24)
                      text: modelData.text
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.body
                      wrapMode: Text.Wrap
                    }
                  }
                }

                Text {
                  visible: root.selectedPeer && !root.hasThread
                  anchors.centerIn: parent
                  text: root.selectedPeer
                    ? "No messages with " + root.selectedPeer.name + " yet."
                    : ""
                  color: Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }

              // ---- compose box -------------------------------------------
              Rectangle {
                id: composeBox
                width: parent.width
                height: Style.space(52)
                color: "transparent"
                Rectangle {
                  anchors.top: parent.top
                  anchors.left: parent.left
                  anchors.right: parent.right
                  height: 1
                  color: Color.popups.border
                }

                TextField {
                  id: input
                  anchors.fill: parent
                  anchors.topMargin: Style.spacing.sm
                  anchors.bottomMargin: Style.spacing.sm
                  anchors.leftMargin: Style.spacing.panelPadding
                  anchors.rightMargin: Style.spacing.panelPadding
                  placeholderText: root.selectedPeer
                    ? "Message " + root.selectedPeer.name + "…"
                    : "Select a peer to chat"
                  enabled: root.selectedPeer !== null
                  onAccepted: root.send()
                }
              }
            }
          }
        }
      }

      // ---- status line -------------------------------------------------------
      Rectangle {
        visible: Lanchat.statusMessage !== ""
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: Style.space(24)
        color: Style.pressedFill
        Text {
          anchors.centerIn: parent
          text: Lanchat.statusMessage
          color: Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }
  }
}
