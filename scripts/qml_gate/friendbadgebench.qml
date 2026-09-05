// friendbadgebench: friendBadge width must fit the "✓ Friend" label
// (no right-side clipping) and must not overlap the name Text.
import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 400; height: 600

  property bool done: false
  function fail(msg) { console.log("BENCH-FAIL " + msg); Qt.exit(1) }

  PeerList {
    id: peerListPanel
    anchors.fill: parent

    peerRowH: Style.space(32)
    selectedPeerId: ""
    selectedRoom: ({ members: {} })
    inRoom: false
    showFwAlert: false
    bottomInset: 0
    friendStateFn: function(id) { return id === "peer-1" ? "friend" : "" }
    shortFpFn: function(fp) { return String(fp).substring(0, 6) }
  }

  Component.onCompleted: {
    Qt.callLater(function() {
      if (benchRoot.done) return
      benchRoot.done = true

      // One friend peer; the name is long enough to force elide so we
      // also prove the shrunken name span still clears the badge.
      Lanchat.displayPeers = [
        { id: "peer-1", name: "Alice-With-A-Very-Long-Display-Name", status: "available" }
      ]

      Qt.callLater(function() {
        var list = null
        function findLV(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof ListView) list = c
            findLV(c)
          }
        }
        findLV(peerListPanel)
        if (!list) return fail("ListView not found")
        if (list.count !== 1) return fail("rows=" + list.count + " != 1")
        var row = list.itemAtIndex(0)
        if (!row) return fail("row0 null")

        // Locate the friendBadge Item and its Text label inside the row.
        var badge = null
        function findBadge(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof Text && c.text.indexOf("Friend") !== -1) badge = c.parent
            findBadge(c)
          }
        }
        findBadge(row)
        if (!badge) return fail("friendBadge not found")
        var label = null
        for (var i = 0; i < badge.children.length; i++)
          if (badge.children[i] instanceof Text) label = badge.children[i]
        if (!label) return fail("friend label Text not found")
        if (!label.visible) return fail("friend label not visible")
        if (badge.width < Style.space(30))
          return fail("badge width " + badge.width + " not grown beyond old 30sp cap")
        console.log("BENCH-OK-BADGE-WIDTH " + badge.width)

        // (a) label painted width + 2*pad <= badge width  ->  no clipping.
        var pad = 8
        var lw = Math.max(label.implicitWidth, label.paintedWidth)
        if (lw + 2 * pad > badge.width + 0.5)
          return fail("label " + lw + " + pad > badge width " + badge.width)
        console.log("BENCH-PASS-FRIEND-LABEL-FITS label=" + lw + " badgeW=" + badge.width)

        // (b) name Text's right edge must stay left of badge.left (no overlap).
        var nameText = null
        for (var j = 0; j < row.children.length; j++) {
          var ch = row.children[j]
          if (ch instanceof Text && ch.elide === Text.ElideRight) nameText = ch
        }
        if (!nameText) return fail("name Text not found")
        var nameRight = nameText.x + nameText.width
        if (nameRight > badge.x + 0.5)
          return fail("name right " + nameRight + " overlaps badge left " + badge.x)
        console.log("BENCH-PASS-NAME-CLEARS-BADGE nameRight=" + nameRight + " badgeLeft=" + badge.x)

        // Stranger row sanity: the "＋" button keeps the fixed size.
        Lanchat.displayPeers = [
          { id: "peer-2", name: "Stranger", status: "available" }
        ]
        Qt.callLater(function() {
          var row2 = list.itemAtIndex(0)
          if (!row2) return fail("stranger row null")
          var plusBtn = null
          function findPlus(item) {
            for (var k = 0; k < item.children.length; k++) {
              var c = item.children[k]
              if (c instanceof Button && c.text === "\uFF0B") plusBtn = c
              findPlus(c)
            }
          }
          findPlus(row2)
          if (!plusBtn) return fail("＋ button not found")
          if (plusBtn.width !== Style.space(24))
            return fail("plus button width " + plusBtn.width + " != " + Style.space(24))
          console.log("BENCH-OK-PLUS-BUTTON-SIZE " + plusBtn.width)
          Qt.exit(0)
        })
      })
    })
  }
}
