import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "shared"

// Service entry point. Runs at startup and stays loaded (keepLoaded), so the
// Lanchat singleton — which spawns the daemon — exists as soon as the shell
// comes up, independent of whether the bar widget is placed anywhere.
Item {
  id: root

  property var shell: null

  // Touching the singleton here is what creates it (and starts the daemon)
  // at boot. Keep the reference live so it cannot be garbage collected.
  readonly property bool daemonReady: Lanchat.daemonReady
  readonly property int onlineCount: Lanchat.onlineCount
  readonly property int unreadCount: Lanchat.unreadCount

  onDaemonReadyChanged: {}
  onOnlineCountChanged: {}
  onUnreadCountChanged: {}

  Component.onCompleted: Lanchat.startDaemon()
}
