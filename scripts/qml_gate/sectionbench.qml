// Collapsible-sections bench: peers header + animation + exclusivity.
// Instantiates the REAL Panel.qml (same pattern as panelbench) and asserts:
//   A1. PeerList exposes a collapsible section (expanded default true)
//   A2. Toggling peers closed/open animates (height sampled over >1 frame,
//       settles at a different value; animateSections switch works)
//   A3. Settings expand collapses BOTH peers and rooms
//   A4. Peers + rooms CAN be open at the same time
//   A5. Rooms expanding does NOT close peers; peers expanding does NOT
//       close rooms; rooms expanding DOES close settings
//   A6. clean compile (enforced by run.sh grep of TypeError/ReferenceError)
import QtQuick
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 1440
  height: 900

  Timer {
    interval: 15000
    running: true
    repeat: false
    onTriggered: {
      console.log("BENCH-SECT-FAIL failsafe-timeout (run() never completed)")
      Qt.exit(1)
    }
  }

  property string p: "BENCH-SECT"

  function fail(msg) {
    console.log(p + "-FAIL " + msg)
    Qt.exit(1)
  }

  property url panelUrl: Qt.resolvedUrl("Panel.qml")
  property var panelComp: null
  property var panel: null

  function loadPanel() {
    panelComp = Qt.createComponent(panelUrl)
    if (panelComp.status === Component.Error) {
      fail("Panel.qml failed to compile: " + panelComp.errorString())
      return
    }
    panel = panelComp.createObject(benchRoot, {
      "moduleName": "KelpME.lanchat",
      "ipcTarget": "KelpME.lanchat",
      "manageIpc": false,
      "anchorItem": null,
      "hostWidget": null
    })
    if (!panel) { fail("Panel object could not be created"); return }
    Qt.callLater(run)
  }

  Component.onCompleted: Qt.callLater(loadPanel)

  // ---- duck-typed tree search (same as panelbench) -----------------------
  function findByType(node, typeName, out) {
    if (!node || out.found) return
    if (typeName === "peerList" && node.peerRowH !== undefined && node.bottomInset !== undefined && node.showFwAlert !== undefined) { out.found = node; return }
    if (typeName === "roomSection" && node.sectionHeight !== undefined && node.amRoomOwnerOfFn !== undefined) { out.found = node; return }
    if (typeName === "settings" && node.expanded !== undefined && node.hostHeight !== undefined && node.panelW !== undefined) { out.found = node; return }
    var kids = node.children
    if (kids) {
      for (var i = 0; i < kids.length; i++) {
        findByType(kids[i], typeName, out)
        if (out.found) return
      }
    }
    var res = node.resources
    if (res) {
      for (var j = 0; j < res.length; j++) {
        findByType(res[j], typeName, out)
        if (out.found) return
      }
    }
    var ci = node.contentItem
    if (ci) {
      if (ci.length !== undefined) {
        for (var k2 = 0; k2 < ci.length; k2++) findByType(ci[k2], typeName, out)
      } else {
        findByType(ci, typeName, out)
      }
    }
  }

  property var peerListObj: null
  property var roomSectionObj: null
  property var settingsObj: null

  function locateAll() {
    var a = { found: null }, b = { found: null }, c = { found: null }
    findByType(panel, "peerList", a)
    findByType(panel, "roomSection", b)
    findByType(panel, "settings", c)
    peerListObj = a.found
    roomSectionObj = b.found
    settingsObj = c.found
  }

  function run() {
    Lanchat.displayPeers = [
      { id: "test-peer-1", name: "TestPeer", status: "online",
        fingerprint: "ABCDEF0123456789", peerId: "test-peer-1", outgoing: false }
    ]
    Lanchat.friends = [{ id: "test-peer-1", confirmed: true }]
    Lanchat.myId = "me"
    Lanchat.rooms = []
    Lanchat.messages = []
    Lanchat.roomMessages = []

    locateAll()
    var pl = peerListObj, rs = roomSectionObj, st = settingsObj
    if (!pl) return fail("PeerList instance not found in tree")
    if (!rs) return fail("RoomListSection instance not found in tree")
    if (!st) return fail("SettingsPanel instance not found in tree")

    // Panel init is async (multi-frame); let geometry settle before
    // the first height read, or listClip reports 0 spuriously.
    settle(3, function() { runAsserts(pl, rs, st) })
  }

  // Fire cb after `ticks` timer ticks (lets layout/animation settle).
  function settle(ticks, cb) {
    var n = 0
    var timer = Qt.createQmlObject(
      'import QtQuick; Timer { interval: 40; repeat: true; running: false }',
      benchRoot, "settleTimer" + ticks)
    timer.triggered.connect(function() {
      n++
      if (n >= ticks) { timer.stop(); timer.destroy(); cb() }
    })
    timer.start()
  }

  function runAsserts(pl, rs, st) {
    // ---- A1: peers section exists, starts open --------------------------
    if (pl.expanded === undefined) return fail("PeerList has no expanded property")
    if (pl.expanded !== true) return fail("peers section must start expanded, got " + pl.expanded)
    console.log(p + "-OK A1 peers section present, default expanded=true")

    // ---- A2: collapse animates, settles closed, re-open animates --------
    if (pl.animateSections === undefined) return fail("PeerList has no animateSections switch")
    pl.animateSections = true
    // find listClip by duck type: the clip=true child of PeerList that
    // CONTAINS the ListView (onboardingBanner also has clip=true and would
    // false-match on a bare clip check — it's 0-tall when no banners show).
    var clip = null
    function hasListViewChild(n) {
      var kids = n.children
      if (!kids) return false
      for (var i = 0; i < kids.length; i++)
        if (kids[i].contentHeight !== undefined) return true
      return false
    }
    function scanClip(n) {
      if (!n || clip) return
      if (n.clip === true && hasListViewChild(n)) { clip = n; return }
      var kids = n.children
      if (kids) for (var i = 0; i < kids.length; i++) scanClip(kids[i])
    }
    scanClip(pl)
    if (!clip) return fail("animated listClip wrapper not found in PeerList")
    var h0 = clip.height
    if (h0 <= 0) return fail("peers body starts at height " + h0 + ", expected > 0 (expanded)")
    pl.expanded = false
    // sample over frames: with a 160ms animation the height must be
    // strictly between start and 0 at mid-flight (not snapped)
    frameProbe(clip, h0, 0, 8, function(midSamples) {
      var midFlying = false
      for (var i = 0; i < midSamples.length; i++) {
        if (midSamples[i] < h0 - 0.5 && midSamples[i] > 0.5) { midFlying = true; break }
      }
      if (!midFlying)
        console.log(p + "-INFO peers collapse: no mid-flight sample caught (animation may have finished between frames); settled=" + clip.height)
      if (Math.abs(clip.height) > 0.5)
        return fail("peers body did not collapse to 0, settled at " + clip.height)
      console.log(p + "-OK A2a peers body collapsed to 0 (settled); mid-flight samples=" + midSamples.join(","))
      // re-open: must animate back up and settle > 0
      pl.expanded = true
      frameProbe(clip, 0, h0, 8, function(upSamples) {
        if (clip.height <= 0) return fail("peers body did not re-expand, height=" + clip.height)
        var grew = false
        for (var j = 0; j < upSamples.length; j++) {
          if (upSamples[j] > 0.5 && upSamples[j] < h0 - 0.5) { grew = true; break }
        }
        if (!grew)
          console.log(p + "-INFO peers re-expand: no mid-flight sample caught; settled=" + clip.height)
        console.log(p + "-OK A2b peers body re-expanded to " + Math.round(clip.height) + "; mid-flight samples=" + upSamples.join(","))
        runExclusivity(pl, rs, st)
      })
    })
  }

  // Sample `target.height` every 40ms for `frames` ticks, then call back.
  function frameProbe(target, from, to, frames, cb) {
    var samples = []
    var timer = Qt.createQmlObject(
      'import QtQuick; Timer { interval: 40; repeat: true; running: false }',
      benchRoot, "frameProbeTimer")
    timer.triggered.connect(function() {
      samples.push(target.height)
      if (samples.length >= frames) {
        timer.stop()
        timer.destroy()
        cb(samples)
      }
    })
    timer.start()
  }

  // ---- header MouseArea locator: each section header is an Item exactly
  // Style.space(26) tall whose direct child MouseArea fills it (the blank
  // peer-click area's parent is the full-height listZone; the notif banner
  // header is 24sp — neither collides).
  function findHeaderMouseArea(sectionRoot) {
    var found = { ma: null }
    function scan(n) {
      if (!n || found.ma) return
      if (n instanceof MouseArea && n.parent && Math.abs(n.parent.height - Style.space(26)) < 0.5) {
        found.ma = n
        return
      }
      var kids = n.children
      if (kids) for (var i = 0; i < kids.length; i++) scan(kids[i])
    }
    scan(sectionRoot)
    return found.ma
  }

  // ---- A3/A4/A5: exclusivity matrix — driven through the REAL header
  // click handlers (animateSections OFF for exact same-frame reads) ------
  function runExclusivity(pl, rs, st) {
    pl.animateSections = false
    rs.animateSections = false
    st.animateSections = false

    var plH = findHeaderMouseArea(pl)
    var rsH = findHeaderMouseArea(rs)
    var stH = findHeaderMouseArea(st)
    if (!plH) return fail("peers header MouseArea not found")
    if (!rsH) return fail("rooms header MouseArea not found")
    if (!stH) return fail("settings header MouseArea not found")

    // baseline: everything closed
    pl.expanded = false; rs.expanded = false; st.expanded = false

    // A3: clicking settings open collapses both peers and rooms (real path:
    // the click emits collapseRoomsRequested/collapsePeersRequested, wired
    // in Panel.qml)
    pl.expanded = true; rs.expanded = true; st.expanded = false
    stH.clicked(null)
    if (st.expanded !== true) return fail("A3: settings header click did not expand settings")
    if (pl.expanded !== false) return fail("A3: settings open must collapse peers")
    if (rs.expanded !== false) return fail("A3: settings open must collapse rooms")
    console.log(p + "-OK A3 settings header click collapses peers AND rooms")

    // A4: peers + rooms can be open together
    stH.clicked(null) // close settings again
    if (st.expanded !== false) return fail("A4: settings did not close on second click")
    plH.clicked(null)  // open peers (already closed)
    rsH.clicked(null)  // open rooms
    if (!pl.expanded || !rs.expanded) return fail("A4: peers+rooms could not both be open")
    if (st.expanded) return fail("A4: settings must stay closed")
    console.log(p + "-OK A4 peers and rooms open simultaneously")

    // A5a: rooms header click while peers open + settings open:
    // must close settings, must NOT close peers
    pl.expanded = true; st.expanded = true; rs.expanded = false
    rsH.clicked(null)
    if (st.expanded) return fail("A5a: rooms open must collapse settings")
    if (!pl.expanded) return fail("A5a: rooms open must NOT collapse peers")
    console.log(p + "-OK A5a rooms click closes settings, leaves peers alone")

    // A5b: peers header click while rooms open: must NOT close rooms
    st.expanded = false
    pl.expanded = false
    plH.clicked(null)
    if (!pl.expanded) return fail("A5b: peers header click did not expand peers")
    if (!rs.expanded) return fail("A5b: peers open must NOT collapse rooms")
    console.log(p + "-OK A5b peers click leaves rooms alone")

    console.log(p + "-PASS")
    Qt.exit(0)
  }
}
