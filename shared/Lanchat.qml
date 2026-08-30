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
  property string statusMessage: ""
  property bool panelOpen: false
  property string version: ""

  property bool httpEnabled: false
  property int httpPort: 4814
  property bool apiFullAccess: false
  property string panelSize: "medium"
  property string status: "available"

  property bool online: true
  property var friends: []        // [{id,address,name,confirmed}]

  property string downloadDir: ""
  property int sendDelay: 0
  property var pendingSends: []  // [{mid, to, text, remaining, total}]

  property var peers: []          // [{id,name,address,port,lastSeen}]
  property var messages: []       // [{from,fromName,text,ts,outgoing}]
  property int unreadCount: 0
  property int onlineCount: 0

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

  function startDaemon() {
    if (daemon.running) return
    daemon.command = ["python3", lanchat.serverPath()]
    daemon.running = true
  }

  // ---- commands to the daemon -------------------------------------------

  // Send a message, honoring the undo/send-delay window. When sendDelay>0 the
  // message is held in pendingSends and auto-released after the delay, so the
  // user can undo it.
  function send(to, text, attachment) {
    if (!text && !attachment) return
    if (!to) return
    var mid = "m" + Date.now().toString(36) + Math.floor(Math.random() * 1e6).toString(36)
    if (sendDelay > 0) {
      pendingSends = pendingSends.concat([{ mid: mid, to: to, text: text, attachment: attachment, total: sendDelay, remaining: sendDelay }])
      undoTimer.restart()
    } else {
      daemon.write(JSON.stringify({ cmd: "send", to: to, text: text || "", attachment: attachment || null }) + "\n")
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

  function deleteMessage(mid) {
    daemon.write(JSON.stringify({ cmd: "deleteMessage", mid: mid }) + "\n")
  }

  function setDownloadDir(dir) {
    downloadDir = dir
    daemon.write(JSON.stringify({ cmd: "setDownloadDir", dir: dir }) + "\n")
  }

  function setSendDelay(seconds) {
    sendDelay = seconds
    daemon.write(JSON.stringify({ cmd: "setSendDelay", seconds: seconds }) + "\n")
  }

  function acceptAttachment(from, fileId, name) {
    daemon.write(JSON.stringify({ cmd: "acceptAttachment", from: from, fileId: fileId, name: name }) + "\n")
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

  // Accept/reject an incoming friend request.
  function acceptFriend(id) {
    daemon.write(JSON.stringify({ cmd: "acceptFriend", id: id }) + "\n")
  }
  function rejectFriend(id) {
    daemon.write(JSON.stringify({ cmd: "rejectFriend", id: id }) + "\n")
  }

  // Toggle full API access to chat data (read history/peers) vs send-only.
  function setApiFullAccess(on) {
    apiFullAccess = on
    daemon.write(JSON.stringify({ cmd: "setApiFullAccess", enabled: on }) + "\n")
  }

  // Set the panel size: "small" | "medium" | "large" | "xl" | "full".
  function setPanelSize(size) {
    panelSize = size
    daemon.write(JSON.stringify({ cmd: "setPanelSize", size: size }) + "\n")
  }

  // Set user status: "available" | "dnd" | "away" | "brb".
  function setStatus(s) {
    status = s
    daemon.write(JSON.stringify({ cmd: "setStatus", status: s }) + "\n")
  }

  // ---- events from the daemon -------------------------------------------

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
      if (obj.status !== undefined) lanchat.status = obj.status
      lanchat.refreshHistory()
      lanchat.refreshPeers()
      break

    case "status":
      lanchat.status = obj.status || "available"
      break

    case "panel-size":
      lanchat.panelSize = obj.size || "medium"
      break

    case "api-full-access":
      lanchat.apiFullAccess = obj.enabled === true
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
      break

    case "friend-accepted":
      lanchat.statusMessage = (obj.name || "Peer") + " is now a friend"
      lanchat.statusTimer.restart()
      break

    case "friend-rejected":
      lanchat.statusMessage = (obj.name || "Peer") + " declined your request"
      lanchat.statusTimer.restart()
      break

    case "download-dir":
      lanchat.downloadDir = obj.dir || ""
      break

    case "send-delay":
      lanchat.sendDelay = obj.seconds || 0
      break

    case "chat-cleared":
      // Remove all messages with this peer from the UI list.
      lanchat.messages = lanchat.messages.filter(function(m) {
        return !(m.to === obj.peer || m.from === obj.peer)
      })
      break

    case "message-deleted":
      if (obj.ok) {
        lanchat.messages = lanchat.messages.filter(function(m) { return m.mid !== obj.mid })
      }
      break

    case "attachment-saved":
      lanchat.statusMessage = obj.ok ? ("Saved to " + obj.path) : ("Failed to save attachment: " + obj.path)
      lanchat.statusTimer.restart()
      break

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
      lanchat.onlineCount = next.length
      break
    }

    case "peer-gone": {
      var list = lanchat.peers.filter(function(p) { return p.id !== obj.id })
      lanchat.peers = list
      lanchat.onlineCount = list.length
      break
    }

    case "message": {
      var msgs = lanchat.messages.slice()
      msgs.push(obj.message)
      lanchat.messages = msgs
      if (!lanchat.panelOpen && !obj.message.outgoing) lanchat.unreadCount++
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
      lanchat.onlineCount = (obj.peers || []).length
      break

    case "error":
      lanchat.statusMessage = obj.message || "Lanchat error"
      lanchat.statusTimer.restart()
      break

    case "notice":
      lanchat.statusMessage = obj.message || ""
      lanchat.statusTimer.restart()
      break
    }
  }

  function onDaemonExit(code) {
    lanchat.daemonReady = false
    lanchat.statusMessage = "Lanchat daemon stopped (exit " + code + ")"
    lanchat.statusTimer.restart()
  }

  // Transient status (errors/notices) clears itself after a few seconds so it
  // never sticks around and hides UI.
  property Timer statusTimer: Timer {
    interval: 6000
    onTriggered: lanchat.statusMessage = ""
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
          daemon.write(JSON.stringify({ cmd: "send", to: s.to, text: s.text || "", attachment: s.attachment || null }) + "\n")
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
