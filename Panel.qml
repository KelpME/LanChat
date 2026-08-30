import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Ui
import "shared"

// Lanchat chat panel: a peer list on the left, the selected peer's thread and
// a compose box on the right. Hosted in a KeyboardPanel window anchored to the
// bar widget; all state comes from the shared Lanchat singleton. The left
// column's footer holds the HTTP API toggle (enable/disable from the UI).
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

  function copyToClipboard(text) {
    if (!text) return
    Quickshell.execDetached(["bash", "-c", "printf %s " + Util.shellQuote(text) + " | wl-copy"])
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
    contentWidth: win.fittedContentWidth(Style.space(580))
    contentHeight: win.fittedContentHeight(Style.space(460))

    Rectangle {
      width: parent.width
      height: parent.height
      color: Color.popups.background

      // ---- body: peer list + thread -----------------------------------
      Rectangle {
        width: parent.width
        height: parent.height
        color: "transparent"

        Row {
          anchors.fill: parent

          // Left: peer list
          Rectangle {
            width: Style.space(280)
            height: parent.height
            color: Util.alpha(Color.foreground, 0.04)

            Item {
              anchors.fill: parent

              // ---- peers list (scrollable) ---------------------------
              ListView {
                id: peerList
                width: parent.width
                anchors.top: parent.top
                anchors.bottom: peersOnlineBar.top
                clip: true
                model: Lanchat.peers
                spacing: Style.spacing.xs
                anchors.topMargin: Style.spacing.sm
                anchors.bottomMargin: Style.spacing.xs
                anchors.leftMargin: Style.spacing.sm
                anchors.rightMargin: Style.spacing.sm

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
                    visible: modelData.id === root.selectedPeerId
                    width: 3
                    height: parent.height * 0.5
                    radius: 1.5
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    color: Color.accent
                  }

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.sm
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.sm
                    text: modelData.name
                    color: modelData.id === root.selectedPeerId
                      ? Color.accent
                      : Color.popups.text
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

              // ---- peers online: pinned above settings ----------------
              Item {
                id: peersOnlineBar
                width: parent.width
                height: Style.space(26)
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: settings.top

                Text {
                  anchors.left: parent.left
                  anchors.leftMargin: Style.spacing.sm
                  anchors.verticalCenter: parent.verticalCenter
                  text: (Lanchat.onlineCount === 1 ? "1 peer" : Lanchat.onlineCount + " peers") + " online"
                  color: Lanchat.onlineCount > 0 ? Color.accent : Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }

              // ---- settings: collapsible ------------------------------
              Column {
                id: settings
                property bool expanded: true
                width: parent.width
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: settingsHeader.height + (settings.expanded ? settingsBody.height : 0)

                // header
                Item {
                  id: settingsHeader
                  width: parent.width
                  height: Style.space(26)

                  Rectangle {
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    height: 1
                    color: Color.popups.border
                  }

                  MouseArea {
                    anchors.fill: parent
                    onClicked: settings.expanded = !settings.expanded
                  }

                  Text {
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.sm
                    anchors.verticalCenter: parent.verticalCenter
                    text: settings.expanded ? "Settings ▾" : "Settings ▸"
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.weight: Font.Bold
                  }
                }

                // body (API + name)
                Column {
                  id: settingsBody
                  width: parent.width
                  visible: settings.expanded

                  // API row
                  Item {
                    width: parent.width
                    height: Style.space(34)

                    Row {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.spacing.xs

                      Text {
                        text: "API"
                        color: Color.popups.text
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        font.weight: Font.Bold
                      }

                      Text {
                        text: Lanchat.httpEnabled ? ":" + Lanchat.httpPort : "off"
                        color: Lanchat.httpEnabled ? Color.accent : Color.muted
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                      }
                    }

                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      checked: Lanchat.httpEnabled
                      onToggled: Lanchat.setHttpEnabled(!Lanchat.httpEnabled)
                    }
                  }

                  // Name row
                  Item {
                    width: parent.width
                    height: Style.space(42)

                    Text {
                      id: nameLabel
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Name"
                      color: Color.muted
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    TextField {
                      id: nameInput
                      anchors.left: nameLabel.right
                      anchors.leftMargin: Style.spacing.sm
                      anchors.right: rollButton.left
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      maximumLength: 32
                      text: Lanchat.myName
                      placeholderText: "your name"
                      horizontalPadding: Style.space(8)
                      verticalPadding: Style.space(4)
                      onAccepted: Lanchat.setMyName(nameInput.text)
                      onEditingFinished: if (nameInput.text.trim() !== "") Lanchat.setMyName(nameInput.text)
                    }

                    // Re-roll to a fresh random friendly name.
                    Button {
                      id: rollButton
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "\uF046"
                      onClicked: Lanchat.regenerateName()
                    }
                  }
                }
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
            width: parent.width - Style.space(280) - 1
            height: parent.height

            ListView {
              id: list
              width: parent.width
              height: parent.height - composeBox.height
              clip: true
              spacing: Style.spacing.sm
              model: root.thread

              header: Item { width: parent.width; height: Style.spacing.md }
              footer: Item { width: parent.width; height: Style.spacing.lg }

              onCountChanged: Qt.callLater(function() { positionViewAtEnd() })

              delegate: Column {
                required property var modelData
                width: list.width
                spacing: Style.spacing.xs

                // Meta row: who + when
                Text {
                  anchors.left: modelData.outgoing ? undefined : parent.left
                  anchors.right: modelData.outgoing ? parent.right : undefined
                  text: (modelData.outgoing ? "You · " : modelData.fromName + " · ") + root.timeLabel(modelData.ts)
                  color: Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }

                // Message bubble. The text anchors to fill the bubble with a
                // set padding; the bubble grows with the text (no circular
                // width dependency that used to clip long messages).
                Rectangle {
                  id: bubble
                  readonly property real bubbleMaxWidth: list.width * 0.8
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
                    text: modelData.text
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    wrapMode: Text.Wrap
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
                        root.copyToClipboard(modelData.text)
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

            // ---- compose box -----------------------------------------
            Rectangle {
              id: composeBox
              width: parent.width
              height: Style.space(58)
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
                anchors.topMargin: Style.spacing.md
                anchors.bottomMargin: Style.spacing.md
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

      // ---- transient status banner (top, so it never hides the input) --
      Rectangle {
        visible: Lanchat.statusMessage !== ""
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: Style.space(46)
        height: Style.space(26)
        color: Style.pressedFill
        Text {
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          anchors.leftMargin: Style.spacing.panelPadding
          anchors.rightMargin: Style.spacing.panelPadding
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
