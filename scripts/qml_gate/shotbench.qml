// shotbench: live marketplace-preview screenshots of the REAL Panel.qml.
// Unlike the assert-only benches, this maps the panel on the live compositor
// (bar strip + panel-below-bar = production geometry) and drives /usr/bin/grim
// from QML to capture card+padding regions. Two scenes are shot in one run:
//   scene 1: 1:1 chat with a friend (turns, attachment, read receipts, typing)
//   scene 2: group room chat
// Env: SHOT_DIR (required) — output dir for shot1.png / shot2.png.
// Exit 0 only when both grim invocations were issued; the runner verifies
// the files afterwards.
import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 0
  height: 0

  property string shotDir: Quickshell.env("SHOT_DIR") || "/tmp"
  property var panel: null
  property bool shot1: false
  property bool shot2: false

  // Fake bar: a transparent top strip window whose sole job is to give the
  // KeyboardPanel an anchorWindow/bar pair so cardOrigin lands below the
  // (real, live) bar: y = barH + gap, x centered. Invisible (Background
  // layer, transparent) — the real bar remains what the camera sees.
  PanelWindow {
    id: fakeBarWin
    anchors { top: true; bottom: false; left: true; right: true }
    implicitHeight: 35
    visible: true
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Background
    WlrLayershell.namespace: "lanchat-shotbench-anchor"
    Item { id: anchorSlot; width: 24; height: 24 }
  }
  QtObject {
    id: fakeBar
    property string position: "top"
    property int barSize: 35
    property var clickTargets: []
  }

  // ---- card geometry ---------------------------------------------------------
  // The KeyboardPanel inside Panel.qml is a fullscreen layer-shell window at
  // 0,0; the visible card sits inside it at `cardOrigin` with
  // contentWidth x contentHeight. Those three properties are readable on the
  // KeyboardPanel object (a non-Item child in panel.data), so no window
  // enumeration is needed (Quickshell.windows does not exist in 0.3.1).
  function cardRect() {
    if (!panel) return null
    for (var i = 0; i < panel.data.length; i++) {
      var n = panel.data[i]
      if (n && n.cardOrigin !== undefined && n.contentWidth !== undefined) {
        var o = n.cardOrigin
        if (o && n.contentWidth > 0 && n.contentHeight > 0)
          return { x: o.x, y: o.y, w: n.contentWidth, h: n.contentHeight }
      }
    }
    return null
  }

  // ---- capture ---------------------------------------------------------------
  // In-process grabToImage of the visible card. KeyboardPanel exposes
  // `contentItem` as an ALIAS to contentHolder.children (a list, not an
  // Item), so grab the first child's parent (the BorderSurface card) when
  // available. Deterministic: unlike an external grim screenshot it renders
  // the scene graph directly, so there is no race with the compositor
  // mapping the layer surface (which produced blank 45KB grabs).
  function keyboardPanel() {
    if (!panel) return null
    for (var i = 0; i < panel.data.length; i++) {
      var n = panel.data[i]
      if (n && n.cardOrigin !== undefined && n.contentWidth !== undefined) return n
    }
    return null
  }
  function pending() { return pending1 || pending2 }
  property bool pending1: false
  property bool pending2: false

  function grab(tag) {
    var kp = keyboardPanel()
    if (!kp) { console.log("SHOT-FAIL no keyboard panel for " + tag); return }
    var kids = kp.contentItem
    var content = (kids && kids.length) ? kids[0] : null
    if (!content) { console.log("SHOT-FAIL no panel content item for " + tag); return }
    // Walk up to the BorderSurface card: its width is contentWidth and its
    // parent is the fullscreen window item (wider), so stop there. Grabbing
    // the card (not the inner content) keeps the frame + background in shot.
    var target = content
    while (target.parent && target.parent.width <= kp.contentWidth + 8
           && target.parent.width >= target.width)
      target = target.parent
    console.log("SHOTGEOM " + tag + " " + Math.round(target.width) + "x" + Math.round(target.height))
    if (tag === "shot1") pending1 = true
    if (tag === "shot2") pending2 = true
    target.grabToImage(function(result) {
      var ok = result.saveToFile(shotDir + "/" + tag + ".png")
      console.log("SHOT " + tag + " saved=" + ok)
      if (tag === "shot1") pending1 = false
      if (tag === "shot2") pending2 = false
    }, Qt.size(Math.round(target.width), Math.round(target.height)))
  }

  // ---- scene data -----------------------------------------------------------
  function now() { return Date.now() }
  function ago(min) { return now() - min * 60000 }

  function fillCommon() {
    Lanchat.myId = "test-peer-1"
    Lanchat.myName = "ImpossibleIndyGrab"
    Lanchat.daemonReady = true
    Lanchat.daemonState = "running"
    Lanchat.customW = 1900
    Lanchat.customH = 1010
    Lanchat.showReadReceipts = true
    Lanchat.readReceiptsEnabled = true
    Lanchat.showTyping = true
    Lanchat.typingEnabled = true
    Lanchat.displayPeers = [
      { id: "alice-2", name: "FullCabNollieHeelflip", status: "online" },
      { id: "bob-3", name: "360DoubleTreFlip", status: "away" },
      { id: "carol-4", name: "KickflipMesa", status: "online" },
      { id: "dave-5", name: "BluntToFake", status: "offline" }
    ]
    Lanchat.onlineCount = 3
    Lanchat.rooms = [
      { roomId: "r1", name: "Skate Session", owner: "alice-2", seq: 7,
        colorsEnabled: false, memberCount: 4 }
    ]
    Lanchat.roomStates = {
      r1: {
        roomId: "r1", name: "Skate Session", owner: "alice-2", seq: 7,
        memberCount: 4,
        members: {
          "alice-2": { name: "FullCabNollieHeelflip" },
          "test-peer-1": { name: "ImpossibleIndyGrab" },
          "bob-3": { name: "360DoubleTreFlip" },
          "carol-4": { name: "KickflipMesa" }
        }
      }
    }
  }

  function scenePeer() {
    Lanchat.selectedRoomId = ""
    Lanchat.messages = [
      { mid: "m1", from: "alice-2", to: "test-peer-1", ts: ago(24),
        text: "session at the plaza tomorrow? spot report says dry",
        outgoing: false },
      { mid: "m2", from: "test-peer-1", to: "alice-2", ts: ago(23),
        text: "in. bringing the cruiser, parking lot gates open at 9",
        outgoing: true },
      { mid: "m3", from: "alice-2", to: "test-peer-1", ts: ago(21),
        text: "did you land the heelflip yet?",
        outgoing: false },
      { mid: "m4", from: "test-peer-1", to: "alice-2", ts: ago(20),
        text: "landed two in a row last night, video proof coming",
        outgoing: true },
      { mid: "m5", from: "alice-2", to: "test-peer-1", ts: ago(9),
        text: "send it over before I forget", outgoing: false },
      { mid: "m6", from: "test-peer-1", to: "alice-2", ts: ago(8),
        text: "here", outgoing: true,
        attachment: { name: "heelflip-line.mp4" } },
      { mid: "m7", from: "alice-2", to: "test-peer-1", ts: ago(3),
        text: "got it, that pop is clean", outgoing: false },
      { mid: "m8", from: "test-peer-1", to: "alice-2", ts: ago(2),
        text: "save me a spot at the bank ramp", outgoing: true }
    ]
    Lanchat.readReceipts = { m2: true, m4: true, m6: true, m8: true }
    var t = {}
    t["bob-3"] = "360DoubleTreFlip"
    Lanchat.typing = t
  }

  function sceneRoom() {
    Lanchat.typing = {}
    Lanchat.selectedPeerId = ""
    Lanchat.selectedRoomId = "r1"
    Lanchat.roomMessages = [
      { mid: "r1", room: "r1", from: "bob-3", fromName: "360DoubleTreFlip", ts: ago(18),
        text: "who is up for the plaza run tomorrow?", outgoing: false },
      { mid: "r2", room: "r1", from: "carol-4", fromName: "KickflipMesa", ts: ago(17),
        text: "me. the flat bar is back in the good spot", outgoing: false },
      { mid: "r3", room: "r1", from: "test-peer-1", fromName: "ImpossibleIndyGrab", ts: ago(16),
        text: "count me in, meeting at the fountain?", outgoing: true },
      { mid: "r4", room: "r1", from: "alice-2", fromName: "FullCabNollieHeelflip", ts: ago(12),
        text: "9am. bringing the camera this time", outgoing: false },
      { mid: "r5", room: "r1", from: "bob-3", fromName: "360DoubleTreFlip", ts: ago(6),
        text: "wax is in my trunk if anyone needs it", outgoing: false },
      { mid: "r6", room: "r1", from: "test-peer-1", fromName: "ImpossibleIndyGrab", ts: ago(4),
        text: "spot list for the day", outgoing: true,
        attachment: { name: "plaza-lines.pdf" } },
      { mid: "r7", room: "r1", from: "carol-4", fromName: "KickflipMesa", ts: ago(2),
        text: "see you all tomorrow", outgoing: false }
    ]
  }

  // ---- drive ------------------------------------------------------------------
  function loadPanel() {
    // Same trick as panelbench: create the real Panel.qml via component so
    // its qs.Ui Panel base binds correctly.
    var comp = Qt.createComponent(Qt.resolvedUrl("Panel.qml"))
    if (comp.status === Component.Error) {
      console.log("SHOT-FAIL Panel.qml compile: " + comp.errorString())
      Qt.exit(1)
      return
    }
    panel = comp.createObject(benchRoot, {
      moduleName: "kelpme.lanchat",
      ipcTarget: "kelpme.lanchat",
      manageIpc: false,
      anchorItem: anchorSlot,
      bar: fakeBar,
      hostWidget: null,
      animateSections: false
    })
    if (!panel) { console.log("SHOT-FAIL panel null"); Qt.exit(1); return }

    fillCommon()
    scenePeer()
    panel.open()
    // map + layout + fonts settle before the first capture
    Qt.callLater(function() { timer1.start() })
  }

  Timer {
    id: timer1
    interval: 1400
    onTriggered: {
      benchRoot.grab("shot1")
      timer2.start()
    }
  }
  Timer {
    id: timer2
    interval: 1600
    onTriggered: {
      benchRoot.sceneRoom()
      timer3.start()
    }
  }
  Timer {
    id: timer3
    interval: 1200
    onTriggered: {
      benchRoot.grab("shot2")
      timer4.start()
    }
  }
  Timer {
    id: timer4
    interval: 1200
    onTriggered: {
      var done = !benchRoot.pending()
      console.log("SHOT " + (done ? "OK" : "PENDING-DIED"))
      Qt.exit(done ? 0 : 1)
    }
  }

  // Failsafe.
  Timer {
    interval: 12000
    running: true
    onTriggered: { console.log("SHOT-FAIL timeout"); Qt.exit(1) }
  }

  Component.onCompleted: Qt.callLater(loadPanel)
}
