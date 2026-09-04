import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 400; height: 800

  property bool done: false
  function fail(msg) { console.log("BENCH-FAIL " + msg); Qt.exit(1) }

  SettingsPanel {
    id: settings
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    hostHeight: benchRoot.height
    alertStackBottom: 0
    selectedOwnedRoom: null
    themePalette: []
    ownsAnyRoom: false
    panelW: 400
    panelH: 600

    // signal capture with arg checks
    property int sigCopy: 0; property string lastCopy: ""
    property int sigApplySize: 0; property string lastSize: ""
    property int sigApplyManual: 0; property string lastW: ""; property string lastH: ""
    property int sigOthers: 0
    onCopyToClipboardRequested: function(text) { sigCopy++; lastCopy = text }
    onApplyPanelSizeRequested: function(size) { sigApplySize++; lastSize = size }
    onApplyManualSizeRequested: function(w, h) { sigApplyManual++; lastW = w; lastH = h }
    onOpenHelpRequested: sigOthers++
    onCommitNameRequested: sigOthers++
    onAddFriendRequested: sigOthers++
    onPickDownloadDirRequested: sigOthers++
    onCopyDiagnosticsRequested: sigOthers++
    onCollapseRoomsRequested: sigOthers++
  }

  property var _col: settings.children[0] // the Column (settingsCol)
  property var _header: _col.children.length > 0 ? _col.children[0] : null // settingsHeader
  property var _body: _col.children.length > 1 ? _col.children[1] : null // settingsBody (Flickable)

  property bool _armedChecked: false
  property bool _disarmChecked: false

  Component.onCompleted: Qt.callLater(run)

  function run() {
    // (a) collapsed height == settingsHeader height
    if (!_header) return fail("settingsHeader not found (col children: " + _col.children.length + ")")
    if (Math.abs(settings.height - _header.height) > 0.5)
      return fail("collapsed height " + settings.height + " != header " + _header.height)
    console.log("BENCH-OK collapsed height == settingsHeader height (" + settings.height + ")")

    // (b) expanded toggles property and grows height
    settings.expanded = true
    if (!settings.expanded) return fail("expanded toggle failed")
    if (settings.height <= _header.height) return fail("expanded height did not grow: " + settings.height)
    console.log("BENCH-OK expanded height grows: " + settings.height)
    settings.expanded = false
    if (Math.abs(settings.height - _header.height) > 0.5) return fail("collapse did not restore height")
    console.log("BENCH-OK collapse restores collapsed height")

    // (d) confirm-timer flows auto-disarm: arm both, wait 3s (timers are 2.5s)
    settings.confirmClearAll = true
    settings.clearConfirmTimer.restart()
    settings.showClearAllCheck = true
    settings.clearAllCheckTimer.restart()
    _armedTimer.start()
  }

  Timer {
    id: _armedTimer
    interval: 200
    onTriggered: {
      if (!settings.confirmClearAll || !settings.showClearAllCheck)
        return benchRoot.fail("confirm flags not armed after restart()")
      console.log("BENCH-OK confirm flags armed")
      _disarmTimer.start()
    }
  }

  Timer {
    id: _disarmTimer
    interval: 3000
    onTriggered: {
      if (settings.confirmClearAll) return benchRoot.fail("clearConfirmTimer did not disarm confirmClearAll")
      if (settings.showClearAllCheck) return benchRoot.fail("clearAllCheckTimer did not disarm showClearAllCheck")
      console.log("BENCH-OK confirm timers auto-disarm")

      // (c) every signal fires with correct args
      var s = settings
      s.openHelpRequested()
      s.commitNameRequested()
      s.copyToClipboardRequested("hello")
      s.addFriendRequested()
      s.applyPanelSizeRequested("small")
      s.applyManualSizeRequested("500", "400")
      s.pickDownloadDirRequested()
      s.copyDiagnosticsRequested()
      s.collapseRoomsRequested()
      if (s.sigCopy !== 1 || s.lastCopy !== "hello") return benchRoot.fail("copyToClipboard signal bad: " + s.sigCopy + " " + s.lastCopy)
      if (s.sigApplySize !== 1 || s.lastSize !== "small") return benchRoot.fail("applyPanelSize signal bad")
      if (s.sigApplyManual !== 1 || s.lastW !== "500" || s.lastH !== "400") return benchRoot.fail("applyManualSize signal bad")
      if (s.sigOthers !== 7) return benchRoot.fail("signal count wrong: " + s.sigOthers + " != 7")
      console.log("BENCH-OK all 9 signals fired with correct args")
      console.log("BENCH-PASS")
      Qt.exit(0)
    }
  }
}
