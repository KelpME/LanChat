// RoomListSection: the left column's collapsible rooms section — header row
// (toggle + create button), unaccepted invites, per-room groups with member
// lines and owner controls. Extracted from Panel.qml (step 5/8) with zero
// behavior change. The create-room mini-dialog itself is a panel-wide overlay
// and STAYS in Panel.qml; this component emits roomCreateRequested() and the
// panel's call-site handler opens the dialog.
import QtQuick
import qs.Commons
import qs.Ui

Column {
  id: roomsSection
  property bool expanded: false
  width: parent.width
  anchors.left: parent.left
  anchors.right: parent.right
  // NOTE: anchors.bottom (settings.top) is set at the Panel.qml call site —
  // the `settings` id is file-local to Panel.qml and not visible from here.
  height: roomsHeader.height + (roomsSection.expanded ? roomsListCol.height : 0)

  // ---- inputs from the host panel ----
  property real peerRowH
  // The settings section lives in Panel.qml's scope (ids are file-local):
  // the two sections are mutually exclusive, so expanding this one must
  // collapse settings. Wired at the call site.
  property Item settingsCol
  // ---- functions supplied by the host panel ----
  property var amRoomOwnerOfFn: function(roomId) { return false }
  property var selectRoomFn: function(roomId) { }
  property var leaveSelectedRoomFn: function() { }

  // ---- outgoing signals (host panel wires these to its own handlers) ----
  signal roomSelected(string roomId)
  signal roomLeaveRequested()
  signal roomCreateRequested()

  // ---- public surface for the host panel (was: roomsSection.expanded = ...) ----
  function expand() { roomsSection.expanded = true }
  function collapseSection() { roomsSection.expanded = false }
  readonly property real sectionHeight: roomsSection.height

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
      onClicked: {
        roomsSection.expanded = !roomsSection.expanded
        // Mutually exclusive with Settings (same rule in
        // reverse) so only one section eats the list space.
        if (roomsSection.expanded) settingsCol.expanded = false
      }
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
      onClicked: roomCreateRequested()
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
        height: Style.space(26)
        color: Style.selectedAccentFill

        Row {
          anchors.fill: parent
          anchors.leftMargin: Style.spacing.sm
          anchors.rightMargin: Style.spacing.sm
          spacing: Style.spacing.sm

          Text {
            width: parent.width - Style.space(80)
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
              roomsSection.selectRoomFn(modelData.roomId)
            }
          }
        }
      }
    }

    Repeater {
      model: Lanchat.rooms
      delegate: Column {
        id: roomGroup
        required property var modelData
        readonly property string roomId: modelData.roomId
        readonly property var room: Lanchat.roomStates[modelData.roomId] || modelData
        // Per-group collapse: the chevron on the header row
        // toggles the member list under THIS group only.
        // Clicking the group name still opens the room chat.
        property bool expanded: true
        width: roomsListCol.width
        spacing: 0

        // Group header row: # name, member count, leave ✕.
        Rectangle {
          width: roomGroup.width
          height: roomsSection.peerRowH
          radius: Style.cornerRadius
          color: modelData.roomId === Lanchat.selectedRoomId
            ? Style.selectedFill : "transparent"

          MouseArea {
            anchors.fill: parent
            onClicked: roomsSection.roomSelected(modelData.roomId)
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
            id: chevronLabel
            anchors.left: parent.left
            anchors.leftMargin: Style.spacing.sm
            anchors.verticalCenter: parent.verticalCenter
            text: roomGroup.expanded ? "▾" : "▸"
            color: Color.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          Text {
            anchors.left: chevronLabel.right
            anchors.leftMargin: Style.space(4)
            anchors.right: roomMembersLabel.left
            anchors.rightMargin: Style.spacing.sm
            anchors.verticalCenter: parent.verticalCenter
            text: "# " + modelData.name
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            elide: Text.ElideRight
          }

          Text {
            id: roomMembersLabel
            anchors.right: roomCollapseBtn.left
            anchors.rightMargin: Style.spacing.sm
            anchors.verticalCenter: parent.verticalCenter
            text: Object.keys(roomGroup.room.members || {}).length
            color: Color.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          // Collapse toggle for THIS group's member list. Sits
          // as a later sibling of the row MouseArea so the
          // click lands here, not on room-select.
          Button {
            id: roomCollapseBtn
            anchors.right: roomLeaveBtn.left
            anchors.rightMargin: Style.spacing.xs
            anchors.verticalCenter: parent.verticalCenter
            text: roomGroup.expanded ? "▾" : "▸"
            fontSize: Style.font.caption
            foreground: Color.muted
            tooltipText: roomGroup.expanded ? "Hide members" : "Show members"
            onClicked: roomGroup.expanded = !roomGroup.expanded
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
            onClicked: roomsSection.roomLeaveRequested()
          }
        }

        // Member lines: ONE text line tall each, directly below
        // their group. Same controls the roster rows had: color
        // dot, ★ owner marker, (you), and for the room owner the
        // remove ✕ + per-member can-add toggle. Hidden while
        // this group is collapsed.
        Repeater {
          visible: roomGroup.expanded
          model: roomGroup.expanded ? Object.keys(roomGroup.room.members || {}) : []
          delegate: Rectangle {
            id: memberLine
            required property var modelData
            width: roomGroup.width
            height: Style.space(20)
            color: "transparent"

            readonly property var member: roomGroup.room
              ? (roomGroup.room.members[modelData] || {}) : {}
            readonly property bool lineIsOwner: roomGroup.room
              && roomGroup.room.owner === modelData
            readonly property bool lineIsMe: Lanchat.myId === modelData

            Row {
              anchors.left: parent.left
              anchors.leftMargin: Style.space(24)
              anchors.right: parent.right
              anchors.rightMargin: Style.spacing.sm
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(6)

              // Member color dot (palette token resolved
              // per-viewer; hex carries the shared value).
              Rectangle {
                width: Style.space(8)
                height: Style.space(8)
                radius: width / 2
                anchors.verticalCenter: parent.verticalCenter
                color: Lanchat.roomMemberColor(memberLine.member) || Color.muted
                border.width: 1
                border.color: Color.popups.border
              }

              Text {
                width: memberLine.width - Style.space(24)
                  - Style.space(20)
                  - (memberLine.lineIsOwner || !roomsSection.amRoomOwnerOfFn(roomGroup.roomId)
                     ? 0 : Style.space(150))
                anchors.verticalCenter: parent.verticalCenter
                text: (memberLine.lineIsOwner ? "★ " : "")
                      + (memberLine.member.name || "Unknown")
                      + (memberLine.lineIsMe ? " (you)" : "")
                      + (memberLine.member.canInvite && !memberLine.lineIsOwner ? " · can add" : "")
                color: memberLine.lineIsMe ? Color.accent : Color.popups.text
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }

              // Owner controls on the room owner's screen only:
              // remove + per-member can-add toggle. Their own
              // row and the owner's row get no buttons.
              Button {
                visible: roomsSection.amRoomOwnerOfFn(roomGroup.roomId)
                         && !memberLine.lineIsOwner && !memberLine.lineIsMe
                text: "✕"
                fontSize: Style.font.caption
                foreground: Color.urgent
                tooltipText: "Remove from room"
                onClicked: Lanchat.roomRemove(roomGroup.roomId, memberLine.modelData)
              }
              Button {
                visible: roomsSection.amRoomOwnerOfFn(roomGroup.roomId)
                         && !memberLine.lineIsOwner && !memberLine.lineIsMe
                text: (memberLine.member.canInvite ? "can add ✓" : "can add ✗")
                fontSize: Style.font.caption
                foreground: memberLine.member.canInvite ? Color.accent : Color.muted
                tooltipText: "Toggle whether this member may add people"
                onClicked: Lanchat.roomSetCanInvite(roomGroup.roomId,
                  memberLine.modelData, !memberLine.member.canInvite)
              }
            }
          }
        }

        Rectangle {
          visible: roomGroup.expanded
                   && roomGroup.room && roomGroup.roomId === Lanchat.selectedRoomId
                   && !Lanchat.roomHostOnline
          width: roomGroup.width
          height: visible ? Style.space(18) : 0
          color: "transparent"

          Text {
            anchors.left: parent.left
            anchors.leftMargin: Style.space(24)
            anchors.verticalCenter: parent.verticalCenter
            text: "host offline — changes frozen"
            color: Color.urgent
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
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
