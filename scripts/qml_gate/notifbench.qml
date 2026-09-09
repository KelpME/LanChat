// notifbench: friend-request banner buttons must not overflow the banner's
// right edge (Reject was clipped by notifBanner's clip:true) and Accept must
// not overlap Reject.
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
    friendStateFn: function(id) { return "" }
    shortFpFn: function(fp) { return String(fp).substring(0, 6) }
  }

  Component.onCompleted: {
    Qt.callLater(function() {
      if (benchRoot.done) return
      benchRoot.done = true

      peerListPanel.notifExpanded = true  // defaults true; set explicitly for the gate
      // One INCOMING friend request (same fields the real singleton's
      // friend-request event handler builds for upsertFriendRequest).
      Lanchat.friendRequests = [
        { peerId: "req-1", name: "Bob", outgoing: false,
          ts: Date.now(), mid: "", fingerprint: "req-1" }
      ]

      Qt.callLater(function() {
        var banner = null
        function findBanner(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c.objectName === "notifBanner") banner = c
            findBanner(c)
          }
        }
        findBanner(peerListPanel)
        if (!banner) return fail("notifBanner not found")
        if (!banner.visible || banner.height <= 0)
          return fail("banner not visible (h=" + banner.height + ")")

        var reject = null
        function findBtn(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof Button && c.text === "Reject") reject = c
            findBtn(c)
          }
        }
        findBtn(banner)
        if (!reject) return fail("Reject button not found in banner")
        if (!reject.visible) return fail("Reject button not visible")

        // (a) Reject maps to real coordinates inside the banner.
        var p = reject.mapToItem(banner, 0, 0)
        if (!isFinite(p.x) || !isFinite(p.y))
          return fail("Reject mapToItem not finite")
        console.log("BENCH-OK-REJECT-COORDS x=" + p.x + " y=" + p.y + " w=" + reject.width)

        // (b) Reject's right edge must stay inside the banner (no clipping).
        var rejectRight = p.x + reject.width
        if (rejectRight > banner.width + 0.5)
          return fail("Reject right " + rejectRight + " overflows banner width " + banner.width)
        console.log("BENCH-PASS-NO-OVERFLOW rejectRight=" + rejectRight + " bannerW=" + banner.width)

        // (c) Accept's right edge must stay left of Reject's left edge.
        var accept = null
        function findAcc(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof Button && c.text === "Accept") accept = c
            findAcc(c)
          }
        }
        findAcc(banner)
        if (!accept) return fail("Accept button not found in banner")
        var ap = accept.mapToItem(banner, 0, 0)
        var acceptRight = ap.x + accept.width
        if (acceptRight > p.x + 0.5)
          return fail("Accept right " + acceptRight + " overlaps Reject left " + p.x)
        console.log("BENCH-PASS-NO-OVERLAP acceptRight=" + acceptRight + " rejectLeft=" + p.x)

        Qt.exit(0)
      })
    })
  }
}
