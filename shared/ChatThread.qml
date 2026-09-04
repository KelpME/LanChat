// ChatThread: the 1:1 thread ListView — messages, scroll position tracking,
// lazy older-page loading, and the empty-state text. Extracted from Panel.qml
// (step 6/8) with zero behavior change.
//
// Sizing: the original computed its height from sibling ids (composeBox,
// threadHeader, chatAlertBar) that are file-local to Panel.qml — the Panel.qml
// call site now provides width/height (option (a) of the coupling arbitration:
// layout math stays at the call site). Inside this file only parent-relative
// sizing is used.
//
// Panel-side state stays on the panel: selection (selectedPeer, selectedPeerId,
// hasThread), model (thread), and edit focus (editingMid). The panel's
// editMsg/copyToClipboard functions are invoked through the editRequested and
// copyRequested signals (same handlers the ChatMessage delegate already used).
import QtQuick
import qs.Commons
import qs.Ui

ListView {
  id: chatThread

  // ---- inputs from the host panel (types verbatim from Panel.qml) ----
  property bool inRoom: false
  property var thread: []
  property string selectedPeerId: ""
  property var selectedPeer: null
  property bool hasThread: false
  property string editingMid: ""
  property var timeLabel: function(ts) { return "" }

  // ---- outgoing signals (host panel wires these to its own handlers) ----
  signal editRequested(string mid, string text)
  signal copyRequested(string text)

  visible: !inRoom
  width: inRoom ? 0 : parent.width
  clip: true
  spacing: Style.spacing.sm
  model: chatThread.thread
  header: Item { width: parent.width; height: Style.spacing.md }
    footer: Item { width: parent.width; height: Style.spacing.lg }

    onCountChanged: Qt.callLater(function() { positionViewAtEnd() })

    // Lazy-load: when scrolled to the top, fetch an older page.
    onAtYBeginningChanged: {
      if (chatThread.atYBeginning && chatThread.selectedPeerId)
        Lanchat.loadOlder(chatThread.selectedPeerId)
    }
    onContentYChanged: {
      if (contentY <= 2 && chatThread.selectedPeerId)
        Lanchat.loadOlder(chatThread.selectedPeerId)
    }

    delegate: Column {
      required property var modelData
      width: chatThread.width
      spacing: Style.spacing.xs

      ChatMessage {
        id: chatMsg
        modelData: modelData
        maxWidth: chatThread.width * 0.8
        editingMid: chatThread.editingMid
        timeLabel: chatThread.timeLabel
        onEditRequested: function(mid, text) { chatThread.editRequested(mid, text) }
        onCopyRequested: function(text) { chatThread.copyRequested(text) }
      }
    }

    Text {
      visible: chatThread.selectedPeer && !chatThread.hasThread
      anchors.centerIn: parent
      width: parent.width - Style.space(24)
      text: chatThread.selectedPeer
        ? "No messages with " + chatThread.selectedPeer.name + " yet."
        : ""
      color: Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      wrapMode: Text.Wrap
      horizontalAlignment: Text.AlignHCenter
    }
  }
