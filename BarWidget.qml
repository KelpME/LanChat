import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "shared"

// Lanchat bar widget: one chat icon, a live online/unread badge, and a
// click that summons the chat panel. The panel is hosted here (loaded via a
// Loader, clock-style) and the bar renders it as a popup off this button.
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
    tooltipText: Lanchat.onlineCount === 0
      ? "Lanchat — no peers online"
      : "Lanchat — " + Lanchat.onlineCount + " online" +
        (Lanchat.unreadCount > 0 ? " · " + Lanchat.unreadCount + " unread" : "")
    onPressed: function(b) {
      if (b === Qt.LeftButton) root.togglePanel()
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
    color: Color.accent
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
