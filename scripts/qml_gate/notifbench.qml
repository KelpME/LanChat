// notifbench: friend-request banner buttons must not overflow the banner's
// right edge (Reject was clipped by notifBanner's clip:true) and Accept must
// not overlap Reject.
//
// Multi-request regression (2026-09): TWO simultaneous incoming requests must
// BOTH render (count label + both Reject buttons in bounds), and
// reconcileFriendRequests must keep a request whose peer has NO friends entry
// (the UDP bootstrap path never adds one) and drop it only once the peer is
// confirmed. The old rule ("kept only while an UNCONFIRMED friends entry
// exists") failed that: the second friends event wiped the first request.
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
      // TWO INCOMING friend requests (same fields the real singleton's
      // friend-request event handler builds for upsertFriendRequest).
      Lanchat.friendRequests = [
        { peerId: "req-1", name: "Bob", outgoing: false,
          ts: Date.now(), mid: "", fingerprint: "req-1" },
        { peerId: "req-2", name: "Carol", outgoing: false,
          ts: Date.now(), mid: "", fingerprint: "req-2" }
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

        var rejects = []
        function findBtn(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof Button && c.text === "Reject") rejects.push(c)
            findBtn(c)
          }
        }
        findBtn(banner)

        // (M1) count label says "2 friend requests"
        var countLabel = null
        function findCountLabel(item) {
          for (var i = 0; i < item.children.length; i++) {
            var c = item.children[i]
            if (c instanceof Text && c.text.indexOf("friend requests") >= 0)
              countLabel = c
            findCountLabel(c)
          }
        }
        findCountLabel(banner)
        if (!countLabel)
          return fail("count label not found in banner")
        if (countLabel.text.indexOf("2 friend requests") < 0)
          return fail("count label says '" + countLabel.text + "', expected '2 friend requests'")
        console.log("BENCH-OK-M1 count label: '" + countLabel.text + "'")

        // (M2) BOTH Reject buttons render and map inside the banner
        if (rejects.length !== 2)
          return fail("expected 2 Reject buttons in banner, found " + rejects.length)
        for (var ri = 0; ri < rejects.length; ri++) {
          var rj = rejects[ri]
          if (!rj.visible) return fail("Reject #" + ri + " not visible")
          var rp = rj.mapToItem(banner, 0, 0)
          if (!isFinite(rp.x) || !isFinite(rp.y))
            return fail("Reject #" + ri + " mapToItem not finite")
          if (rp.x + rj.width > banner.width + 0.5)
            return fail("Reject #" + ri + " right " + (rp.x + rj.width)
                        + " overflows banner width " + banner.width)
        }
        console.log("BENCH-OK-M2 two Reject buttons render inside banner bounds")

        // (M3/M4 reconcile semantics moved to reconcilebench.qml, which drives
        // the REAL singleton code — the stub has no reconcileFriendRequests.)

        // (G) legacy single-request geometry asserts unchanged: first visible
        // Reject in bounds, Accept left of it.
        var reject = rejects[0]
        var p = reject.mapToItem(banner, 0, 0)
        console.log("BENCH-OK-REJECT-COORDS x=" + p.x + " y=" + p.y + " w=" + reject.width)
        var rejectRight = p.x + reject.width
        if (rejectRight > banner.width + 0.5)
          return fail("Reject right " + rejectRight + " overflows banner width " + banner.width)
        console.log("BENCH-PASS-NO-OVERFLOW rejectRight=" + rejectRight + " bannerW=" + banner.width)

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
