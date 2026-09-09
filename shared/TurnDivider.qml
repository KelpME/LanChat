import QtQuick
import qs.Commons

// Voice-change divider: sender name of the run above, a thin rule, sender
// name of the run below — the conversation's turn boundary reads at a
// glance. Shared by ChatThread (1:1: identity = outgoing flag) and
// RoomView (identity = `from`). The caller computes showDivider
// (index > 0 && the identity differs) and both labels.
Column {
  id: turnDivider

  // The caller guards index > 0 and the identity comparison.
  property bool showDivider: true
  property string topLabel: ""
  property string bottomLabel: ""

  width: parent.width
  spacing: Style.space(3)
  visible: showDivider

  Text {
    anchors.horizontalCenter: parent.horizontalCenter
    text: turnDivider.topLabel
    color: Color.popups.text
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }

  Rectangle {
    width: parent.width
    height: 1
    color: Color.popups.border
  }

  Text {
    anchors.horizontalCenter: parent.horizontalCenter
    text: turnDivider.bottomLabel
    color: Color.popups.text
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }
}
