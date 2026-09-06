// Bench for the full member room color on the text bubble: a ListView with
// two incoming messages from p1 (Red #D35F5F) and p2 (Blue #1A3A6B).
// Asserts (after the model populates one frame late):
//   1. p1 bubble is exactly #D35F5F (full member color, not a 25% composite)
//   2. p2 bubble is exactly #1A3A6B
//   3. p1 ink != p2 ink (luminance flip works at full opacity)
//   4. meta text color is Color.muted for both, NOT member color
// Then colorsEnabled = false (whole benchRoom object REASSIGNED — mutating a
// JS object field fires no QML change notification): both bubbles fall back
// to the base fill (no member hex).
import QtQuick
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 500; height: 600

  property bool done: false
  // Members shared between the enabled/disabled passes so only
  // colorsEnabled differs (reassignment of benchRoom is what
  // notifies the bindings — see verifyOn below).
  property var membersData: ({
    p1: { name: "Red", color: { token: "hex", hex: "#D35F5F" } },
    p2: { name: "Blue", color: { token: "hex", hex: "#1A3A6B" } }
  })
  property var benchRoom: ({
    colorsEnabled: true,
    members: benchRoot.membersData
  })
  function fail(msg) { console.log("BENCH-ROOMCOLOR-FAIL " + msg); Qt.exit(1) }

  // Normalize a color to lowercase #rrggbb (color.toString() may
  // yield #aarrggbb when alpha was specified — strip opaque alpha).
  function norm(c) {
    var s = String(c).toLowerCase()
    if (s.charAt(0) === "#" && s.length === 9) s = "#" + s.slice(3)
    return s
  }

  ListView {
    id: lv
    anchors.fill: parent
    model: []
    spacing: 4

    delegate: Column {
      id: benchDelegate
      required property var modelData
      width: lv.width

      RoomMessage {
        id: roomMsg
        modelData: benchDelegate.modelData
        maxWidth: 400
        selectedRoom: benchRoot.benchRoom
        timeLabel: function(ts) { return "12:00" }
      }
    }
  }

  Component.onCompleted: Qt.callLater(populate)

  function populate() {
    lv.model = [
      { mid: "m1", outgoing: false, from: "p1", fromName: "Red", ts: 0,
        text: "hello red", edited: false, attachment: null,
        friendRequest: false, held: false },
      { mid: "m2", outgoing: false, from: "p2", fromName: "Blue", ts: 0,
        text: "hello blue", edited: false, attachment: null,
        friendRequest: false, held: false }
    ]
    Qt.callLater(verifyOn)
  }

  // Find each RoomMessage instance: its root Column declares BOTH
  // maxWidth and selectedRoom (the bubble Rectangle has neither;
  // ChatMessage's editRequested marker does not exist here).
  function collect() {
    var msgs = []
    function walkMsgs(n) {
      if (!n) return
      if (n.maxWidth !== undefined && n.selectedRoom !== undefined) {
        msgs.push(n); return
      }
      var kids = n.children
      if (kids) for (var i = 0; i < kids.length; i++) walkMsgs(kids[i])
      var res = n.resources
      if (res) for (var j = 0; j < res.length; j++) walkMsgs(res[j])
    }
    walkMsgs(lv.contentItem)
    var out = []
    for (var m = 0; m < msgs.length; m++) {
      var rm = msgs[m]
      var bubble = null
      var meta = null
      for (var i = 0; i < rm.children.length; i++) {
        var kid = rm.children[i]
        if (bubble === null && kid.bubbleColor !== undefined) bubble = kid
        if (meta === null && kid.text !== undefined
            && String(kid.text).indexOf("·") !== -1) meta = kid
      }
      out.push({ rm: rm, mid: rm.modelData ? rm.modelData.mid : "?",
                 bubble: bubble, meta: meta })
    }
    out.sort(function(a, b) { return a.mid < b.mid ? -1 : 1 })
    return out
  }

  function checkOne(rec, expHex, otherInk) {
    if (!rec.bubble) return fail("bubble Rectangle not found for " + rec.mid)
    if (!rec.meta) return fail("meta Text not found for " + rec.mid)
    var got = norm(rec.bubble.bubbleColor)
    if (got !== norm(expHex))
      return fail(rec.mid + " bubble is " + got + ", expected " + expHex
                  + " (exact member color, NOT 25% composite)")
    if (otherInk !== null) {
      var mine = norm(rec.bubble.ink)
      if (mine === otherInk)
        return fail(rec.mid + " ink equals sibling ink (" + mine + ")"
                    + " — luminance flip failed at full opacity")
    }
    var metaCol = norm(rec.meta.color)
    if (metaCol !== norm(Color.muted))
      return fail(rec.mid + " meta text color is " + metaCol
                  + ", expected Color.muted (" + norm(Color.muted)
                  + "), NOT member color")
    console.log("BENCH-ROOMCOLOR-OK " + rec.mid + " bubble is " + expHex)
    return true
  }

  function verifyOn() {
    var recs = collect()
    if (recs.length !== 2)
      return fail("expected 2 RoomMessage instances, found " + recs.length)
    if (recs[0].mid !== "m1" || recs[1].mid !== "m2")
      return fail("unexpected mids: " + recs[0].mid + "," + recs[1].mid)
    var ink1 = norm(recs[0].bubble.ink)
    if (!checkOne(recs[0], "#D35F5F", null)) return
    if (!checkOne(recs[1], "#1A3A6B", ink1)) return
    console.log("BENCH-ROOMCOLOR-OK meta text color is Color.muted for both, NOT member color")
    console.log("BENCH-ROOMCOLOR-OK p1 ink != p2 ink (luminance flip works at full opacity)")
    // REASSIGN the whole var property: field mutation
    // (benchRoom.colorsEnabled = false) fires no QML change
    // notification and the bindings would stay stale.
    benchRoom = ({ colorsEnabled: false, members: benchRoot.membersData })
    Qt.callLater(verifyOff)
  }

  function verifyOff() {
    var recs = collect()
    if (recs.length !== 2)
      return fail("expected 2 RoomMessage instances after colorsEnabled=false, found " + recs.length)
    for (var i = 0; i < recs.length; i++) {
      var rec = recs[i]
      if (!rec.bubble) return fail("bubble Rectangle not found for " + rec.mid + " after disable")
      var got = norm(rec.bubble.bubbleColor)
      var base = norm(rec.bubble.bubbleBase)
      if (got !== base)
        return fail(rec.mid + " bubble after colorsEnabled=false is " + got
                    + ", expected base fill " + base)
      var hex = norm("#D35F5F")
      if (got === hex)
        return fail(rec.mid + " still shows member hex after colorsEnabled=false")
    }
    console.log("BENCH-ROOMCOLOR-OK both bubbles fell back to the base fill (no member hex)")
    Qt.exit(0)
  }

  Timer {
    interval: 8000
    running: true
    repeat: false
    onTriggered: { console.log("BENCH-ROOMCOLOR-FAIL failsafe-timeout"); Qt.exit(1) }
  }
}
