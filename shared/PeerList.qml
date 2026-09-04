// PeerList: the left column's scrollable peer list, the onboarding and
// notification banners, and the peers-online / daemon-status footer.
// Extracted from Panel.qml (step 4/8) with zero behavior change.
import QtQuick
import qs.Commons
import qs.Ui

Item {
  id: peerListRoot

  // ---- inputs from the host panel ----
  property real peerRowH
  property string selectedPeerId
  property var selectedRoom
  property bool inRoom
  property bool showFwAlert
  property real bottomInset: 0

  // ---- functions supplied by the host panel ----
  property var friendStateFn: function(id) { return "" }
  property var shortFpFn: function(fp) { return "" }

  // ---- state moved in from Panel (used only inside this component) ----
  property bool showOnboarding: true
  property bool notifExpanded: true

  // ---- outgoing signals (host panel wires these to its own handlers) ----
  signal peerSelected(string id)
  signal chatClosed()
  signal addPeerToRoomRequested(string id)
  signal friendAccepted(string id)
  signal friendRejected(string id)
  signal friendCancelled(string id)

  // ---- geometry read by the settings section via the host panel ----
  readonly property real alertStackBottom: notifBanner.y + notifBanner.height

  // ---- peers list (moved verbatim from Panel.qml) ----

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
    anchors.bottom: parent.bottom
    anchors.bottomMargin: Style.spacing.xs + bottomInset
    anchors.left: parent.left
    anchors.right: parent.right
    visible: selectedPeerId !== ""
    onClicked: chatClosed()
  }

  ListView {
    id: peerList
    width: parent.width
    anchors.top: notifBanner.bottom
    anchors.topMargin: Style.spacing.sm
    anchors.leftMargin: Style.spacing.sm
    anchors.rightMargin: Style.spacing.sm
    anchors.bottom: parent.bottom
    anchors.bottomMargin: Style.spacing.xs + bottomInset
    clip: true
    interactive: peerList.contentHeight > peerList.height
    model: Lanchat.displayPeers
    spacing: Style.spacing.xs

    delegate: Rectangle {
      required property var modelData
      width: peerList.width
      height: peerRowH
      radius: Style.cornerRadius
      color: modelData.id === selectedPeerId
        ? Style.selectedFill : "transparent"

      MouseArea {
        anchors.fill: parent
        onClicked: peerSelected(modelData.id)
      }

      Rectangle {
        visible: modelData.id === selectedPeerId
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
        color: modelData.id === selectedPeerId
          ? Color.accent
          : Color.popups.text
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        elide: Text.ElideRight
      }

      // Room-add control: "Add to group" on CONFIRMED FRIEND rows
      // only, visible only while a room roster is open, only for
      // peers not already in that room. One click proposes/adds
      // them — same roomAdd path as the roster picker.
      Button {
        id: roomAddBadge
        readonly property bool inSelectedRoom:
          selectedRoom && (modelData.id in selectedRoom.members)
        anchors.verticalCenter: parent.verticalCenter
        anchors.right: friendBadge.left
        anchors.rightMargin: Style.spacing.xs
        visible: inRoom
                 && Lanchat.isConfirmedFriend(modelData.id)
                 && !inSelectedRoom
        text: "Add to group"
        fontSize: Style.font.caption
        foreground: Color.accent
        tooltipText: "Add " + (modelData.name || "peer") + " to the room"
        onClicked: addPeerToRoomRequested(modelData.id)
      }

      // Friend control on the peer card: an "add friend" button for
      // strangers, or a friend/pending badge once a request is in.
      Item {
        id: friendBadge
        anchors.verticalCenter: parent.verticalCenter
        anchors.right: parent.right
        anchors.rightMargin: Style.spacing.sm
        width: friendStateFn(modelData.id) === "" ? Style.space(24)
             : friendStateFn(modelData.id) === "friend" ? Style.space(30)
             : Style.space(14)
        height: Style.space(18)

        // Stranger -> "+" button to send a friend request.
        Button {
          anchors.fill: parent
          visible: friendStateFn(modelData.id) === ""
          text: "\uFF0B"   // ＋
          fontSize: Style.font.caption
          tooltipText: "Send friend request to " + modelData.name
          onClicked: Lanchat.requestFriend(modelData.id)
        }

        // Pending / friend -> state badge.
        Text {
          anchors.centerIn: parent
          visible: friendStateFn(modelData.id) !== ""
          text: friendStateFn(modelData.id) === "friend" ? "\u2713 Friend" : "\u2026"
          color: friendStateFn(modelData.id) === "friend" ? Color.accent : Color.muted
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
    height: showOnboarding && Lanchat.visibility === "private"
            ? onbContent.implicitHeight + Style.space(12) : 0
    anchors.top: peersOnlineBar.bottom
    anchors.topMargin: Style.spacing.xs
    visible: showOnboarding && Lanchat.visibility === "private"
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
        onClicked: showOnboarding = false
      }
    }
  }

  // ---- friend-request notifications: pinned below the peer
  // list so an incoming request is always in view -------------
  Item {
    id: notifBanner
    objectName: "notifBanner"
    width: parent.width
    height: Lanchat.friendRequests.length === 0 ? 0
           : notifExpanded ? Style.space(24) + notifRows.implicitHeight + Style.spacing.xs
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
        text: (notifExpanded ? "\u25BC " : "\u25B6 ") + Lanchat.friendRequests.length
              + (Lanchat.friendRequests.length === 1 ? " friend request" : " friend requests")
        color: Color.accent
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        font.weight: Font.Bold
      }
      MouseArea {
        anchors.fill: parent
        onClicked: notifExpanded = !notifExpanded
      }
    }

    Column {
      id: notifRows
      anchors.top: parent.top
      anchors.topMargin: Style.space(24)
      width: parent.width
      visible: notifExpanded
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
              onClicked: friendAccepted(modelData.peerId)
            }
            Button {
              visible: !modelData.outgoing
              text: "Reject"
              fontSize: Style.font.caption
              onClicked: friendRejected(modelData.peerId)
            }
            Button {
              visible: modelData.outgoing
              text: "Cancel"
              fontSize: Style.font.caption
              tooltipText: "Withdraw this friend request"
              onClicked: friendCancelled(modelData.peerId)
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
              text: "Fingerprint: " + shortFpFn(modelData.fingerprint || modelData.peerId)
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
}
