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

  property bool httpEnabled: false
  property int httpPort: 4814

  property bool online: true
  property var friends: []        // [{id,address,name,confirmed}]

  property var peers: []          // [{id,name,address,port,lastSeen}]
  property var messages: []       // [{from,fromName,text,ts,outgoing}]
  property int unreadCount: 0
  property int onlineCount: 0

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

  function send(to, text) {
    if (!text || !to) return
    daemon.write(JSON.stringify({ cmd: "send", to: to, text: text }) + "\n")
  }

  function refreshHistory() {
    daemon.write(JSON.stringify({ cmd: "history" }) + "\n")
  }

  function refreshPeers() {
    daemon.write(JSON.stringify({ cmd: "list" }) + "\n")
  }

  function clearUnread() {
    unreadCount = 0
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
      if (obj.httpEnabled !== undefined) lanchat.httpEnabled = obj.httpEnabled
      if (obj.httpPort !== undefined) lanchat.httpPort = obj.httpPort
      if (obj.online !== undefined) lanchat.online = obj.online
      if (obj.friends !== undefined) lanchat.friends = obj.friends
      lanchat.refreshHistory()
      lanchat.refreshPeers()
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

    case "history":
      lanchat.messages = obj.messages || []
      break

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
