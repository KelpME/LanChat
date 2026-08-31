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

  // Path to the daemon's diagnostic log (set from the daemon's ready event).
  property string logPathValue: ""
  function logPath() {
    return lanchat.logPathValue
  }

  function startDaemon() {
    if (daemon.running) return
    daemon.command = ["python3", lanchat.serverPath()]
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
  // request to a peer; they accept/reject it in the notifications banner.
  function requestFriend(id) {
    daemon.write(JSON.stringify({ cmd: "send", to: id,
      text: "wants to add you as a friend", friend_request: true }) + "\n")
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

  // (1.3) Add a confirmed friend directly by their cert fingerprint. Optional
  // address/port help the daemon dial immediately; without them the friend's
  // address is learned from their next hello (confirmed friends are accepted
  // in private mode).
  function addFriendByFingerprint(id, address, port) {
    var fid = String(id || "").trim()
    if (!fid) return
    daemon.write(JSON.stringify({ cmd: "setFriend", id: fid,
      address: String(address || "").trim(), port: port || 0 }) + "\n")
  }

  // Accept/reject an incoming friend request.
  function acceptFriend(id) {
    daemon.write(JSON.stringify({ cmd: "acceptFriend", id: id }) + "\n")
  }
  function rejectFriend(id) {
    daemon.write(JSON.stringify({ cmd: "rejectFriend", id: id }) + "\n")
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
      lanchat.myName = obj.name
      lanchat.myPort = obj.port
      lanchat.daemonReady = true
      if (obj.version) lanchat.version = obj.version
      if (obj.httpEnabled !== undefined) lanchat.httpEnabled = obj.httpEnabled
      if (obj.httpPort !== undefined) lanchat.httpPort = obj.httpPort
      if (obj.online !== undefined) lanchat.online = obj.online
      if (obj.friends !== undefined) lanchat.friends = obj.friends
      if (obj.downloadDir !== undefined) lanchat.downloadDir = obj.downloadDir
      if (obj.sendDelay !== undefined) lanchat.sendDelay = obj.sendDelay
      if (obj.apiFullAccess !== undefined) lanchat.apiFullAccess = obj.apiFullAccess
      if (obj.panelSize !== undefined) lanchat.panelSize = obj.panelSize
      if (obj.visibility !== undefined) lanchat.visibility = obj.visibility
      if (obj.acceptRequests !== undefined) lanchat.acceptRequests = obj.acceptRequests
      if (obj.customW !== undefined) lanchat.customW = obj.customW
      if (obj.customH !== undefined) lanchat.customH = obj.customH
      if (obj.status !== undefined) lanchat.status = obj.status
      if (obj.soundEnabled !== undefined) lanchat.soundEnabled = obj.soundEnabled
      if (obj.typingEnabled !== undefined) lanchat.typingEnabled = obj.typingEnabled
      if (obj.showTyping !== undefined) lanchat.showTyping = obj.showTyping
      if (obj.readReceiptsEnabled !== undefined) lanchat.readReceiptsEnabled = obj.readReceiptsEnabled
      if (obj.showReadReceipts !== undefined) lanchat.showReadReceipts = obj.showReadReceipts
      if (obj.logPath) lanchat.logPathValue = obj.logPath
      lanchat.reconcileFriendRequests()
      lanchat.rebuildDisplayPeers()
      lanchat.refreshHistory()
      lanchat.refreshPeers()
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

    case "api-full-access":
      lanchat.apiFullAccess = obj.enabled === true
      break

    case "visibility":
      if (obj.visibility) lanchat.visibility = obj.visibility
      break

    case "accept-requests":
      lanchat.acceptRequests = obj.enabled === true
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
        name: obj.outgoing ? (obj.toName || obj.to || "") : (obj.fromName || obj.from || ""),
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
      break
    }

    case "peer-gone": {
      // Keep confirmed/pending friends in the list (marked offline); only
      // strangers are removed. The merge happens in rebuildDisplayPeers.
      var list = lanchat.peers.filter(function(p) { return p.id !== obj.id })
      lanchat.peers = list
      lanchat.rebuildDisplayPeers()
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

  Component.onCompleted: lanchat.startDaemon()
}
