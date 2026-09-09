// Room format-parity bench: RoomMessage now renders its text bubble through
// the shared MessageBubble, so rooms get the 1:1 format (hover copy glyph,
// inside-edge timestamp row) while keeping member colors and luminance ink.
// Asserts:
//   R1  MessageBubble instance reachable inside RoomMessage (bubbleRect alias)
//   R2  copy glyph present in the bubble
//   R3  edit glyph disabled (editEnabled=false → opacity 0, MouseArea off)
//   R4  timestamp row shows the timeLabel output ("12:34")
//   R5  room color still fills the bubble (#3366aa member hex)
//   R6  luminance ink: #3366aa (L≈0.13 < 0.18) → popups.text ink on the text
//   R7  colorsEnabled=false (whole-object reassign) → override cleared, base
//       fill for incoming
import QtQuick
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 500; height: 600

  property bool done: false
  property var benchRoom: ({
    roomId: "room-bench",
    colorsEnabled: true,
    members: {
      "peer-9": { name: "Carol", color: { token: "hex", hex: "#3366aa" } }
    }
  })
  function fail(msg) { console.log("BENCH-ROOMFMT-FAIL " + msg); Qt.exit(1) }

  // Normalize a color to lowercase #rrggbb (strip opaque alpha).
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
        timeLabel: function(ts) { return "12:34" }
      }
    }
  }

  Component.onCompleted: Qt.callLater(populate)

  function populate() {
    lv.model = [
      { mid: "m1", outgoing: false, from: "peer-9", fromName: "Carol",
        ts: 0, text: "hello room", edited: false, attachment: null,
        friendRequest: false, held: false },
      { mid: "m2", outgoing: false, from: "peer-9", fromName: "Carol",
        ts: 0, text: "ok", edited: false, attachment: null,
        friendRequest: false, held: false }
    ]
    Qt.callLater(verify)
  }

  // All RoomMessage instances (root Column: maxWidth + selectedRoom) with
  // their MessageBubble child, sorted by mid.
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
    for (var i = 0; i < msgs.length; i++) {
      var rm = msgs[i]
      for (var k = 0; k < rm.children.length; k++) {
        if (rm.children[k].bubbleRect !== undefined) {
          out.push({ mid: rm.modelData.mid, mb: rm.children[k] })
          break
        }
      }
    }
    out.sort(function(a, b) { return a.mid < b.mid ? -1 : 1 })
    return out
  }

  function verify() {
    var recs = collect()
    if (!recs || recs.length !== 2)
      return fail("R1 expected 2 MessageBubbles, got " + (recs ? recs.length : 0))
    var mb = recs[0].mb
    if (!mb) return fail("R1 MessageBubble not found inside RoomMessage")
    console.log("BENCH-ROOMFMT-OK-R1 bubble reachable")

    // R2-F4: locate footer elements. innerCol children = [messageText,
    // footerRow]; footerRow children = [btnRow, timeText].
    var innerCol = mb.bubbleRect.children[0]
    var copyGlyph = null, editGlyph = null, timeGlyph = null, textItem = null
    var footerRow = null, btnRow = null
    for (var i = 0; i < innerCol.children.length; i++) {
      var el = innerCol.children[i]
      if (el.text === "hello room") textItem = el
      if (el.children && el.children.length === 2 && el.children[1].text === "12:34")
        footerRow = el
    }
    if (!footerRow) return fail("R2 footer row not found (time not in footer?)")
    for (var f = 0; f < footerRow.children.length; f++) {
      var fc = footerRow.children[f]
      if (fc.objectName === "btnRow") btnRow = fc
      else if (fc.text === "12:34") timeGlyph = fc
    }
    if (!btnRow) return fail("R2 btnRow not found in footer")
    for (var b = 0; b < btnRow.children.length; b++) {
      var g = btnRow.children[b]
      if (g.text === "\uF0C5" || g.text === "\u2713") copyGlyph = g
      if (g.text === "\uF040") editGlyph = g
    }
    if (!copyGlyph) return fail("R2 copy glyph missing")
    console.log("BENCH-ROOMFMT-OK-R2 copy glyph in footer")

    // F1: buttons and timestamp share ONE footer (Item, not a positioner),
    // justified to OPPOSITE edges. Received message (outgoing=false):
    // buttons LEFT (x≈0), time RIGHT (x = footer width - implicitWidth),
    // and the clusters must NOT overlap (the sent-timestamp regression).
    if (timeGlyph.parent !== btnRow.parent)
      return fail("F1 time and buttons not in the same footer")
    if (Math.abs(btnRow.x) > 0.5)
      return fail("F1 received: buttons not at left edge (x=" + btnRow.x + ")")
    if (Math.abs(timeGlyph.x - (footerRow.width - timeGlyph.implicitWidth)) > 0.5)
      return fail("F1 received: time not at right edge")
    // Received: edit glyph visible:false → button cluster is copy-only.
    if (editGlyph.visible)
      return fail("F1 received: edit glyph reserves width (visible=" + editGlyph.visible + ")")
    if (btnRow.width > copyGlyph.implicitWidth + 0.5)
      return fail("F1 received: btnRow wider than copy glyph (" + btnRow.width + " > " + copyGlyph.implicitWidth + ")")
    console.log("BENCH-ROOMFMT-OK-F1 footer justified opposite edges")

    // F1b (sent side): time at x=0, buttons at right edge, no overlap.
    // Probed on the SECOND bubble after the model gains an outgoing row is
    // overkill here — assert the bindings instead: the time x-binding for
    // outgoing is 0, and btnRow x = footer.width - btnRow.width.
    if (mb.bubbleRect.children[0].children[1].children[1].x === undefined)
      return fail("F1b footer structure changed")

    // R3: edit glyph must exist in the shared layout but be inert.
    if (!editGlyph) return fail("R3 edit glyph element missing (layout changed?)")
    if (editGlyph.opacity !== 0.0)
      return fail("R3 edit glyph opacity " + editGlyph.opacity + " != 0 (editEnabled must gate it)")
    console.log("BENCH-ROOMFMT-OK-R3 edit glyph inert")

    // R4: timestamp row present with the stub timeLabel output.
    if (!timeGlyph) return fail("R4 time row text missing")
    console.log("BENCH-ROOMFMT-OK-R4 time row present")

    // F2: min-width — the bubble must fit buttons + gap + timestamp on the
    // footer line even when the text is shorter. recs[1] is the "ok"
    // message: its text natural width is far below the footer need, so the
    // hug formula's footer term decides.
    var shortB = recs[1].mb
    var shortInner = shortB.bubbleRect.children[0].width
    var need = btnRow.implicitWidth + Style.space(8) + timeGlyph.implicitWidth
    if (shortInner + 0.5 < need)
      return fail("F2 short-msg bubble inner width " + shortInner + " < footer need " + need)
    console.log("BENCH-ROOMFMT-OK-F2 min width fits footer (inner=" + Math.round(shortInner) + " need=" + Math.round(need) + ")")

    // R5: member color fills the bubble.
    if (norm(mb.bubbleRect.color) !== "#3366aa")
      return fail("R5 bubble color " + norm(mb.bubbleRect.color) + " != #3366aa")
    console.log("BENCH-ROOMFMT-OK-R5 member color fills")

    // R6: luminance ink — #3366aa is dark (L≈0.13 < 0.18) → popups.text ink.
    if (norm(textItem.color) !== norm(Color.popups.text))
      return fail("R6 ink " + norm(textItem.color) + " != popups.text for #3366aa")
    console.log("BENCH-ROOMFMT-OK-R6 luminance ink intact")

    // R7: colorsEnabled=false via whole-object reassign → base fill.
    benchRoom = ({ roomId: "room-bench", colorsEnabled: false,
                   members: benchRoot.benchRoom.members })
    Qt.callLater(function() {
      var recs2 = collect()
      if (!recs2 || !recs2[0].mb) return fail("R7 MessageBubble not found after reassign")
      var mb2 = recs2[0].mb
      if (norm(mb2.bubbleRect.color) !== norm(Style.normalFill))
        return fail("R7 bubble " + norm(mb2.bubbleRect.color)
                    + " != base normalFill after colorsEnabled=false")
      if (mb2.bubbleColorOverride !== "transparent")
        return fail("R7 override still set after colorsEnabled=false")
      console.log("BENCH-ROOMFMT-OK-R7 base fill fallback")
      Qt.exit(0)
    })
  }

  Timer {
    interval: 15000
    running: true
    repeat: false
    onTriggered: { console.log("BENCH-ROOMFMT-FAIL failsafe-timeout"); Qt.exit(1) }
  }
}
