import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "shared"

// Lanchat chat panel: a peer list on the left, the selected peer's thread and
// a compose box on the right. Hosted in a KeyboardPanel window anchored to the
// bar widget; all state comes from the shared Lanchat singleton. The left
// column's footer holds the HTTP API toggle (enable/disable from the UI).
Panel {
  id: root
  moduleName: "KelpME.lanchat"
  ipcTarget: "KelpME.lanchat"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null

  // Width of the left peer column (draggable via the divider). Not persisted.
  property real peerColW: Style.space(280)

  // The conversation currently on screen ("" = none selected).
  property string selectedPeerId: ""

  // The most recent un-accepted incoming attachment for the selected peer.
  readonly property var pendingAttachment: {
    var all = Lanchat.messages
    for (var i = all.length - 1; i >= 0; i--) {
      var m = all[i]
      if (!m.outgoing && m.from === selectedPeerId && m.attachment && !m.attachment.accepted)
        return m.attachment
    }
    return null
  }

  readonly property var selectedPeer: {
    var list = Lanchat.peers
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === selectedPeerId) return list[i]
    }
    return null
  }

  // Live-filtered thread for the selected peer: delivered messages plus any
  // held (DND/offline-queued) outgoing messages.
  readonly property var thread: {
    var out = []
    var all = Lanchat.messages
    for (var i = 0; i < all.length; i++) {
      var m = all[i]
      var mine = m.outgoing && m.to === selectedPeerId
      var theirs = !m.outgoing && m.from === selectedPeerId
      if (mine || theirs) out.push(m)
    }
    // Append held messages (queued because the peer was DND/offline).
    var held = Lanchat.heldQueue
    for (var j = 0; j < held.length; j++) {
      if (held[j].to === selectedPeerId) {
        out.push({ to: selectedPeerId, from: "", fromName: "You", text: held[j].text || "", held: true, outgoing: true, ts: Date.now() })
      }
    }
    return out
  }

  readonly property bool hasThread: thread.length > 0

  // Name of the selected peer if they are currently typing, else "".
  readonly property string typingForPeer: {
    var t = Lanchat.typing[selectedPeerId]
    return t ? String(t) : ""
  }

  // Held (un-sent) messages for the selected peer, for the undo bar.
  readonly property var pendingForPeer: {
    var out = []
    var list = Lanchat.pendingSends
    for (var i = 0; i < list.length; i++) {
      if (list[i].to === selectedPeerId) out.push(list[i])
    }
    return out
  }

  function selectPeer(id) {
    selectedPeerId = id
    Lanchat.resetHistoryMeta(id)
    Lanchat.refreshHistory(id, 0, 50)
    list.positionViewAtEnd()
  }

  function send() {
    var text = input.text.trim()
    if (!text || !selectedPeerId) return
    if (editingMid !== "") {
      Lanchat.editMessage(editingMid, text)
      editingMid = ""
    } else {
      Lanchat.send(selectedPeerId, text)
    }
    input.text = ""
    list.positionViewAtEnd()
  }

  // Pick a file to attach and send it.
  function attachAndSend() {
    if (!selectedPeerId) return
    attachProc.command = ["zenity", "--file-selection", "--title", "Choose file to send"]
    attachProc.running = true
  }

  // Undo a held (un-sent) message.
  function undoPending(mid) {
    Lanchat.undo(mid)
  }

  function clearChat() {
    if (selectedPeerId) Lanchat.clearChat(selectedPeerId)
  }

  function unfriendSelected() {
    if (selectedPeerId) {
      Lanchat.unfriend(selectedPeerId)
      selectedPeerId = ""
    }
  }

  function deleteMsg(mid) {
    Lanchat.deleteMessage(mid)
  }

  // Begin editing a message: load its text into the compose box and flag the
  // next send as an edit of that mid.
  property string editingMid: ""
  property bool diagExpanded: false
  function editMsg(mid, text) {
    editingMid = mid
    input.text = text
    input.forceActiveFocus()
  }

  function pickDownloadDir() {
    dirProc.command = ["zenity", "--file-selection", "--directory", "--title", "Choose download folder"]
    dirProc.running = true
  }

  function timeLabel(ts) {
    var d = new Date(ts)
    return Qt.formatTime(d, "HH:mm")
  }

  function copyToClipboard(text) {
    if (!text) return
    Quickshell.execDetached(["bash", "-c", "printf %s " + Util.shellQuote(text) + " | wl-copy"])
  }

  // Copy all current diagnostic log lines to the clipboard.
  function copyDiagnostics() {
    var lines = []
    for (var i = 0; i < Lanchat.diagnostics.length; i++) {
      lines.push(root.diagLine(Lanchat.diagnostics[i]))
    }
    copyToClipboard(lines.join("\n"))
  }

  // Format one diagnostic entry: timestamp + message + any extra fields.
  function diagLine(d) {
    if (!d) return ""
    var line = root.timeLabel(d.ts) + "  " + d.message
    for (var k in d) {
      if (k !== "ts" && k !== "message") line += "  " + k + "=" + d[k]
    }
    return line
  }

  function isConfirmedFriend(id) {
    var list = Lanchat.friends
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id && list[i].confirmed) return true
    }
    return false
  }

  // Friend relationship of a peer: "friend" (confirmed), "pending" (a request
  // is outstanding in one direction), or "" (stranger). Drives the peer-list
  // indicator so you can see who you're actually friends with.
  function friendState(id) {
    var list = Lanchat.friends
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) return list[i].confirmed ? "friend" : "pending"
    }
    return ""
  }

  function acceptFriend(id) {
    Lanchat.acceptFriend(id)
  }
  function rejectFriend(id) {
    Lanchat.rejectFriend(id)
  }

  // Path to the help document (HELP.md next to the panel).
  function helpPath() {
    var url = Qt.resolvedUrl("HELP.md").toString()
    if (url.indexOf("file://") === 0) url = url.slice(7)
    return decodeURIComponent(url)
  }

  // Open the help document in the default viewer.
  function openHelp() {
    Quickshell.execDetached(["xdg-open", root.helpPath()])
  }

  // Open the daemon diagnostic log (surfaces why peers vanish / messages drop).
  function openLog() {
    Quickshell.execDetached(["xdg-open", Lanchat.logPath()])
  }

  // Last component of a path (folder name).
  function pathBasename(path) {
    var p = String(path || "")
    p = p.replace(/\/+$/, "")
    var idx = p.lastIndexOf("/")
    return idx >= 0 ? p.slice(idx + 1) : p
  }

  // Wraps a right-side options area. If the options are wider than the row,
  // they clip and a trailing "…" appears, hinting the user to widen the column.
  component ClippedOptions: Item {
    id: coRoot
    default property alias options: clipRow.data
    implicitHeight: clipRow.implicitHeight
    // Fill whatever width the parent row gives us on the right side.
    anchors.right: parent.right
    anchors.rightMargin: Style.spacing.sm
    anchors.left: parent.left
    anchors.leftMargin: Style.space(70)   // leave room for the label on the left
    anchors.verticalCenter: parent.verticalCenter
    clip: true

    Row {
      id: clipRow
      anchors.left: parent.left
      spacing: Style.spacing.xs
    }

    Text {
      visible: clipRow.implicitWidth > parent.width
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      text: "\u2026"
      color: Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      z: 2
    }
  }

  onOpenedChanged: {
    Lanchat.panelOpen = root.opened
    if (root.opened) {
      Lanchat.clearUnread()
      if (selectedPeerId === "" && Lanchat.peers.length > 0)
        selectedPeerId = Lanchat.peers[0].id
      Qt.callLater(function() { list.positionViewAtEnd() })
    }
  }

  // Stops the typing indicator on the peer side after idle.
  property Timer typingTimer: Timer {
    interval: 2000
    onTriggered: {
      if (root.selectedPeerId) Lanchat.sendTypingStopped(root.selectedPeerId)
    }
  }

  Component.onCompleted: Lanchat.panelOpen = false

  // File picker for attaching a file to send.
  Process {
    id: attachProc
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(data) {
        var path = String(data).trim()
        if (!path || path === "") return
        root.sendFile(path)
      }
    }
  }

  // Folder picker for the download directory.
  Process {
    id: dirProc
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(data) {
        var dir = String(data).trim()
        if (!dir || dir === "") return
        Lanchat.setDownloadDir(dir)
      }
    }
  }

  // Register an attachment and send it to the selected peer.
  function sendFile(path) {
    if (!selectedPeerId) return
    Lanchat.send(selectedPeerId, "", { name: path.split("/").pop(), path: path })
  }

  // Panel dimensions by size setting, as INDEPENDENT fractions of each screen
  // axis. Width is a fraction of screen width, height a fraction of screen
  // height; X and Y grow incrementally from small to XL. "full" fills the
  // available screen area exactly (what the KeyboardPanel allows), so the card
  // border aligns with the screen edge consistently.
  readonly property real screenW: win.screen ? win.screen.width : 1440
  readonly property real screenH: win.screen ? win.screen.height : 900
  readonly property real wFrac: Lanchat.panelSize === "small" ? 1/2
    : Lanchat.panelSize === "large" ? 4/5
    : Lanchat.panelSize === "xl" ? 9/10
    : 2/3  // medium (also the default)
  readonly property real hFrac: Lanchat.panelSize === "small" ? 0.45
    : Lanchat.panelSize === "large" ? 3/4
    : Lanchat.panelSize === "xl" ? 0.85
    : 3/5  // medium (also the default)
  readonly property int panelW: Lanchat.panelSize === "full"
    ? Math.round(screenW - Style.space(10))
    : Math.round(screenW * wFrac)
  readonly property int panelH: Lanchat.panelSize === "full"
    ? Math.round(screenH - Style.space(10) - Style.space(35))
    : Math.round(screenH * hFrac)

  KeyboardPanel {
    id: win
    anchorItem: root.anchorItem
    bar: root.bar
    owner: root.hostWidget || root
    open: root.opened
    centerOnBar: true
    contentWidth: win.fittedContentWidth(root.panelW)
    contentHeight: win.fittedContentHeight(root.panelH)

    Rectangle {
      width: parent.width
      height: parent.height
      color: Color.popups.background

      // ---- body: peer list + thread -----------------------------------
      Rectangle {
        width: parent.width
        height: parent.height
        color: "transparent"

        Row {
          anchors.fill: parent

          // Left: peer list
          Rectangle {
            width: root.peerColW
            height: parent.height
            color: Util.alpha(Color.foreground, 0.04)

            Item {
              anchors.fill: parent

              // ---- peers list (scrollable) ---------------------------
              ListView {
                id: peerList
                width: parent.width
                anchors.top: parent.top
                anchors.bottom: peersOnlineBar.top
                clip: true
                model: Lanchat.peers
                spacing: Style.spacing.xs
                anchors.topMargin: Style.spacing.sm
                anchors.bottomMargin: Style.spacing.xs
                anchors.leftMargin: Style.spacing.sm
                anchors.rightMargin: Style.spacing.sm

                delegate: Rectangle {
                  required property var modelData
                  width: peerList.width
                  height: Style.space(40)
                  radius: Style.cornerRadius
                  color: modelData.id === root.selectedPeerId
                    ? Style.selectedFill : "transparent"

                  MouseArea {
                    anchors.fill: parent
                    onClicked: root.selectPeer(modelData.id)
                  }

                  Rectangle {
                    visible: modelData.id === root.selectedPeerId
                    width: 3
                    height: parent.height * 0.5
                    radius: 1.5
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    color: Color.accent
                  }

                  // Status dot: colored per the peer's status.
                  Rectangle {
                    width: Style.space(9)
                    height: Style.space(9)
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.sm
                    color: modelData.status === "dnd" ? "#e33"
                      : modelData.status === "away" ? Qt.rgba(0.9,0.7,0.2,1)
                      : modelData.status === "brb" ? Qt.rgba(0.9,0.5,0.3,1)
                      : Color.accent  // available
                  }

                  // Name on the left, status label on the right — spread apart
                  // with generous gaps so the row reads cleanly.
                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.xl
                    anchors.right: friendBadge.left
                    anchors.rightMargin: Style.spacing.md
                    text: modelData.name
                    color: modelData.id === root.selectedPeerId
                      ? Color.accent
                      : Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    elide: Text.ElideRight
                  }

                  // Friend indicator: shows when this peer is a confirmed friend
                  // or has a pending request. Collapses to nothing for strangers.
                  Text {
                    id: friendBadge
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: statusText.left
                    anchors.rightMargin: Style.spacing.sm
                    width: visible ? implicitWidth : 0
                    visible: root.friendState(modelData.id) !== ""
                    text: root.friendState(modelData.id) === "friend" ? "\u2713 Friend" : "\u2026 requested"
                    color: root.friendState(modelData.id) === "friend" ? Color.accent : Color.muted
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.weight: Font.DemiBold
                  }

                  Text {
                    id: statusText
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.sm
                    text: modelData.status === "dnd" ? "\u2715"  // ✕ Do Not Disturb
                      : modelData.status === "away" ? "\u23F0"   // ⏰ Away
                      : modelData.status === "brb" ? "\u23EB"    // ⏫ Be Right Back
                      : "\u2713"                                  // ✓ Available
                    color: Color.muted
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                  }
                }

                Text {
                  visible: Lanchat.peers.length === 0
                  anchors.centerIn: parent
                  width: parent.width - Style.space(24)
                  text: "No peers online. They'll appear here automatically."
                  color: Qt.lighter(Color.muted, 1.6)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.Wrap
                  horizontalAlignment: Text.AlignHCenter
                }
              }

              // ---- peers online: pinned above settings ----------------
              Item {
                id: peersOnlineBar
                width: parent.width
                height: Style.space(26)
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: settings.top

                Text {
                  anchors.left: parent.left
                  anchors.leftMargin: Style.spacing.sm
                  anchors.verticalCenter: parent.verticalCenter
                  text: (Lanchat.onlineCount === 1 ? "1 peer" : Lanchat.onlineCount + " peers") + " online"
                  color: Lanchat.onlineCount > 0 ? Color.accent : Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }

              // ---- settings: collapsible ------------------------------
              Column {
                id: settings
                property bool expanded: true
                width: parent.width
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                // Cap height to the column so the body scrolls instead of
                // overflowing off the app when the panel is short/narrow.
                height: settingsHeader.height + (settings.expanded
                  ? Math.min(settingsBody.contentHeight, Math.max(0, parent.height - settingsHeader.height - Style.space(30)))
                  : 0)

                // header
                Item {
                  id: settingsHeader
                  width: parent.width
                  height: Style.space(26)

                  Rectangle {
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    height: 1
                    color: Color.popups.border
                  }

                  MouseArea {
                    anchors.fill: parent
                    onClicked: settings.expanded = !settings.expanded
                  }

                  Text {
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.sm
                    anchors.verticalCenter: parent.verticalCenter
                    text: settings.expanded ? "Settings ▾" : "Settings ▸"
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.weight: Font.Bold
                  }
                }

                // body (name + online + API + undo + save) — compact rows,
                // scrollable so it never overflows the panel.
                Flickable {
                  id: settingsBody
                  width: parent.width
                  height: Math.min(bodyCol.implicitHeight, parent.height - settingsHeader.height - Style.space(30))
                  visible: settings.expanded
                  contentWidth: width
                  contentHeight: bodyCol.implicitHeight
                  clip: true
                  boundsBehavior: Flickable.StopAtBounds
                  interactive: contentHeight > height

                  Column {
                    id: bodyCol
                    width: parent.width
                    spacing: Style.spacing.xs
                    anchors.top: parent.top

                    // Name row (top)
                    Item {
                      width: parent.width
                    height: Style.space(30)

                    Text {
                      id: nameLabel
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Name"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    TextField {
                      id: nameInput
                      anchors.left: nameLabel.right
                      anchors.leftMargin: Style.spacing.sm
                      anchors.right: rollButton.left
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      maximumLength: 32
                      text: Lanchat.myName
                      placeholderText: "your name"
                      horizontalPadding: Style.space(8)
                      verticalPadding: Style.space(4)
                      onAccepted: Lanchat.setMyName(nameInput.text)
                      onEditingFinished: if (nameInput.text.trim() !== "") Lanchat.setMyName(nameInput.text)
                    }

                    // Re-roll to a fresh random friendly name.
                    Button {
                      id: rollButton
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "\uF046"
                      onClicked: Lanchat.regenerateName()
                    }
                  }

                  // Online row
                  Item {
                    width: parent.width
                    height: Style.space(28)

                    MouseArea {
                      id: tooltipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Online"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    PanelToolTip {
                      visible: tooltipHover.containsMouse
                      text: "Go offline to stop broadcasting and drop inbound messages."
                    }

                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      checked: Lanchat.online
                      onToggled: Lanchat.setOnline(!Lanchat.online)
                    }
                  }

                  // Status row.
                  Item {
                    width: parent.width
                    height: Style.space(28)

                    MouseArea {
                      id: statusTipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Status"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    PanelToolTip {
                      visible: statusTipHover.containsMouse
                      text: "Your status is shown to friends."
                    }

                    ClippedOptions {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm

                      Button { text: "\uF2BD"; tooltipText: "Available"; fontSize: Style.font.body; onClicked: Lanchat.setStatus("available") }
                      Button { text: "\uF1F6"; tooltipText: "Do Not Disturb"; fontSize: Style.font.body; onClicked: Lanchat.setStatus("dnd") }
                      Button { text: "\uF017"; tooltipText: "Away"; fontSize: Style.font.body; onClicked: Lanchat.setStatus("away") }
                      Button { text: "\uF0F4"; tooltipText: "Be Right Back"; fontSize: Style.font.body; onClicked: Lanchat.setStatus("brb") }
                    }
                  }

                  // Message sound row.
                  Item {
                    width: parent.width
                    height: Style.space(28)

                    MouseArea {
                      id: soundTipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Message sound"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    PanelToolTip {
                      visible: soundTipHover.containsMouse
                      text: "Play a chime when a new message arrives."
                    }

                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      checked: Lanchat.soundEnabled
                      onToggled: Lanchat.setSoundEnabled(!Lanchat.soundEnabled)
                    }
                  }

                  // Send typing indicator.
                  Item {
                    width: parent.width
                    height: Style.space(28)
                    MouseArea { id: sendTypingTip; anchors.fill: parent; hoverEnabled: true }
                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Let friends see me typing"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }
                    PanelToolTip {
                      visible: sendTypingTip.containsMouse
                      text: "When on, your friends see \u201Ctyping\u2026\u201D while you type."
                    }
                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      checked: Lanchat.typingEnabled
                      onToggled: Lanchat.setTypingEnabled(!Lanchat.typingEnabled)
                    }
                  }

                  // Show typing indicator.
                  Item {
                    width: parent.width
                    height: Style.space(28)
                    MouseArea { id: showTypingTip; anchors.fill: parent; hoverEnabled: true }
                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Show when friends are typing"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }
                    PanelToolTip {
                      visible: showTypingTip.containsMouse
                      text: "When on, you see \u201C[friend] is typing\u2026\u201D in the chat."
                    }
                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      checked: Lanchat.showTyping
                      onToggled: Lanchat.setShowTyping(!Lanchat.showTyping)
                    }
                  }

                  // Send read receipts.
                  Item {
                    width: parent.width
                    height: Style.space(28)
                    MouseArea { id: sendReadTip; anchors.fill: parent; hoverEnabled: true }
                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Let friends see when I've read"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }
                    PanelToolTip {
                      visible: sendReadTip.containsMouse
                      text: "When on, your friends see a \u2713 on messages you've read."
                    }
                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      checked: Lanchat.readReceiptsEnabled
                      onToggled: Lanchat.setReadReceiptsEnabled(!Lanchat.readReceiptsEnabled)
                    }
                  }

                  // Show read receipts.
                  Item {
                    width: parent.width
                    height: Style.space(28)
                    MouseArea { id: showReadTip; anchors.fill: parent; hoverEnabled: true }
                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Show when friends have read"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }
                    PanelToolTip {
                      visible: showReadTip.containsMouse
                      text: "When on, you see a \u2713 on messages your friends have read."
                    }
                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      checked: Lanchat.showReadReceipts
                      onToggled: Lanchat.setShowReadReceipts(!Lanchat.showReadReceipts)
                    }
                  }

                  // API row
                  Item {
                    width: parent.width
                    height: Style.space(28)

                    MouseArea {
                      id: apiTipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Row {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.spacing.xs

                      Text {
                        text: "API"
                        color: Color.popups.text
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        font.weight: Font.Bold
                      }

                      Text {
                        text: Lanchat.httpEnabled ? ":" + Lanchat.httpPort : "off"
                        color: Lanchat.httpEnabled ? Color.accent : Color.muted
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                      }
                    }

                    PanelToolTip {
                      visible: apiTipHover.containsMouse
                      text: "HTTP API for scripts/agents. On = agents can send messages."
                    }

                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      checked: Lanchat.httpEnabled
                      onToggled: Lanchat.setHttpEnabled(!Lanchat.httpEnabled)
                    }
                  }

                  // API full-access row (agent can read chat data).
                  Item {
                    width: parent.width
                    height: Style.space(28)
                    visible: Lanchat.httpEnabled

                    MouseArea {
                      id: fullTipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Agent full access"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }

                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      checked: Lanchat.apiFullAccess
                      onToggled: Lanchat.setApiFullAccess(!Lanchat.apiFullAccess)
                    }

                    PanelToolTip {
                      visible: fullTipHover.containsMouse
                      text: "On = agent can read your chats, peers, and files. Off = send-only."
                    }
                  }

                  // Send-delay row (undo window).
                  Item {
                    width: parent.width
                    height: Style.space(28)

                    MouseArea {
                      id: undoTipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Undo delay"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    PanelToolTip {
                      visible: undoTipHover.containsMouse
                      text: "Hold messages for N seconds so you can undo them before they send."
                    }

                    TextField {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      width: Style.space(48)
                      text: Lanchat.sendDelay
                      maximumLength: 3
                      horizontalPadding: Style.space(6)
                      verticalPadding: Style.space(4)
                      onEditingFinished: {
                        var v = parseInt(text, 10)
                        if (!isNaN(v)) Lanchat.setSendDelay(v)
                      }
                    }
                  }

                  // Download folder row.
                  Item {
                    width: parent.width
                    height: Style.space(28)

                    MouseArea {
                      id: saveTipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Save to"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    PanelToolTip {
                      visible: saveTipHover.containsMouse
                      text: "Folder where accepted files are saved (default ~/Downloads)."
                    }

                    Row {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.spacing.xs

                      Text {
                        anchors.verticalCenter: parent.verticalCenter
                        width: Style.space(110)
                        text: root.pathBasename(Lanchat.downloadDir)
                        color: Color.popups.text
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideRight
                        maximumLineCount: 1
                        horizontalAlignment: Text.AlignRight
                      }

                      Button {
                        width: Style.space(22)
                        height: Style.space(18)
                        text: "\u2026"
                        fontSize: Style.font.caption
                        onClicked: root.pickDownloadDir()
                      }
                    }
                  }

                  // Size row (small/medium/large panel).
                  Item {
                    width: parent.width
                    height: Style.space(28)

                    MouseArea {
                      id: sizeTipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Panel size"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    PanelToolTip {
                      visible: sizeTipHover.containsMouse
                      text: "S/M/L/XL/F — panel window size. F fills the screen."
                    }

                    ClippedOptions {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm

                      Button { width: Style.space(24); height: Style.space(18); text: "S"; fontSize: Style.font.caption; onClicked: Lanchat.setPanelSize("small") }
                      Button { width: Style.space(24); height: Style.space(18); text: "M"; fontSize: Style.font.caption; onClicked: Lanchat.setPanelSize("medium") }
                      Button { width: Style.space(24); height: Style.space(18); text: "L"; fontSize: Style.font.caption; onClicked: Lanchat.setPanelSize("large") }
                      Button { width: Style.space(28); height: Style.space(18); text: "XL"; fontSize: Style.font.caption; onClicked: Lanchat.setPanelSize("xl") }
                      Button { width: Style.space(24); height: Style.space(18); text: "F"; fontSize: Style.font.caption; onClicked: Lanchat.setPanelSize("full") }
                    }
                  }

                  // Diagnostics section — shows the daemon's diagnostic log
                  // lines inline (peer expiry, dropped messages, send failures).
                  Item {
                    width: parent.width
                    height: Style.space(30)

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Diagnostics"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    // Copy all diagnostic lines to the clipboard.
                    Button {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.space(72)
                      anchors.verticalCenter: parent.verticalCenter
                      text: "\uF0C5"  // nf-fa-copy
                      tooltipText: "Copy all logs"
                      fontSize: Style.font.bodySmall
                      onClicked: root.copyDiagnostics()
                    }

                    // Toggle the diagnostics list open/closed.
                    Button {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: diagExpanded ? "\u25B2 Hide" : "\u25BC Show"
                      fontSize: Style.font.caption
                      onClicked: diagExpanded = !diagExpanded
                    }
                  }

                  // The diagnostic lines, scrollable, newest last.
                  Item {
                    visible: diagExpanded
                    width: parent.width
                    height: Math.min(Lanchat.diagnostics.length > 0
                      ? Math.min(160, Lanchat.diagnostics.length * Style.space(16))
                      : Style.space(20),
                      parent.height)
                    clip: true

                    Flickable {
                      anchors.fill: parent
                      contentHeight: diagCol.implicitHeight
                      Column {
                        id: diagCol
                        width: parent.width
                        spacing: Style.spacing.xs
                        Repeater {
                          model: Lanchat.diagnostics
                          Text {
                            width: parent.width
                            text: root.diagLine(modelData)
                            color: Color.muted
                            font.family: Style.font.family
                            font.pixelSize: Style.font.caption
                            wrapMode: Text.Wrap
                          }
                        }
                      }
                    }
                  }

                  // Version row.
                  Item {
                    width: parent.width
                    height: Style.space(26)

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Version"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    Text {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "v" + Lanchat.version
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }
                  }
                  }
                }
              }
            }
          }

          // Draggable divider between peer list and thread.
          Rectangle {
            width: Style.space(8)
            height: parent.height
            color: "transparent"
            anchors.margins: 0

            Rectangle {
              width: 1
              height: parent.height
              anchors.horizontalCenter: parent.horizontalCenter
              color: Color.popups.border
            }

            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.SizeHorCursor
              property bool dragging: false
              onPressed: dragging = true
              onReleased: dragging = false
              onPositionChanged: {
                // New width = handle's center X within the panel (parent of the Row).
                if (dragging) {
                  var px = parent.mapToItem(parent.parent, mouse.x, 0).x
                  root.peerColW = Math.max(Style.space(140), Math.min(px + Style.space(4), parent.parent.width - Style.space(200)))
                }
              }
            }
          }

          // Right: thread + compose
          Column {
            width: parent.width - root.peerColW - 1
            height: parent.height

            ListView {
              id: list
              width: parent.width
              height: parent.height - composeBox.height
              clip: true
              spacing: Style.spacing.sm
              model: root.thread

              header: Item {
                width: parent.width
                height: root.selectedPeerId ? Style.space(26) : Style.spacing.md
                visible: root.selectedPeerId !== ""

                Text {
                  anchors.left: parent.left
                  anchors.leftMargin: Style.spacing.sm
                  anchors.verticalCenter: parent.verticalCenter
                  visible: root.typingForPeer !== ""
                  text: root.typingForPeer + " is typing…"
                  color: Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.italic: true
                }

                Button {
                  anchors.right: parent.right
                  anchors.rightMargin: Style.spacing.sm
                  anchors.verticalCenter: parent.verticalCenter
                  text: "Unfriend"
                  fontSize: Style.font.caption
                  visible: root.selectedPeerId !== ""
                  onClicked: root.unfriendSelected()
                }
              }
              footer: Item { width: parent.width; height: Style.spacing.lg }

              onCountChanged: Qt.callLater(function() { positionViewAtEnd() })

              // Lazy-load: when scrolled to the top, fetch an older page.
              onAtYBeginningChanged: {
                if (list.atYBeginning && root.selectedPeerId)
                  Lanchat.loadOlder(root.selectedPeerId)
              }
              onContentYChanged: {
                if (contentY <= 2 && root.selectedPeerId)
                  Lanchat.loadOlder(root.selectedPeerId)
              }

              delegate: Column {
                required property var modelData
                width: list.width
                spacing: Style.spacing.xs

                // Meta row: who + when (+ edited marker + read state)
                Text {
                  anchors.left: modelData.outgoing ? undefined : parent.left
                  anchors.right: modelData.outgoing ? parent.right : undefined
                  text: (modelData.outgoing ? "You · " : modelData.fromName + " · ") + root.timeLabel(modelData.ts)
                    + (modelData.edited ? " (edited)" : "")
                    + (modelData.outgoing && modelData.mid && Lanchat.readReceipts[modelData.mid] ? " · ✓" : "")
                  color: Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }

                // Message bubble. The text anchors to fill the bubble with a
                // set padding; the bubble grows with the text (no circular
                // width dependency that used to clip long messages).
                Rectangle {
                  id: bubble
                  // A held handshake request shows as a banner, not a text
                  // bubble — hide the bubble so the content isn't leaked
                  // before the friend accepts.
                  visible: !(modelData.friendRequest && modelData.held)
                  readonly property real bubbleMaxWidth: list.width * 0.8
                  readonly property real bubblePaddingX: Style.space(14)
                  readonly property real bubblePaddingY: Style.space(9)
                  readonly property bool hovered: bubbleMouse.containsMouse
                  property bool copied: false

                  width: Math.min(bubbleMaxWidth, messageText.implicitWidth + bubblePaddingX * 2 + Style.space(20))
                  height: messageText.implicitHeight + bubblePaddingY * 2
                  radius: Math.max(Style.cornerRadius, Style.space(6))
                  anchors.left: modelData.outgoing ? undefined : parent.left
                  anchors.right: modelData.outgoing ? parent.right : undefined
                  border.width: modelData.outgoing ? 0 : 1
                  border.color: Style.normalBorderColor
                  color: modelData.outgoing
                    ? Style.selectedAccentFill
                    : Style.normalFill

                  MouseArea {
                    id: bubbleMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.NoButton
                  }

                  Text {
                    id: messageText
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: parent.bubblePaddingX
                    anchors.rightMargin: parent.bubblePaddingX + Style.space(12)
                    text: modelData.text
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    wrapMode: Text.Wrap
                  }

                  // Held indicator: "!" shown while the message is queued for a
                  // DND/offline peer, with a hover tooltip.
                  Text {
                    visible: modelData.held
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.topMargin: Style.space(3)
                    anchors.leftMargin: Style.space(3)
                    text: "!"
                    color: Color.urgent
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    font.weight: Font.Bold

                    MouseArea {
                      id: heldTipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }
                    PanelToolTip {
                      visible: heldTipHover.containsMouse
                      text: "Held — " + (Lanchat.peerStatus(modelData.to) === "dnd" ? "recipient is on Do Not Disturb" : "recipient is offline") + ". Will send when they're available."
                    }
                  }

                  // Edit button (outgoing messages only), shown on hover.
                  Text {
                    visible: modelData.outgoing && (parent.hovered || root.editingMid === modelData.mid)
                    anchors.top: parent.top
                    anchors.right: parent.right
                    anchors.topMargin: Style.space(5)
                    anchors.rightMargin: Style.space(22)
                    text: "\uF040"
                    color: Color.muted
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    opacity: 0.8
                    MouseArea {
                      anchors.fill: parent
                      onClicked: root.editMsg(modelData.mid, modelData.text)
                    }
                  }

                  // Copy button: small icon in the top-right corner,
                  // revealed on hover. Flashes a checkmark after copying.
                  Text {
                    anchors.top: parent.top
                    anchors.right: parent.right
                    anchors.topMargin: Style.space(5)
                    anchors.rightMargin: Style.space(5)
                    text: parent.copied ? "\u2713" : "\uF0C5"
                    color: parent.copied ? Color.accent : Color.muted
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    visible: parent.hovered || parent.copied
                    opacity: parent.copied ? 1.0 : 0.8

                    MouseArea {
                      anchors.fill: parent
                      onClicked: {
                        root.copyToClipboard(modelData.text)
                        bubble.copied = true
                        copyReset.restart()
                      }
                    }
                  }

                  Timer {
                    id: copyReset
                    interval: 1500
                    onTriggered: bubble.copied = false
                  }
                }

                // Friend request banner (pending handshake, both directions).
                Rectangle {
                  visible: modelData.friendRequest && modelData.held
                  anchors.left: parent.left
                  width: list.width * 0.8
                  height: Style.space(38)
                  radius: Style.cornerRadius
                  color: Style.selectedAccentFill

                  Row {
                    anchors.centerIn: parent
                    spacing: Style.spacing.sm

                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      text: modelData.outgoing
                        ? "Friend request sent — waiting for " + (modelData.toName || "them") + " to accept"
                        : "Friend request from " + modelData.fromName
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }

                    Button {
                      visible: !modelData.outgoing
                      text: "Accept"
                      onClicked: root.acceptFriend(modelData.from)
                    }
                    Button {
                      visible: !modelData.outgoing
                      text: "Reject"
                      onClicked: root.rejectFriend(modelData.from)
                    }
                  }
                }
              }

              Text {
                visible: root.selectedPeer && !root.hasThread
                anchors.centerIn: parent
                width: parent.width - Style.space(24)
                text: root.selectedPeer
                  ? "No messages with " + root.selectedPeer.name + " yet."
                  : ""
                color: Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
              }
            }

            // ---- pending attachment accept bar -------------------------
            Rectangle {
              id: pendingBar
              visible: root.pendingAttachment !== null
              width: parent.width
              height: Style.space(38)
              color: Style.selectedAccentFill
              Rectangle {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: Color.popups.border
              }

              Row {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Style.spacing.sm
                anchors.rightMargin: Style.spacing.sm
                spacing: Style.spacing.sm

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.pendingAttachment
                    ? "Incoming file: " + root.pendingAttachment.name
                    : ""
                  color: Color.popups.text
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }

                Item { width: Style.space(10); height: 1 }

                Button {
                  text: "Save"
                  onClicked: {
                    if (root.pendingAttachment)
                      Lanchat.acceptAttachment(root.pendingAttachment.from, root.pendingAttachment.fileId, root.pendingAttachment.name)
                  }
                }
              }
            }

            // ---- pending message undo (countdown ring) -----------------
            Rectangle {
              visible: root.pendingForPeer.length > 0
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.bottom: composeBox.top
              height: Style.space(40)
              color: Style.pressedFill
              Rectangle {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: Color.popups.border
              }

              Repeater {
                model: root.pendingForPeer
                delegate: Row {
                  required property var modelData
                  anchors.left: parent.left
                  anchors.leftMargin: Style.spacing.sm
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.spacing.sm

                  // Undo button with countdown ring.
                  Rectangle {
                    width: Style.space(26)
                    height: Style.space(26)
                    radius: width / 2
                    color: "transparent"
                    border.width: 2
                    border.color: Color.accent
                    // countdown ring: arc via a Canvas is heavy; use opacity as a
                    // simple visual proxy of remaining fraction.
                    opacity: 0.5 + 0.5 * (modelData.remaining / modelData.total)

                    Text {
                      anchors.centerIn: parent
                      text: "\u21A9"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }

                    MouseArea {
                      anchors.fill: parent
                      onClicked: root.undoPending(modelData.mid)
                    }
                  }

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Pending… " + Math.ceil(modelData.remaining) + "s"
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                }
              }
            }

            // ---- compose box -----------------------------------------
            Rectangle {
              id: composeBox
              width: parent.width
              height: Style.space(58)
              color: "transparent"
              Rectangle {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: Color.popups.border
              }

              Item {
                anchors.fill: parent
                anchors.leftMargin: Style.spacing.sm
                anchors.rightMargin: Style.spacing.sm

                // Attachment button.
                Button {
                  id: attachBtn
                  width: Style.space(34)
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  text: "\uF0C6"  // nf-fa-paperclip
                  onClicked: root.attachAndSend()
                  enabled: root.selectedPeer !== null
                }

                TextField {
                  id: input
                  anchors.left: attachBtn.right
                  anchors.leftMargin: Style.spacing.sm
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.topMargin: Style.spacing.md
                  anchors.bottomMargin: Style.spacing.md
                  placeholderText: root.selectedPeer
                    ? "Message " + root.selectedPeer.name + "…"
                    : "Select a peer to chat"
                  enabled: root.selectedPeer !== null
                  onAccepted: root.send()
                  onTextChanged: {
                    if (root.selectedPeerId && text.length > 0) {
                      Lanchat.sendTyping(root.selectedPeerId)
                      typingTimer.restart()
                    } else {
                      if (root.selectedPeerId) Lanchat.sendTypingStopped(root.selectedPeerId)
                      typingTimer.stop()
                    }
                  }
                  onEditingFinished: {
                    if (root.selectedPeerId) Lanchat.sendTypingStopped(root.selectedPeerId)
                    typingTimer.stop()
                  }
                }
              }
            }
          }
        }
      }

      // ---- transient status banner (top, so it never hides the input) --
      Rectangle {
        visible: Lanchat.statusMessage !== ""
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.topMargin: Style.space(46)
        height: Style.space(26)
        color: Style.pressedFill
        Text {
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          anchors.leftMargin: Style.spacing.panelPadding
          anchors.rightMargin: Style.spacing.panelPadding
          text: Lanchat.statusMessage
          color: Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }
  }
}
