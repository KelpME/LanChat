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

  // Peer-list row height — single text line (same as room member lines).
  property real peerRowH: Style.space(24)

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

  // Am I the owner of the room with this id? (Owner controls render on that
  // room's member lines in the rooms list regardless of what's open.)
  function amRoomOwnerOf(roomId) {
    var r = Lanchat.roomStates[roomId]
    if (!r && Lanchat.rooms) {
      for (var i = 0; i < Lanchat.rooms.length; i++)
        if (Lanchat.rooms[i].roomId === roomId) { r = Lanchat.rooms[i]; break }
    }
    return !!r && Lanchat.myId !== "" && r.owner === Lanchat.myId
  }

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
    var text = inputText.trim()
    // Allow sending attachments with no text when any are staged.
    if (!text && root.pendingCount === 0) return
    // Room send: ONE message per staged file, with the typed text riding the
    // FIRST file as its caption (exactly like a 1:1 attachment + text). Plain
    // text sends stay text-only — no phantom attachments.
    if (root.inRoom) {
      if (root.pendingCount > 0) {
        for (var r = 0; r < root.pendingAttachments.length; r++) {
          var ra = root.pendingAttachments[r]
          Lanchat.sendRoomFile(Lanchat.selectedRoomId, ra.path, ra.name, r === 0 ? text : "")
        }
      } else {
        Lanchat.sendRoom(Lanchat.selectedRoomId, text)
      }
      setInputText("")
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
    setInputText("")
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
  function commitName(t) {
    var name = String(t || "").trim()
    if (name !== "") Lanchat.setMyName(name)
  }

  // Begin editing a message: load its text into the compose box and flag the
  // next send as an edit of that mid.
  // Compose-input access: the TextField (id: input) moved into
  // shared/ComposeBox.qml (step 3) — the panel reaches it through these
  // accessors instead of the file-local id (same read/clear/focus effects
  // the inline code had).
  readonly property string inputText: composeBox ? composeBox.inputText : ""
  function setInputText(t) {
    composeBox.inputText = t
  }
  function focusInput() {
    composeBox.focusInput()
  }
  property string editingMid: ""
  // Whether the friend-request notifications banner is expanded (dropdown).
  // (1.3) First-run onboarding: shows a dismissible banner below the peer list
  // explaining that Lanchat is invisible by default. Dismissed state is local
  // to this session (not persisted) — keeps the first use obvious without a
  // permanent config flag.
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

  // True when there's a chat alert to show in the input box for the
  // currently selected peer (or a peer-agnostic one). Alerts render as a
  // temporary overlay inside the compose input (auto-clear after ~5s via
  // chatAlertTimer) — never as a layout bar that can push UI off-screen.
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
    setInputText(text)
    focusInput()
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

  // Typing lifecycle moved into the compose input (step 3): the child emits
  // these; the panel keeps the timer and the stopped-call semantics.
  signal typingStarted()
  signal typingStopped()

  // Stops the typing indicator on the peer side after idle.
  property Timer typingTimer: Timer {
    interval: 2000
    onTriggered: {
      if (root.selectedPeerId) Lanchat.sendTypingStopped(root.selectedPeerId)
    }
  }
  onTypingStarted: typingTimer.restart()
  onTypingStopped: {
    if (root.selectedPeerId) Lanchat.sendTypingStopped(root.selectedPeerId)
    typingTimer.stop()
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

  // Create-room inline mini-dialog state (rendered inside the KeyboardPanel
  // content, so no external modal is needed).
  property bool showRoomNameDialog: false
  property string newRoomName: ""
  function commitCreateRoom() {
    var n = roomNameField ? roomNameField.text.trim() : ""
    if (n !== "") {
      Lanchat.createRoom(n)
      roomListSection.expand()
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
              PeerList {
                id: peerListPanel
                anchors.fill: parent

                peerRowH: root.peerRowH
                selectedPeerId: root.selectedPeerId
                selectedRoom: root.selectedRoom
                inRoom: root.inRoom
                showFwAlert: root.showFwAlert
                bottomInset: roomListSection.sectionHeight

                friendStateFn: root.friendState
                shortFpFn: root.shortFp

                onPeerSelected: function(id) { root.selectPeer(id) }
                onChatClosed: root.closeChat()
                onAddPeerToRoomRequested: function(id) { root.addPeerToRoom(id) }
                onFriendAccepted: function(id) { root.acceptFriend(id) }
                onFriendRejected: function(id) { root.rejectFriend(id) }
                onFriendCancelled: function(id) { root.cancelFriend(id) }
              }
              // ---- rooms: collapsible section UNDER the peers list ------
              // Each room row expands to show its member list beneath it —
              // ONE text line tall per member, cramming everyone in. Member
              // rows carry the same controls the roster had (remove ✕ +
              // per-member can-add toggle for the owner). The whole section
              // stays collapsible; adding people is unchanged (the "Add to
              // group" button on friend peer rows).
              RoomListSection {
                id: roomListSection
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: settings.top

                peerRowH: root.peerRowH
                settingsCol: settings
                amRoomOwnerOfFn: root.amRoomOwnerOf
                selectRoomFn: root.selectRoom
                leaveSelectedRoomFn: root.leaveSelectedRoom

                onRoomSelected: function(roomId) { root.selectRoom(roomId) }
                onRoomLeaveRequested: root.leaveSelectedRoom()
                onRoomCreateRequested: { root.showRoomNameDialog = true; root.newRoomName = "" }
              }
              // ---- settings: collapsible ------------------------------
              SettingsPanel {
                id: settings
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom

                expanded: false
                alertStackBottom: peerListPanel.alertStackBottom
                hostHeight: parent.height

                selectedOwnedRoom: root.selectedOwnedRoom
                themePalette: root.themePalette
                ownsAnyRoom: root.ownsAnyRoom
                panelW: root.panelW
                panelH: root.panelH

                diagLine: root.diagLine
                shortPath: root.shortPath

                onCollapseRoomsRequested: roomListSection.collapseSection()
                onOpenHelpRequested: root.openHelp()
                onCommitNameRequested: function(text) { root.commitName(text) }
                onCopyToClipboardRequested: function(text) { root.copyToClipboard(text) }
                onAddFriendRequested: root.addFriendByFingerprint()
                onApplyPanelSizeRequested: function(size) { root.applyPanelSize(size) }
                onApplyManualSizeRequested: function(wText, hText) { root.applyManualSize(wText, hText) }
                onPickDownloadDirRequested: root.pickDownloadDir()
                onCopyDiagnosticsRequested: root.copyDiagnostics()
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

            // The 1:1 thread and the room view are shared/ChatThread.qml and
            // shared/RoomView.qml (step 6/8). Their height formulas referenced the
            // sibling ids composeBox/threadHeader/chatAlertBar — file-local to
            // Panel.qml — so sizing stays here at the call site (height passed
            // on the instance; each root uses width: parent.width internally).
            ChatThread {
              id: chatThreadView
              height: parent.height - composeBox.height - threadHeader.height
                      - (chatAlertBar.visible ? chatAlertBar.height : 0)
              inRoom: root.inRoom
              thread: root.thread
              selectedPeerId: root.selectedPeerId
              selectedPeer: root.selectedPeer
              hasThread: root.hasThread
              editingMid: root.editingMid
              timeLabel: root.timeLabel
              onEditRequested: function(mid, text) { root.editMsg(mid, text) }
              onCopyRequested: function(text) { root.copyToClipboard(text) }
            }

            // ROOM VIEW (when a room is selected) — see shared/RoomView.qml header.
            RoomView {
              id: roomViewPane
              height: parent.height - composeBox.height - threadHeader.height
                      - (chatAlertBar.visible ? chatAlertBar.height : 0)
              inRoom: root.inRoom
              roomThread: root.roomThread
              selectedRoom: root.selectedRoom
              timeLabel: root.timeLabel
            }
            // ---- incoming-file bar -------------------------------------
            // ACTIONABLE file receipt bar between the thread and compose.
            // Its height IS accounted for by the thread's height formula
            // (pendingAttachment is only non-null for the selected peer, and
            // the compose box is where the Save happens, so this never
            // overflows). Transient chat alerts (warnings, save results,
            // notices) do NOT use this bar anymore — they render inside the
            // compose input for a few seconds (see inputAlert overlay).
            Rectangle {
              id: chatAlertBar
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
                  width: Math.max(10, parent.width - Style.space(84))
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
                    return ""
                  }
                  color: Color.popups.text
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
            // The staged preview + input row moved to shared/ComposeBox.qml
            // (step 3). The instance keeps the composeBox id so the undo bar
            // and the thread height formulas still resolve it, and the panel
            // keeps send() — the child emits sendRequested() (zero behavior
            // change; inputs mirror what `root.` provided inline before).
            ComposeBox {
              id: composeBox
              pendingCount: root.pendingCount
              pendingHeaderH: root.pendingHeaderH
              pendingRowH: root.pendingRowH
              pendingMaxVisible: root.pendingMaxVisible
              pendingListH: root.pendingListH
              pendingAttachments: root.pendingAttachments
              selectedPeer: root.selectedPeer
              inRoom: root.inRoom
              selectedRoom: root.selectedRoom
              visibleChatAlert: root.visibleChatAlert
              selectedPeerId: root.selectedPeerId
              isImagePath: root.isImagePath
              onSendRequested: root.send()
              onAttachRequested: root.attachAndSend()
              onCancelRequested: root.cancelAttachments()
              onRemoveRequested: function(index) { root.removeAttachment(index) }
              onTypingStarted: root.typingStarted()
              onTypingStopped: root.typingStopped()
            }
          }
        }
      }
    }
  }
}
