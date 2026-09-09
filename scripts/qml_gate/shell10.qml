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
        friendRequest: false, held: false }
    ]
    Qt.callLater(verify)
  }

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
    if (msgs.length !== 1) return null
    var rm = msgs[0]
    for (var i = 0; i < rm.children.length; i++) {
      if (rm.children[i].bubbleRect !== undefined) return rm.children[i]
    }
    return null
  }

  function verify() {
    var mb = collect()
    if (!mb) return fail("R1 MessageBubble not found inside RoomMessage")
    console.log("BENCH-ROOMFMT-OK-R1 bubble reachable")

    // R2: copy glyph present.
    var innerCol = mb.bubbleRect.children[0]
    var copyGlyph = null, editGlyph = null, timeGlyph = null, textItem = null
    for (var i = 0; i < innerCol.children.length; i++) {
      var el = innerCol.children[i]
      if (el instanceof Row) {
        for (var k = 0; k < el.children.length; k++) {
          var g = el.children[k]
          if (g.text === "\uF0C5" || g.text === "\u2713") copyGlyph = g
          if (g.text === "\uF040") editGlyph = g
        }
      }
      if (el.text === "12:34") timeGlyph = el
      if (el.text === "hello room") textItem = el
    }
    if (!copyGlyph) return fail("R2 copy glyph missing")
    console.log("BENCH-ROOMFMT-OK-R2 copy glyph present")

    // R3: edit glyph must exist in the shared layout but be inert.
    if (!editGlyph) return fail("R3 edit glyph element missing (layout changed?)")
    if (editGlyph.opacity !== 0.0)
      return fail("R3 edit glyph opacity " + editGlyph.opacity + " != 0 (editEnabled must gate it)")
    console.log("BENCH-ROOMFMT-OK-R3 edit glyph inert")

    // R4: timestamp row present with the stub timeLabel output.
    if (!timeGlyph) return fail("R4 time row text missing")
    console.log("BENCH-ROOMFMT-OK-R4 time row present")

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
      var mb2 = collect()
      if (!mb2) return fail("R7 MessageBubble not found after reassign")
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
