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

  // Width of the left peer column (draggable via the divider). Persisted so
  // the layout survives a restart. Mid-drag, the live width comes from
  // dragPeerColW; otherwise it falls back to the persisted value (or the UI
  // default when the user has never moved the divider).
  property real dragPeerColW: 0
  readonly property real peerColW: dragPeerColW > 0
    ? dragPeerColW
    : (Lanchat.peerColW > 0 ? Lanchat.peerColW : Style.space(280))

  // Peer-list row height — single source of truth for the delegate rendering.
  property real peerRowH: Style.space(40)

  // The conversation currently on screen ("" = none selected).
  property string selectedPeerId: ""

  // ---- group chat rooms ---------------------------------------------------
  // selectedRoomId and selectedPeerId are mutually exclusive: selecting a
  // room clears the peer chat and vice versa. Room rendering keys off
  // selectedRoomId !== "".
  // readonly property string selectedRoomId: Lanchat.selectedRoomId (mirrored
  // on the singleton so BarWidget/unread logic can see it too)

  // The room snapshot currently on screen (null when no room selected).
  readonly property var selectedRoom: Lanchat.roomStates[Lanchat.selectedRoomId] || null

  // Am I the owner of the open room? (Owner-only controls in the roster.)
  readonly property bool amRoomOwner: !!root.selectedRoom
    && Lanchat.myId !== "" && root.selectedRoom.owner === Lanchat.myId

  // Live-filtered thread for the selected ROOM (messages carry room=roomId).
  readonly property var roomThread: {
    var out = []
    var all = Lanchat.roomMessages
    for (var i = 0; i < all.length; i++)
      if (all[i].room === Lanchat.selectedRoomId) out.push(all[i])
    return out
  }

  // The stage-then-confirm pending attachments list is shared with 1:1 sends;
  // in a room it sends roomFile messages instead (see send()).
  readonly property bool inRoom: Lanchat.selectedRoomId !== ""

  // Add a peer to the open room (the ＋ badge and the roster picker both use
  // this; the daemon enforces friend/owner rules on its end).
  function addPeerToRoom(peerId) {
    if (peerId !== "" && Lanchat.selectedRoomId !== "")
      Lanchat.roomAdd(Lanchat.selectedRoomId, peerId)
  }

  // The current Omarchy theme's palette for the room color picker: the
  // canonical token set the daemon-side color records reference. Swatches
  // resolve to the VIEWER's theme values (all offered, none filtered —
  // approved point 5). Values mirror the same keys colors.toml defines.
  readonly property var themePalette: [
    { token: "accent", hex: String(Color.accent) },
    { token: "foreground", hex: String(Color.foreground) },
    { token: "muted", hex: String(Color.muted) },
    { token: "background", hex: String(Color.background) },
    { token: "urgent", hex: String(Color.urgent) },
    { token: "red", hex: "#D35F5F" },
    { token: "orange", hex: "#c63d3d" },
    { token: "yellow", hex: "#FFC107" },
    { token: "green", hex: "#FFC107" },
    { token: "cyan", hex: "#eaeaea" },
    { token: "blue", hex: "#f59e0b" },
    { token: "magenta", hex: "#B91C1C" },
    { token: "brown", hex: "#631e1e" }
  ]

  // Does this user own at least one room? (Gates the Settings kill-switch.)
  readonly property bool ownsAnyRoom: {
    var mine = Lanchat.myId
    for (var i = 0; i < Lanchat.rooms.length; i++)
      if (Lanchat.rooms[i].owner === mine) return true
    return false
  }

  // The room-state snapshot of a room I own — the kill-switch applies to the
  // currently OPEN owned room, else the first owned room.
  readonly property var selectedOwnedRoom: {
    var mine = Lanchat.myId
    var snap = Lanchat.roomStates[Lanchat.selectedRoomId]
    if (snap && snap.owner === mine) return snap
    for (var k in Lanchat.roomStates)
      if (Lanchat.roomStates[k].owner === mine) return Lanchat.roomStates[k]
    return null
  }

  // The most recent un-accepted incoming attachment for the selected peer.
  // Returns the whole message (carries `mid` + the attachment metadata) so the
  // accept bar can echo the message id back for accepted-marking.
  readonly property var pendingAttachment: {
    var all = Lanchat.messages
    for (var i = all.length - 1; i >= 0; i--) {
      var m = all[i]
      if (!m.outgoing && m.from === selectedPeerId && m.attachment && !m.attachment.accepted)
        return m
    }
    return null
  }

  // True while the in-flight download matches THIS pending attachment (so a
  // download in another conversation can't light up this peer's Save bar).
  readonly property bool pendingDownloading: {
    var p = root.pendingAttachment
    return Lanchat.dlActive && !!p && Lanchat.dlFileId === p.attachment.fileId
  }

  readonly property var selectedPeer: {
    var list = Lanchat.displayPeers
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === selectedPeerId) return list[i]
    }
    return null
  }

  // Live-filtered thread for the selected peer. The daemon is the single
  // source of truth for delivery — it holds undelivered messages server-side
  // and flushes on reconnect — so the client only renders delivered messages.
  readonly property var thread: {
    var out = []
    var all = Lanchat.messages
    for (var i = 0; i < all.length; i++) {
      var m = all[i]
      var mine = m.outgoing && m.to === selectedPeerId
      var theirs = !m.outgoing && m.from === selectedPeerId
      if (mine || theirs) out.push(m)
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
    root.confirmUnfriend = false
    selectedPeerId = id
    Lanchat.resetHistoryMeta(id)
    Lanchat.refreshHistory(id, 0, 50)
    list.positionViewAtEnd()
  }

  function send() {
    var text = input.text.trim()
    // Allow sending attachments with no text when any are staged.
    if (!text && root.pendingCount === 0) return
    // Room send: text goes to the room; staged files each become a roomFile
    // (same stage-then-confirm UX, no auto-send — approved point 11).
    if (root.inRoom) {
      if (root.pendingCount > 0) {
        for (var r = 0; r < root.pendingAttachments.length; r++) {
          var ra = root.pendingAttachments[r]
          Lanchat.sendRoomFile(Lanchat.selectedRoomId, ra.path, ra.name)
          if (r === 0 && text) Lanchat.sendRoom(Lanchat.selectedRoomId, text)
        }
      } else {
        Lanchat.sendRoom(Lanchat.selectedRoomId, text)
      }
      input.text = ""
      root.pendingAttachments = []
      list.positionViewAtEnd()
      return
    }
    if (!selectedPeerId) return
    if (editingMid !== "") {
      Lanchat.editMessage(editingMid, text)
      editingMid = ""
    } else if (root.pendingCount > 0) {
      // Send each staged file as its own attachment message; the typed text
      // rides on the first one.
      for (var i = 0; i < root.pendingAttachments.length; i++) {
        var attach = root.pendingAttachments[i]
        Lanchat.send(selectedPeerId, i === 0 ? text : "", attach)
      }
    } else {
      Lanchat.send(selectedPeerId, text)
    }
    input.text = ""
    root.pendingAttachments = []
    list.positionViewAtEnd()
  }

  // Pick a file to attach and send it. The panel is a full-screen
  // dismiss-on-outside-click popup (KeyboardPanel), so an external picker
  // window would never receive a click — the overlay swallows it and closes
  // the panel. Close the panel first so zenity runs without an overlay above
  // it, then reopen (and send) when the picker returns.
  function attachAndSend() {
    if (!selectedPeerId && !root.inRoom) return
    attachProc.command = ["zenity", "--file-selection", "--multiple", "--separator", "|", "--title", "Choose files to send"]
    root.close()
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
    root.confirmUnfriend = false
    if (selectedPeerId) {
      Lanchat.unfriend(selectedPeerId)
      selectedPeerId = ""
    }
  }

  // Deselect the active peer so no chat shows on the right. Also stops the
  // typing indicator and abandons any in-progress edit.
  function closeChat() {
    root.confirmUnfriend = false
    if (selectedPeerId) Lanchat.sendTypingStopped(selectedPeerId)
    editingMid = ""
    selectedPeerId = ""
    Lanchat.selectedRoomId = ""
  }

  // Select a room (mutually exclusive with the peer chat) and pull its history.
  function selectRoom(roomId) {
    root.confirmUnfriend = false
    if (selectedPeerId) Lanchat.sendTypingStopped(selectedPeerId)
    editingMid = ""
    selectedPeerId = ""
    Lanchat.selectRoom(roomId)
  }

  function leaveSelectedRoom() {
    if (Lanchat.selectedRoomId) Lanchat.roomLeave(Lanchat.selectedRoomId)
    Lanchat.selectedRoomId = ""
  }

  // Commit the display-name field if it holds a non-empty value. Guarded so a
  // transient empty state (field cleared mid-edit) doesn't wipe the name; only
  // a real value is saved.
  function commitName() {
    var t = nameInput.text.trim()
    if (t !== "") Lanchat.setMyName(t)
  }

  function deleteMsg(mid) {
    Lanchat.deleteMessage(mid)
  }

  // Begin editing a message: load its text into the compose box and flag the
  // next send as an edit of that mid.
  property string editingMid: ""
  property bool diagExpanded: false
  // Whether the friend-request notifications banner is expanded (dropdown).
  property bool notifExpanded: true

  // (1.3) First-run onboarding: shows a dismissible banner below the peer list
  // explaining that Lanchat is invisible by default. Dismissed state is local
  // to this session (not persisted) — keeps the first use obvious without a
  // permanent config flag.
  property bool showOnboarding: true

  // Friend requests accept in a single step — the requester's verified
  // fingerprint (first 6 digits) is shown inline on the request banner, and
  // Accept confirms immediately. No separate confirm page.


  // Two-step confirm for the "Clear all chats" action. Resets after a couple
  // of seconds so the button doesn't stay armed.
  property bool confirmClearAll: false
  property Timer clearConfirmTimer: Timer {
    interval: 2500
    onTriggered: root.confirmClearAll = false
  }

  // Two-step guard on the thread header's Unfriend button: the first click
  // arms a "Confirm?" state (auto-disarms after a few seconds so it doesn't
  // stay hot); the second click actually unfriends. Mirrors the "Clear all
  // chats" confirm pattern.
  property bool confirmUnfriend: false
  property Timer unfriendConfirmTimer: Timer {
    interval: 2500
    onTriggered: root.confirmUnfriend = false
  }

  // Shows a brief checkmark next to the "Clear all chats" button after a
  // successful clear, instead of a full-width banner.
  property bool showClearAllCheck: false
  property Timer clearAllCheckTimer: Timer {
    interval: 2500
    onTriggered: root.showClearAllCheck = false
  }

  // True when there's a chat alert to show in the thread's alert bar for the
  // currently selected peer (or a peer-agnostic one).
  readonly property bool visibleChatAlert: {
    Lanchat.chatAlert !== "" &&
    (Lanchat.chatAlertPeerId === "" || Lanchat.chatAlertPeerId === selectedPeerId)
  }

  // Show the persistent firewall warning in the peers-online bar: only when
  // the daemon is actually running AND the port is confirmed blocked. If the
  // daemon is down (that's its own alert) or the port is open/unknown, don't
  // show a redundant firewall warning.
  readonly property bool showFwAlert: {
    Lanchat.daemonState === "running" && Lanchat.firewall.open === false
  }

  // Outgoing attachments staged in the compose area, NOT yet sent. Picking
  // files appends here; the user reviews each, removes any, then presses Send.
  // [{name, path}]
  property var pendingAttachments: []

  // True if an attachment path looks like an image by extension, so the
  // compose preview can show a thumbnail.
  function isImagePath(p) {
    if (!p) return false
    var ext = String(p).split(".").pop().toLowerCase()
    return ["png","jpg","jpeg","gif","bmp","webp","svg","avif"].indexOf(ext) >= 0
  }
  readonly property int pendingCount: root.pendingAttachments.length

  // Preview geometry: a header row (count + Send/Cancel) plus a scrollable
  // list of removable rows, capped at a few visible before it scrolls.
  readonly property real pendingHeaderH: Style.space(40)
  readonly property real pendingRowH: Style.space(44)
  readonly property int pendingMaxVisible: 4
  readonly property real pendingListH: Math.min(root.pendingCount, root.pendingMaxVisible) * root.pendingRowH

  // Append one or more picked paths to the staged list, skipping duplicates.
  // `paths` may be a single path string or an array.
  function stagePaths(paths) {
    var list = root.pendingAttachments.slice()
    if (typeof paths === "string") paths = [paths]
    for (var i = 0; i < paths.length; i++) {
      var p = String(paths[i] || "").trim()
      if (!p) continue
      var dup = false
      for (var j = 0; j < list.length; j++) {
        if (list[j].path === p) { dup = true; break }
      }
      if (!dup) list.push({ name: p.split("/").pop(), path: p })
    }
    root.pendingAttachments = list
  }

  // Remove one staged file from the preview (before sending).
  function removeAttachment(index) {
    var list = root.pendingAttachments.slice()
    list.splice(index, 1)
    root.pendingAttachments = list
  }

  // Clear all staged attachments (cancel).
  function cancelAttachments() {
    root.pendingAttachments = []
  }

  function editMsg(mid, text) {
    editingMid = mid
    input.text = text
    input.forceActiveFocus()
  }

  function pickDownloadDir() {
    dirProc.command = ["zenity", "--file-selection", "--directory", "--title", "Choose download folder"]
    root.close()
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
  // Withdraw a friend request WE sent that is still pending.
  function cancelFriend(id) {
    Lanchat.cancelFriendRequest(id)
  }

  // Shorten a cert fingerprint to its first 6 hex digits for a quick identity
  // spot-check without the 64-char string overflowing the narrow panel.
  function shortFp(fp) {
    var s = String(fp || "")
    if (s.length <= 6) return s
    return s.slice(0, 6)
  }

  // (1.3) Read the "Add friend by fingerprint" field and add the friend.
  // The friend's address is learned automatically from discovery; the
  // fingerprint is the peer's verified identity, so no manual address is
  // needed (and would be overkill / error-prone to enter by hand).
  function addFriendByFingerprint() {
    var fid = addFrInput ? addFrInput.text.trim() : ""
    if (!fid) return
    Lanchat.addFriendByFingerprint(fid)
    addFrInput.text = ""
  }

  // Path to the help document (HELP.html next to the panel). HTML is used so
  // the default browser handles it — .md has no reliable xdg handler, which is
  // why the help button used to do nothing on some systems.
  function helpPath() {
    var url = Qt.resolvedUrl("HELP.html").toString()
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

  // Shorten a filesystem path for display: always keep at least the folder
  // and its immediate parent visible (e.g. /home/tmo/Downloads stays, a deeper
  // /data/a/b/c/downloads becomes …/b/c/downloads). The leading part is
  // collapsed so the tail — the part you actually care about — stays readable.
  function shortPath(path) {
    var p = String(path || "")
    p = p.replace(/\/+$/, "")
    if (!p) return ""
    var parts = p.split("/").filter(function(s) { return s !== "" })
    if (parts.length <= 2) return p
    // Keep the last two components, prefix with an ellipsis.
    return "…/" + parts.slice(parts.length - 2).join("/")
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
      anchors.right: parent.right
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
      // Refresh firewall state so the peers-online alert is current.
      Lanchat.refreshFirewall()
      if (selectedPeerId === "" && Lanchat.displayPeers.length > 0)
        selectedPeerId = Lanchat.displayPeers[0].id
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
        var line = String(data).trim()
        if (!line || line === "") return
        // zenity --multiple returns paths joined by "|".
        root.stagePaths(line.split("|"))
      }
    }
    // Reopen the panel once the picker is done (file chosen OR cancelled) so
    // the user lands back in the chat either way.
    onExited: function(exitCode) {
      root.open()
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
    onExited: function(exitCode) {
      root.open()
    }
  }

  // Register an attachment and send it to the selected peer.
  function sendFile(path) {
    root.stagePaths(path)
  }

  // Create-room inline mini-dialog state (rendered inside the KeyboardPanel
  // content, so no external modal is needed).
  property bool showRoomNameDialog: false
  property string newRoomName: ""
  function commitCreateRoom() {
    var n = roomNameField ? roomNameField.text.trim() : ""
    if (n !== "") {
      Lanchat.createRoom(n)
      roomsSection.expanded = true
    }
    showRoomNameDialog = false
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

  // Manual size override, persisted in Lanchat (0 = follow the preset
  // fractions). The preset buttons and W×H boxes write through
  // Lanchat.setCustomSize so the size survives a restart.
  function fracFor(size, axis) {
    if (axis === "w") return size === "small" ? 1/2 : size === "large" ? 4/5 : size === "xl" ? 9/10 : 2/3
    return size === "small" ? 0.45 : size === "large" ? 3/4 : size === "xl" ? 0.85 : 3/5
  }

  function applyPanelSize(size) {
    Lanchat.setPanelSize(size)
    if (size === "full") {
      Lanchat.setCustomSize(Math.round(root.screenW - Style.space(10)),
                            Math.round(root.screenH - Style.space(10) - Style.space(35)))
    } else {
      Lanchat.setCustomSize(Math.round(root.screenW * root.fracFor(size, "w")),
                            Math.round(root.screenH * root.fracFor(size, "h")))
    }
  }

  function applyManualSize(wText, hText) {
    var wv = parseInt(wText, 10)
    var hv = parseInt(hText, 10)
    var w = (!isNaN(wv) && wv > 0) ? wv : Lanchat.customW
    var h = (!isNaN(hv) && hv > 0) ? hv : Lanchat.customH
    Lanchat.setCustomSize(w, h)
  }

  readonly property int panelW: Lanchat.customW > 0 ? Lanchat.customW
    : Lanchat.panelSize === "full"
      ? Math.round(screenW - Style.space(10))
      : Math.round(screenW * wFrac)
  readonly property int panelH: Lanchat.customH > 0 ? Lanchat.customH
    : Lanchat.panelSize === "full"
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

      // ---- create-room inline mini-dialog (rooms header ＋ button) ------
      // Rendered INSIDE the panel (no external modal — the KeyboardPanel
      // overlay would swallow clicks on an external window).
      Rectangle {
        id: roomNameDialog
        visible: root.showRoomNameDialog
        anchors.centerIn: parent
        width: Style.space(280)
        height: Style.space(120)
        radius: Math.max(Style.cornerRadius, Style.space(6))
        color: Color.popups.background
        border.width: 1
        border.color: Color.popups.border
        z: 10

        Column {
          anchors.fill: parent
          anchors.margins: Style.space(12)
          spacing: Style.space(8)

          Text {
            text: "Create a room"
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            font.weight: Font.DemiBold
          }

          TextField {
            id: roomNameField
            width: parent.width
            placeholderText: "room name"
            focus: root.showRoomNameDialog
            onAccepted: root.commitCreateRoom()
          }

          Row {
            spacing: Style.space(6)
            Button {
              text: "Create"
              fontSize: Style.font.caption
              onClicked: root.commitCreateRoom()
            }
            Button {
              text: "Cancel"
              fontSize: Style.font.caption
              onClicked: { root.newRoomName = ""; root.showRoomNameDialog = false }
            }
          }
        }
        // Bind the field to the root state while the dialog is open.
        onVisibleChanged: if (visible) roomNameField.text = ""
      }

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
              // Clicking blank space in the peer list (below the last row,
              // or anywhere when no peers are listed) deselects the active
              // peer so no chat shows on the right. This area sits BEHIND
              // the ListView, so clicks on an actual peer row still land on
              // the row's own MouseArea and select it; only clicks that fall
              // through (empty space) reach here and close the conversation.
              // The ListView is only interactive (grabber) when it actually
              // overflows — exactly when rows fill the column and no blank
              // space exists to click.
              MouseArea {
                id: peerListBlankArea
                anchors.top: notifBanner.bottom
                anchors.topMargin: Style.spacing.sm
                anchors.bottom: roomsSection.top
                anchors.bottomMargin: Style.spacing.xs
                anchors.left: parent.left
                anchors.right: parent.right
                visible: root.selectedPeerId !== ""
                onClicked: root.closeChat()
              }

              ListView {
                id: peerList
                width: parent.width
                anchors.top: notifBanner.bottom
                anchors.topMargin: Style.spacing.sm
                anchors.leftMargin: Style.spacing.sm
                anchors.rightMargin: Style.spacing.sm
                anchors.bottom: roomsSection.top
                anchors.bottomMargin: Style.spacing.xs
                clip: true
                interactive: peerList.contentHeight > peerList.height
                model: Lanchat.displayPeers
                spacing: Style.spacing.xs

                delegate: Rectangle {
                  required property var modelData
                  width: peerList.width
                  height: root.peerRowH
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

                  // Status dot: colored per the peer's status; gray when offline.
                  Rectangle {
                    width: Style.space(9)
                    height: Style.space(9)
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.sm
                    color: modelData.status === "offline" ? Color.muted
                      : modelData.status === "dnd" ? "#e33"
                      : modelData.status === "away" ? Qt.rgba(0.9,0.7,0.2,1)
                      : modelData.status === "brb" ? Qt.rgba(0.9,0.5,0.3,1)
                      : Color.accent  // available
                  }

                  // Name on the left, status label on the right — spread apart
                  // with generous gaps so the row reads cleanly.
                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    // Clear the status dot: dot starts at spacing.sm and is
                    // space(9) wide, so the name begins past it.
                    anchors.leftMargin: Style.spacing.sm + Style.space(9) + Style.spacing.xs
                    anchors.right: roomAddBadge.visible ? roomAddBadge.left : friendBadge.left
                    anchors.rightMargin: Style.spacing.md
                    text: modelData.name
                    color: modelData.id === root.selectedPeerId
                      ? Color.accent
                      : Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    elide: Text.ElideRight
                  }

                  // Room-add control: a "＋" on CONFIRMED FRIEND rows only,
                  // visible only while a room roster is open, only for peers
                  // not already in that room. One click proposes/adds them —
                  // same roomAdd path as the roster drag/picker.
                  Button {
                    id: roomAddBadge
                    readonly property bool inSelectedRoom:
                      root.selectedRoom && (modelData.id in root.selectedRoom.members)
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: friendBadge.left
                    anchors.rightMargin: Style.spacing.xs
                    visible: root.inRoom
                             && Lanchat.isConfirmedFriend(modelData.id)
                             && !inSelectedRoom
                    text: "＋"
                    fontSize: Style.font.caption
                    foreground: Color.accent
                    tooltipText: "Add " + (modelData.name || "peer") + " to the room"
                    onClicked: root.addPeerToRoom(modelData.id)
                  }

                  // Friend control on the peer card: an "add friend" button for
                  // strangers, or a friend/pending badge once a request is in.
                  Item {
                    id: friendBadge
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.sm
                    width: root.friendState(modelData.id) === "" ? Style.space(30)
                         : root.friendState(modelData.id) === "friend" ? Style.space(44)
                         : Style.space(16)
                    height: Style.space(20)

                    // Stranger -> "+" button to send a friend request.
                    Button {
                      anchors.fill: parent
                      visible: root.friendState(modelData.id) === ""
                      text: "\uFF0B"   // ＋
                      fontSize: Style.font.caption
                      tooltipText: "Send friend request to " + modelData.name
                      onClicked: Lanchat.requestFriend(modelData.id)
                    }

                    // Pending / friend -> state badge.
                    Text {
                      anchors.centerIn: parent
                      visible: root.friendState(modelData.id) !== ""
                      text: root.friendState(modelData.id) === "friend" ? "\u2713 Friend" : "\u2026"
                      color: root.friendState(modelData.id) === "friend" ? Color.accent : Color.muted
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.DemiBold
                    }
                  }
                }

                Text {
                  visible: Lanchat.displayPeers.length === 0
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

              // ---- (1.3) First-run onboarding: a dismissible banner below the
              // peer list explaining the private-by-default model. Shown until
              // the user dismisses it (session-local). Styled to match the
              // friend-request banner and auto-sized so text never overflows.
              Item {
                id: onboardingBanner
                width: parent.width
                height: root.showOnboarding && Lanchat.visibility === "private"
                        ? onbContent.implicitHeight + Style.space(12) : 0
                anchors.top: peersOnlineBar.bottom
                anchors.topMargin: Style.spacing.xs
                visible: root.showOnboarding && Lanchat.visibility === "private"
                clip: true

                Rectangle {
                  anchors.fill: parent
                  color: Style.selectedAccentFill
                  Rectangle {
                    anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
                    height: 1; color: Color.popups.border
                  }
                }

                Row {
                  id: onbContent
                  anchors.fill: parent
                  anchors.leftMargin: Style.spacing.sm
                  anchors.rightMargin: Style.spacing.sm
                  anchors.topMargin: Style.space(6)
                  anchors.bottomMargin: Style.space(6)
                  spacing: Style.spacing.sm

                  Text {
                    id: onbText
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - Style.space(40)
                    text: "You're invisible on the network. To connect, add a friend's "
                          + "My ID (fingerprint) in Settings, or switch on Discoverable for a trusted LAN."
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WordWrap
                  }

                  Button {
                    id: onbDismiss
                    anchors.verticalCenter: parent.verticalCenter
                    text: "\u2715"  // ✕ dismiss
                    fontSize: Style.font.caption
                    onClicked: root.showOnboarding = false
                  }
                }
              }

              // ---- friend-request notifications: pinned below the peer
              // list so an incoming request is always in view -------------
              Item {
                id: notifBanner
                width: parent.width
                height: Lanchat.friendRequests.length === 0 ? 0
                       : root.notifExpanded ? Style.space(24) + notifRows.implicitHeight + Style.spacing.xs
                       : Style.space(24)
                anchors.top: onboardingBanner.bottom
                anchors.topMargin: Style.spacing.xs
                visible: Lanchat.friendRequests.length > 0
                clip: true

                Rectangle {
                  anchors.fill: parent
                  color: Style.selectedAccentFill
                  Rectangle {
                    anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
                    height: 1; color: Color.popups.border
                  }
                }

                // Header row: summary + expand/collapse chevron.
                Item {
                  anchors.top: parent.top
                  anchors.left: parent.left; anchors.right: parent.right
                  height: Style.space(24)

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left; anchors.leftMargin: Style.spacing.sm
                    text: (root.notifExpanded ? "\u25BC " : "\u25B6 ") + Lanchat.friendRequests.length
                          + (Lanchat.friendRequests.length === 1 ? " friend request" : " friend requests")
                    color: Color.accent
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.weight: Font.Bold
                  }
                  MouseArea {
                    anchors.fill: parent
                    onClicked: root.notifExpanded = !root.notifExpanded
                  }
                }

                Column {
                  id: notifRows
                  anchors.top: parent.top
                  anchors.topMargin: Style.space(24)
                  width: parent.width
                  visible: root.notifExpanded
                  spacing: Style.spacing.xs

                  Repeater {
                    model: Lanchat.friendRequests
                    Column {
                      required property var modelData
                      width: notifBanner.width - Style.space(16)
                      anchors.horizontalCenter: parent.horizontalCenter
                      spacing: Style.spacing.xs

                      // Line 1: label + Accept/Reject (incoming) or Cancel
                      // (outgoing — retract a request we sent). The requester's
                      // name sits on its own line below so it reads clearly.
                      Row {
                        width: parent.width
                        spacing: Style.spacing.sm

                        Text {
                          anchors.verticalCenter: parent.verticalCenter
                          width: parent.width - Style.space(96)
                          text: modelData.outgoing
                            ? "Waiting for them to accept"
                            : "Friend request from"
                          color: Color.popups.text
                          font.family: Style.font.family
                          font.pixelSize: Style.font.caption
                          elide: Text.ElideRight
                        }
                        Item { width: Style.space(4) }
                        Button {
                          visible: !modelData.outgoing
                          text: "Accept"
                          fontSize: Style.font.caption
                          onClicked: root.acceptFriend(modelData.peerId)
                        }
                        Button {
                          visible: !modelData.outgoing
                          text: "Reject"
                          fontSize: Style.font.caption
                          onClicked: root.rejectFriend(modelData.peerId)
                        }
                        Button {
                          visible: modelData.outgoing
                          text: "Cancel"
                          fontSize: Style.font.caption
                          tooltipText: "Withdraw this friend request"
                          onClicked: root.cancelFriend(modelData.peerId)
                        }
                      }

                      // Line 2: the requester's display name — its own row so
                      // it's prominent and never crowds the buttons above.
                      Row {
                        visible: !modelData.outgoing
                        width: parent.width
                        spacing: Style.spacing.sm
                        Text {
                          anchors.verticalCenter: parent.verticalCenter
                          width: parent.width
                          text: modelData.name || "Someone"
                          color: Color.popups.text
                          font.family: Style.font.family
                          font.pixelSize: Style.font.body
                          font.weight: Font.DemiBold
                          elide: Text.ElideRight
                        }
                      }

                      // Verified requester fingerprint (first 6 digits) on the
                      // incoming request, so you can spot-check identity before
                      // accepting. Shown on its own row so it never overflows.
                      Row {
                        visible: !modelData.outgoing
                        width: parent.width
                        spacing: Style.spacing.sm
                        Text {
                          anchors.verticalCenter: parent.verticalCenter
                          width: parent.width - Style.space(96)
                          text: "Fingerprint: " + root.shortFp(modelData.fingerprint || modelData.peerId)
                          color: Color.popups.mutedText
                          font.family: Style.font.mono || Style.font.family
                          font.pixelSize: Style.font.micro
                          elide: Text.ElideRight
                        }
                      }
                    }
                  }
                }
              }

              // ---- peers online: pinned above settings ----------------
              Column {
                id: peersOnlineBar
                width: parent.width
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.topMargin: Style.spacing.xs
                spacing: Style.space(3)

                Text {
                  id: daemonStatusText
                  width: parent.width
                  leftPadding: Style.spacing.sm
                  rightPadding: Style.spacing.sm
                  wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                  text: Lanchat.daemonState === "running"
                    ? ((Lanchat.onlineCount === 1 ? "1 peer" : Lanchat.onlineCount + " peers") + " online")
                    : (Lanchat.daemonState === "starting"
                       ? "Starting daemon…"
                       : "⚠ Daemon not running — lanchat is offline")
                  color: Lanchat.daemonState === "running"
                    ? (Lanchat.onlineCount > 0 ? Color.accent : Color.muted)
                    : Color.urgent
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }

                // Firewall warning: the daemon is up but port 4812 is
                // blocked, so LAN peers can't reach us. Persistent (unlike
                // the transient chat alert) so it's not missed. Wraps so the
                // text is never clipped on narrow panels.
                Text {
                  id: firewallAlertText
                  width: parent.width
                  leftPadding: Style.spacing.sm
                  rightPadding: Style.spacing.sm
                  visible: showFwAlert
                  wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                  text: "⚠ Firewall blocking port 4812 — peers can't reach you"
                  color: Color.urgent
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }

              // ---- rooms: collapsible section UNDER the peers list ------
              // Sits directly above settings (element ordering, anchor chain:
              // rooms.top ← peersOnlineBar stack bottom; peerList.bottom ←
              // rooms.top). Collapsed by default; expands into peer-list
              // space, which shrinks by its own anchors — no height math.
              Column {
                id: roomsSection
                property bool expanded: false
                width: parent.width
                anchors.left: parent.left
                anchors.right: parent.right
                // Anchor to the BOTTOM stack like settings does, so it hugs
                // the settings header and grows upward into list space.
                anchors.bottom: settings.top
                height: roomsHeader.height + (roomsSection.expanded ? roomsListCol.height : 0)

                // header row: toggle + create
                Item {
                  id: roomsHeader
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
                    onClicked: roomsSection.expanded = !roomsSection.expanded
                  }

                  Text {
                    anchors.left: parent.left
                    anchors.leftMargin: Style.spacing.sm
                    anchors.verticalCenter: parent.verticalCenter
                    text: (roomsSection.expanded ? "Rooms ▾ " : "Rooms ▸ ")
                          + "(" + Lanchat.rooms.length + ")"
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.weight: Font.Bold
                  }

                  Button {
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.sm
                    anchors.verticalCenter: parent.verticalCenter
                    text: "＋"
                    fontSize: Style.font.caption
                    foreground: Color.popups.text
                    tooltipText: "Create a room"
                    onClicked: { root.showRoomNameDialog = true; root.newRoomName = "" }
                  }
                }

                // body: room rows (only when expanded; a room click opens it)
                Column {
                  id: roomsListCol
                  visible: roomsSection.expanded
                  width: parent.width
                  spacing: Style.spacing.xs

                  // Unaccepted invites first (Accept opens + joins the room).
                  Repeater {
                    model: Lanchat.roomInvites
                    delegate: Rectangle {
                      required property var modelData
                      width: roomsListCol.width
                      height: Style.space(34)
                      color: Style.selectedAccentFill

                      Row {
                        anchors.fill: parent
                        anchors.leftMargin: Style.spacing.sm
                        anchors.rightMargin: Style.spacing.sm
                        spacing: Style.spacing.sm

                        Text {
                          width: parent.width - Style.space(120)
                          anchors.verticalCenter: parent.verticalCenter
                          text: (modelData.fromName || "Someone") + " invited you to " + (modelData.name || "a room")
                          color: Color.popups.text
                          font.family: Style.font.family
                          font.pixelSize: Style.font.caption
                          elide: Text.ElideRight
                        }
                        Button {
                          anchors.verticalCenter: parent.verticalCenter
                          text: "Join"
                          fontSize: Style.font.caption
                          onClicked: {
                            Lanchat.roomJoin(modelData.roomId)
                            root.selectRoom(modelData.roomId)
                          }
                        }
                      }
                    }
                  }

                  Repeater {
                    model: Lanchat.rooms
                    delegate: Rectangle {
                      required property var modelData
                      width: roomsListCol.width
                      height: root.peerRowH
                      radius: Style.cornerRadius
                      color: modelData.roomId === Lanchat.selectedRoomId
                        ? Style.selectedFill : "transparent"

                      MouseArea {
                        anchors.fill: parent
                        onClicked: root.selectRoom(modelData.roomId)
                      }

                      Rectangle {
                        visible: modelData.roomId === Lanchat.selectedRoomId
                        width: 3
                        height: parent.height * 0.5
                        radius: 1.5
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        color: Color.accent
                      }

                      Text {
                        anchors.left: parent.left
                        anchors.leftMargin: Style.spacing.sm
                        anchors.right: roomLeaveBtn.left
                        anchors.rightMargin: Style.spacing.sm
                        anchors.verticalCenter: parent.verticalCenter
                        text: "# " + modelData.name
                        color: Color.popups.text
                        font.family: Style.font.family
                        font.pixelSize: Style.font.bodySmall
                        elide: Text.ElideRight
                      }

                      Button {
                        id: roomLeaveBtn
                        anchors.right: parent.right
                        anchors.rightMargin: Style.spacing.sm
                        anchors.verticalCenter: parent.verticalCenter
                        visible: modelData.roomId === Lanchat.selectedRoomId
                        text: "✕"
                        fontSize: Style.font.caption
                        foreground: Color.muted
                        tooltipText: "Leave this room"
                        onClicked: root.leaveSelectedRoom()
                      }
                    }
                  }

                  Text {
                    visible: Lanchat.rooms.length === 0 && Lanchat.roomInvites.length === 0
                    width: parent.width
                    leftPadding: Style.spacing.sm
                    text: "No rooms yet — use ＋ to create one"
                    color: Color.muted
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                }
              }

              // ---- settings: collapsible ------------------------------
              Column {
                id: settings
                property bool expanded: false
                width: parent.width
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                // Cap height so the expanded body stops at the bottom of the
                // pinned alert stack (notifBanner) — settings can never grow
                // into/over the always-visible alerts, so expanding it only
                // crushes the peer list and never pushes anything off-screen.
                height: settingsHeader.height + (settings.expanded
                  ? Math.min(settingsBody.contentHeight, Math.max(0, settings.parent.height - (notifBanner.y + notifBanner.height) - settingsHeader.height - Style.space(12)))
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

                  // Help "?" — opens the built-in HELP.html in the default
                  // viewer. Sits on top of the header's toggle MouseArea (a
                  // later sibling, so it wins the click) and must not toggle
                  // the settings expand/collapse.
                  Button {
                    id: helpBtn
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.sm
                    anchors.verticalCenter: parent.verticalCenter
                    text: "?"
                    fontSize: Style.font.caption
                    foreground: Color.popups.text
                    tooltipText: "Open help (HELP.html)"
                    onClicked: root.openHelp()
                  }
                }

                // body (name + online + API + undo + save) — compact rows,
                // scrollable so it never overflows the panel.
                Flickable {
                  id: settingsBody
                  width: parent.width
                  height: Math.min(bodyCol.implicitHeight, Math.max(0, settings.parent.height - (notifBanner.y + notifBanner.height) - settingsHeader.height - Style.space(12)))
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

                  // ---- Identity ----
                  Item {
                    width: parent.width
                    height: Style.space(18)
                    Text {
                      anchors.horizontalCenter: parent.horizontalCenter

                      anchors.verticalCenter: parent.verticalCenter
                      text: "IDENTITY"
                      color: Color.accent
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                      font.underline: true
                    }
                  }

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
                      // Commit the name automatically a moment after typing
                      // stops (debounced) — no Enter needed. `editingFinished`
                      // only fires on genuine focus loss, which clicking
                      // non-focusable panel areas doesn't always trigger.
                      onTextChanged: nameSaveTimer.restart()
                      onAccepted: root.commitName()
                      onEditingFinished: root.commitName()
                      Timer {
                        id: nameSaveTimer
                        interval: 600
                        onTriggered: root.commitName()
                      }
                    }

                    // Re-roll to a fresh random friendly name.
                    Button {
                      id: rollButton
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "\uF074" // nf-fa-random (shuffle) — re-roll the name
                      tooltipText: "Randomize name"
                      onClicked: Lanchat.regenerateName()
                    }
                  }

                  Item {
                    width: parent.width
                    height: Style.space(30)

                    Text {
                      id: myIdLabel
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "My ID"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    Text {
                      anchors.left: myIdLabel.right
                      anchors.leftMargin: Style.spacing.sm
                      anchors.right: myIdCopy.left
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: (Lanchat.myId || "…").slice(0, 20) + "…"
                      color: Color.accent
                      font.family: Style.font.mono || Style.font.family
                      font.pixelSize: Style.font.micro
                      horizontalAlignment: Text.AlignRight
                      elide: Text.ElideRight
                    }

                    Button {
                      id: myIdCopy
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "\uF0C5"  // copy icon
                      onClicked: root.copyToClipboard(Lanchat.myId)
                    }
                  }

                  Item {
                    width: parent.width
                    height: Style.space(30)

                    Text {
                      id: addFrLabel
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Add friend"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    TextField {
                      id: addFrInput
                      anchors.left: addFrLabel.right
                      anchors.leftMargin: Style.spacing.sm
                      anchors.right: addFrButton.left
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      maximumLength: 128
                      placeholderText: "cert fingerprint"
                      horizontalPadding: Style.space(8)
                      verticalPadding: Style.space(4)
                      onAccepted: root.addFriendByFingerprint()
                    }

                    Button {
                      id: addFrButton
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Add"
                      onClicked: root.addFriendByFingerprint()
                    }
                  }

                  // ---- Presence ----
                  Item {
                    width: parent.width
                    height: Style.space(18)
                    Text {
                      anchors.horizontalCenter: parent.horizontalCenter

                      anchors.verticalCenter: parent.verticalCenter
                      text: "PRESENCE"
                      color: Color.accent
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                      font.underline: true
                    }
                  }

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

                  Item {
                    width: parent.width
                    height: Style.space(28)

                    MouseArea {
                      id: visTipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Discoverable (open mode)"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }

                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      checked: Lanchat.visibility === "open"
                      onToggled: Lanchat.setVisibility(Lanchat.visibility === "open" ? "private" : "open")
                    }

                    PanelToolTip {
                      visible: visTipHover.containsMouse
                      text: "On = broadcast your presence on the LAN (trusted networks). Off = invisible; connect by adding a fingerprint."
                    }
                  }

                  Item {
                    width: parent.width
                    height: Style.space(28)

                    MouseArea {
                      id: reqTipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Receive friend requests"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }

                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      checked: Lanchat.acceptRequests
                      onToggled: Lanchat.setAcceptRequests(!Lanchat.acceptRequests)
                    }

                    PanelToolTip {
                      visible: reqTipHover.containsMouse
                      text: "On = receive friend requests (shown with the requester's verified fingerprint). Off = reject all incoming requests."
                    }
                  }

                  // ---- Rooms: member-colors kill-switch ------------------
                  // OWNER-level room state (default ON): when off, room
                  // rendering falls back to the standard theme palette
                  // (approved decision #3). Enabled only when the user owns
                  // at least one room — the toggle applies per owned room via
                  // the selected room; the tooltip says so. Room state is
                  // mirrored from the daemon (roomState events), not a
                  // session-local bool.
                  Item {
                    width: parent.width
                    height: Style.space(30)
                    visible: root.ownsAnyRoom

                    property bool tipHover: tipHoverArea.containsMouse

                    MouseArea {
                      id: tipHoverArea
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Rooms: members pick their colors"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }

                    ToggleSwitch {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      enabled: root.selectedOwnedRoom !== null
                      checked: root.selectedOwnedRoom
                        ? root.selectedOwnedRoom.colorsEnabled !== false : true
                      onToggled: {
                        if (root.selectedOwnedRoom)
                          Lanchat.toggleRoomColors(Lanchat.selectedRoomId, !root.selectedOwnedRoom.colorsEnabled)
                      }
                    }

                    PanelToolTip {
                      visible: parent.tipHover
                      text: "Applies to rooms YOU own. Off = members' names/bubbles render with the standard theme colors."
                    }
                  }

                  // ---- Chat ----
                  Item {
                    width: parent.width
                    height: Style.space(18)
                    Text {
                      anchors.horizontalCenter: parent.horizontalCenter

                      anchors.verticalCenter: parent.verticalCenter
                      text: "CHAT"
                      color: Color.accent
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                      font.underline: true
                    }
                  }

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
                        text: root.shortPath(Lanchat.downloadDir)
                        color: Color.popups.text
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideLeft
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

                  Item {
                    width: parent.width
                    height: Style.space(30)

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Clear all chats"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    PanelToolTip {
                      visible: clearAllHover.containsMouse
                      text: "Deletes every conversation on this machine."
                    }
                    MouseArea {
                      id: clearAllHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Button {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: root.confirmClearAll ? "Confirm?" : "Clear"
                      fontSize: Style.font.caption
                      onClicked: {
                        if (!root.confirmClearAll) { root.confirmClearAll = true; root.clearConfirmTimer.restart() }
                        else {
                          root.confirmClearAll = false
                          Lanchat.clearAllChats()
                          root.showClearAllCheck = true
                          root.clearAllCheckTimer.restart()
                        }
                      }
                    }

                    // Brief checkmark feedback after a successful clear-all.
                    Text {
                      visible: root.showClearAllCheck
                      anchors.right: parent.right
                      anchors.rightMargin: Style.space(64)
                      anchors.verticalCenter: parent.verticalCenter
                      text: "\u2713"
                      color: Color.accent
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                  }

                  // ---- Appearance ----
                  Item {
                    width: parent.width
                    height: Style.space(18)
                    Text {
                      anchors.horizontalCenter: parent.horizontalCenter

                      anchors.verticalCenter: parent.verticalCenter
                      text: "APPEARANCE"
                      color: Color.accent
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                      font.underline: true
                    }
                  }

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

                      Button { width: Style.space(24); height: Style.space(18); text: "S"; fontSize: Style.font.caption; onClicked: root.applyPanelSize("small") }
                      Button { width: Style.space(24); height: Style.space(18); text: "M"; fontSize: Style.font.caption; onClicked: root.applyPanelSize("medium") }
                      Button { width: Style.space(24); height: Style.space(18); text: "L"; fontSize: Style.font.caption; onClicked: root.applyPanelSize("large") }
                      Button { width: Style.space(28); height: Style.space(18); text: "XL"; fontSize: Style.font.caption; onClicked: root.applyPanelSize("xl") }
                      Button { width: Style.space(24); height: Style.space(18); text: "F"; fontSize: Style.font.caption; onClicked: root.applyPanelSize("full") }
                    }
                  }

                  Item {
                    width: parent.width
                    height: Style.space(28)

                    MouseArea {
                      id: sizeWHHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    Text {
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Size (W × H)"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    PanelToolTip {
                      visible: sizeWHHover.containsMouse
                      text: "Panel width × height in pixels. Pick a preset above, or type your own and press Enter / Apply."
                    }

                    Row {
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(4)

                      TextField {
                        id: sizeWField
                        width: Style.space(40)
                        text: Lanchat.customW > 0 ? String(Lanchat.customW) : String(root.panelW)
                        maximumLength: 5
                        horizontalPadding: Style.space(4)
                        verticalPadding: Style.space(3)
                        inputMethodHints: Qt.ImhDigitsOnly
                        onEditingFinished: root.applyManualSize(sizeWField.text, sizeHField.text)
                      }
                      Text {
                        text: "×"
                        color: Color.popups.text
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        anchors.verticalCenter: parent.verticalCenter
                      }
                      TextField {
                        id: sizeHField
                        width: Style.space(40)
                        text: Lanchat.customH > 0 ? String(Lanchat.customH) : String(root.panelH)
                        maximumLength: 5
                        horizontalPadding: Style.space(4)
                        verticalPadding: Style.space(3)
                        inputMethodHints: Qt.ImhDigitsOnly
                        onEditingFinished: root.applyManualSize(sizeWField.text, sizeHField.text)
                      }
                      Button {
                        text: "Apply"
                        fontSize: Style.font.caption
                        onClicked: root.applyManualSize(sizeWField.text, sizeHField.text)
                      }
                    }
                  }

                  // ---- Agents ----
                  Item {
                    width: parent.width
                    height: Style.space(18)
                    Text {
                      anchors.horizontalCenter: parent.horizontalCenter

                      anchors.verticalCenter: parent.verticalCenter
                      text: "AGENTS"
                      color: Color.accent
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                      font.underline: true
                    }
                  }

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

                  // ---- Reachability ----
                  Item {
                    width: parent.width
                    height: Style.space(18)
                    Text {
                      anchors.horizontalCenter: parent.horizontalCenter

                      anchors.verticalCenter: parent.verticalCenter
                      text: "REACHABILITY"
                      color: Color.accent
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                      font.underline: true
                    }
                  }

                  Item {
                    width: parent.width
                    height: Style.space(30)

                    MouseArea {
                      id: fwTipHover
                      anchors.fill: parent
                      hoverEnabled: true
                    }

                    // Colored status dot: accent=open, urgent=blocked,
                    // muted=unknown.
                    Rectangle {
                      id: fwDot
                      anchors.left: parent.left
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      width: Style.space(8)
                      height: Style.space(8)
                      radius: width / 2
                      color: Lanchat.firewall.open === true ? Color.accent
                           : Lanchat.firewall.open === false ? Color.urgent
                           : Color.muted
                    }

                    Text {
                      id: fwLabel
                      anchors.left: fwDot.right
                      anchors.leftMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: "Port 4812"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }

                    Text {
                      id: fwState
                      anchors.left: fwLabel.right
                      anchors.leftMargin: Style.spacing.sm
                      anchors.right: fwToggleBtn.left
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: Lanchat.firewall.open === true ? "Open"
                          : Lanchat.firewall.open === false ? "Blocked"
                          : "Unknown"
                      color: Lanchat.firewall.open === true ? Color.accent
                           : Lanchat.firewall.open === false ? Color.urgent
                           : Color.muted
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      elide: Text.ElideRight
                    }

                    PanelToolTip {
                      visible: fwTipHover.containsMouse
                      text: Lanchat.firewall.detail || "Check whether lanchat's port is reachable."
                    }

                    // Single toggle button: if the port is open, clicking
                    // closes it; if closed (or unknown), clicking opens it —
                    // whichever `make firewall-*` the current state calls for.
                    Button {
                      id: fwToggleBtn
                      anchors.right: parent.right
                      anchors.rightMargin: Style.spacing.sm
                      anchors.verticalCenter: parent.verticalCenter
                      text: Lanchat.firewall.open === true ? "\uF023" : "\uF09C"  // lock when open (action=close), unlock otherwise (action=open)
                      tooltipText: Lanchat.firewall.open === true
                        ? "Port 4812 is open — click to close it (block inbound)"
                        : "Port 4812 is closed — click to open it to the LAN (asks for your password)"
                      onClicked: Lanchat.firewall.open === true
                        ? Lanchat.firewallClose()
                        : Lanchat.firewallOpen()
                    }
                  }

                  // ---- Developer ----
                  Item {
                    width: parent.width
                    height: Style.space(18)
                    Text {
                      anchors.horizontalCenter: parent.horizontalCenter

                      anchors.verticalCenter: parent.verticalCenter
                      text: "DEVELOPER"
                      color: Color.accent
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                      font.underline: true
                    }
                  }

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
              onReleased: {
                dragging = false
                // Persist the final divider position so the peer-column
                // layout survives a restart.
                if (root.dragPeerColW > 0) {
                  Lanchat.setPeerColW(root.dragPeerColW)
                  root.dragPeerColW = 0
                }
              }
              onPositionChanged: {
                // New width = handle's center X within the panel (parent of the Row).
                if (dragging) {
                  var px = parent.mapToItem(parent.parent, mouse.x, 0).x
                  root.dragPeerColW = Math.max(Style.space(140), Math.min(px + Style.space(4), parent.parent.width - Style.space(200)))
                }
              }
            }
          }

          // Right: thread + compose
          Column {
            width: parent.width - root.peerColW - 1
            height: parent.height

            // Pinned thread header: stays fixed at the top while messages
            // scroll beneath. Holds the typing indicator, per-chat actions
            // (Clear chat, Unfriend) AND the app-level Check-for-updates /
            // Discard controls, which must stay visible even with no peer
            // selected — so this bar is always shown; only the two per-peer
            // buttons below hide when nothing is selected.
            Rectangle {
              id: threadHeader
              width: parent.width
              height: Style.space(30)
              visible: true
              color: Style.normalFill

              Rectangle {
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: Color.popups.border
              }

              Text {
                anchors.left: discardBtn.visible ? discardBtn.right : updateBtn.right
                anchors.leftMargin: Style.spacing.sm
                anchors.verticalCenter: parent.verticalCenter
                visible: root.typingForPeer !== ""
                text: root.typingForPeer + " is typing…"
                color: Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.italic: true
              }

              // Per-chat actions only show when they can actually act.
              // "Unfriend" needs a real (confirmed) friend relationship —
              // pointless over a stranger's or a still-pending request's
              // thread. "Clear chat" needs history to clear — pointless for
              // a brand-new conversation with no messages yet.
              //
              // Rightmost is a Close button (deselect the peer so no chat
              // shows) with a thin vertical divider between it and the
              // Unfriend / Clear-chat cluster. All per-chat controls hide
              // when nothing is selected; the divider tracks the Close
              // button so it never floats alone.
              Rectangle {
                id: chatActionsSep
                visible: closeChatBtn.visible && (unfriendBtn.visible || clearChatBtn.visible)
                width: 1
                height: Math.min(16, parent.height - Style.space(8))
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: closeChatBtn.left
                anchors.rightMargin: Style.spacing.sm
                color: Color.popups.border
              }

              // Close the current conversation: deselect the peer so no chat
              // shows on the right (mirrors clicking empty space in the peer
              // list). Shown whenever a conversation is open — i.e. a peer is
              // selected — even if it has no messages yet, so an empty
              // conversation can always be closed. The glyph is FontAwesome's
              // fa-times (\uF00D) so it renders thin and matches the refresh
              // icon and text labels around it, colored with the row's default
              // foreground (no muted override) so it reads like the other
              // controls in the header.
              Button {
                id: closeChatBtn
                visible: root.selectedPeerId !== "" || root.inRoom
                anchors.right: parent.right
                anchors.rightMargin: Style.spacing.sm
                anchors.verticalCenter: parent.verticalCenter
                text: "\uF00D"  // fa-times (thin close glyph)
                fontSize: Style.font.caption
                tooltipText: "Close conversation (deselect peer)"
                onClicked: root.closeChat()
              }

              Button {
                id: unfriendBtn
                visible: root.friendState(root.selectedPeerId) === "friend"
                // If the button disappears while armed (e.g. the peer is no
                // longer a friend), drop the pending confirm so it can't
                // re-surface against another peer.
                onVisibleChanged: if (!visible) root.confirmUnfriend = false
                anchors.right: chatActionsSep.left
                anchors.rightMargin: Style.spacing.sm
                anchors.verticalCenter: parent.verticalCenter
                text: root.confirmUnfriend ? "Confirm unfriend?" : "Unfriend"
                fontSize: Style.font.caption
                foreground: root.confirmUnfriend ? Color.urgent : Color.foreground
                onClicked: {
                  if (!root.confirmUnfriend) {
                    // First click: arm the confirm state (auto-disarms shortly).
                    root.confirmUnfriend = true
                    root.unfriendConfirmTimer.restart()
                  } else {
                    // Second click: actually unfriend.
                    root.confirmUnfriend = false
                    root.unfriendConfirmTimer.stop()
                    root.unfriendSelected()
                  }
                }
              }
              Button {
                id: clearChatBtn
                visible: root.hasThread
                anchors.right: unfriendBtn.left
                anchors.rightMargin: Style.spacing.sm
                anchors.verticalCenter: parent.verticalCenter
                text: "Clear chat"
                fontSize: Style.font.caption
                onClicked: root.clearChat()
              }

              // Update-availability alert + apply: shows a highlighted refresh
              // icon when a newer commit is on the remote, and a plain one to
              // re-check. The check is read-only (ls-remote + rev-parse) and
              // never touches the checkout; CLICKING while a badge shows runs
              // the update (Lanchat.applyUpdate) — safe unless local edits
              // block it, in which case the Discard & update button offers a
              // clean install. Uses `foreground` (icon color) not `color`
              // (fill) so its transparent background matches the Unfriend /
              // Clear-chat buttons beside it.
              Button {
                id: updateBtn
                anchors.left: parent.left
                anchors.leftMargin: Style.spacing.sm
                anchors.verticalCenter: parent.verticalCenter
                text: "\uF021" // nf-fa-refresh
                fontSize: Style.font.caption
                foreground: Lanchat.updateApplyState === "dirty" ? Color.urgent
                  : Lanchat.updateApplying ? Color.muted
                  : Lanchat.updateAvailable ? Color.accent
                  : Lanchat.updateChecking ? Color.muted
                  : Color.foreground
                tooltipText: Lanchat.updateApplyState === "dirty" ? "Local changes block the update — use \u201CDiscard & update\u201D"
                  : Lanchat.updateApplying ? "Updating…"
                  : Lanchat.updateAvailable ? "Update available — click to update"
                  : Lanchat.updateChecking ? "Checking for updates…"
                  : "Check for updates"
                onClicked: Lanchat.updateAvailable ? Lanchat.applyUpdate(false) : Lanchat.checkForUpdate()
              }

              // Clean-install button: appears only when a safe update is
              // blocked by local (uncommitted) edits in the installed checkout
              // (e.g. a parallel session's in-flight work). Clicking discards
              // those edits and resets the checkout to the remote commit — a
              // clean install of the latest version. Config/certs/history live
              // outside the plugin folder, so they are NOT touched.
              Button {
                id: discardBtn
                visible: Lanchat.updateApplyState === "dirty"
                anchors.left: updateBtn.right
                anchors.leftMargin: Style.spacing.sm
                anchors.verticalCenter: parent.verticalCenter
                text: "Discard & update"
                fontSize: Style.font.caption
                foreground: Color.urgent
                tooltipText: "Discard local changes and install the latest version (clean install)"
                onClicked: Lanchat.applyUpdate(true)
              }

              // Update-available alert pill, pinned to the refresh button's
              // top-right corner and nudged off it so it reads as a badge —
              // mirrors the bar widget's friend-request pill (accent fill on
              // a background-ringed dot). Shows "!" whenever the read-only git
              // probe reports a newer commit on the remote.
              Rectangle {
                id: updateBadge
                visible: Lanchat.updateAvailable
                anchors.top: updateBtn.top
                anchors.right: updateBtn.right
                anchors.topMargin: -3
                anchors.rightMargin: -3
                width: Style.space(12)
                height: Style.space(12)
                radius: height / 2
                color: Color.accent
                border.color: Color.background
                border.width: 1

                Text {
                  anchors.centerIn: parent
                  text: "\u0021" // "!"
                  color: Color.background
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.weight: Font.Bold
                }
              }
            }

          ListView {
            id: list
            visible: !root.inRoom
            width: root.inRoom ? 0 : parent.width
            height: parent.height - composeBox.height - threadHeader.height
            clip: true
            spacing: Style.spacing.sm
            model: root.thread
            header: Item { width: parent.width; height: Style.spacing.md }
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
                    // Show the attachment (paperclip + name) so an attachment-only
                    // message isn't a blank bubble; text + attachment stack.
                    text: (modelData.attachment && modelData.attachment.name)
                      ? ((modelData.text ? modelData.text + "\n" : "") + "\uD83D\uDCCE " + modelData.attachment.name)
                      : modelData.text
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    wrapMode: Text.Wrap
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

            // ---- ROOM VIEW (when a room is selected) -------------------
            // Replaces the 1:1 thread: roster column (between the divider
            // and the chat) + room thread + per-message room styling.
            Row {
              id: roomView
              visible: root.inRoom
              width: parent.width
              height: parent.height - composeBox.height - threadHeader.height

              // ---- roster column ---------------------------------------
              Rectangle {
                id: rosterCol
                width: Style.space(170)
                height: parent.height
                color: Util.alpha(Color.foreground, 0.04)

                Rectangle {
                  anchors.right: parent.right
                  anchors.top: parent.top
                  anchors.bottom: parent.bottom
                  width: 1
                  color: Color.popups.border
                }

                Column {
                  anchors.fill: parent
                  spacing: Style.spacing.xs

                  // Roster header: frozen-state banner. Gated on the
                  // singleton's mirrored host-online property — never a
                  // session-local bool alone.
                  Rectangle {
                    id: roomBanner
                    width: parent.width
                    height: roomBanner.visible ? roomBannerText.implicitHeight + Style.space(10) : 0
                    visible: !Lanchat.roomHostOnline
                    color: Style.selectedAccentFill

                    Text {
                      id: roomBannerText
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.top: parent.top
                      anchors.topMargin: Style.space(5)
                      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                      text: "Host offline — changes frozen"
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }
                  }

                  Text {
                    id: rosterHeader
                    width: parent.width
                    leftPadding: Style.spacing.sm
                    text: "Members (" + (root.selectedRoom ? Object.keys(root.selectedRoom.members || {}).length : 0) + ")"
                    color: Color.muted
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.weight: Font.Bold
                    topPadding: Style.space(4)
                  }

                  ListView {
                    id: rosterList
                    width: parent.width
                    // Anchor-chain height: the list flexes between the header
                    // stack and the bottom controls — no fragile arithmetic.
                    height: parent.height - roomBanner.height - rosterHeader.height
                            - rosterBottom.height
                    clip: true
                    interactive: rosterList.contentHeight > rosterList.height
                    model: root.selectedRoom ? Object.keys(root.selectedRoom.members || {}) : []
                    spacing: Style.spacing.xs

                    delegate: Rectangle {
                      id: rosterRow
                      required property var modelData
                      required property int index
                      width: rosterList.width
                      height: Style.space(38)
                      color: "transparent"

                      readonly property var member: root.selectedRoom
                        ? (root.selectedRoom.members[modelData] || {}) : {}
                      readonly property bool rowIsOwner: root.selectedRoom
                        && root.selectedRoom.owner === modelData
                      readonly property bool rowIsMe: Lanchat.myId === modelData

                      Row {
                        anchors.left: parent.left
                        anchors.leftMargin: Style.spacing.sm
                        anchors.right: parent.right
                        anchors.rightMargin: Style.spacing.sm
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Style.spacing.sm

                        // Member color dot (palette-token resolved per-viewer).
                        Rectangle {
                          width: Style.space(10)
                          height: Style.space(10)
                          radius: width / 2
                          anchors.verticalCenter: parent.verticalCenter
                          color: Lanchat.roomMemberColor(rosterRow.member) || Color.muted
                          border.width: 1
                          border.color: Color.popups.border
                        }

                        Column {
                          width: parent.width - Style.space(26)
                          anchors.verticalCenter: parent.verticalCenter

                          Text {
                            width: parent.width
                            text: (rosterRow.rowIsOwner ? "★ " : "")
                                  + (rosterRow.member.name || "Unknown")
                                  + (rosterRow.rowIsMe ? " (you)" : "")
                            color: Color.popups.text
                            font.family: Style.font.family
                            font.pixelSize: Style.font.caption
                            elide: Text.ElideRight
                          }

                          // Owner controls: remove + per-member canInvite.
                          Row {
                            visible: root.amRoomOwner && !rosterRow.rowIsOwner
                            spacing: Style.space(6)

                            Button {
                              text: "✕"
                              fontSize: Style.font.caption
                              foreground: Color.urgent
                              tooltipText: "Remove from room"
                              onClicked: Lanchat.roomRemove(Lanchat.selectedRoomId, rosterRow.modelData)
                            }
                            Button {
                              text: (rosterRow.member.canInvite ? "✓ can add" : "✗ can add")
                              fontSize: Style.font.caption
                              foreground: rosterRow.member.canInvite ? Color.accent : Color.muted
                              tooltipText: "Toggle whether this member may add people"
                              onClicked: Lanchat.roomSetCanInvite(Lanchat.selectedRoomId,
                                rosterRow.modelData, !rosterRow.member.canInvite)
                            }
                          }
                        }
                      }
                    }
                  }

                  // ---- roster bottom: my color + add-member ----------------
                  Column {
                    id: rosterBottom
                    width: parent.width
                    leftPadding: Style.spacing.sm
                    rightPadding: Style.spacing.sm
                    topPadding: Style.space(4)
                    spacing: Style.space(4)

                    // My-color control: every member picks THEIR color from
                    // the current theme's palette (all swatches offered —
                    // none filtered; approved point 5) or "Match my theme
                    // accent" (token "theme", re-resolves on theme change).
                    Column {
                      width: parent.width - parent.leftPadding - parent.rightPadding
                      spacing: Style.space(4)

                      Text {
                        text: "My color"
                        color: Color.muted
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        font.weight: Font.Bold
                      }

                      Flow {
                        width: parent.width
                        spacing: Style.space(4)

                        // Palette swatches: read from the current Omarchy theme
                        // palette (canonical token set shared by colors.toml).
                        Repeater {
                          model: root.themePalette

                          Rectangle {
                            required property var modelData
                            width: Style.space(16)
                            height: Style.space(16)
                            radius: Style.space(3)
                            color: modelData.hex
                            border.width: 1
                            border.color: Color.popups.border

                            MouseArea {
                              anchors.fill: parent
                              onClicked: Lanchat.setRoomColor(Lanchat.selectedRoomId,
                                parent.parent.modelData.token, parent.parent.modelData.hex)
                            }
                          }
                        }
                      }

                      Button {
                        text: "Match my theme accent"
                        fontSize: Style.font.caption
                        onClicked: Lanchat.setRoomColor(Lanchat.selectedRoomId, "theme", String(Color.accent))
                      }
                    }

                    // Add-member: pick a FRIEND from a menu — no fingerprint
                    // typing. Lists every confirmed friend not already in the
                    // room; picking one proposes/adds them (same roomAdd path
                    // as drag-drop; owner executes).
                    Column {
                      visible: root.amRoomOwner
                      width: parent.width - parent.parent.leftPadding - parent.parent.rightPadding
                      spacing: Style.space(4)

                      Text {
                        text: "Add member"
                        color: Color.muted
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        font.weight: Font.Bold
                      }

                      // Candidates: display peers (online + offline friends)
                      // who are confirmed friends and not yet in the room.
                      readonly property var candidates: {
                        var out = []
                        var members = root.selectedRoom ? (root.selectedRoom.members || {}) : {}
                        var list = Lanchat.displayPeers
                        for (var i = 0; i < list.length; i++) {
                          var p = list[i]
                          if (p.id && p.id !== Lanchat.myId && !(p.id in members)
                              && Lanchat.isConfirmedFriend(p.id))
                            out.push(p)
                        }
                        return out
                      }

                      ComboBox {
                        id: addMemberSelect
                        width: parent.width
                        model: parent.candidates.map(function(p) { return p.name || p.id.slice(0, 8) })
                        visible: parent.candidates.length > 0
                        font.pixelSize: Style.font.caption
                      }

                      Text {
                        visible: parent.candidates.length === 0
                        width: parent.width
                        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                        text: "No friends left to add — befriend people first, or use the ＋ on their peer row"
                        color: Color.muted
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                      }

                      Button {
                        visible: parent.candidates.length > 0
                        text: "Add to room"
                        fontSize: Style.font.caption
                        onClicked: {
                          var c = parent.parent.candidates
                          var idx = addMemberSelect.currentIndex
                          if (c && idx >= 0 && idx < c.length)
                            root.addPeerToRoom(c[idx].id)
                        }
                      }
                    }

                    Text {
                      visible: !root.amRoomOwner
                      width: parent.width
                      wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                      text: "Use the ＋ on a friend's peer row to propose adding them"
                      color: Color.muted
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }
                  }
                }
              }

              // ---- room chat pane --------------------------------------
              Column {
                width: parent.width - rosterCol.width
                height: parent.height

                ListView {
                  id: roomList
                  width: parent.width
                  height: parent.height
                  clip: true
                  spacing: Style.spacing.sm
                  model: root.roomThread

                  header: Item { width: parent.width; height: Style.spacing.md }
                  footer: Item { width: parent.width; height: Style.spacing.lg }
                  onCountChanged: Qt.callLater(function() { positionViewAtEnd() })

                  delegate: Column {
                    required property var modelData
                    width: roomList.width
                    spacing: Style.spacing.xs

                    // Meta row: sender + time, tinted with the sender's
                    // member color when the room's colors are enabled.
                    Text {
                      anchors.left: modelData.outgoing ? undefined : parent.left
                      anchors.right: modelData.outgoing ? parent.right : undefined
                      text: (modelData.outgoing ? "You · " : modelData.fromName + " · ")
                            + root.timeLabel(modelData.ts)
                      color: {
                        if (!root.selectedRoom || !root.selectedRoom.colorsEnabled) return Color.muted
                        var mem = (root.selectedRoom.members || {})[modelData.from]
                        var col = Lanchat.roomMemberColor(mem)
                        return col !== "" ? col : Color.muted
                      }
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }

                    // Text bubble: member color tints the bubble; the INK is
                    // derived from the bubble background's luminance so text
                    // always passes contrast (the approved mechanism — a
                    // low-contrast color choice yields adapted text, never an
                    // excluded swatch). Same function local and remote.
                    Rectangle {
                      readonly property color memberTint: {
                        if (!root.selectedRoom || !root.selectedRoom.colorsEnabled) return "transparent"
                        var mem = (root.selectedRoom.members || {})[modelData.from]
                        var col = Lanchat.roomMemberColor(mem)
                        return col !== "" ? col : "transparent"
                      }
                      readonly property color bubbleBase: modelData.outgoing
                        ? Style.selectedAccentFill : Style.normalFill
                      // Composite the member tint over the base bubble fill
                      // at 25% (low-alpha tint, so the derived ink always
                      // clears the contrast ratio).
                      readonly property color bubbleColor: {
                        var t = memberTint
                        if (t === "transparent") return bubbleBase
                        return Qt.rgba(t.r * 0.25 + bubbleBase.r * 0.75,
                                       t.g * 0.25 + bubbleBase.g * 0.75,
                                       t.b * 0.25 + bubbleBase.b * 0.75,
                                       Math.max(bubbleBase.a, 0.85))
                      }
                      // Relative luminance (sRGB-linearized) of the bubble
                      // fill; ink flips dark/light at L 0.35.
                      function rlin(c) {
                        return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
                      }
                      readonly property real lum: 0.2126 * rlin(bubbleColor.r)
                        + 0.7152 * rlin(bubbleColor.g) + 0.0722 * rlin(bubbleColor.b)
                      readonly property color ink: lum > 0.35 ? Color.background : Color.popups.text

                      visible: (modelData.text !== "")
                      width: Math.min(roomList.width * 0.8,
                                      roomMsgText.implicitWidth + Style.space(28) + Style.space(20))
                      height: roomMsgText.implicitHeight + Style.space(18)
                      radius: Math.max(Style.cornerRadius, Style.space(6))
                      anchors.left: modelData.outgoing ? undefined : parent.left
                      anchors.right: modelData.outgoing ? parent.right : undefined
                      border.width: modelData.outgoing ? 0 : 1
                      border.color: Style.normalBorderColor
                      color: bubbleColor

                      Text {
                        id: roomMsgText
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: Style.space(14)
                        anchors.rightMargin: Style.space(14)
                        text: modelData.text
                        color: parent.ink
                        font.family: Style.font.family
                        font.pixelSize: Style.font.body
                        wrapMode: Text.Wrap
                      }
                    }

                    // Room-file bubble: metadata + Save when the sender↔me
                    // friend link exists; the befriend notice when it doesn't
                    // (the alert re-evaluates on friend events — Save
                    // reappears without a manual re-request).
                    Rectangle {
                      visible: modelData.attachment && modelData.attachment.name
                      readonly property var att: modelData.attachment || {}
                      readonly property bool senderIsFriend:
                        Lanchat.isConfirmedFriend(modelData.from)
                      readonly property bool fileDownloading:
                        Lanchat.dlActive && Lanchat.dlFileId === att.fileId
                      readonly property var statusMap:
                        Lanchat.roomFileStatuses[Lanchat.selectedRoomId + "|" + (modelData.mid || "")] || {}
                      function rlin2(c) {
                        return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
                      }
                      readonly property color fileInk: {
                        var c = Style.normalFill
                        if (!modelData.outgoing && root.selectedRoom && root.selectedRoom.colorsEnabled) {
                          var mem = (root.selectedRoom.members || {})[modelData.from]
                          var col = Lanchat.roomMemberColor(mem)
                          if (col !== "") {
                            c = Qt.rgba(col.r * 0.25 + c.r * 0.75,
                                        col.g * 0.25 + c.g * 0.75,
                                        col.b * 0.25 + c.b * 0.75, 1)
                          }
                        }
                        var lum = 0.2126 * rlin2(c.r) + 0.7152 * rlin2(c.g) + 0.0722 * rlin2(c.b)
                        return lum > 0.35 ? Color.background : Color.popups.text
                      }

                      width: Math.min(roomList.width * 0.8, Style.space(340))
                      height: fileCol.implicitHeight + Style.space(20)
                      radius: Math.max(Style.cornerRadius, Style.space(6))
                      anchors.left: modelData.outgoing ? undefined : parent.left
                      anchors.right: modelData.outgoing ? parent.right : undefined
                      border.width: modelData.outgoing ? 0 : 1
                      border.color: Style.normalBorderColor
                      color: Style.normalFill

                      Column {
                        id: fileCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.topMargin: Style.space(10)
                        anchors.leftMargin: Style.space(14)
                        anchors.rightMargin: Style.space(14)
                        spacing: Style.space(4)

                        Text {
                          width: parent.width
                          text: "\uD83D\uDCCE " + (parent.parent.att.name || "file")
                          color: parent.parent.fileInk
                          font.family: Style.font.family
                          font.pixelSize: Style.font.body
                          elide: Text.ElideMiddle
                        }
                        Text {
                          width: parent.width
                          text: (parent.parent.att.size >= 1024
                            ? Math.round(parent.parent.att.size / 1024) + " KB"
                            : (parent.parent.att.size || 0) + " B")
                          color: parent.parent.fileInk
                          opacity: 0.8
                          font.family: Style.font.family
                          font.pixelSize: Style.font.caption
                        }

                        // Sender-side per-member delivery chips (daemon-driven
                        // roomFileStatus reports; "…" for members with no
                        // report yet — surfaced honestly, never client-guessed).
                        Flow {
                          visible: modelData.outgoing && root.selectedRoom
                          width: parent.width
                          spacing: Style.space(4)

                          Repeater {
                            model: root.selectedRoom ? Object.keys(root.selectedRoom.members || {}) : []
                            delegate: Rectangle {
                              id: statusChip
                              required property var modelData
                              readonly property var st:
                                (fileBubbleRef.statusMap || {})[modelData] || null
                              width: roomStatusText.implicitWidth + Style.space(10)
                              height: Style.space(16)
                              radius: height / 2
                              color: st && st.status === "saved" ? Color.accent : Style.pressedFill
                              visible: modelData !== Lanchat.myId
                              Text {
                                id: roomStatusText
                                anchors.centerIn: parent
                                text: statusChip.st
                                  ? (statusChip.st.status === "saved" ? "✓ " : "! ")
                                    + (statusChip.st.name || statusChip.modelData.slice(0, 8))
                                  : "… " + statusChip.modelData.slice(0, 8)
                                color: statusChip.st && statusChip.st.status === "saved"
                                  ? Color.background : Color.popups.text
                                font.family: Style.font.family
                                font.pixelSize: Style.font.caption
                              }
                            }
                          }
                        }

                        // Save / notice row (non-outgoing only).
                        Row {
                          visible: !modelData.outgoing
                          spacing: Style.space(6)

                          Button {
                            visible: fileBubbleRef.senderIsFriend
                            text: fileBubbleRef.fileDownloading ? "Saving…" : "Save"
                            enabled: !fileBubbleRef.fileDownloading
                            fontSize: Style.font.caption
                            onClicked: Lanchat.acceptRoomAttachment(modelData.from,
                              fileBubbleRef.att.fileId, fileBubbleRef.att.name,
                              modelData.mid, fileBubbleRef.att.sha256 || "",
                              Lanchat.selectedRoomId)
                          }
                          Text {
                            visible: !fileBubbleRef.senderIsFriend
                            text: "⚠ Befriend " + (modelData.fromName || "the sender")
                                  + " to accept this file"
                            color: Color.urgent
                            font.family: Style.font.family
                            font.pixelSize: Style.font.caption
                            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                          }
                        }
                      }
                      // Reference back to the bubble's readonly properties from
                      // the delegates (fileBubbleRef = the Rectangle above).
                      readonly property var fileBubbleRef: fileCol.parent
                    }
                  }
                }
              }
            }

            // ---- chat alert bar --------------------------------------
            // One bar between the thread and compose for all user-facing
            // chat alerts, in priority order: a pending incoming file
            // (actionable, persists), then a transient chat alert (save
            // result, add-friend prompt, or server notice).
            Rectangle {
              id: chatAlertBar
              visible: root.pendingAttachment !== null || root.visibleChatAlert
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
                  width: root.pendingAttachment !== null
                    ? Math.max(10, parent.width - Style.space(84))
                    : Math.max(10, parent.width - Style.space(12))
                  text: {
                    var p = root.pendingAttachment
                    if (p) {
                      if (root.pendingDownloading) {
                        if (Lanchat.dlTotal > 0) {
                          var pct = Math.min(99, Math.floor(100 * Lanchat.dlBytes / Lanchat.dlTotal))
                          return "Saving " + p.attachment.name + "\u2026 " + pct + "%"
                        }
                        return "Saving " + p.attachment.name + "\u2026"
                      }
                      return "Incoming file: " + p.attachment.name
                    }
                    if (root.visibleChatAlert) return Lanchat.chatAlert
                    return ""
                  }
                  color: (root.pendingAttachment === null && Lanchat.chatAlertIsError)
                    ? Color.urgent
                    : Color.popups.text
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }

                Item { width: Style.space(10); height: 1 }

                Button {
                  visible: root.pendingAttachment !== null
                  text: root.pendingDownloading ? "Saving\u2026" : "Save"
                  enabled: !root.pendingDownloading
                  onClicked: {
                    var p = root.pendingAttachment
                    if (p)
                      Lanchat.acceptAttachment(p.from, p.attachment.fileId, p.attachment.name, p.mid, p.attachment.sha256 || "")
                  }
                }
              }
            }

            // ---- pending message undo (countdown ring) -----------------
            Rectangle {
              id: undoBar
              visible: root.pendingForPeer.length > 0
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.bottom: composeBox.top
              // Grows to show each held message (capped) so the user can see
              // exactly what they sent while the countdown runs.
              height: Math.min(root.pendingForPeer.length, 3) * Style.space(42) + Style.space(6)
              color: Style.pressedFill
              Rectangle {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: Color.popups.border
              }

              Column {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.topMargin: Style.space(3)
                anchors.leftMargin: Style.spacing.sm
                anchors.rightMargin: Style.spacing.sm
                spacing: Style.space(2)

                Repeater {
                  model: root.pendingForPeer
                  delegate: Row {
                    required property var modelData
                    width: parent.width
                    height: Style.space(38)
                    spacing: Style.spacing.sm

                    // Undo button with countdown ring.
                    Rectangle {
                      width: Style.space(26)
                      height: Style.space(26)
                      anchors.verticalCenter: parent.verticalCenter
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

                    // The message content the user is about to send: text
                    // (if any) and the attachment name (if any), so it's
                    // reviewable while the countdown runs.
                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      width: Math.max(10, parent.width - Style.space(120))
                      text: {
                        var parts = []
                        if (modelData.text) parts.push(String(modelData.text))
                        if (modelData.attachment && modelData.attachment.name)
                          parts.push("\uD83D\uDCCE " + modelData.attachment.name)
                        return parts.length ? parts.join("  ·  ") : "(attachment)"
                      }
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      elide: Text.ElideRight
                    }

                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      text: Math.ceil(modelData.remaining) + "s"
                      color: Color.accent
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                      font.weight: Font.Bold
                    }
                  }
                }
              }
            }

            // ---- compose box -----------------------------------------
            Rectangle {
              id: composeBox
              width: parent.width
              // Grows to fit the staged-attachment preview (header + list)
              // above the input when files are staged.
              height: Style.space(58) + (root.pendingCount > 0 ? root.pendingHeaderH + root.pendingListH : 0)
              color: "transparent"
              Rectangle {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: Color.popups.border
              }

              Column {
                anchors.fill: parent

                // ---- staged-attachments preview (not yet sent) ----------
                Rectangle {
                  visible: root.pendingCount > 0
                  width: parent.width
                  height: root.pendingHeaderH + root.pendingListH
                  color: Style.normalFill
                  Rectangle {
                    anchors.bottom: parent.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    height: 1
                    color: Color.popups.border
                  }

                  Column {
                    anchors.fill: parent

                    // Header: count + actions.
                    Row {
                      width: parent.width
                      height: root.pendingHeaderH
                      anchors.leftMargin: Style.spacing.sm
                      anchors.rightMargin: Style.spacing.sm
                      spacing: Style.spacing.sm

                      Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: root.pendingCount + (root.pendingCount === 1 ? " file ready" : " files ready")
                        color: Color.popups.text
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        font.weight: Font.DemiBold
                      }

                      Item { width: 1; height: 1 }

                      Button {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "\u2795 Add more"
                        onClicked: root.attachAndSend()
                      }
                      Button {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "\u2713 Send"
                        onClicked: root.send()
                        enabled: root.selectedPeer !== null || root.inRoom
                      }
                      Button {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "\u2715 Clear"
                        onClicked: root.cancelAttachments()
                      }
                    }

                    // Removable file list (scrolls if more than a few staged).
                    ListView {
                      width: parent.width
                      height: root.pendingListH
                      clip: true
                      model: root.pendingAttachments
                      interactive: root.pendingCount > root.pendingMaxVisible

                      delegate: Item {
                        required property var modelData
                        required property int index
                        width: parent.width
                        height: root.pendingRowH

                        Row {
                          anchors.left: parent.left
                          anchors.right: parent.right
                          anchors.verticalCenter: parent.verticalCenter
                          anchors.leftMargin: Style.spacing.sm
                          anchors.rightMargin: Style.spacing.sm
                          spacing: Style.spacing.sm

                          // Image thumbnail (only for image files).
                          Rectangle {
                            width: root.isImagePath(modelData.path) ? Style.space(36) : Style.space(36)
                            height: Style.space(32)
                            radius: Style.space(4)
                            color: root.isImagePath(modelData.path) ? "transparent" : Style.pressedFill
                            border.width: root.isImagePath(modelData.path) ? 1 : 0
                            border.color: Color.popups.border
                            clip: true

                            Image {
                              visible: root.isImagePath(modelData.path)
                              anchors.fill: parent
                              source: "file://" + modelData.path
                              fillMode: Image.PreserveAspectFit
                              layer.enabled: true
                              layer.smooth: true
                            }
                            Text {
                              visible: !root.isImagePath(modelData.path)
                              anchors.centerIn: parent
                              text: "\uF0C6"  // nf-fa-paperclip
                              color: Color.muted
                              font.family: Style.font.family
                              font.pixelSize: Style.font.caption
                            }
                          }

                          Text {
                            width: parent.width - Style.space(36) - Style.space(56)
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData.name
                            color: Color.popups.text
                            font.family: Style.font.family
                            font.pixelSize: Style.font.caption
                            elide: Text.ElideMiddle
                          }

                          Item { width: 1; height: 1 }

                          // Remove this file from the staging list.
                          Button {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "\u2715"
                            onClicked: root.removeAttachment(index)
                          }
                        }
                      }
                    }
                  }
                }

                // Input row (attach button + text field).
                Item {
                  width: parent.width
                  height: Style.space(58)
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
                    enabled: root.selectedPeer !== null || root.inRoom
                  }

                  TextField {
                    id: input
                    anchors.left: attachBtn.right
                    anchors.leftMargin: Style.spacing.sm
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.topMargin: Style.spacing.md
                    anchors.bottomMargin: Style.spacing.md
                    placeholderText: root.inRoom
                      ? "Message #" + (root.selectedRoom ? root.selectedRoom.name : "room") + "…"
                      : (root.selectedPeer
                        ? "Message " + root.selectedPeer.name + "…"
                        : "Select a peer to chat")
                    enabled: root.selectedPeer !== null || root.inRoom
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
      }
    }
  }
}
