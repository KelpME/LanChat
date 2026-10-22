// Compose input height bench (inputLoader zero-height bug): instantiate the
// real ComposeBox with panelVisible: true and assert the loaded input
// TextField has nonzero height and a sane width. RED = height 0 / not loaded.
import QtQuick
import qs.Commons
import qs.Ui
import "shared"

Item {
  id: benchRoot
  width: 500; height: 200

  function fail(msg) { console.log("BENCH-COMPOSE-FAIL " + msg); Qt.exit(1) }

  ComposeBox {
    id: composeBox
    anchors.fill: parent
    panelVisible: true
    selectedPeer: null
    inRoom: false
  }

  Component.onCompleted: Qt.callLater(check)

  function check() {
    const ld = null
    // Locate inputLoader by walking the tree for a Loader with an item that is a TextField.
    let found = null
    function walk(item) {
      if (found) return
      if (item instanceof Loader && item.item && item.item.toString().indexOf("TextField") !== -1) { found = item; return }
      for (let i = 0; i < item.children.length; i++) walk(item.children[i])
    }
    walk(composeBox)
    if (!found) { fail("inputLoader/TextField not found in tree"); return }
    if (found.height <= 0) { fail("inputLoader height is " + found.height + " (must be > 0)"); return }
    if (found.item.height <= 0) { fail("TextField height is " + found.item.height); return }
    if (found.width <= 0) { fail("inputLoader width is " + found.width); return }
    console.log("BENCH-COMPOSE-PASS loader h=" + found.height + " w=" + found.width)
    Qt.exit(0)
  }
}
