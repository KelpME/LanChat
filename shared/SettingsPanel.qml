// The settings panel (gear column) of the left peer column: collapsible —
// header row plus the expanded body. Extracted verbatim from Panel.qml
// (zero behavior change); root keeps the mutator functions and this child
// signals back. The instance keeps `id: settings` in Panel.qml so every
// external wiring (RoomListSection's settingsCol, notifBanner anchors) is
// unchanged.
import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "."

Item {
  id: settingsPanel
  // The old block WAS the Column with this exact height binding; the new
  // root reproduces it so external anchors (notifBanner.bottom, peer-column
  // layout) see identical geometry.
  height: settingsHeader.height + (settingsPanel.expanded
    ? Math.min(settingsBody.contentHeight,
               Math.min(settingsPanel.hostHeight * 0.8,
                        Math.max(0, settingsPanel.hostHeight - settingsPanel.alertStackBottom - settingsHeader.height - Style.space(12))))
    : 0)

  property bool expanded: false
  property bool diagExpanded: false
  signal collapseRoomsRequested()

  // --- sibling/parent geometry inputs (no out-of-item refs in the body) ---
  // Bottom of the pinned alert stack (PeerList's notifBanner region): the
  // expanded body may never grow into/over the always-visible alerts.
  property real alertStackBottom: 0
  // Height of the host left-column (the Column's former parent): bounds the
  // expanded height at 80% of it.
  property real hostHeight: 0

  // --- plain pass-through inputs -------------------------------------------
  property var selectedOwnedRoom: null
  property var themePalette: []
  property bool ownsAnyRoom: false
  property real panelW: 0
  property real panelH: 0

  // --- fn inputs (single-sourced on root) ----------------------------------
  property var diagLine: function(d) { return "" }
  property var shortPath: function(p) { return p }

  // --- confirm flows (moved in with their timers) --------------------------
  // Two-step confirm for the "Clear all chats" action. Resets after a couple
  // of seconds so the button doesn't stay armed.
  property bool confirmClearAll: false
  property Timer clearConfirmTimer: Timer {
    interval: 2500
    onTriggered: settingsPanel.confirmClearAll = false
  }

  // Shows a brief checkmark next to the "Clear all chats" button after a
  // successful clear, instead of a full-width banner.
  property bool showClearAllCheck: false
  property Timer clearAllCheckTimer: Timer {
    interval: 2500
    onTriggered: settingsPanel.showClearAllCheck = false
  }

  // --- action signals (root keeps the implementations) ---------------------
  signal openHelpRequested()
  signal commitNameRequested(string text)
  signal copyToClipboardRequested(string text)
  signal addFriendRequested()
  signal applyPanelSizeRequested(string size)
  signal applyManualSizeRequested(string wText, string hText)
  signal pickDownloadDirRequested()
  signal copyDiagnosticsRequested()

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

  // ---- the original settings Column, body untouched -----------------------
  Column {
    id: settingsCol
    width: parent.width
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
              // Cap height so the expanded body stops at the bottom of the
              // pinned alert stack (notifBanner) — settings can never grow
              // into/over the always-visible alerts, so expanding it only
              // crushes the peer list and never pushes anything off-screen.
              // Expanded height = 80% of the column's full height (user
              // preference), still bounded by the alert stack.
              height: settingsHeader.height + (settingsPanel.expanded
                ? Math.min(settingsBody.contentHeight,
                           Math.min(settingsPanel.hostHeight * 0.8,
                                    Math.max(0, settingsPanel.hostHeight - settingsPanel.alertStackBottom - settingsHeader.height - Style.space(12))))
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
                  onClicked: {
                    settingsPanel.expanded = !settingsPanel.expanded
                    // Mutually exclusive with Rooms: expanding Settings
                    // collapses the rooms list so the two never split the
                    // peer-list space between them.
                    if (settingsPanel.expanded) settingsPanel.collapseRoomsRequested()
                  }
                }

                Text {
                  anchors.left: parent.left
                  anchors.leftMargin: Style.spacing.sm
                  anchors.verticalCenter: parent.verticalCenter
                  text: settingsPanel.expanded ? "Settings ▾" : "Settings ▸"
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
                  onClicked: settingsPanel.openHelpRequested()
                }
              }

              // body (name + online + API + undo + save) — compact rows,
              // scrollable so it never overflows the panel.
              Flickable {
                id: settingsBody
                width: parent.width
                height: Math.min(bodyCol.implicitHeight,
                                 Math.min(settingsPanel.hostHeight * 0.8,
                                          Math.max(0, settingsPanel.hostHeight - settingsPanel.alertStackBottom - settingsHeader.height - Style.space(12))))
                visible: settingsPanel.expanded
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
                    onAccepted: settingsPanel.commitNameRequested(text)
                    onEditingFinished: settingsPanel.commitNameRequested(text)
                    Timer {
                      id: nameSaveTimer
                      interval: 600
                      onTriggered: settingsPanel.commitNameRequested(nameInput.text)
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
                    onClicked: settingsPanel.copyToClipboardRequested(Lanchat.myId)
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
                    onAccepted: settingsPanel.addFriendRequested()
                  }

                  Button {
                    id: addFrButton
                    anchors.right: parent.right
                    anchors.rightMargin: Style.spacing.sm
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Add"
                    onClicked: settingsPanel.addFriendRequested()
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
                  visible: settingsPanel.ownsAnyRoom

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
                    enabled: settingsPanel.selectedOwnedRoom !== null
                    checked: settingsPanel.selectedOwnedRoom
                      ? settingsPanel.selectedOwnedRoom.colorsEnabled !== false : true
                    onToggled: {
                      if (settingsPanel.selectedOwnedRoom)
                        Lanchat.toggleRoomColors(Lanchat.selectedRoomId, !settingsPanel.selectedOwnedRoom.colorsEnabled)
                    }
                  }

                  PanelToolTip {
                    visible: parent.tipHover
                    text: "Applies to rooms YOU own. Off = members' names/bubbles render with the standard theme colors."
                  }
                }

                // ---- Rooms: my color ------------------------------------
                // Every member picks THEIR room color here (moved from the
                // roster): theme-palette swatches (all offered, none
                // filtered) or "Match my theme accent" (token "theme",
                // re-resolves live on theme change). Applies to every room
                // they're a member of. Hidden while colors are disabled in
                // the room they'd apply to.
                Column {
                  visible: Lanchat.rooms.length > 0
                  width: parent.width
                  leftPadding: Style.spacing.sm
                  rightPadding: Style.spacing.sm
                  spacing: Style.space(4)

                  Text {
                    text: "My room color"
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.weight: Font.Bold
                  }

                  Flow {
                    width: parent.width - Style.space(24)
                    spacing: Style.space(4)

                    // Palette swatches: read from the current Omarchy theme
                    // palette (canonical token set shared by colors.toml).
                    Repeater {
                      model: settingsPanel.themePalette

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
                          onClicked: {
                            // Apply to every room I'm in that still has
                            // colors enabled (owner-level kill-switch off
                            // disables rendering, but the choice persists).
                            for (var i = 0; i < Lanchat.rooms.length; i++)
                              Lanchat.setRoomColor(Lanchat.rooms[i].roomId,
                                parent.parent.modelData.token, parent.parent.modelData.hex)
                          }
                        }
                      }
                    }
                  }

                  Button {
                    text: "Match my theme accent"
                    fontSize: Style.font.caption
                    onClicked: {
                      for (var i = 0; i < Lanchat.rooms.length; i++)
                        Lanchat.setRoomColor(Lanchat.rooms[i].roomId, "theme", String(Color.accent))
                    }
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
                      text: settingsPanel.shortPath(Lanchat.downloadDir)
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
                      onClicked: settingsPanel.pickDownloadDirRequested()
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
                    text: settingsPanel.confirmClearAll ? "Confirm?" : "Clear"
                    fontSize: Style.font.caption
                    onClicked: {
                      if (!settingsPanel.confirmClearAll) { settingsPanel.confirmClearAll = true; settingsPanel.clearConfirmTimer.restart() }
                      else {
                        settingsPanel.confirmClearAll = false
                        Lanchat.clearAllChats()
                        settingsPanel.showClearAllCheck = true
                        settingsPanel.clearAllCheckTimer.restart()
                      }
                    }
                  }

                  // Brief checkmark feedback after a successful clear-all.
                  Text {
                    visible: settingsPanel.showClearAllCheck
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

                    Button { width: Style.space(24); height: Style.space(18); text: "S"; fontSize: Style.font.caption; onClicked: settingsPanel.applyPanelSizeRequested("small") }
                    Button { width: Style.space(24); height: Style.space(18); text: "M"; fontSize: Style.font.caption; onClicked: settingsPanel.applyPanelSizeRequested("medium") }
                    Button { width: Style.space(24); height: Style.space(18); text: "L"; fontSize: Style.font.caption; onClicked: settingsPanel.applyPanelSizeRequested("large") }
                    Button { width: Style.space(28); height: Style.space(18); text: "XL"; fontSize: Style.font.caption; onClicked: settingsPanel.applyPanelSizeRequested("xl") }
                    Button { width: Style.space(24); height: Style.space(18); text: "F"; fontSize: Style.font.caption; onClicked: settingsPanel.applyPanelSizeRequested("full") }
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
                      text: Lanchat.customW > 0 ? String(Lanchat.customW) : String(settingsPanel.panelW)
                      maximumLength: 5
                      horizontalPadding: Style.space(4)
                      verticalPadding: Style.space(3)
                      inputMethodHints: Qt.ImhDigitsOnly
                      onEditingFinished: settingsPanel.applyManualSizeRequested(sizeWField.text, sizeHField.text)
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
                      text: Lanchat.customH > 0 ? String(Lanchat.customH) : String(settingsPanel.panelH)
                      maximumLength: 5
                      horizontalPadding: Style.space(4)
                      verticalPadding: Style.space(3)
                      inputMethodHints: Qt.ImhDigitsOnly
                      onEditingFinished: settingsPanel.applyManualSizeRequested(sizeWField.text, sizeHField.text)
                    }
                    Button {
                      text: "Apply"
                      fontSize: Style.font.caption
                      onClicked: settingsPanel.applyManualSizeRequested(sizeWField.text, sizeHField.text)
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
                    onClicked: settingsPanel.copyDiagnosticsRequested()
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
                          text: settingsPanel.diagLine(modelData)
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
            }  }
