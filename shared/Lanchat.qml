import QtQuick
import Quickshell
import Quickshell.Io

pragma Singleton

// Shared state for the whole KelpME.lanchat plugin. A true QML singleton, so
// no matter how many bar surfaces or entry points exist, exactly one daemon
// Process is spawned and every UI reads the same peers/messages state.
//
// Transport contract with server.py:
//   stdin  <-  {"cmd":"send","to":<id>,"text":"..."}  /  {"cmd":"history"}  /  {"cmd":"list"}
//   stdout ->  newline-delimited JSON events: ready, peer, peer-gone,
//              message, history, peers, error, notice.
QtObject {
  id: lanchat

  property string myName: ""
  property int myPort: 0
  property bool daemonReady: false
  // Human-readable daemon lifecycle state for the UI. "starting" = process is
  // up but not yet ready; "running" = ready event received; "down" = process
  // exited or was never started (so the user can tell when the daemon died).
  property string daemonState: "down"

  // Transient chat alert shown in the thread's alert bar: save results,
  // add-friend prompt, and server notices. chatAlertPeerId scopes it to a
  // peer's thread; empty means show for any selected peer. Dev-facing alerts
  // (daemon errors, crashes) go to `diagnostics` instead.
  property string chatAlert: ""
  property bool chatAlertIsError: false
  property string chatAlertPeerId: ""
  function showChatAlert(msg, isError, peerId) {
    chatAlert = msg
    chatAlertIsError = !!isError
    chatAlertPeerId = peerId || ""
    chatAlertTimer.restart()
  }

  property bool panelOpen: false
  property string version: ""
  // This device's cert fingerprint (stable identity) — shown as "My ID" in
  // Settings and used to add friends by fingerprint in private mode.
  property string myId: ""

  property bool httpEnabled: false
  property int httpPort: 4814
  property bool apiFullAccess: false
  property string panelSize: "medium"
  // (1.3) Discovery/request model.
  property string visibility: "private"   // "open" | "private"
  property bool acceptRequests: true
  // Manual pixel override for panel size; 0 = follow the panelSize preset.
  property int customW: 0
  property int customH: 0
  property int peerColW: 0
  property string status: "available"
  property bool soundEnabled: true
  property bool typingEnabled: true
  property bool showTyping: true
  property bool readReceiptsEnabled: true
  property bool showReadReceipts: true

  property bool online: true
  property var friends: []        // [{id,address,name,confirmed}]

  property string downloadDir: ""

  // Live state of the in-flight attachment download (idle when dlFileId is "").
  property string dlFileId: ""
  property string dlName: ""
  property double dlBytes: 0
  property double dlTotal: 0
  readonly property bool dlActive: lanchat.dlFileId !== ""
  property int sendDelay: 0
  property var pendingSends: []  // [{mid, to, text, remaining, total}] undo-window
  property var friendRequests: [] // [{peerId, name, outgoing, ts, mid}] pending friend requests (notifications)
  property var typing: ({})      // peerId -> name currently typing
  property var readReceipts: {}  // mid -> true (peer confirmed reading)

  property var peers: []          // [{id,name,address,port,lastSeen}] online peers
  property var displayPeers: []   // peers merged with offline confirmed/pending friends
  property var messages: []       // [{from,fromName,text,ts,outgoing}]
  property int unreadCount: 0
  property int onlineCount: 0
  property var diagnostics: []    // [{ts, message, ...}] diagnostic log lines

  // ---- group chat rooms ---------------------------------------------------
  // Rooms the daemon knows: authoritative copy (rooms we own) merged with the
  // last-known cache (rooms owned elsewhere) — the roster NEVER blanks while
  // the host is offline; the daemon emits full snapshots (room-state), the UI
  // only renders what it carries (daemon is the single source of truth).
  property var rooms: []          // [{roomId,name,owner,seq,colorsEnabled,memberCount}] summaries
  property var roomStates: ({})   // roomId -> full authoritative room snapshot
  property string selectedRoomId: ""
  property var roomMessages: []   // [{...,room:roomId}] messages belonging to rooms
  property var roomInvites: []    // [{roomId,name,from,fromName}] unaccepted invites
  property var roomFileStatuses: ({}) // roomId|mid -> {peerId: {status,name}}
  property bool roomHostOnline: true // mirrored room/owner connectivity (frozen-state banner)

  // Re-evaluate host-online for the selected room: owner == us → online;
  // otherwise the owner must appear in the live peer set (the daemon drops
  // disappeared peers, so presence in `peers` = we have a live socket).
  function updateRoomHostOnline() {
    var snap = roomStates[selectedRoomId]
    if (!snap || !snap.owner) { roomHostOnline = true; return }
    if (snap.owner === myId) { roomHostOnline = true; return }
    var found = false
    for (var i = 0; i < peers.length; i++)
      if (peers[i].id === snap.owner) { found = true; break }
    roomHostOnline = found
  }

  // Per-peer lazy-load state: total messages on the server and how many we've
  // loaded for each peer (so we can fetch older ones on scroll).
  property var historyMeta: ({})   // peerId -> {total, loaded}

  readonly property bool hasPeers: onlineCount > 0

  // ---- daemon lifecycle -------------------------------------------------

  function serverPath() {
    var url = Qt.resolvedUrl("../server.py").toString()
    if (url.indexOf("file://") === 0) url = url.slice(7)
    return decodeURIComponent(url)
  }

  // The daemon runs under systemd (systemd/lanchat.service), so this shell
  // spawns a bridge instead of server.py directly. The bridge connects to the
  // daemon's unix-socket control channel and proxies stdin<->socket and
  // socket->stdout, keeping the Process-based command/event wiring below
  // unchanged. It exits when the daemon is down; onDaemonExit reports that and
  // restartTimer respawns the bridge.
  function bridgePath() {
    var url = Qt.resolvedUrl("../lanchat-bridge.py").toString()
    if (url.indexOf("file://") === 0) url = url.slice(7)
    return decodeURIComponent(url)
  }

  // Path to the daemon's diagnostic log (set from the daemon's ready event).
  property string logPathValue: ""
  function logPath() {
    return lanchat.logPathValue
  }

  // Firewall reachability state (is lanchat's port 4812 open inbound?).
  // fwOpen: true=open, false=blocked, null=unknown (couldn't read).
  property var firewall: ({ open: null, backend: "", detail: "" })
  // Last error string when an open/close action failed (e.g. sudoers missing).
  property string firewallError: ""

  // ---- update availability ----------------------------------------------
  // The plugin is a git checkout, so an update is available when the local
  // HEAD differs from the remote's HEAD. The check is read-only (ls-remote +
  // rev-parse) — it never modifies the checkout, so it won't disturb a
  // parallel session or block an update. updateError is set when the check
  // couldn't run (offline / no git).
  property bool updateAvailable: false
  property bool updateChecking: false
  property string updateError: ""

  // Update-apply state. updateApplying is true while a background update runs;
  // updateApplyState is "" (idle), "dirty" (local edits block a safe update —
  // the UI offers a clean install), or "applied" (success, shell restarting).
  property bool updateApplying: false
  property string updateApplyState: ""

  // Absolute path of the installed plugin directory (resolved from server.py,
  // so it's portable across machines and works even if the plugin is relocated).
  function pluginDir() {
    var sp = lanchat.serverPath()
    var idx = sp.lastIndexOf("/")
    return idx > 0 ? sp.slice(0, idx) : ""
  }

  // Compare local HEAD vs remote HEAD (read-only). Runs in a background
  // Process so the UI never blocks; tolerant of an offline machine.
  function checkForUpdate() {
    var dir = lanchat.pluginDir()
    if (!dir) { lanchat.updateError = "unknown plugin directory"; return }
    lanchat.updateChecking = true
    lanchat.updateError = ""
    var cmd = "cd " + dir + " && local=$(git rev-parse HEAD 2>/dev/null); " +
      "remote=$(git ls-remote origin HEAD 2>/dev/null | awk '{print $1}'); " +
      "if [ -z \"$remote\" ]; then echo \"UNKNOWN\"; " +
      "elif [ \"$local\" = \"$remote\" ]; then echo \"CURRENT\"; " +
      "else echo \"UPDATE\"; fi"
    updateProc.command = ["bash", "-c", cmd]
    updateProc.running = true
  }

  // Apply the remote update to the installed checkout. SAFE by default
  // (force=false): if the checkout has local (uncommitted) edits — e.g. a
  // parallel session's in-flight work — we refuse to touch it and report
  // "dirty" so the UI can offer a clean install. force=true discards local
  // edits and resets to the remote commit (a clean install of the latest).
  // Uses `git fetch origin main` (the real branch ref) + `git reset --hard
  // origin/main` — the reliable path; `omarchy plugin update`'s HEAD-fetch is
  // known to leave origin/main stale. Runs in a background Process. The daemon
  // auto-restarts via the lanchat.path watcher (server.py change); QML reloads
  // on the shell restart that restartShell() schedules after APPLIED.
  function applyUpdate(force) {
    var dir = lanchat.pluginDir()
    if (!dir) { lanchat.updateError = "unknown plugin directory"; return }
    if (lanchat.updateApplying) return
    lanchat.updateApplying = true
    lanchat.updateApplyState = ""
    lanchat.updateError = ""
    // Skip the dirty-guard when force=true (clean install): reset regardless.
    var safe = force ? "" :
      "test -z \"$(git status --porcelain)\" || { echo DIRTY; exit 0; }; "
    var cmd = "cd " + dir + " && git fetch origin main 2>/dev/null && { " + safe +
      "git reset --hard origin/main 2>/dev/null && echo APPLIED || echo ERROR; } || echo ERROR"
    applyProc.command = ["bash", "-c", cmd]
    applyProc.running = true
  }

  // Restart the Omarchy shell after an update so the newly-checked-out QML
  // (compiled into quickshell at startup, cached) actually loads. Clears the
  // QML cache first, then detaches the restart via setsid so it survives this
  // shell being torn down. A short sleep lets the UI paint the "applied" state
  // (and the chat alert) before the shell goes away.
  function restartShell() {
    var cmd = "sleep 0.8; rm -rf \"$HOME/.cache/quickshell/qmlcache\"; " +
      "setsid /usr/sbin/omarchy-restart-shell >/dev/null 2>&1 &"
    restartProc.command = ["bash", "-c", cmd]
    restartProc.running = true
  }

  // Path to the systemd-ensure helper (installs/enables the daemon's systemd
  // unit on first run so a fresh plugin install is fully automatic).
  function ensureSystemdPath() {
    var url = Qt.resolvedUrl("../lanchat-ensure-systemd.py").toString()
    if (url.indexOf("file://") === 0) url = url.slice(7)
    return decodeURIComponent(url)
  }

  // True once the systemd unit has been confirmed installed+enabled this
  // session. Reset on shell restart (Component.onCompleted re-runs ensure).
  property bool systemdEnsured: false

  function startDaemon() {
    if (daemon.running) return
    if (!lanchat.systemdEnsured) {
      // First run (or first run after a fresh install): make sure the daemon's
      // systemd unit is installed, enabled, and started before we spawn the
      // bridge. The ensure helper is idempotent and needs no sudo.
      lanchat.daemonState = "starting"
      ensureProcess.command = ["python3", lanchat.ensureSystemdPath()]
      ensureProcess.running = true
      return
    }
    lanchat.daemonState = "starting"
    daemon.command = ["python3", lanchat.bridgePath()]
    daemon.running = true
  }

  // ---- commands to the daemon -------------------------------------------

  // Current status of a peer ("" if not discovered / offline).
  function peerStatus(id) {
    var list = lanchat.displayPeers
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) {
        var s = list[i].status || "available"
        return s === "offline" ? "" : s
      }
    }
    return ""  // not online
  }

  // Is a peer currently available to receive messages? False if offline or DND.
  function canDeliver(id) {
    var s = peerStatus(id)
    return s !== "" && s !== "dnd"
  }

  // Is this peer a confirmed friend? Sending to anyone else initiates a
  // handshake (friend request held until they accept) instead of a plain message.
  function isConfirmedFriend(id) {
    var list = lanchat.friends
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id && list[i].confirmed) return true
    }
    return false
  }

  // A friend request is outstanding in one direction (we requested them or they
  // requested us), but they haven't accepted yet.
  function isPending(id) {
    var list = lanchat.friends
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id && !list[i].confirmed) return true
    }
    return false
  }

  // Display name of a discovered peer (fallback: friends list, then their id).
  function peerName(id) {
    var list = lanchat.displayPeers
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) return list[i].name
    }
    var friends = lanchat.friends
    for (var j = 0; j < friends.length; j++) {
      if (friends[j].id === id) return friends[j].name || id
    }
    return id
  }

  // Merge the live (online) peers with confirmed/pending friends so a friend
  // who goes offline stays visible, marked "offline", instead of vanishing.
  // Strangers still disappear when they stop broadcasting. Rebuilds the
  // display list (and onlineCount) whenever peers or friends change.
  function rebuildDisplayPeers() {
    var map = {}
    var peers = lanchat.peers
    for (var i = 0; i < peers.length; i++) map[peers[i].id] = peers[i]
    var friends = lanchat.friends
    for (var j = 0; j < friends.length; j++) {
      var f = friends[j]
      if (!map[f.id]) {
        map[f.id] = { id: f.id, name: f.name || lanchat.peerName(f.id),
                      address: f.address || "", port: 0, httpPort: 0,
                      status: "offline", lastSeen: 0, version: "" }
      }
    }
    var out = []
    for (var id in map) out.push(map[id])
    out.sort(function(x, y) {
      return String(x.name).toLowerCase().localeCompare(String(y.name).toLowerCase())
    })
    lanchat.displayPeers = out
    var online = 0
    for (var k = 0; k < out.length; k++) if (out[k].status !== "offline") online++
    lanchat.onlineCount = online
  }

  // Explicit "add a friend" action — decoupled from messaging. Sends a friend
  // request to a peer. Friendship is the bootstrap (first contact between
  // strangers), so the request goes over UDP (signed), NOT TCP — the TCP path
  // requires an established connection that doesn't exist between non-friends
  // yet (chicken-and-egg). The recipient verifies our signature and the
  // request appears in their notifications banner.
  function requestFriend(id) {
    var name = lanchat.peerName(id)
    daemon.write(JSON.stringify({ cmd: "udpFriendRequest", to: id, name: name }) + "\n")
  }

  // The daemon's send cmd. Friendship is its own action (requestFriend), so a
  // message is never silently turned into a friend request.
  function sendPayload(to, text, attachment) {
    return JSON.stringify({ cmd: "send", to: to, text: text || "", attachment: attachment || null,
      friend_request: false }) + "\n"
  }

  // Send a message. If the peer is DND or offline, hold it in the queue with a
  // "!" indicator until they're available again. Otherwise send normally
  // (honoring the undo/send-delay window).
  function send(to, text, attachment) {
    if (!text && !attachment) return
    if (!to) return
    var mid = "m" + Date.now().toString(36) + Math.floor(Math.random() * 1e6).toString(36)
    // A stranger can't be messaged yet — adding a friend is a separate action
    // (the peer-card button). Prompt, don't silently turn the message into a
    // friend request.
    if (!lanchat.isConfirmedFriend(to) && !lanchat.isPending(to)) {
      lanchat.showChatAlert("Add " + lanchat.peerName(to) + " as a friend to start chatting", false, to)
      return
    }
    if (sendDelay > 0) {
      pendingSends = pendingSends.concat([{ mid: mid, to: to, text: text, attachment: attachment, total: sendDelay, remaining: sendDelay }])
      undoTimer.restart()
    } else {
      daemon.write(lanchat.sendPayload(to, text, attachment))
    }
  }

  // Cancel a held (undelivered) message.
  function undo(mid) {
    pendingSends = pendingSends.filter(function(s) { return s.mid !== mid })
  }

  function refreshHistory(peer, offset, limit) {
    daemon.write(JSON.stringify({ cmd: "history", peer: peer || "", offset: offset || 0, limit: limit || 100 }) + "\n")
  }

  // ---- group chat rooms ---------------------------------------------------

  function refreshRooms() {
    daemon.write(JSON.stringify({ cmd: "roomList" }) + "\n")
  }

  function createRoom(name) {
    daemon.write(JSON.stringify({ cmd: "createRoom", name: name }) + "\n")
  }

  function selectRoom(roomId) {
    selectedRoomId = roomId || ""
    if (roomId) {
      daemon.write(JSON.stringify({ cmd: "roomHistory", roomId: roomId, offset: 0, limit: 100 }) + "\n")
      daemon.write(JSON.stringify({ cmd: "roomList" }) + "\n")
    }
  }

  function sendRoom(roomId, text) {
    if (!roomId || !text || !text.trim()) return
    daemon.write(JSON.stringify({ cmd: "roomSend", roomId: roomId, text: text }) + "\n")
  }

  function sendRoomFile(roomId, path, name, caption) {
    if (!roomId || !path) return
    daemon.write(JSON.stringify({ cmd: "roomFile", roomId: roomId,
      attachment: { path: path, name: name || "" }, text: caption || "" }) + "\n")
  }

  function roomInvite(roomId, peerId) {
    daemon.write(JSON.stringify({ cmd: "roomInvite", roomId: roomId, peer: peerId }) + "\n")
  }

  function roomAdd(roomId, peerId) {
    daemon.write(JSON.stringify({ cmd: "roomAdd", roomId: roomId, peer: peerId }) + "\n")
  }

  function roomJoin(roomId) {
    daemon.write(JSON.stringify({ cmd: "roomJoin", roomId: roomId }) + "\n")
  }

  function roomLeave(roomId) {
    daemon.write(JSON.stringify({ cmd: "roomLeave", roomId: roomId }) + "\n")
  }

  function roomRemove(roomId, peerId) {
    daemon.write(JSON.stringify({ cmd: "roomRemove", roomId: roomId, peer: peerId }) + "\n")
  }

  function roomSetCanInvite(roomId, peerId, allowed) {
    daemon.write(JSON.stringify({ cmd: "roomSetCanInvite", roomId: roomId, peer: peerId, allowed: allowed }) + "\n")
  }

  function setRoomColor(roomId, token, hex) {
    daemon.write(JSON.stringify({ cmd: "setRoomColor", roomId: roomId, token: token, hex: hex }) + "\n")
  }

  function toggleRoomColors(roomId, enabled) {
    daemon.write(JSON.stringify({ cmd: "toggleRoomColors", roomId: roomId, enabled: enabled }) + "\n")
  }

  // Room file save: same pull transport as 1:1, plus the room id so the
  // daemon enforces the friend trust gate and reports roomFileStatus.
  function acceptRoomAttachment(from, fileId, name, mid, sha256, roomId) {
    dlFileId = fileId
    dlName = name || ""
    dlBytes = 0
    dlTotal = 0
    daemon.write(JSON.stringify({ cmd: "acceptAttachment", from: from, fileId: fileId, name: name,
      mid: mid || "", sha256: sha256 || "", room: roomId || "" }) + "\n")
  }

  // Resolve a member color to a renderable color. token "theme" → the
  // viewer's own theme accent (live-updates on theme change); otherwise the
  // hex carried in the room event (identical on every machine).
  function roomMemberColor(member) {
    if (!member || !member.color) return ""
    var c = member.color
    if (c.token === "theme") return String(Color.accent)
    return c.hex || ""
  }

  // Load an older page of a peer's history (lazy-load on scroll to top).
  function loadOlder(peer) {
    var meta = historyMeta[peer] || { total: 0, loaded: 0 }
    if (meta.loaded >= meta.total) return
    var chunk = 50
    daemon.write(JSON.stringify({ cmd: "history", peer: peer, offset: meta.loaded, limit: chunk }) + "\n")
  }

  // Reset a peer's history view (e.g. on clear chat or peer switch).
  function resetHistoryMeta(peer) {
    historyMeta[peer] = { total: 0, loaded: 0 }
  }

  function refreshPeers() {
    daemon.write(JSON.stringify({ cmd: "list" }) + "\n")
  }

  function clearUnread() {
    unreadCount = 0
  }

  function clearChat(peer) {
    daemon.write(JSON.stringify({ cmd: "clearChat", peer: peer }) + "\n")
  }

  // Clear every conversation at once.
  function clearAllChats() {
    daemon.write(JSON.stringify({ cmd: "clearAllChats" }) + "\n")
  }

  function deleteMessage(mid) {
    daemon.write(JSON.stringify({ cmd: "deleteMessage", mid: mid }) + "\n")
  }

  function editMessage(mid, text) {
    daemon.write(JSON.stringify({ cmd: "editMessage", mid: mid, text: text }) + "\n")
  }

  function setDownloadDir(dir) {
    downloadDir = dir
    daemon.write(JSON.stringify({ cmd: "setDownloadDir", dir: dir }) + "\n")
  }

  function setSendDelay(seconds) {
    sendDelay = seconds
    daemon.write(JSON.stringify({ cmd: "setSendDelay", seconds: seconds }) + "\n")
  }

  // Accept an incoming file: ask the daemon to pull it from the sender and
  // save it. mid + sha256 let the daemon echo the right completion and verify
  // the download. Sets live download state so the UI can show progress.
  function acceptAttachment(from, fileId, name, mid, sha256) {
    dlFileId = fileId
    dlName = name || ""
    dlBytes = 0
    dlTotal = 0
    daemon.write(JSON.stringify({ cmd: "acceptAttachment", from: from, fileId: fileId, name: name,
      mid: mid || "", sha256: sha256 || "" }) + "\n")
  }

  // Toggle the optional HTTP API (start/stop the daemon's HTTP server).
  function setHttpEnabled(enabled) {
    httpEnabled = enabled
    daemon.write(JSON.stringify({ cmd: "setHttp", enabled: enabled }) + "\n")
  }

  // Firewall controls: read current status, or open/close port 4812.
  function refreshFirewall() {
    daemon.write(JSON.stringify({ cmd: "firewallStatus" }) + "\n")
  }
  function firewallOpen() {
    lanchat.firewallError = ""
    daemon.write(JSON.stringify({ cmd: "firewallOpen" }) + "\n")
  }
  function firewallClose() {
    lanchat.firewallError = ""
    daemon.write(JSON.stringify({ cmd: "firewallClose" }) + "\n")
  }

  // Set this machine's display name (persisted by the daemon).
  function setMyName(name) {
    var clean = String(name || "").trim()
    if (!clean) return
    myName = clean
    daemon.write(JSON.stringify({ cmd: "setName", name: clean }) + "\n")
  }

  // Ask the daemon to re-roll to a fresh random friendly name.
  function regenerateName() {
    daemon.write(JSON.stringify({ cmd: "regenerateName" }) + "\n")
  }

  // Online/offline presence toggle.
  function setOnline(on) {
    online = on
    daemon.write(JSON.stringify({ cmd: "setOnline", online: on }) + "\n")
  }

  // (1.3) Add a confirmed friend directly by their cert fingerprint. The
  // friend's address is learned automatically from discovery (confirmed
  // friends are accepted in private mode), so only the fingerprint is needed.
  function addFriendByFingerprint(id) {
    var fid = String(id || "").trim()
    if (!fid) return
    daemon.write(JSON.stringify({ cmd: "setFriend", id: fid }) + "\n")
  }

  // Accept/reject an incoming friend request.
  function acceptFriend(id) {
    daemon.write(JSON.stringify({ cmd: "acceptFriend", id: id }) + "\n")
  }
  function rejectFriend(id) {
    daemon.write(JSON.stringify({ cmd: "rejectFriend", id: id }) + "\n")
  }

  // Withdraw a friend request WE sent that is still pending (peer hasn't
  // accepted). Retracts it on both sides and clears the outgoing banner.
  function cancelFriendRequest(id) {
    daemon.write(JSON.stringify({ cmd: "cancelFriendRequest", id: id }) + "\n")
  }

  // Remove a peer from your friends list (unfriend).
  function unfriend(id) {
    daemon.write(JSON.stringify({ cmd: "unfriend", id: id }) + "\n")
  }

  // Broadcast typing / stopped-typing / read to a peer (gated by toggles).
  function sendTyping(to) {
    if (!typingEnabled) return
    daemon.write(JSON.stringify({ cmd: "typing", to: to }) + "\n")
  }
  function sendTypingStopped(to) {
    if (!typingEnabled) return
    daemon.write(JSON.stringify({ cmd: "typingStopped", to: to }) + "\n")
  }
  function sendReadReceipt(to, mid) {
    if (!readReceiptsEnabled) return
    daemon.write(JSON.stringify({ cmd: "readReceipt", to: to, mid: mid }) + "\n")
  }

  function setTypingEnabled(on) {
    typingEnabled = on
    daemon.write(JSON.stringify({ cmd: "setTypingEnabled", enabled: on }) + "\n")
  }
  function setShowTyping(on) {
    showTyping = on
    daemon.write(JSON.stringify({ cmd: "setShowTyping", enabled: on }) + "\n")
  }
  function setReadReceiptsEnabled(on) {
    readReceiptsEnabled = on
    daemon.write(JSON.stringify({ cmd: "setReadReceiptsEnabled", enabled: on }) + "\n")
  }
  function setShowReadReceipts(on) {
    showReadReceipts = on
    daemon.write(JSON.stringify({ cmd: "setShowReadReceipts", enabled: on }) + "\n")
  }

  // Toggle full API access to chat data (read history/peers) vs send-only.
  function setApiFullAccess(on) {
    apiFullAccess = on
    daemon.write(JSON.stringify({ cmd: "setApiFullAccess", enabled: on }) + "\n")
  }

  // (1.3) Set discovery visibility: "open" (broadcast, discoverable) or
  // "private" (invisible). Takes effect immediately in the daemon's UDP loop.
  function setVisibility(v) {
    var val = (v === "open") ? "open" : "private"
    visibility = val
    daemon.write(JSON.stringify({ cmd: "setVisibility", visibility: val }) + "\n")
  }

  // (1.3) Toggle whether inbound friend requests are accepted.
  function setAcceptRequests(on) {
    acceptRequests = !!on
    daemon.write(JSON.stringify({ cmd: "setAcceptRequests", enabled: !!on }) + "\n")
  }

  // Set the panel size: "small" | "medium" | "large" | "xl" | "full".
  function setPanelSize(size) {
    panelSize = size
    daemon.write(JSON.stringify({ cmd: "setPanelSize", size: size }) + "\n")
  }

  // Set a manual pixel size for the panel. 0 on either axis means "follow
  // the preset" for that axis; persisted so the size survives a restart.
  function setCustomSize(w, h) {
    customW = w
    customH = h
    daemon.write(JSON.stringify({ cmd: "setCustomSize", w: w, h: h }) + "\n")
  }

  // Persist the left peer-column width set by the draggable divider.
  // 0 = fall back to the UI default.
  function setPeerColW(w) {
    peerColW = w
    daemon.write(JSON.stringify({ cmd: "setPeerColW", w: w }) + "\n")
  }

  // Set user status: "available" | "dnd" | "away" | "brb".
  function setStatus(s) {
    status = s
    daemon.write(JSON.stringify({ cmd: "setStatus", status: s }) + "\n")
  }

  // Toggle the new-message sound.
  function setSoundEnabled(on) {
    soundEnabled = on
    daemon.write(JSON.stringify({ cmd: "setSoundEnabled", enabled: on }) + "\n")
  }

  // Play the bundled message chime via paplay (no deps).
  // Silenced when DND — don't disturb means no audio either.
  function playMessageSound() {
    if (!soundEnabled) return
    if (lanchat.status === "dnd") return
    var url = Qt.resolvedUrl("../sounds/message.ogg").toString()
    if (url.indexOf("file://") === 0) url = url.slice(7)
    Quickshell.execDetached(["paplay", decodeURIComponent(url)])
  }

  // ---- events from the daemon -------------------------------------------

  // Insert or replace a message in the list keyed by mid (dedup on reveal).
  function upsertMessage(m) {
    // Room messages live in their own store (roomMessages) so the room view
    // updates live — a message carrying a room id must never land in the 1:1
    // message list (that would double-render it once the room view ALSO
    // shows it, and leave the 1:1 thread with stray content).
    if (m.room) {
      var rms = lanchat.roomMessages.slice()
      var ridx = -1
      for (var r = 0; r < rms.length; r++) {
        if (rms[r].mid === m.mid) { ridx = r; break }
      }
      if (ridx >= 0) rms[ridx] = m
      else rms.push(m)
      lanchat.roomMessages = rms
      return
    }
    if (!m.mid) { lanchat.messages = lanchat.messages.concat([m]); return }
    var msgs = lanchat.messages.slice()
    var idx = -1
    for (var i = 0; i < msgs.length; i++) {
      if (msgs[i].mid === m.mid) { idx = i; break }
    }
    if (idx >= 0) msgs[idx] = m
    else msgs.push(m)
    lanchat.messages = msgs
  }

  // ---- friend request notifications (decoupled from the chat thread) -----

  // Add or update a pending friend request in the notifications list.
  function upsertFriendRequest(r) {
    var list = lanchat.friendRequests.slice()
    var idx = -1
    for (var i = 0; i < list.length; i++) {
      if (list[i].peerId === r.peerId) { idx = i; break }
    }
    if (idx >= 0) list[idx] = r
    else list.push(r)
    lanchat.friendRequests = list
  }

  // Drop any notification whose peer is no longer pending (accepted, rejected,
  // or removed). Called whenever the friend list changes.
  function reconcileFriendRequests() {
    var friends = lanchat.friends
    var kept = []
    for (var i = 0; i < lanchat.friendRequests.length; i++) {
      var r = lanchat.friendRequests[i]
      var still = false
      for (var j = 0; j < friends.length; j++) {
        if (friends[j].id === r.peerId) { still = !friends[j].confirmed; break }
      }
      if (still) kept.push(r)
    }
    lanchat.friendRequests = kept
  }

  function onDaemonLine(raw) {
    var obj
    try { obj = JSON.parse(raw) } catch (e) { return }
    if (!obj || !obj.event) return

    switch (obj.event) {
    case "ready":
      lanchat.daemonState = "running"
      lanchat.myName = obj.name
      lanchat.myPort = obj.port
      lanchat.myId = obj.id || ""
      lanchat.daemonReady = true
      if (obj.version) lanchat.version = obj.version
      if (obj.httpEnabled !== undefined) lanchat.httpEnabled = obj.httpEnabled
      if (obj.httpPort !== undefined) lanchat.httpPort = obj.httpPort
      if (obj.online !== undefined) lanchat.online = obj.online
      if (obj.friends !== undefined) lanchat.friends = obj.friends
      if (obj.rooms !== undefined) lanchat.rooms = obj.rooms
      if (obj.downloadDir !== undefined) lanchat.downloadDir = obj.downloadDir
      if (obj.sendDelay !== undefined) lanchat.sendDelay = obj.sendDelay
      if (obj.apiFullAccess !== undefined) lanchat.apiFullAccess = obj.apiFullAccess
      if (obj.panelSize !== undefined) lanchat.panelSize = obj.panelSize
      if (obj.visibility !== undefined) lanchat.visibility = obj.visibility
      if (obj.acceptRequests !== undefined) lanchat.acceptRequests = obj.acceptRequests
      if (obj.customW !== undefined) lanchat.customW = obj.customW
      if (obj.customH !== undefined) lanchat.customH = obj.customH
      if (obj.peerColW !== undefined) lanchat.peerColW = obj.peerColW
      if (obj.status !== undefined) lanchat.status = obj.status
      if (obj.soundEnabled !== undefined) lanchat.soundEnabled = obj.soundEnabled
      if (obj.typingEnabled !== undefined) lanchat.typingEnabled = obj.typingEnabled
      if (obj.showTyping !== undefined) lanchat.showTyping = obj.showTyping
      if (obj.readReceiptsEnabled !== undefined) lanchat.readReceiptsEnabled = obj.readReceiptsEnabled
      if (obj.showReadReceipts !== undefined) lanchat.showReadReceipts = obj.showReadReceipts
      if (obj.logPath) lanchat.logPathValue = obj.logPath
      if (obj.firewall) lanchat.firewall = obj.firewall
      lanchat.reconcileFriendRequests()
      lanchat.rebuildDisplayPeers()
      lanchat.refreshHistory()
      lanchat.refreshPeers()
      lanchat.checkForUpdate()
      break

    case "show-typing":
      lanchat.showTyping = obj.enabled === true
      if (!lanchat.showTyping) lanchat.typing = {}
      break

    case "show-read-receipts":
      lanchat.showReadReceipts = obj.enabled === true
      if (!lanchat.showReadReceipts) lanchat.readReceipts = {}
      break

    case "typing-enabled":
      lanchat.typingEnabled = obj.enabled === true
      break

    case "read-receipts-enabled":
      lanchat.readReceiptsEnabled = obj.enabled === true
      break

    case "sound-enabled":
      lanchat.soundEnabled = obj.enabled === true
      break

    case "status":
      lanchat.status = obj.status || "available"
      break

    case "panel-size":
      lanchat.panelSize = obj.size || "medium"
      break

    case "custom-size":
      if (obj.w !== undefined) lanchat.customW = obj.w
      if (obj.h !== undefined) lanchat.customH = obj.h
      break

    case "peer-col-w":
      if (obj.w !== undefined) lanchat.peerColW = obj.w
      break

    case "api-full-access":
      lanchat.apiFullAccess = obj.enabled === true
      break

    case "visibility":
      if (obj.visibility) lanchat.visibility = obj.visibility
      break

    case "accept-requests":
      lanchat.acceptRequests = obj.enabled === true
      break

    case "firewall-status":
      if (obj.open !== undefined || obj.backend !== undefined) {
        lanchat.firewall = { open: obj.open, backend: obj.backend || "", detail: obj.detail || "" }
      }
      if (obj.error) {
        lanchat.firewallError = obj.error
        lanchat.showChatAlert(obj.error || "Firewall action failed", true, "")
      } else {
        lanchat.firewallError = ""
      }
      break

    case "http":
      lanchat.httpEnabled = obj.enabled === true
      if (obj.port) lanchat.httpPort = obj.port
      break

    case "online":
      lanchat.online = obj.online === true
      break

    case "friends":
      lanchat.friends = obj.friends || []
      lanchat.reconcileFriendRequests()
      lanchat.rebuildDisplayPeers()
      break

    case "friend-accepted":
      // Accepted -> relationship confirmed; the notifications banner drops the
      // request via the friends-event reconcile. Nothing to show here.
      break

    case "friend-rejected":
      // A request was declined (by us or by them). Drop it from the
      // notifications list directly so it clears immediately, independent of
      // the friends-event reconcile. No global status banner.
      lanchat.friendRequests = lanchat.friendRequests.filter(function(r) {
        return r.peerId !== (obj.id || obj.peerId)
      })
      break

    case "friend-request": {
      // A handshake is pending. Surface it in the notifications banner (not the
      // chat thread): inbound -> Accept/Reject, outbound -> waiting. Adding a
      // friend is its own flow, separate from messaging.
      var fr = {
        peerId: obj.outgoing ? (obj.to || "") : (obj.from || ""),
        // Name resolution: the UDP bootstrap path sends the requester's name
        // in `name`; the TCP path sends it in `fromName`. Accept either so we
        // never fall back to showing the raw fingerprint as a display name.
        name: obj.outgoing
          ? (obj.toName || obj.to || "")
          : (obj.name || obj.fromName || obj.from || ""),
        outgoing: !!obj.outgoing,
        ts: obj.ts || Date.now(),
        mid: obj.mid || "",
        // (1.3) The requester's verified cert fingerprint, so the UI can show
        // it and require confirmation it matches before accepting.
        fingerprint: obj.fingerprint || obj.from || ""
      }
      lanchat.upsertFriendRequest(fr)
      break
    }

    case "download-dir":
      lanchat.downloadDir = obj.dir || ""
      break

    case "send-delay":
      lanchat.sendDelay = obj.seconds || 0
      break

    case "chat-cleared":
      // Remove messages for this peer from the UI list. An empty peer means
      // every conversation was cleared.
      lanchat.messages = obj.peer
        ? lanchat.messages.filter(function(m) { return !(m.to === obj.peer || m.from === obj.peer) })
        : []
      break

    case "message-deleted":
      if (obj.ok) {
        lanchat.messages = lanchat.messages.filter(function(m) { return m.mid !== obj.mid })
      }
      break

    case "message-edited":
      if (obj.ok) {
        var upd = lanchat.messages.slice()
        for (var ei = 0; ei < upd.length; ei++) {
          if (upd[ei].mid === obj.mid) {
            upd[ei].text = obj.text
            upd[ei].edited = true
            break
          }
        }
        lanchat.messages = upd
      }
      break

    case "attachment-progress":
      if (obj.fileId && obj.fileId === lanchat.dlFileId) {
        lanchat.dlBytes = obj.bytes || 0
        lanchat.dlTotal = obj.total || 0
      }
      break

    case "attachment-saved":
      lanchat.dlFileId = ""
      lanchat.dlBytes = 0
      lanchat.dlTotal = 0
      // Find the owning peer (via the message's `from`) so the result shows in
      // the right thread's alert bar.
      var fromPeer = ""
      if (obj.mid) {
        var msgs = lanchat.messages.slice()
        for (var si = 0; si < msgs.length; si++) {
          if (msgs[si].mid === obj.mid && msgs[si].attachment) {
            fromPeer = msgs[si].from || ""
            break
          }
        }
      }
      if (obj.ok) {
        if (obj.mid) {
          // Mark the matching message accepted so the pending accept bar clears.
          var upd = lanchat.messages.slice()
          for (var ai = 0; ai < upd.length; ai++) {
            if (upd[ai].mid === obj.mid && upd[ai].attachment) {
              upd[ai].attachment.accepted = true
              break
            }
          }
          lanchat.messages = upd
        }
        lanchat.showChatAlert(obj.path ? "Saved to " + obj.path : "Saved", false, fromPeer)
      } else {
        lanchat.showChatAlert(obj.error
          ? ("Failed to save attachment: " + obj.error)
          : "Failed to save attachment", true, fromPeer)
      }
      break

    case "typing": {
      var nt = {}
      for (var tk in lanchat.typing) nt[tk] = lanchat.typing[tk]
      if (lanchat.showTyping) nt[obj.from] = obj.fromName || obj.from
      else delete nt[obj.from]
      lanchat.typing = nt
      break
    }

    case "typing-stopped": {
      var ns = {}
      for (var sk in lanchat.typing) if (sk !== obj.from) ns[sk] = lanchat.typing[sk]
      lanchat.typing = ns
      break
    }

    case "read-receipt": {
      var nr = {}
      for (var rk in lanchat.readReceipts) nr[rk] = lanchat.readReceipts[rk]
      if (lanchat.showReadReceipts) nr[obj.mid] = true
      else delete nr[obj.mid]
      lanchat.readReceipts = nr
      break
    }

    case "peer": {
      var peer = obj.peer
      var next = lanchat.peers.slice()
      var found = -1
      for (var i = 0; i < next.length; i++) {
        if (next[i].id === peer.id) { found = i; break }
      }
      if (found >= 0) next[found] = peer
      else next.push(peer)
      lanchat.peers = next
      lanchat.rebuildDisplayPeers()
      lanchat.updateRoomHostOnline()
      break
    }

    case "peer-gone": {
      // Keep confirmed/pending friends in the list (marked offline); only
      // strangers are removed. The merge happens in rebuildDisplayPeers.
      var list = lanchat.peers.filter(function(p) { return p.id !== obj.id })
      lanchat.peers = list
      lanchat.rebuildDisplayPeers()
      lanchat.updateRoomHostOnline()
      break
    }

    case "message": {
      lanchat.upsertMessage(obj.message)
      var msgOut = !!obj.message.outgoing
      if (!lanchat.panelOpen && !msgOut) {
        lanchat.unreadCount++
        lanchat.playMessageSound()
      }
      // If the panel is open for this peer, send a read receipt back.
      if (lanchat.panelOpen && !msgOut && obj.message.from) {
        lanchat.sendReadReceipt(obj.message.from, obj.message.mid)
      }
      break
    }

    case "roomHistory": {
      // Full snapshot (per-room): replace this room's messages.
      var rm = lanchat.roomMessages.slice()
      var keep = rm.filter(function(m) { return m.room !== obj.roomId })
      var page = obj.messages || []
      lanchat.roomMessages = keep.concat(page)
      break
    }

    case "room-state": {
      // Authoritative snapshot from the daemon. Mirror into roomStates so the
      // roster renders exactly what the daemon says (single source of truth).
      var snap = obj.room
      if (!snap || !snap.roomId) break
      var states = {}
      for (var sk in lanchat.roomStates) states[sk] = lanchat.roomStates[sk]
      states[snap.roomId] = snap
      lanchat.roomStates = states
      // Mirror host connectivity: the owner is "online" if we hold a live
      // socket to them (the daemon drops peers when they vanish) or we ARE
      // the owner. The frozen banner reads this, never a session-local bool.
      if (lanchat.myId && snap.owner === lanchat.myId) lanchat.roomHostOnline = true
      break
    }

    case "room-list": {
      lanchat.rooms = obj.rooms || []
      // Drop room-state mirrors for rooms that vanished (left/disbanded).
      var known = {}
      for (var ri = 0; ri < lanchat.rooms.length; ri++) known[lanchat.rooms[ri].roomId] = true
      var pruned = {}
      for (var pk in lanchat.roomStates) if (known[pk]) pruned[pk] = lanchat.roomStates[pk]
      lanchat.roomStates = pruned
      // Re-evaluate host-online for the selected room against live peers.
      lanchat.updateRoomHostOnline()
      break
    }

    case "room-invite": {
      var exists = false
      for (var ii = 0; ii < lanchat.roomInvites.length; ii++)
        if (lanchat.roomInvites[ii].roomId === obj.roomId) { exists = true; break }
      if (!exists)
        lanchat.roomInvites = lanchat.roomInvites.concat([{ roomId: obj.roomId, name: obj.name,
          from: obj.from, fromName: obj.fromName }])
      break
    }

    case "room-file-status": {
      // Per-member delivery report for a room file (sender side): keyed by
      // roomId+mid so the file bubble can show ✓ saved / error per member.
      var key = obj.roomId + "|" + (obj.mid || "")
      var cur = lanchat.roomFileStatuses[key] || {}
      var nextStatuses = {}
      for (var fk in lanchat.roomFileStatuses) nextStatuses[fk] = lanchat.roomFileStatuses[fk]
      cur[obj.peer] = { status: obj.status, error: obj.error || "", name: obj.peerName || "" }
      nextStatuses[key] = cur
      lanchat.roomFileStatuses = nextStatuses
      break
    }

    case "history": {
      // Full snapshot (no peer) replaces everything.
      if (!obj.peer) { lanchat.messages = obj.messages || []; break }

      // Per-peer lazy-load page: track meta + merge older messages (prepend).
      var peer = obj.peer
      var meta = lanchat.historyMeta[peer] || { total: 0, loaded: 0 }
      meta.total = obj.total !== undefined ? obj.total : meta.total
      meta.loaded = (obj.messages || []).length
      var nextMeta = {}
      for (var mk in lanchat.historyMeta) nextMeta[mk] = lanchat.historyMeta[mk]
      nextMeta[peer] = meta
      lanchat.historyMeta = nextMeta

      // Prepend the older page to messages, dedup by mid.
      var incoming = obj.messages || []
      var have = {}
      for (var i = 0; i < lanchat.messages.length; i++) {
        var m = lanchat.messages[i]
        if (m.mid) have[m.mid] = true
      }
      var merged = incoming.slice()
      for (var j = 0; j < lanchat.messages.length; j++) {
        var mm = lanchat.messages[j]
        if (!mm.mid || !have[mm.mid]) merged.push(mm)
      }
      lanchat.messages = merged
      break
    }

    case "peers":
      lanchat.peers = obj.peers || []
      lanchat.rebuildDisplayPeers()
      break

    case "error":
      // Dev-facing: daemon reported a failure. Goes to the diagnostics log,
      // not the user-facing chat alert bar.
      lanchat.pushDiagnostic(obj.message || "Lanchat error")
      break

    case "notice":
      // User-facing setup/config notice (e.g. first-run token message).
      lanchat.showChatAlert(obj.message || "", false, "")
      break

    case "diagnostic": {
      // Keep a rolling log of diagnostics so they can be read inline in the
      // panel (peer expiry, dropped messages, send failures, etc.).
      var d = {
        ts: obj.ts || Date.now(),
        message: obj.message || ""
      }
      for (var dk in obj) {
        if (dk !== "event" && dk !== "message" && dk !== "ts") d[dk] = obj[dk]
      }
      var diag = lanchat.diagnostics.slice()
      diag.push(d)
      if (diag.length > 100) diag = diag.slice(diag.length - 100)
      lanchat.diagnostics = diag
      break
    }
    }
  }

  function onDaemonExit(code) {
    lanchat.daemonReady = false
    lanchat.daemonState = "down"
    // Dev-facing crash alert → diagnostics log (the restart message is the
    // important part; the user sees the daemon come back).
    lanchat.pushDiagnostic("Lanchat daemon stopped (exit " + code + ") — restarting…")
    // Auto-restart after a short delay so a crash doesn't leave the app dead,
    // but with a gap to avoid a tight loop if it keeps failing.
    lanchat.restartTimer.restart()
  }

  // Push a message into the rolling diagnostics log (capped at 100 lines).
  function pushDiagnostic(message) {
    var d = { ts: Date.now(), message: message }
    var diag = lanchat.diagnostics.slice()
    diag.push(d)
    if (diag.length > 100) diag = diag.slice(diag.length - 100)
    lanchat.diagnostics = diag
  }

  // Transient chat alerts (save results, add-friend prompt, notices) clear
  // themselves after a few seconds so they never stick around and hide UI.
  property Timer chatAlertTimer: Timer {
    interval: 5000
    onTriggered: lanchat.chatAlert = ""
  }

  // Restarts the daemon after a crash. 2s gap avoids a tight restart loop.
  property Timer restartTimer: Timer {
    interval: 2000
    onTriggered: lanchat.startDaemon()
  }

  // Drives the send-delay countdown; releases held messages when their time
  // elapses, decrementing remaining so the UI can draw a countdown ring.
  property Timer undoTimer: Timer {
    interval: 100
    repeat: true
    onTriggered: {
      var still = []
      for (var i = 0; i < lanchat.pendingSends.length; i++) {
        var s = lanchat.pendingSends[i]
        s.remaining -= 0.1
        if (s.remaining <= 0) {
          daemon.write(lanchat.sendPayload(s.to, s.text, s.attachment))
        } else {
          still.push(s)
        }
      }
      lanchat.pendingSends = still
      if (still.length === 0) stop()
    }
  }

  property Process daemon: Process {
    id: daemon
    stdinEnabled: true

    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(data) { lanchat.onDaemonLine(data) }
    }

    onExited: function(code) { lanchat.onDaemonExit(code) }
  }

  // One-shot: installs/enables the daemon's systemd unit on first run. Runs
  // before the bridge is spawned; on success it marks systemdEnsured and
  // starts the bridge. On failure it reports the daemon down and retries via
  // restartTimer (the unit may need a moment, or systemd isn't available).
  property Process ensureProcess: Process {
    id: ensureProcess
    onExited: function(code) {
      if (code === 0) {
        lanchat.systemdEnsured = true
        lanchat.startDaemon()
      } else {
        lanchat.daemonState = "down"
        lanchat.pushDiagnostic("Lanchat systemd unit not ready (exit " + code + ") — retrying…")
        lanchat.restartTimer.restart()
      }
    }
  }

  // Background process for the update-availability check (see checkForUpdate).
  // Parses the single-token stdout: UPDATE / CURRENT / UNKNOWN.
  property Process updateProc: Process {
    id: updateProc
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(data) {
        var t = String(data).trim()
        lanchat.updateChecking = false
        if (t === "CURRENT") lanchat.updateAvailable = false
        else if (t === "UPDATE") lanchat.updateAvailable = true
        else if (t === "UNKNOWN") lanchat.updateError = "Could not check for updates"
      }
    }
    onExited: function(code) {
      lanchat.updateChecking = false
    }
  }

  // Background process for applying an update (see applyUpdate). Parses the
  // single-token stdout: APPLIED (success -> restart shell), DIRTY (local
  // edits blocked the safe update -> offer a clean install), ERROR.
  property Process applyProc: Process {
    id: applyProc
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(data) {
        var t = String(data).trim()
        if (t === "APPLIED") {
          lanchat.updateApplyState = "applied"
          lanchat.updateAvailable = false
          lanchat.showChatAlert("Update applied — restarting shell…", false, "")
          lanchat.restartShell()
        } else if (t === "DIRTY") {
          lanchat.updateApplyState = "dirty"
          lanchat.showChatAlert("Local changes block the update — use \u201CDiscard & update\u201D for a clean install.",
            true, "")
        } else {
          lanchat.updateApplyState = ""
          lanchat.showChatAlert("Update failed — check your connection and try again.", true, "")
        }
      }
    }
    onExited: function(code) { lanchat.updateApplying = false }
  }

  // Background process that restarts the Omarchy shell after an update (see
  // restartShell). Nothing to parse — it's detached and outlives this shell.
  property Process restartProc: Process {
    id: restartProc
  }

  Component.onCompleted: lanchat.startDaemon()
}
