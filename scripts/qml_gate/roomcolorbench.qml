// Bench for the full member room color on the text bubble: a ListView with
// two incoming messages from p1 (Red #D35F5F) and p2 (Blue #1A3A6B).
// Asserts (after the model populates one frame late):
//   1. p1 bubble is exactly #D35F5F (full member color, not a 25% composite)
//   2. p2 bubble is exactly #1A3A6B
//   3. p1 ink != p2 ink (luminance flip works at full opacity)
//   4. (1.5.65) the meta "Name · time" row no longer exists — the sender
//      name lives on the voice-change dividers (RoomView) and the time rides
//      the bubble corner, so there is no meta text to assert muted.
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
      // Post-refactor: RoomMessage's text bubble is the shared MessageBubble
      // (first child). Its bubbleRect alias exposes the bubble Rectangle.
      var bubble = null
      var mb = null
      for (var i = 0; i < rm.children.length; i++) {
        if (rm.children[i].bubbleRect !== undefined) { mb = rm.children[i]; break }
      }
      if (mb) bubble = mb
      out.push({ rm: rm, mid: rm.modelData ? rm.modelData.mid : "?",
                 bubble: bubble ? bubble.bubbleRect : null,
                 mb: bubble })
    }
    out.sort(function(a, b) { return a.mid < b.mid ? -1 : 1 })
    return out
  }
  // meta row removed in 1.5.65 (names on dividers, time on the bubble);
  // kept so the collect() record shape is unchanged.
  function meta_placeholder() { return null }

  function checkOne(rec, expHex, otherInk) {
    if (!rec.mb) return fail("MessageBubble not found for " + rec.mid)
    var got = norm(rec.mb.bubbleRect.color)
    if (got !== norm(expHex))
      return fail(rec.mid + " bubble is " + got + ", expected " + expHex
                  + " (exact member color, NOT 25% composite)")
    if (otherInk !== null) {
      var mine = norm(rec.mb.bubbleTextItem.color)
      if (mine === otherInk)
        return fail(rec.mid + " ink equals sibling ink (" + mine + ")"
                    + " — luminance flip failed at full opacity")
    }
    // meta row removed in 1.5.65: names on dividers, time on the bubble.
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
      if (!rec.mb) return fail("MessageBubble not found for " + rec.mid + " after disable")
      var got = norm(rec.mb.bubbleRect.color)
      var base = norm(rec.mb.bubbleRect.color)
      if (rec.mb.bubbleColorOverride !== "transparent")
        return fail(rec.mid + " still carries a member-color override after colorsEnabled=false")
      if (rec.mb.modelData.outgoing) continue
      if (got !== norm(Style.normalFill))
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
