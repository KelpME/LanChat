// INT-OVERFLOW bench: the tooLarge check compares p.attachment.size against
// Lanchat.attachmentMaxBytes. Lanchat.qml declares `property int
// attachmentMaxBytes: 4 * 1024 * 1024 * 1024` — 4 GiB exceeds int32 max.
// If the engine wraps, the default reads 0 (every file "too large"); the
// ready event's 55 GiB would wrap to a NEGATIVE int (same again).
//   I1 default declaration holds a positive sane value (> 1 GiB)
//   I2 after the real ready event (attachmentMaxBytes=59055800320) the
//      property still compares sanely: 197432 is NOT too large
//   I3 51 GB (51000000000, the field-reported size) is NOT over the 55 GiB
//      limit; a genuinely over-ceiling 60 GiB IS flagged
import QtQuick
import qs.Commons

Item {
  id: benchRoot
  property bool done: false
  property var real1: null
  function fail(msg) { console.log("BENCH-INT-FAIL " + msg); Qt.exit(1) }

  Timer {
    interval: 15000; running: true; repeat: false
    onTriggered: { console.log("BENCH-INT-FAIL failsafe-timeout"); Qt.exit(1) }
  }

  Component.onCompleted: {
    Qt.callLater(function() {
      if (benchRoot.done) return
      benchRoot.done = true
      var comp = Qt.createComponent("LanchatReal.qml")
      if (comp.status === Component.Error)
        return fail("compile: " + comp.errorString())
      real1 = comp.createObject(benchRoot, {
        manageDaemon: false, soundEnabled: false, panelOpen: false,
        selectedPeerId: "", selectedRoomId: ""
      })
      if (!real1) return fail("create: " + comp.errorString())
      Qt.callLater(run)
    })
  }

  function run() {
    var L = benchRoot.real1

    // I1: the DECLARED default (4 GiB) must survive as a positive int.
    console.log("BENCH-INT-INFO default attachmentMaxBytes=" + L.attachmentMaxBytes)
    if (!(L.attachmentMaxBytes > 1024 * 1024 * 1024))
      return fail("I1 default attachmentMaxBytes=" + L.attachmentMaxBytes + " (wrapped?)")
    console.log("BENCH-INT-OK-I1")

    // I2: the real ready event value (55 GiB from this machine's config).
    L.onDaemonLine(JSON.stringify({ event: "ready", id: "self", name: "P",
      version: "t", port: 4812, httpEnabled: false, httpPort: 0,
      httpBind: "127.0.0.1", visibility: "private", acceptRequests: true,
      online: true, friends: [], rooms: [],
      downloadDir: "/tmp", attachmentMaxBytes: 59055800320, sendDelay: 0,
      apiFullAccess: false, panelSize: "medium", status: "available",
      soundEnabled: false, typingEnabled: true, showTyping: true,
      readReceiptsEnabled: false, showReadReceipts: false }))
    console.log("BENCH-INT-INFO after ready attachmentMaxBytes=" + L.attachmentMaxBytes
                + " attachmentMaxGiB=" + L.attachmentMaxGiB)
    if (197432 > L.attachmentMaxBytes)
      return fail("I2 197 KB flagged too large — limit wrapped to " + L.attachmentMaxBytes)
    console.log("BENCH-INT-OK-I2")

    // I3a: 51 GB (the field-reported "too big" file) is UNDER the 55 GiB
    // ceiling — the bar must NOT flag it (it was flagged when the int wrapped).
    if (51000000000 > L.attachmentMaxBytes)
      return fail("I3a 51 GB flagged over " + L.attachmentMaxBytes)
    console.log("BENCH-INT-OK-I3a")
    // I3b: a genuinely over-ceiling size IS flagged.
    if (!(64424509440 > L.attachmentMaxBytes))
      return fail("I3b 60 GiB not flagged over " + L.attachmentMaxBytes)
    console.log("BENCH-INT-OK-I3b")

    Qt.exit(0)
  }
}
