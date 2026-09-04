import QtQuick
import Quickshell
import Quickshell.Io
pragma Singleton
QtObject {
  id: lanchat
  property string myName: "TestUser"
  property int myPort: 4814
  property bool daemonReady: true
  property string daemonState: "running"
  property string chatAlert: ""
  property string chatAlertPeerId: ""
  property string selectedRoomId: ""
  property string myId: "test-peer-1"
  property var messages: []
  property var roomMessages: []
  property var rooms: []
  property var roomStates: ({})
  property var friends: []
  property var readReceipts: ({})
  property var peers: []
  property var diagnostics: []
  property real peerColW: 280
  property bool updateAvailable: false
  property bool discoverable: true
  property bool colorsEnabled: false
  function send(peerId, text, attachment) { console.log("stub send", peerId, text) }
  function sendRoom(roomId, text) { console.log("stub sendRoom", roomId, text) }
  function sendRoomFile(roomId, path, name, caption) { console.log("stub sendRoomFile", roomId, path) }
  function editMessage(mid, text) { console.log("stub editMessage", mid) }
  function undo(mid) { console.log("stub undo", mid) }
  function deleteMessage(mid) { console.log("stub deleteMessage", mid) }
  function clearChat(peerId) { console.log("stub clearChat", peerId) }
  function requestFriend(id) { console.log("stub requestFriend", id) }
  function acceptFriend(id) { console.log("stub acceptFriend", id) }
  function rejectFriend(id) { console.log("stub rejectFriend", id) }
  function cancelFriend(id) { console.log("stub cancelFriend", id) }
  function unfriend(id) { console.log("stub unfriend", id) }
  function isConfirmedFriend(id) { return false }
  // bench-only model stubs (used by shell4 / PeerList bench)
  property var displayPeers: []
  property var friendRequests: []
  function roomAdd(roomId, peerId) { console.log("stub roomAdd") }
  function roomRemove(roomId, peerId) { console.log("stub roomRemove") }
  function roomLeave(roomId) { console.log("stub roomLeave") }
  function roomJoin(roomId) { console.log("stub roomJoin") }
  function selectRoom(roomId) { console.log("stub selectRoom", roomId) }
  function sendTypingStopped(peerId) { console.log("stub typingStopped") }
  function loadOlder(peerId) { console.log("stub loadOlder", peerId) }
  function sendTyping(peerId) { console.log("stub typing", peerId) }
  function roomMemberColor(mem) { return "#888888" }
  function shellQuote(s) { return "'" + s + "'" }
  function roomSetCanInvite(roomId, peerId, can) { console.log("stub roomSetCanInvite", roomId, peerId, can) }
  property bool roomHostOnline: true
  property var roomInvites: []
}
