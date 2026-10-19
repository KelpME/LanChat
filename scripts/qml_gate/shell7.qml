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
  function countTextFields(node) {
    var n = 0
    if (node.toString().indexOf("TextField") !== -1) n++
    for (var i = 0; i < node.children.length; i++) n += countTextFields(node.children[i])
    return n
  }
  // The body Loader lives under the Flickable's contentItem.
  function findBodyLoader(body) {
    var scopes = [body.contentItem, body]
    for (var s = 0; s < scopes.length; s++) {
      var sc = scopes[s]
      if (!sc) continue
      for (var i = 0; i < sc.children.length; i++)
        if (sc.children[i].toString().indexOf("QQuickLoader") !== -1) return sc.children[i]
    }
    return null
  }

  SettingsPanel {
    id: settings
    // bench switch: asserts read height the frame after toggling;
    // the production animation defers that. Animation is sectionbench's job.
    animateSections: false
    panelVisible: true // bench: panel window is "shown"; gates the body Loader
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

    // (b2) #9076: fields exist only while expanded && panelVisible
    var bodyLoader = findBodyLoader(_body)
    if (!bodyLoader) {
      var kinds = []
      for (var j = 0; j < _body.children.length; j++) kinds.push(_body.children[j].toString())
      return fail("settings body Loader not found (body children: " + kinds.join(" | ") + ")")
    }
    if (!bodyLoader.item) return fail("body loader item missing while expanded+panelVisible")
    var tfCount = countTextFields(bodyLoader.item)
    if (tfCount <= 0) {
      var kinds2 = []
      function dump(n, d) {
        if (d > 3) return
        kinds2.push(n.toString().replace(/\(0x[0-9a-f]+\)/, ""))
        for (var k = 0; k < n.children.length; k++) dump(n.children[k], d + 1)
      }
      dump(bodyLoader.item, 0)
      return fail("no TextFields instantiated while expanded+panelVisible; tree: " + kinds2.join(" | "))
    }
    console.log("BENCH-OK settings fields instantiated while expanded+visible (" + tfCount + " TextFields)")
    settings.expanded = false
    if (Math.abs(settings.height - _header.height) > 0.5) return fail("collapse did not restore height")
    console.log("BENCH-OK collapse restores collapsed height")
    if (bodyLoader.item !== null) return fail("body loader still active after collapse")
    console.log("BENCH-OK settings loader item null when collapsed (fields destroyed)")

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

      // (c) every signal fires with correct args. Reset sigOthers first:
      // with #9076's lazy instantiation the name field's creation-time
      // debounce no longer contributes a phantom commit signal (the field
      // is destroyed on collapse before its 2.5s timer can fire), so only
      // the 6 explicitly-emitted non-copy/size signals must land here.
      var s = settings
      s.sigOthers = 0
      s.openHelpRequested()
      s.commitNameRequested("TestUser")
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
      if (s.sigOthers !== 6) return benchRoot.fail("signal count wrong: " + s.sigOthers + " != 6")
      console.log("BENCH-OK all 9 signals fired with correct args")
      console.log("BENCH-PASS")
      Qt.exit(0)
    }
  }
}
