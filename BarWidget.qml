import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "shared"

// Lanchat bar widget: a chat icon whose color reflects your status, a live
// online/unread badge, a left-click that opens the chat panel, and a
// right-click menu to toggle online/offline or set your status.
BarWidget {
  id: root
  moduleName: "KelpME.lanchat"

  // ---- panel routing contract for the bar host --------------------------
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

  function open() {
    if (panelLoader.item) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item) panelLoader.item.close()
  }

  function togglePanel() {
    if (panelLoader.item) panelLoader.item.toggle()
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  // Icon color reflects status: red=DND, white=Available, yellow=BRB, orange=Away.
  readonly property color statusColor: Lanchat.status === "dnd" ? "#e33"
    : Lanchat.status === "brb" ? Qt.rgba(0.95, 0.8, 0.2, 1)
    : Lanchat.status === "away" ? Qt.rgba(0.95, 0.55, 0.2, 1)
    : Color.foreground  // available

  readonly property string statusLabel: Lanchat.status === "dnd" ? "Do Not Disturb"
    : Lanchat.status === "brb" ? "Be Right Back"
    : Lanchat.status === "away" ? "Away"
    : "Available"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\uF086" // nf-fa-comments
    active: Lanchat.onlineCount > 0
    useActiveColor: false
    foreground: Lanchat.daemonState === "running"
      ? (Lanchat.online ? root.statusColor : Qt.darker(root.statusColor, 1.4))
      : Color.urgent
    tooltipText: Lanchat.daemonState !== "running"
      ? "Lanchat daemon not running" + (Lanchat.daemonState === "starting" ? " (starting…)" : "")
      : "Lanchat — " + root.statusLabel +
        (Lanchat.online ? "" : " (offline)") +
        (Lanchat.onlineCount > 0 ? " · " + Lanchat.onlineCount + " online" : "")
    onPressed: function(b) {
      if (b === Qt.LeftButton) root.togglePanel()
      else if (b === Qt.RightButton) root.menuOpen = !root.menuOpen
    }
  }

  // Right-click menu: online/offline toggle + status presets.
  property bool menuOpen: false

  PopupCard {
    id: statusMenu
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.menuOpen
    contentWidth: statusMenu.fittedContentWidth(Style.space(200))
    contentHeight: statusMenu.fittedContentHeight(menuCol.implicitHeight)

    Column {
      id: menuCol
      anchors.fill: parent
      spacing: Style.space(2)

      MenuItem {
        text: Lanchat.online ? "Go offline" : "Go online"
        onClicked: { Lanchat.setOnline(!Lanchat.online); root.menuOpen = false }
      }

      Rectangle { width: parent.width; height: 1; color: Color.popups.border }

      MenuItem {
        text: "Available"
        background: Rectangle {
          color: Lanchat.status === "available" ? Style.selectedFill : "transparent"
          radius: Style.cornerRadius
        }
        onClicked: { Lanchat.setStatus("available"); root.menuOpen = false }
      }
      MenuItem {
        text: "Do Not Disturb"
        background: Rectangle {
          color: Lanchat.status === "dnd" ? Style.selectedFill : "transparent"
          radius: Style.cornerRadius
        }
        onClicked: { Lanchat.setStatus("dnd"); root.menuOpen = false }
      }
      MenuItem {
        text: "Away"
        background: Rectangle {
          color: Lanchat.status === "away" ? Style.selectedFill : "transparent"
          radius: Style.cornerRadius
        }
        onClicked: { Lanchat.setStatus("away"); root.menuOpen = false }
      }
      MenuItem {
        text: "Be Right Back"
        background: Rectangle {
          color: Lanchat.status === "brb" ? Style.selectedFill : "transparent"
          radius: Style.cornerRadius
        }
        onClicked: { Lanchat.setStatus("brb"); root.menuOpen = false }
      }
    }
  }

  IpcHandler {
    target: "KelpME.lanchat"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
  }

  // Unread badge, top-right corner of the widget slot.
  Rectangle {
    visible: Lanchat.unreadCount > 0
    anchors.top: parent.top
    anchors.right: parent.right
    anchors.topMargin: 1
    anchors.rightMargin: 1
    width: Math.max(badgeText.implicitWidth + Style.space(7), Style.space(15))
    height: Style.space(15)
    radius: height / 2
    color: Color.urgent
    border.color: Color.background
    border.width: 1

    Text {
      id: badgeText
      anchors.centerIn: parent
      text: Lanchat.unreadCount > 99 ? "99+" : Lanchat.unreadCount
      color: Color.background
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      font.weight: Font.Bold
    }
  }
}
